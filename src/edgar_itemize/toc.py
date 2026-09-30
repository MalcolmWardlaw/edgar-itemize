"""Table-of-contents detection over the candidate list.

Turn 3 (overnight casebook, Family II): position-aware duplication, hint
corroboration, cross-kind restarts, mid-run blip tolerance, furniture
bridging, and a chain-completion veto that keeps real stub runs in the tree.

Turn 7 Phase C.2 (`_contract_regions`): credit-agreement tables of contents
carry none of the 10-K evidence — no "TABLE OF CONTENTS" caption, no dot
leaders, no page numbers, no hrefs in the text era — so the generic path leaves
them in the tree, where their rows are accepted as sections and then attached
forward to a body article (`gram.article_after_section`, the F1 toc-like class,
833 documents in runs/overnight/turn6_rebase/aas_doc_classification_v3.parquet).
The discriminating evidence is cross-window and only visible over the whole run:
a dense prose-free run of section rows whose labels are re-found later, spread
over a document region several times longer than the run itself.  See
`_contract_regions` for the rule.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import regex

from .blocks import Block
from .candidates import Candidate

_TOC_HEAD_RE = regex.compile(r"^\s*(?:table of contents|index|contents|form 10-k\s*(?:annual report\s*)?(?:index|table of contents))\b", regex.IGNORECASE)

# TOC furniture between entries: dot/underscore leaders, or a short line ending in
# a page number ("Business ....... 3", "PART II          24").
_FURNITURE_RE = regex.compile(r"(?:\.\s?){4,}|(?:_\s?){4,}|(?:\s|\.)(?:[A-Z]-)?\d{1,3}\s*$")


@dataclass(slots=True)
class TocRegion:
    first_cand: int  # index into candidates list
    last_cand: int
    block_start: int
    block_end: int
    reason: str


def _leader(c: Candidate) -> bool:
    return "toc.leader_or_pageno" in c.rule_ids


def _hinted(c: Candidate) -> bool:
    return c.toc_hint


def _furniture_gap(blocks: list[Block], a: Block, b: Block) -> bool:
    """True when the blocks between a and b are mostly TOC furniture (B1 bridging)."""
    between = [blocks[j] for j in range(a.idx + 1, min(b.idx, a.idx + 25))]
    if not between:
        return False
    furn = sum(1 for x in between if len(x.text) <= 90 and _FURNITURE_RE.search(x.text))
    return furn >= 0.6 * len(between)


_PROSE_RE = regex.compile(r"[a-z]{3,}\s+[a-z]{3,}\s+[a-z]{3,}")

# Contract TOC thresholds (Turn 7 C.2).  Calibrated on the 299-document ctoc bank
# (runs/judge/ctoc-windows.jsonl) — see docs/TURN7_PLAN.md Phase C item 2.
_CTOC_MIN_ROWS = 6  # section rows in the run
_CTOC_MIN_DUP = 0.6  # share of the run's section labels re-found later
_CTOC_SPREAD = 2.5  # later copies must span this many times the run's own span


def _prose_between(blocks: list[Block], a: Block, b: Block, *, limit: int = 40) -> bool:
    """A body paragraph sits between two rows: they are not both TOC entries."""
    for j in range(a.idx + 1, min(b.idx, a.idx + limit)):
        x = blocks[j]
        if x.kind == "para" and len(x.text) > 200 and _PROSE_RE.search(x.text):
            return True
    return False


def _contract_regions(  # noqa: C901
    blocks: list[Block],
    cands: list[Candidate],
    *,
    max_gap_blocks: int,
    max_gap_chars: int,
) -> list[TocRegion]:
    """toc.section_run / toc.dup_later_contract — leaderless contract tables of contents.

    Runs are built over the section and article candidates alone (clause labels all
    carry order key 0, so a single "(a)" between two rows would read as an order
    restart and split the run).  A run is a maximal sequence of section/article
    candidates that are close in blocks and characters, carry no body paragraph
    between them, and whose per-kind order keys strictly increase.

    A run is condemned when it is numerous (`toc.section_run`: >= 6 section rows,
    consecutive heads within `max_gap_chars` normalized characters, no body
    paragraph between them) AND most of its labels are re-found later in the
    document, spread over a stretch several times longer than the run itself
    (`toc.dup_later_contract`).  The spread test is what separates a table of
    contents from the first agreement of a compound exhibit, whose repeated section
    list is as long as the run; the density test is what separates it from a
    genuinely terse body.  Spans are normalized characters, not raw bytes: a
    table-cell index row carries a kilobyte of markup and a text-era one carries
    none, so raw offsets would measure the publisher rather than the density.

    Unlike the 10-K path this asks nothing of the later copies' scores.  A contract
    section heading in the body runs straight into its own prose ("SECTION 6.01.
    Events of Default.  Any one or more of ..."), which costs it the short-block and
    style evidence a one-line TOC row collects, so body copies routinely score
    *below* the rows that index them (the ctoc bank: rows 0.55-0.65, body copies
    0.45-0.55).  Requiring a stronger later copy, as `toc.duplicated_later` does,
    would condemn none of them.
    """
    idx = [i for i, c in enumerate(cands) if c.kind in ("section", "article")]
    if len(idx) < _CTOC_MIN_ROWS:
        return []
    runs: list[list[int]] = []
    cur: list[int] = []
    run_max: dict[str, int] = {}
    for i in idx:
        b = cands[i]
        bb = blocks[b.block_idx]
        ok = False
        if cur:
            a = cands[cur[-1]]
            ba = blocks[a.block_idx]
            blk_limit = max_gap_blocks * 4 if (ba.in_table and bb.in_table) else max_gap_blocks
            # row-to-row distance, measured head to head: a section heading that runs
            # into its own prose lives in one long block, so block-end-to-block-start
            # (the 10-K measure) reads as zero and glues a whole body into one "run"
            dist = b.norm_start - a.norm_start
            close = bb.idx - ba.idx <= blk_limit and 0 <= dist <= max_gap_chars
            if not close and 0 <= dist <= max_gap_chars * 6 and _furniture_gap(blocks, ba, bb):
                close = True
            ok = close and b.order_key > run_max.get(b.kind, -1) and not _prose_between(blocks, ba, bb)
        if ok:
            cur.append(i)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [i]
            run_max = {}
        run_max[b.kind] = max(run_max.get(b.kind, -1), b.order_key)
    if len(cur) >= 2:
        runs.append(cur)

    # section candidates by label, in index order, so a run's later copies are a
    # bisect per label rather than a scan of the whole tail per run (mega-exhibits
    # carry tens of thousands of candidates and hundreds of runs)
    by_label: dict[str, list[int]] = {}
    for i, c in enumerate(cands):
        if c.kind == "section":
            by_label.setdefault(c.label_canon, []).append(i)

    out: list[TocRegion] = []
    for run in runs:
        members = [cands[i] for i in run]
        sec = [c for c in members if c.kind == "section"]
        if len(sec) < _CTOC_MIN_ROWS:
            continue
        labels = {c.label_canon for c in sec}
        later: list[Candidate] = []
        n_dup = 0
        for lab in labels:
            idxs = by_label[lab]
            j = bisect_right(idxs, run[-1])
            if j < len(idxs):
                n_dup += 1
                later += [cands[k] for k in idxs[j:]]
        if n_dup < _CTOC_MIN_DUP * len(labels):
            continue
        # spans in normalized characters, not raw bytes: a table-cell index row carries
        # a kilobyte of markup and a text-era one carries none, so raw offsets measure
        # the publisher, not the density
        span_run = max(c.norm_start for c in sec) - min(c.norm_start for c in sec)
        span_later = max(c.norm_start for c in later) - min(c.norm_start for c in later)
        if span_later < _CTOC_SPREAD * max(1, span_run):
            continue  # the "later copies" are another agreement's list, not this run's body
        for i in run:
            if "toc.section_run" not in cands[i].rule_ids:
                cands[i].rule_ids.append("toc.section_run")
        out.append(TocRegion(run[0], run[-1], members[0].block_idx, members[-1].block_idx, "toc.dup_later_contract"))
    return out


def detect_toc(
    blocks: list[Block],
    cands: list[Candidate],
    *,
    max_gap_blocks: int = 4,
    max_gap_chars: int = 600,
    norm_len: int = 0,
    grammar: str = "form10k",
) -> list[TocRegion]:
    """Find dense runs of candidates that are duplicated later or carry TOC hints.

    A run is a maximal sequence of candidates where consecutive members are
    within `max_gap_blocks` blocks and `max_gap_chars` normalized characters
    (bridged across TOC-furniture blocks), a per-kind order restart starts a
    new run, and a first-of-its-kind candidate arriving at minimal order key
    after an established run is treated as the body beginning (B2 overreach).
    """
    if not cands:
        return []
    min_key: dict[str, int] = {}
    for c in cands:
        min_key[c.kind] = min(min_key.get(c.kind, c.order_key), c.order_key)
    runs: list[list[int]] = [[0]]
    run_max: dict[str, int] = {cands[0].kind: cands[0].order_key}
    for i in range(1, len(cands)):
        a, b = cands[runs[-1][-1]], cands[i]
        ba, bb = blocks[a.block_idx], blocks[b.block_idx]
        # table-of-contents tables emit many small cell blocks between entries: allow a wider block gap there
        blk_limit = max_gap_blocks * 4 if (ba.in_table and bb.in_table) else max_gap_blocks
        close = bb.idx - ba.idx <= blk_limit and bb.norm_start - ba.norm_end <= max_gap_chars
        if not close and bb.norm_start - ba.norm_end <= max_gap_chars * 6 and _furniture_gap(blocks, ba, bb):
            close = True  # text-era TOCs interleave dot-leader lines between candidate rows (B1)
        restart = b.order_key <= run_max.get(b.kind, -1)  # order restarts: a new sequence begins
        if restart and close:
            # mid-run blip: a single mislabeled row ("Item X" read as a regression)
            # does not end the run when the next member restores order (B2 underreach)
            nxt = cands[i + 1] if i + 1 < len(cands) else None
            if (
                nxt is not None
                and nxt.kind in run_max
                and nxt.order_key > run_max.get(nxt.kind, -1)
                and blocks[nxt.block_idx].norm_start - bb.norm_end <= max_gap_chars
            ):
                runs[-1].append(i)  # absorbed; run_max deliberately not updated
                continue
        # a kind never seen in an established run, entering at its minimal order key,
        # is the body starting (PART I after an items-only TOC run), not a TOC row (B2)
        body_start = b.kind not in run_max and len(runs[-1]) >= 4 and b.order_key <= min_key.get(b.kind, -1)
        if close and not restart and not body_start:
            runs[-1].append(i)
            # forward-jump blip: a mislabeled row ("Item X" read as ITEM 10) jumps
            # ahead while the NEXT row continues from the pre-jump position — keep
            # the row but do not let it poison run_max (B2 underreach)
            nxt = cands[i + 1] if i + 1 < len(cands) else None
            fwd_blip = (
                nxt is not None
                and nxt.kind == b.kind
                and run_max.get(b.kind, -1) < nxt.order_key <= b.order_key
                and blocks[nxt.block_idx].norm_start - bb.norm_end <= max_gap_chars
            )
            if not fwd_blip:
                run_max[b.kind] = max(run_max.get(b.kind, -1), b.order_key)
        else:
            runs.append([i])
            run_max = {b.kind: b.order_key}
    regions: list[TocRegion] = []
    # labels seen after each run (for duplication test)
    for run in runs:
        if len(run) < 4:
            continue
        labels = {cands[i].label_canon for i in run}
        later = [c for c in cands[run[-1] + 1 :]]
        # duplication evidence must come from plausible body headings: weak or
        # continuation-penalized later copies are page furniture (running heads in
        # multi-registrant filings), not proof this run is a TOC
        later_strong = [c for c in later if c.score >= 0.55 and "rej.continued" not in c.rule_ids]
        later_labels = {c.label_canon for c in later_strong}
        dup_frac = len(labels & later_labels) / max(1, len(labels))
        leader_frac = sum(1 for i in run if _leader(cands[i])) / len(run)
        hint_frac = sum(1 for i in run if _hinted(cands[i])) / len(run)
        head_block = blocks[cands[run[0]].block_idx]
        preceded_by_toc_word = any(
            _TOC_HEAD_RE.match(blocks[j].text) for j in range(max(0, head_block.idx - 8), head_block.idx)
        )
        # back-index guard: when the later duplicates are themselves an index in the
        # document's tail (leader-hinted, final 30%), the EARLIER run is the body —
        # a back-of-document cross-reference schedule must not condemn it (E2/A3c)
        dup_later = [c for c in later_strong if c.label_canon in labels]
        back_dup = False
        if norm_len > 0 and dup_later:
            back = [c for c in dup_later if blocks[c.block_idx].norm_start >= 0.7 * norm_len]
            back_dup = len(back) >= 0.6 * len(dup_later) and sum(1 for c in back if _leader(c)) >= 0.5 * max(1, len(back))
        # evidence asymmetry: a real body run outscores its index copies (a back
        # cross-reference schedule with embedded page numbers carries no leader
        # hint but scores visibly below the true headings it repeats); a real TOC
        # never outscores its body duplicates.  Compared per label — each member
        # against its own best later copy — so a couple of well-styled index rows
        # cannot mask the pattern
        best_later: dict[str, float] = {}
        for c in dup_later:
            best_later[c.label_canon] = max(best_later.get(c.label_canon, 0.0), c.score)
        margins = [cands[i].score - best_later[cands[i].label_canon] for i in run if cands[i].label_canon in best_later]
        if margins:
            back_dup = back_dup or sum(1 for m in margins if m >= 0.125) >= 0.6 * len(margins)
        # a TOC run is one whose members are mostly re-found later OR carry TOC hints;
        # href-only hints need corroboration (bare-anchor templates poison them — D2)
        reason = None
        if leader_frac >= 0.5:
            reason = "toc.hints"
        elif hint_frac >= 0.5 and (preceded_by_toc_word or leader_frac >= 0.2 or dup_frac >= 0.6):
            reason = "toc.hints"
        elif dup_frac >= 0.6 and (preceded_by_toc_word or hint_frac >= 0.2 or len(run) >= 8) and not back_dup:
            reason = "toc.duplicated_later"
        elif dup_frac >= 0.75 and len(run) >= 4 and all(cands[i].kind == "part" for i in run):
            reason = "toc.parts_only"
        elif preceded_by_toc_word and dup_frac >= 0.4:
            reason = "toc.header_word"
        if reason:
            regions.append(
                TocRegion(
                    first_cand=run[0],
                    last_cand=run[-1],
                    block_start=cands[run[0]].block_idx,
                    block_end=cands[run[-1]].block_idx,
                    reason=reason,
                )
            )
    if grammar == "contract":
        # A contract index usually carries a partial generic region too (its first rows
        # sit under a "TABLE OF CONTENTS" caption, or a clause label splits the generic
        # run part-way down).  Absorb any region the contract run overlaps rather than
        # standing down, so the condemned span is the whole index and not its head.
        regions += _contract_regions(blocks, cands, max_gap_blocks=max_gap_blocks, max_gap_chars=max_gap_chars)
        regions.sort(key=lambda r: (r.first_cand, r.last_cand))
        merged: list[TocRegion] = []
        for r in regions:
            if merged and r.first_cand <= merged[-1].last_cand:
                prev = merged[-1]
                prev.last_cand = max(prev.last_cand, r.last_cand)
                prev.first_cand = min(prev.first_cand, r.first_cand)
                prev.block_start = cands[prev.first_cand].block_idx
                prev.block_end = cands[prev.last_cand].block_idx
                if r.reason == "toc.dup_later_contract":
                    prev.reason = r.reason
                continue
            merged.append(r)
        regions = merged
    return veto_chain_completing(cands, regions)


def veto_chain_completing(cands: list[Candidate], regions: list[TocRegion]) -> list[TocRegion]:
    """Drop a proposed region whose members are the document's ONLY copies of the
    labels they carry (E2 chain-completion veto).

    A real TOC's entries are re-found in the body; a condemned run of genuine
    stub headings (IBR one-liners) is not.  Requires >=2 strong members (score
    >= 0.6, no leader/page-number text) whose (kind, label) appears on no
    candidate outside every proposed region, so leader-hinted rows and true
    TOCs in image-only documents cannot un-condemn themselves.

    `toc.dup_later_contract` regions are exempt: they are proposed only when most
    of the run IS re-found later, and a handful of rows with no body copy (a
    section the body typesets in a form the grammar misses) must not un-condemn a
    fifty-row index.  Those rows are kept by the per-member rescue in
    `tree_contract.build_contract_tree` instead, which loses no label.
    """
    if not regions:
        return regions
    in_region: set[int] = set()
    for r in regions:
        in_region.update(range(r.first_cand, r.last_cand + 1))
    outside = {(c.kind, c.label_canon) for i, c in enumerate(cands) if i not in in_region}
    kept: list[TocRegion] = []
    for r in regions:
        if r.reason == "toc.dup_later_contract":
            kept.append(r)
            continue
        sole_strong = [
            i
            for i in range(r.first_cand, r.last_cand + 1)
            if cands[i].score >= 0.6 and not _leader(cands[i]) and (cands[i].kind, cands[i].label_canon) not in outside
        ]
        if len(sole_strong) >= 2:
            for i in range(r.first_cand, r.last_cand + 1):
                cands[i].rule_ids.append("toc.vetoed_region")
            continue
        kept.append(r)
    return kept
