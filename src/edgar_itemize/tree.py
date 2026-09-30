"""Grammar-driven tree construction from candidates.

Selection is a maximum-weight strictly-increasing subsequence per level: among
all sets of candidates whose order keys strictly increase with document
position, take the one with the largest total score.  This is deterministic,
tolerant of undetected TOCs (their members carry lower scores) and of
cross-references (they carry negative evidence), and needs no thresholds
beyond a minimum score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import regex

from .blocks import Block
from .candidates import Candidate
from .grammar.form10k import Form10KGrammar
from .toc import _FURNITURE_RE, TocRegion

# gram.synth_item1 (Turn 8): the caption a 10-Q's financial statements actually open
# with, when no "Item 1" heading exists to anchor them (runs/overnight/G1/report.md
# class 2; confirmed at scale in docs/turn8_decisions/a6_10q.md section 4 -- 742/2229
# of the Turn 8 A.6 sample's I.1 misses have the label only inside a correctly-rejected
# TOC row). Matched only against a whole line (optionally preceded by a short company
# name run and followed only by "(Unaudited)"/punctuation) so an ordinary sentence that
# merely mentions "the accompanying consolidated balance sheets" cannot match.
_FIN_CAPTION_RE = regex.compile(
    r"(?:^|\n)\s*[\"'\(\[\*•\-]*"
    r"(?:[A-Z0-9][A-Za-z0-9,\.&'\-]*(?:\s+[A-Za-z0-9,\.&'\-]+){0,6}\s+)?"
    r"(?:index\s+to\s+)?(?:condensed\s+)?(?:consolidated\s+)?"
    r"(?:balance\s+sheets?"
    r"|statements?\s+of\s+(?:income|operations|cash\s+flows?|stockholders.?\s+equity|"
    r"changes\s+in\s+stockholders.?\s+equity|comprehensive\s+income(?:\s*\(loss\))?)"
    r"|financial\s+statements(?:\s+and\s+supplementary\s+data)?"
    r"|notes\s+to\s+(?:the\s+)?(?:condensed\s+)?(?:consolidated\s+)?financial\s+statements)"
    r"\s*(?:\(unaudited\))?\s*[\.:]?\s*$",
    regex.IGNORECASE | regex.MULTILINE,
)


@dataclass(slots=True)
class Node:
    node_id: int
    parent_id: int
    depth: int
    level_kind: str
    label_canon: str | None
    label_raw: str | None
    title: str | None
    raw_start: int
    raw_end: int
    head_raw_start: int
    head_raw_end: int
    norm_start: int
    norm_end: int
    order_key: int
    confidence: float
    rule_ids: list[str] = field(default_factory=list)
    # EX-13 (annual report) headings only: "ITEM 6"/"ITEM 7"/"ITEM 8" that this
    # heading's content satisfies, per the ars kind->item mapping; null elsewhere.
    satisfies_item: str | None = None


@dataclass(slots=True)
class Rejected:
    block_idx: int
    kind: str
    label_canon: str
    score: float
    reason: str
    raw_start: int
    text: str


def max_weight_increasing(items: list[tuple[int, float]]) -> list[int]:
    """items[i] = (order_key, weight) in document order. Return chosen indices.

    Strictly increasing order_key; maximize total weight; ties broken toward
    the chain whose members lie later in the document (body beats TOC).
    """
    n = len(items)
    best = [0.0] * n
    prev = [-1] * n
    for i in range(n):
        k, w = items[i]
        best[i] = w
        for j in range(i):
            if items[j][0] < k and best[j] + w >= best[i] - 1e-9:
                # >= prefers later j on ties
                if best[j] + w > best[i] + 1e-9 or prev[i] == -1 or j > prev[i]:
                    best[i] = best[j] + w
                    prev[i] = j
    if n == 0:
        return []
    end = max(range(n), key=lambda i: (round(best[i], 6), i))
    out = []
    while end != -1:
        out.append(end)
        end = prev[end]
    return out[::-1]


def _synth_item1(
    blocks: list[Block],
    cands: list[Candidate],
    toc_regions: list[TocRegion],
    toc_idx: set[int],
    live: set[int],
    grammar,
    part_nodes: dict[str, "Node"],
    item_nodes: list[tuple["Node", str]],
    doc_raw_end: int,
    norm_len: int,
) -> None:
    """gram.synth_item1 (Turn 8): 10-Q only, called from `build_tree`.

    When Part I has an explicit heading and an Item 2 was found, but no Item 1
    candidate exists anywhere between them (accepted, low-score, nonmonotone, or
    inside a detected TOC region -- a TOC-only "Item 1." is exactly the class this
    exists to fix), synthesize an ITEM I.1 node at the first financial-statement
    caption in that span: the statements are always present (they are what "Item 1"
    means in a 10-Q), so this recovers a real span the grammar otherwise leaves
    unheaded rather than inventing content. Confidence is capped at 0.5, matching
    the other `gram.synth_*` placeholders (`gram.synth_part`, tree_contract's
    `gram.synth_article`) -- never a match for a real found heading's score.

    Does nothing if Part I is itself synthesized (no explicit heading to open a
    span after), if Item 2 was not found (no upper bound for the search), if a
    real Item 1 candidate is found anywhere in the span, or if no caption-shaped
    line is found in the span (no invented anchor without textual evidence).

    "A real Item 1 candidate" means any item-kind candidate with key "1" in the
    span that was NOT rejected specifically as a TOC row: `live` already carries
    a `toc.member_rescued` candidate whose region was condemned but that still
    won its own slot in the tree (the obvious case: it must not be shadowed by a
    second, synthesized node with the same label -- this was caught by the Turn 8
    gate's anchor-move check, docs/turn8_decisions/b_10q_gate.md, before this
    `live` parameter existed). Only a candidate BOTH inside a TOC region AND not
    rescued (`i in toc_idx and i not in live`, i.e. genuinely `rej(c, "toc")`) is
    excluded from counting as evidence.

    The span's lower bound is `part1.raw_start`, not `head_raw_end`: the same
    gate run caught two more duplicate-node cases where a real Item 1 candidate
    sits at or before Part I's own `head_raw_end` -- `gram.part_after_item`
    (Part I's heading is typeset after its first item, so the node's start gets
    pulled back to the item but `head_raw_end` still reflects the later heading
    text) and `pos.inline_after_part` (Item 1 runs into the same block as the
    Part heading, so its own head_raw_start can precede the Part's head_raw_end).
    `raw_start` is pulled back to cover both.

    Each candidate's own position (`c.head_raw_start`), not its block's
    (`blocks[c.block_idx].raw_start`), is what the span check compares: a
    sub-block heading found on a later line of a multi-line block or table row
    (`pos.line2`/`pos.line3`/table-cell candidates) can sit well after its
    block's own start, which otherwise undercounted the span's lower bound the
    same way `head_raw_end` did and produced the same duplicate-node symptom.
    """
    part1 = part_nodes.get("PART I")
    if part1 is None or "gram.synth_part" in part1.rule_ids:
        return
    item2 = next((n for n, want in item_nodes if want == "PART I" and grammar.item_key(n.label_canon) == "2"), None)
    if item2 is None:
        return
    span_lo, span_hi = part1.raw_start, item2.head_raw_start
    if span_hi <= span_lo:
        return
    toc_rejected = toc_idx - live
    has_item1 = any(
        i not in toc_rejected and c.kind == "item" and grammar.item_key(c.label_canon) == "1"
        and span_lo <= c.head_raw_start < span_hi
        for i, c in enumerate(cands)
    )
    if has_item1:
        return
    toc_block_idx = {i for r in toc_regions for i in range(r.block_start, r.block_end + 1)}
    for b in blocks:
        if b.idx in toc_block_idx or not (span_lo <= b.raw_start < span_hi):
            continue
        probe = b.row_text if b.in_table and b.row_text else b.text
        if not _FIN_CAPTION_RE.search(probe):
            continue
        # an UNDETECTED toc/index run (the recurring soft spot: docs/turn8_decisions/
        # a6_10q.md section 5 finding 2) can still bracket a caption-shaped line even
        # when the region itself was never flagged -- a caption entry that itself lacks
        # a trailing page number (this one, or a wrapped one) slips past the toc_idx
        # check above. The block immediately before it is strong, cheap evidence: every
        # OTHER entry in an index has dot leaders or a trailing page number
        # (`_FURNITURE_RE`, toc.py); real body prose does not immediately precede a
        # real caption that way. Skip and keep scanning for a later, real occurrence.
        # Skip this check when the predecessor is itself a DETECTED toc block -- that
        # case is already handled by the toc_block_idx filter above, and a block that
        # follows a real, flagged TOC region is normal, expected body content.
        if b.idx > 0 and (b.idx - 1) not in toc_block_idx and _FURNITURE_RE.search(blocks[b.idx - 1].text):
            continue
        n = Node(0, 0, 2, "item", "ITEM I.1", None, "Financial Statements",
                  b.raw_start, doc_raw_end, b.raw_start, b.raw_start,
                  b.norm_start, norm_len, grammar.order_key("item", "ITEM I.1"), 0.5, ["gram.synth_item1"])
        item_nodes.append((n, "PART I"))
        return


# seq.out_of_order (Turn 8 B.4): index evidence that bars the second placement pass.
# `toc.vetoed_region` on its own is NOT here — per toc.veto_chain_completing that tag means
# a proposed region was REJECTED as a table of contents because its members are the
# document's only copies of their labels, which A.5 read as evidence FOR real content
# (docs/turn8_decisions/a5_out_of_order.md, "Recommended acceptance condition").  What the
# tag marks in practice is `toc.vetoed_index` below.
_OOO_TOC_EVIDENCE = ("toc.leader_or_pageno", "toc.leader_or_pageno_wrapped", "toc.href")

# toc.vetoed_index: the vetoed run this candidate belongs to is a real table of contents,
# not the two-or-three-row stub listing the chain-completion veto was built for.  A.5
# assumed the veto marked stub runs; on the 7,782 nodes the first version of this rule
# produced, the smallest vetoed run holding one has SIX item rows and the median has
# twenty, 2,999 of 3,052 are in strict statutory order, and all 24 vetoed nodes in a
# 60-node hand read are index rows while all 34 real headings carry no vetoed tag at all
# (docs/turn8_decisions/b4_out_of_order.md, "The vetoed-run table").  The threshold is set
# at four item rows -- A.5's stub case is two or three -- and is deliberately not tuned:
# anything from 2 to 9 gives the same answer on this population.  Page-number share is not
# part of the test: a table-cell TOC puts the page in a neighbouring block, so 260 of the
# 3,052 carry no index evidence on the rows themselves.
_VETOED_INDEX_MIN_ITEMS = 4
# A pointer sentence that opens with the statutory title is not a heading, whatever the
# chain did with it (candidates.py `rej.xref_pointer`).
#
# Turn 9 B.2 (docs/turn9_decisions/b2_caption_guard.md) adds the three cover-page caption
# tags on the same terms, and for the same reason: a row of a Rule 12b-25 omission notice,
# of a "documents incorporated by reference" table, or of a filer agent's item-to-page
# cross-reference table says the item is NOT in this document, so it cannot open one.
# They were the dominant residual false-accept family of this rule in the Turn 4 bank --
# 26 of its 39 judged false accepts (docs/turn8_decisions/b4_out_of_order.md, "Every new
# false accept, read", families 1-3).  Like `rej.xref_pointer` they carry no score, so an
# item the monotone chain accepted is untouched; only a NEW out-of-order placement is
# barred.  Membership is list/table-scoped so the Instruction J follower -- the one item
# that is NOT omitted, printed after the omitted-items list, and the single largest class
# this rule recovers -- can never be tagged.
# Turn 10 B.2 (docs/turn10_decisions/a3_fmtable.md) adds `rej.fm_table` on the same terms:
# a row of a >= 2-row front-matter table whose second column says where the item's
# content actually is (the cover-page IBR tables and the filer-agent item-to-page
# cross-reference tables, b4 families 2-3, which the caption guard cannot reach because
# their pointer is never on the label's own line).  78 accepted out-of-order nodes in 33
# filings are barred by it corpus-wide, none of them a body heading
# (runs/judge/turn10-fmtable-census.txt section 5).
_OOO_BLOCKING = ("rej.xref_pointer", "rej.caption_omission", "rej.caption_ibr", "rej.caption_xref",
                 "rej.fm_table")


def vetoed_index_run(cands: list[Candidate], i: int, cache: dict[int, bool] | None = None) -> bool:
    """toc.vetoed_index — is candidate `i` a row of a long, ordered vetoed index?

    The run is the maximal stretch of consecutive candidates carrying
    `toc.vetoed_region` (toc.veto_chain_completing tags every member of the region it
    dropped, so consecutive tags are that region).  It counts as an index when it holds
    at least `_VETOED_INDEX_MIN_ITEMS` item candidates whose order keys strictly increase.
    """
    c = cands[i]
    if "toc.vetoed_region" not in c.rule_ids:
        return False
    lo = hi = i
    while lo > 0 and "toc.vetoed_region" in cands[lo - 1].rule_ids:
        lo -= 1
    if cache is not None and lo in cache:
        return cache[lo]
    while hi + 1 < len(cands) and "toc.vetoed_region" in cands[hi + 1].rule_ids:
        hi += 1
    keys = [cands[j].order_key for j in range(lo, hi + 1) if cands[j].kind == "item"]
    out = len(keys) >= _VETOED_INDEX_MIN_ITEMS and all(a < b for a, b in zip(keys, keys[1:]))
    if cache is not None:
        cache[lo] = out
    return out


def _multi_qualified_keys(label_canon: str, rule_ids: list[str], *, is_form10q: bool) -> list[str]:
    """Turn 12 B.4 (A12): the Part-qualified form of every `multi.ITEM <k>` bare key a
    candidate carries, read off ITS OWN `label_canon` -- on `form10q` a covered item
    shares its carrying heading's Part by construction ("Items 2 and 3." is one heading,
    not two), so no parent lookup is needed, exactly as pipeline.py's `result_rows` reads
    it for `items_found` (section 6.4). On any other grammar (or an unresolved "ITEM
    ?.<k>" canon, which should not reach this function live) the bare key is returned
    unqualified, matching the pre-Turn-12 behavior."""
    keys = [rid.split(" ", 1)[1] for rid in rule_ids if rid.startswith("multi.ITEM ")]
    if is_form10q and " " in label_canon and "." in label_canon.split(" ", 1)[1]:
        part = label_canon.split(" ", 1)[1].split(".", 1)[0]
        return [f"ITEM {part}.{k}" for k in keys]
    return [f"ITEM {k}" for k in keys]


def out_of_order_items(
    cands: list[Candidate], chosen_items: list[int], dropped_items: list[int], toc_idx: set[int],
    grammar=None,
) -> list[int]:
    """seq.out_of_order — a second placement pass after the monotone item chain.

    The chain (`max_weight_increasing`) is one strictly-increasing run over the whole
    document, so a filer who typesets Item 1B after Item 4, or binds the financial
    statements behind the exhibit index, loses that item outright: the candidate is real,
    scored and unopposed, and is still dropped as `nonmonotone` because accepting it would
    break order somewhere else.  12,034 such candidates in 7,950 filings of runs/full_v17
    carry a label the accepted chain never claims anywhere
    (runs/judge/turn8-ooo-census.parquet).

    A dropped candidate is accepted out of order when all of:
      * its `label_canon` is claimed by no accepted item, directly or through a combined
        heading's `multi.ITEM <k>` coverage — the pass adds labels, it never competes for
        one the chain already placed;
      * it carries `gram.item.title_match` — the item's own statutory title opens the text
        right after the label;
      * it carries no index evidence: no `toc.*` hint rule, no membership of a live TOC
        region (whose members are rejected `toc`, or rescued and hence still index-adjacent),
        and no membership of a long ordered vetoed index (`toc.vetoed_index`);
      * it is not a `rej.xref_pointer` sentence, it is not a member of a cover-page
        caption's list or table (`rej.caption_omission`, `rej.caption_ibr`,
        `rej.caption_xref` — Turn 9 B.2), and it is not a row of a two-column front-matter
        table (`rej.fm_table` — Turn 10 B.2).

    Position class is not part of the gate — on the 197 judged `rej_nonmonotone` windows the
    positive rate is the same before, inside and after the chain — it only decides where the
    node goes.  Where two dropped candidates carry the same unclaimed label (66 rows in the
    census) the tie-break is the higher score, then the earlier position, tagged
    `seq.out_of_order_tiebreak`.

    Turn 12 B.4 (A8/A12, docs/turn12_decisions/a4_label_family.md sections 6.4/6.5): the
    `multi.ITEM <k>` coverage above is now Part-qualified on `form10q` (`grammar`, when
    given), matching pipeline.py's `items_found` construction so the two agree on what a
    heading's own Part already claims. A dropped candidate that itself carries
    `lbl.item.multi` (a rejected "Items N and M" heading, A8 section 1.5) is ALSO admitted
    without `gram.item.title_match` when its own label_canon and every one of its covered
    keys' Part-qualified forms are unclaimed AND the title (already stripped of the "and
    M" range text by `candidates._try_match`) opens with the statutory title of the
    carrying key or any covered key -- reusing the existing `lbl.item.multi`/
    `multi.ITEM <k>` tagging and `grammar.title_matches`, not new candidate logic. Scoped
    to `form10q` only (`grammar.name == "form10q"`): the loss condition (section 1.5) was
    measured only on the 10-Q's own rejected-candidate population (35 of 97 real,
    concentrated in one filer), and the 10-K's combined-label population was never banked.

    Returns the chosen candidate indices, sorted.
    """
    is_form10q = grammar is not None and grammar.name == "form10q"
    claimed: set[str] = set()
    for i in chosen_items:
        c = cands[i]
        claimed.add(c.label_canon)
        claimed.update(_multi_qualified_keys(c.label_canon, c.rule_ids, is_form10q=is_form10q))
    title_probe = getattr(grammar, "title_matches", None) if grammar is not None else None
    pool: dict[str, list[int]] = {}
    run_cache: dict[int, bool] = {}
    for i in dropped_items:
        c = cands[i]
        covered_qualified = _multi_qualified_keys(c.label_canon, c.rule_ids, is_form10q=is_form10q)
        if c.label_canon in claimed or i in toc_idx or any(k in claimed for k in covered_qualified):
            continue
        combined = is_form10q and "lbl.item.multi" in c.rule_ids
        if "gram.item.title_match" not in c.rule_ids:
            if not combined or title_probe is None:
                continue
            candidate_keys = [c.label_canon] + covered_qualified
            if not any(title_probe(c.kind, k, c.title) for k in candidate_keys):
                continue
        if any(r in c.rule_ids for r in _OOO_TOC_EVIDENCE) or any(r in c.rule_ids for r in _OOO_BLOCKING):
            continue
        if vetoed_index_run(cands, i, run_cache):
            c.rule_ids.append("toc.vetoed_index")
            continue
        pool.setdefault(c.label_canon, []).append(i)
    out: list[int] = []
    for lab, idxs in pool.items():
        best = max(idxs, key=lambda i: (cands[i].score, -cands[i].head_raw_start, -i))
        cands[best].rule_ids.append("seq.out_of_order")
        if len(idxs) > 1:
            cands[best].rule_ids.append("seq.out_of_order_tiebreak")
        if "gram.item.title_match" not in cands[best].rule_ids:
            # Turn 12 B.4 (A12 section 6.5): promoted solely via the combined-label
            # title-match test above -- traceable separately from an ordinary
            # `seq.out_of_order` recovery, which always carries `gram.item.title_match`.
            cands[best].rule_ids.append("seq.out_of_order_multi")
        out.append(best)
    return sorted(out)


# seq.strong_duplicate (Turn 9 B.4, widened to the 10-K in the B.4k addendum): the
# same-order_key tiebreak.
#
# `max_weight_increasing` maximizes the TOTAL weight of one strictly-increasing run, so
# when the same order key has two live candidates it can keep the weak one because that
# placement lets the rest of the chain follow it: an early "Item 1." stub scoring 0.35
# with Items 2-6 behind it beats a lone body "ITEM 1. FINANCIAL STATEMENTS" scoring 1.05
# that is typeset after them (the financial statements bound at the back of the filing).
# The chain is right about the SLOT -- the label belongs in the tree once -- and wrong
# about WHICH copy fills it, so the fix swaps the copy and leaves the chain alone.
#
# Margin: 0.3, from the gaps the Turn 9 A.5 bank measured on real cases (0.35 vs 1.05,
# 0.5 vs 0.95 -- docs/turn9_decisions/a5_10q_full.md section 4.2).  Below that the two
# copies are ordinary scoring noise and the chain's own answer stands.
#
# Why a swap and not a re-run of the dynamic program with the weak copy suppressed: in
# 96.9% of the corpus-scale pairs the strong copy sits LATER than the weak one
# (4,570/4,715 at margin 0.3, runs/judge/turn9-b4-reach-10q.txt), i.e. after items whose
# order keys are higher, so forcing it into a strictly-increasing run costs those items
# their slots.  Re-running the program trades a recovered anchor for one or more lost
# labels; the swap trades nothing -- the label set the chain produced is exactly
# preserved, only the anchor, score and rules of one node change.
_STRONG_DUPLICATE_MARGIN = 0.3
# Turn 9 B.4 scoped this to the 10-Q, where A.5's bank measured the shape, as a module
# constant rather than an inline `grammar.name == "form10q"` so the 10-K measurement
# (does the same collision exist there, and what would widening change?) could run the
# pass unscoped without pretending a 10-K is a 10-Q (scripts/turn9/b4_unscoped_10k.py
# patched this tuple for that measurement). B.4 section 6 found the identical shape at
# about a quarter of the 10-Q's per-filing rate (539 pairs / 195 of 228,803 10-K filings,
# 0.085%, 96.8% strong-copy-later) and recommended widening as a follow-up gated on its
# own full run_diff. The B.4k addendum (docs/turn9_decisions/b4k_10k_widen.md) is that
# gate: `runs/full_v21` -> `runs/full_v22`, item/part labels +0/-0, core_complete and
# toc_found unchanged, every anchor move enumerated and hand-read. `Form10KGrammar` is
# also the grammar EX-13 (annual report) documents are parsed with (cli.py's `ex13` kind
# forces `synth_root` but reuses the same grammar object), so widening here reaches EX-13
# too -- b4k_10k_widen.md measures that run separately (`runs/full_ex13_v5` ->
# `runs/full_ex13_v6`).
_STRONG_DUPLICATE_GRAMMARS = ("form10q", "form10k")
# A running page header repeating an earlier heading is not a section start whatever it
# scores, so it can never be promoted over the copy the chain picked (C3 M1, page_repeat_pass).
_STRONG_DUPLICATE_BLOCKING = ("rej.continued", "rej.page_repeat")
# Only a LATER copy may take the slot, which is the direction A.5 section 4.2 asked for
# ("even if it sits later in the document") and the only direction the corpus gate
# supports.  The first, unguarded version of this pass made 6,487 anchor moves on
# runs/full_10q_v1 -> v2, 280 of them backwards; a 15-window hand read of the backwards
# ones (runs/judge/turn9-b4-handread-earlier.txt) found 11 of 15 wrong -- they pull a real
# body heading back onto the front-of-document index row that outscores it -- against 28
# of 30 right in the forward direction (runs/judge/turn9-b4-handread.txt).  The cost is
# the four short-distance backwards moves in that sample that were right (a TOC row deep
# in the document losing to a body heading a few thousand bytes earlier); a distance
# threshold would keep them, and is exactly the unprincipled tuning this margin was
# chosen to avoid.
_STRONG_DUPLICATE_LATER_ONLY = True

# --- Turn 10 Phase B.0 (fixed by Phase C's t10-b0-fix): the Part-restart guard -------
#
# The B.4k widening to the 10-K surfaced two `core_complete` losses and one hand-read
# miss, all one shape (docs/turn9_decisions/b4k_10k_widen.md sections 2-3): a later,
# higher-scoring same-canonical-label rival that is not really a second occurrence of
# the chain's section, because it sits inside a later stretch of the document that
# reuses a Part number the chain already used earlier -- an attached 10-Q-style Part
# grafted onto a 10-K whose own real sequence already ran I..IV once (Sun River
# Energy, 0001010549-11-000633; Crimson, 0000813779-10-000010, the hand-read miss).
# Neither failure mode is the McDermott International letter-spaced "I t e m 1 3."
# mis-tokenization (0000950129-05-003051, same doc, section 2 case 1) -- that
# candidate sits in the ordinary body between Item 12 and Item 14, not in a Part
# restart, so this guard does not and should not catch it; see
# tests/test_turn10_backmatter_guard.py for why that stays a documented, xfail'd
# tokenizer gap rather than a rule.
#
# The first shipped version (fdff040) ALSO refused a rival past a bare back-matter
# marker heading (SIGNATURES / EXHIBIT INDEX / ...) in the document's own last 30%.
# The full-corpus gate this rides on (runs/full_v22 -> v23, runs/diff_v22_v23.json,
# runs/judge/turn10-b2-gate.txt section 2) hand-read all 26 anchors that moved
# earlier under it: 7 right (all via the Part-restart condition below), 19 wrong
# (ALL 19 via the marker condition or -- 4 of them -- via a Part-restart false
# positive; see below). The marker condition never once produced a right answer on
# the complete 228,786-document corpus and produced 15 of the 19 wrong ones --
# mostly Fifth Third Bancorp, 13 consecutive years, where a genuine bare "SIGNATURES"
# heading sits well before the document's real, later "ITEM 2. PROPERTIES" (these are
# multi-million-byte filings; the real item content legitimately runs past a
# mid-document signature-shaped heading). It is removed entirely rather than
# re-tuned: `agenda._back_matter_boundary`'s own carve-outs for exactly this
# shape (`agenda.back_after_sigs`, the financial-appendix "short_tail" logic) exist
# precisely because a bare marker heading is not reliable evidence that nothing
# real follows, and a coarser copy of that test inside `build_tree` inherited the
# same unreliability without the node-tree context that makes the real one safe.
#
# The remaining 4 of the 19 wrong anchors were Part-restart false positives: a
# SECOND, undetected front-matter cross-reference table (`toc_idx`, `toc.vetoed_
# region` and `_OOO_TOC_EVIDENCE` below all miss it) whose own tightly-packed early
# "PART I".."PART IV" row collides with the real body's, the same class of false
# positive the original 30-filing acceptance re-check already found and the gap
# test below was built to catch -- except these three filings (0000702808-00-000001,
# 0001012364-00-000024, 0000950124-07-001895) put that second table CLOSE enough
# together (each Part only a few hundred bytes from its neighbor -- a genuine body
# Part is separated from the next by tens of thousands of bytes at the least, every
# real case measured) that the 30%-of-document gap test alone did not exclude it.
# `_ADJACENT_TOC_GAP` below is the fix: a part candidate within that distance of its
# nearest neighbor is, whatever its score or tags, part of a dense listing, not a
# document's own Part transition, and is dropped from the pool entirely -- neither an
# "already used" reference nor a restart trigger. A second, independent filter
# (`_MAX_REPEATED_LABELS`) catches the shape neither the tag filters nor the gap test
# alone can: `0000950124-07-001895` doesn't have one lone repeated Part label (Sun
# River's and Crimson's shape) -- ALL FOUR labels repeat as a second full I..IV cycle
# close enough together that the individual gaps clear the adjacency filter's
# threshold but the document still isn't the "one foreign Part" shape the guard exists
# for; when more than one label has two-plus surviving occurrences, the whole
# document's restart detection is disabled rather than guessed at further.
_ADJACENT_TOC_GAP = 2000
_MAX_REPEATED_LABELS = 1
# agenda.back_short_tail's own ratio (Turn 7 decision (a)): a genuine restart is a
# distinct, later stretch reached only after the real Part sequence completes; Sun
# River's and Crimson's gap from first use to reuse is over half the document.
_BACKMATTER_SHORT_TAIL = 0.3


def _guard_part_restart_starts(cands: list[Candidate], toc_idx: set[int], doc_start: int, doc_end: int,
                                min_score: float = 0.35) -> list[int]:
    """Positions where a `kind == "part"` candidate's label repeats one already seen
    earlier among this document's own (non-TOC) part candidates, FAR enough away to be
    a distinct later stretch rather than the same front matter -- the shape of an
    attached 10-Q-style "PART I-FINANCIAL INFORMATION" reusing a Part number the
    filing's real numbering already used (Sun River Energy; 0000813779-10-000010's own
    "PART I" repeating after II/III/IV, the hand-read miss this guard exists to fix).

    Filters against false restarts (see the module note above): `toc_idx` (`detect_toc`
    regions), `toc.vetoed_region` and `_OOO_TOC_EVIDENCE` (the SAME per-candidate "this
    reads like an index row" tags -- `toc.href` included -- `out_of_order_items` above
    already trusts for an identical purpose) exclude a filing's OWN detected or tagged
    table of contents; `min_score` throws out a candidate that is really a
    cross-reference SENTENCE ("Part II and Part III of this report on Form 10-K",
    score -0.30, `rej.lowercase_title`) rather than a heading. `_ADJACENT_TOC_GAP`
    drops any candidate within that many bytes of its nearest neighbor -- a dense run
    of Part labels close together is a listing, whatever its score or tags; every
    genuine Part-to-Part gap measured on the acceptance filings is at minimum 5,184
    bytes, every false-positive cluster's internal gaps were under 750.
    `_MAX_REPEATED_LABELS` disables the whole document's restart detection when more
    than that many distinct labels have two-plus surviving occurrences -- a real
    restart is ONE label reused once; a document where every Part label repeats as a
    second full cycle is a different, unresolved shape, not this one."""
    doc_len = max(1, doc_end - doc_start)
    parts = sorted((c.head_raw_start, c.label_canon) for i, c in enumerate(cands)
                    if c.kind == "part" and i not in toc_idx and "toc.vetoed_region" not in c.rule_ids
                    and not any(r in c.rule_ids for r in _OOO_TOC_EVIDENCE)
                    and c.score >= min_score)
    kept = [
        (start, label) for k, (start, label) in enumerate(parts)
        if (k == 0 or start - parts[k - 1][0] >= _ADJACENT_TOC_GAP)
        and (k == len(parts) - 1 or parts[k + 1][0] - start >= _ADJACENT_TOC_GAP)
    ]
    counts: dict[str, int] = {}
    for _, label in kept:
        counts[label] = counts.get(label, 0) + 1
    if sum(1 for n in counts.values() if n >= 2) > _MAX_REPEATED_LABELS:
        return []
    first_seen: dict[str, int] = {}
    restarts: list[int] = []
    for start, label in kept:
        prior = first_seen.get(label)
        if prior is not None and (start - prior) >= _BACKMATTER_SHORT_TAIL * doc_len:
            restarts.append(start)
        else:
            first_seen[label] = start
    return restarts


def chain_weight(c: Candidate) -> float:
    """The weight `max_weight_increasing` sees: the score, less the index-row penalty."""
    return c.score - (0.2 if "toc.leader_or_pageno" in c.rule_ids else 0.0)


def strong_duplicate_pass(
    cands: list[Candidate],
    idxs: list[int],
    chosen_set: set[int],
    forced_reason: dict[int, str],
    margin: float = _STRONG_DUPLICATE_MARGIN,
    *,
    doc_raw_start: int = 0,
    doc_raw_end: int = 0,
    toc_idx: "set[int] | frozenset[int]" = frozenset(),
    restart_guard: bool = False,
) -> set[int]:
    """seq.strong_duplicate — give each chain slot to its strongest live candidate.

    For every order key the chain placed, the live candidates sharing that key that sit
    LATER in the document are ranked by `chain_weight`; when the best one beats the one
    the chain chose by at least `margin` it takes the slot.  The displaced
    candidate is rejected `weak_duplicate` (named after the rule, so the flip is
    readable in `rejected-*.parquet`) rather than `nonmonotone`, which would say the
    chain could not place its label -- it did, with the other copy.

    Only the winner's identity changes: the set of order keys, and hence the set of item
    labels in the tree, is what the chain decided.  Ties (equal weight) keep the chain's
    pick, so the pass is a no-op on ordinary duplicate rows.

    Turn 10 Phase B.0 (see the module note above): when `restart_guard` is set, a rival
    is additionally ineligible -- whatever its score -- when its own `head_raw_start` is
    at or past a later Part-number restart (`_guard_part_restart_starts` above,
    doc-wide). Such a rival is tagged `seq.strong_duplicate_backmatter` so `run_diff`
    and the viewer can see why the promotion was refused; the existing
    `seq.strong_duplicate` / `seq.weak_duplicate` ids are unchanged.

    `restart_guard` is 10-K only (the build_tree call site passes
    `grammar.name == "form10k"`): the failure mode it guards against -- an attached
    10-Q-style Part reusing a Part number the filing's own numbering already used
    (Sun River Energy, Crimson/0000813779-10-000010) -- is a 10-K-shaped anomaly by
    definition, a foreign Part sequence grafted onto a document whose own real sequence
    already ran I..IV once. A 10-Q's own Part I/Part II pair has no comparable "full
    cycle then restart" shape to test for, and the 30-hand-read-pair 10-Q re-check
    (turn10-b0-acceptance.txt) found the plain repeat/gap test misfires there instead:
    a short, undetected front-matter mention of "PART II" immediately after a real,
    short "PART I" produces a gap to the real, later "PART II" heading that is easily
    over 30% of a 10-Q's much smaller total size, wrongly flagging four of the 30
    previously-correct promotions as restarts. Scoping the restart half of the guard to
    form10k costs nothing there (its own gate is unaffected, `_STRONG_DUPLICATE_GRAMMARS`
    is unchanged) and removes the false positives on the 10-Q gate this guard must not
    regress. `restart_guard=False` (the default) disables the guard entirely, which
    keeps every caller that does not pass it -- if any is ever added -- byte-identical
    to the pre-Turn-10 behavior.
    """
    restart_starts: list[int] = []
    if restart_guard:
        restart_starts = _guard_part_restart_starts(cands, toc_idx, doc_raw_start, doc_raw_end)

    by_key: dict[int, list[int]] = {}
    for i in idxs:
        by_key.setdefault(cands[i].order_key, []).append(i)
    out = set(chosen_set)
    for i in sorted(chosen_set):
        group = by_key.get(cands[i].order_key, ())
        if len(group) < 2:
            continue
        rivals = [j for j in group
                  if j != i
                  and not any(r in cands[j].rule_ids for r in _STRONG_DUPLICATE_BLOCKING)
                  and (cands[j].head_raw_start > cands[i].head_raw_start or not _STRONG_DUPLICATE_LATER_ONLY)]
        eligible = []
        for j in rivals:
            rs = cands[j].head_raw_start
            if any(rs >= r for r in restart_starts):
                if "seq.strong_duplicate_backmatter" not in cands[j].rule_ids:
                    cands[j].rule_ids.append("seq.strong_duplicate_backmatter")
                continue
            eligible.append(j)
        if not eligible:
            continue
        best = max(eligible, key=lambda j: (chain_weight(cands[j]), -cands[j].head_raw_start, -j))
        if chain_weight(cands[best]) - chain_weight(cands[i]) < margin - 1e-9:
            continue
        cands[best].rule_ids.append("seq.strong_duplicate")
        cands[i].rule_ids.append("seq.weak_duplicate")
        forced_reason[i] = "weak_duplicate"
        out.discard(i)
        out.add(best)
    return out


def build_tree(
    blocks: list[Block],
    cands: list[Candidate],
    toc_regions: list[TocRegion],
    grammar: Form10KGrammar,
    *,
    doc_raw_start: int,
    doc_raw_end: int,
    norm_len: int,
    min_score: float = 0.35,
) -> tuple[list[Node], list[Rejected]]:
    rejected: list[Rejected] = []
    toc_idx: set[int] = set()
    for r in toc_regions:
        toc_idx.update(range(r.first_cand, r.last_cand + 1))

    def rej(c: Candidate, reason: str) -> None:
        rejected.append(Rejected(c.block_idx, c.kind, c.label_canon, c.score, reason, c.head_raw_start, blocks[c.block_idx].text[:120]))

    # per-member rescue: a condemned-region member that is the document's only
    # strong copy of its label is a real heading the region swallowed — losing it
    # loses the label outright (Turn 3 sentinel class)
    # running page headers (continued / exact page-start repeats) are not independent
    # copies of a heading, so they cannot veto the rescue of the real one (Turn 5)
    # Turn 12 B.1: a `rej.vetoed_index_row` row is not an independent copy either -- it is
    # a contents row this pass is about to reject -- so it cannot veto the rescue of a live
    # region's member, for the same reason a running page header cannot.  (It is not in
    # `toc_idx`: the veto dropped its region.)  The A.1 probe removed the condemned set from
    # `strong_outside` in every mode, so this is what its numbers were measured under.
    strong_outside: set[tuple[str, str]] = {
        (c.kind, c.label_canon) for i, c in enumerate(cands)
        if i not in toc_idx and c.score >= 0.55 and "rej.continued" not in c.rule_ids and "rej.page_repeat" not in c.rule_ids
        and "rej.vetoed_index_row" not in c.rule_ids
    }
    live: list[int] = []
    for i, c in enumerate(cands):
        if "rej.regab_back_matter" in c.rule_ids:
            # Turn 13 B.1b (agenda.regab_back_matter, set by pipeline.parse_document before
            # its rebuild): an untitled Reg-AB candidate past the ordinary back-matter
            # boundary -- an exhibit's own heading or an exhibit-index row. Never live, so
            # neither the Reg-AB run nor out_of_order_items can place it.
            rej(c, "regab_back_matter")
            continue
        if "rej.vetoed_index_row" in c.rule_ids:
            # Turn 12 B.1: a row of a long ordered VETOED index that the document re-states
            # later, with the later copy carrying the item's own statutory title
            # (`candidates.vetoed_index_row_pass`).  Read here, BEFORE the `toc_idx` branch
            # and so before the per-member rescue, exactly as `build_contract_tree` reads
            # `rej.ctoc_row`: the veto dropped the region, so these rows are not in
            # `toc_idx` at all and the chain would otherwise anchor the item on the
            # contents page while the real body heading loses as `nonmonotone`.  Rejected
            # with reason `toc` to keep the rejected table's reason column a closed
            # vocabulary (the viewer, the crosswalk and the queue builders read it).
            # A condemned row never becomes live, so `out_of_order_items` cannot re-admit
            # it either -- its own `vetoed_index_run` call still guards the runs this rule
            # does not qualify.
            rej(c, "toc")
            continue
        if i in toc_idx:
            if c.score >= 0.6 and "toc.leader_or_pageno" not in c.rule_ids and (c.kind, c.label_canon) not in strong_outside:
                c.rule_ids.append("toc.member_rescued")
                live.append(i)
            else:
                rej(c, "toc")
        elif c.score < min_score:
            rej(c, "low_score")
        else:
            live.append(i)

    if grammar.name == "form10q":
        part_pos = sorted((blocks[cands[i].block_idx].raw_start, cands[i].label_canon) for i in live if cands[i].kind == "part")
        for i in live:
            c = cands[i]
            if c.kind == "item" and c.label_canon.startswith("ITEM ?."):
                pos = blocks[c.block_idx].raw_start
                cur = "PART I"
                for ps, lab in part_pos:
                    if ps <= pos:
                        cur = lab
                c.label_canon = c.label_canon.replace("ITEM ?.", "ITEM II." if cur == "PART II" else "ITEM I.")
                c.order_key = grammar.order_key("item", c.label_canon)
                c.rule_ids.append("gram.part_from_context")
    chosen: dict[str, list[int]] = {}
    dropped: dict[str, list[int]] = {}
    # candidates placed by the Reg-AB run (Turn 13 B.1b): their item nodes carry
    # `seq.regab_run`, so the run that placed a node is readable from the node table
    regab_run: set[int] = set()
    forced_reason: dict[int, str] = {}
    is_regab = getattr(grammar, "is_regab_item", None)
    for lvl in grammar.levels:
        if lvl.kind in chosen:
            # two LevelSpecs may share a kind (Form10KGrammar's ITEM_RE and _REGAB_RE
            # levels are both `item`): the kind is chained once over all its candidates
            continue
        idxs = [i for i in live if cands[i].kind == lvl.kind]
        # index-looking rows (dot leaders / trailing page numbers) lose weight in the
        # chain so a back-of-document cross-reference index cannot beat the sparser
        # real body chain (A3c); unopposed they still win their label slot
        if lvl.kind == "item" and is_regab is not None:
            # seq.regab_run (Turn 13 B.1, R-B4-3, docs/turn13_decisions/a6a_regab.md
            # 3.2): Regulation AB items are chained as their own increasing run, apart
            # from the ordinary items, and the two runs are reunited before
            # strong_duplicate_pass. In one chain their order keys (11120-11230) sit
            # above ITEM 16's, so a Reg-AB block printed before Part IV knocked the real
            # Item 15/16 out as `nonmonotone` (or lost to it) -- Turn 12 B.4's failed gate.
            chosen_set = set()
            for gi, group in enumerate(([i for i in idxs if not is_regab(cands[i].label_canon)],
                                        [i for i in idxs if is_regab(cands[i].label_canon)])):
                weights = [chain_weight(cands[i]) for i in group]
                pick = max_weight_increasing([(cands[i].order_key, w) for i, w in zip(group, weights)])
                chosen_set |= {group[p] for p in pick}
                if gi == 1:
                    regab_run = {group[p] for p in pick}
        else:
            weights = [chain_weight(cands[i]) for i in idxs]
            pick = max_weight_increasing([(cands[i].order_key, w) for i, w in zip(idxs, weights)])
            chosen_set = {idxs[p] for p in pick}
        if grammar.name in _STRONG_DUPLICATE_GRAMMARS and lvl.kind == "item":
            chosen_set = strong_duplicate_pass(cands, idxs, chosen_set, forced_reason,
                                                doc_raw_start=doc_raw_start,
                                                doc_raw_end=doc_raw_end, toc_idx=toc_idx,
                                                restart_guard=(grammar.name == "form10k"))
        dropped[lvl.kind] = [i for i in idxs if i not in chosen_set]
        chosen[lvl.kind] = sorted(chosen_set)
    # seq.out_of_order: the second placement pass, before the nonmonotone rejections are
    # written, so an item the chain could not place but the document really typesets is a
    # node rather than a rejected row.  Items only: Part labels have no statutory title to
    # corroborate an out-of-order placement with.
    extra = out_of_order_items(cands, chosen.get("item", []), dropped.get("item", []), toc_idx, grammar)
    extra_set = set(extra)
    for kind, idxs in dropped.items():
        for i in idxs:
            if i not in extra_set:
                rej(cands[i], forced_reason.get(i, "nonmonotone"))

    nodes: list[Node] = []
    root = Node(0, -1, 0, "document", None, None, None, doc_raw_start, doc_raw_end, doc_raw_start, doc_raw_start, 0, norm_len, 0, 1.0, ["doc"])
    nodes.append(root)

    # TOC nodes
    for r in toc_regions:
        b0, b1 = blocks[r.block_start], blocks[r.block_end]
        nodes.append(Node(len(nodes), 0, 1, "toc", "TOC", None, None, b0.raw_start, b1.raw_end, b0.raw_start, b0.raw_end, b0.norm_start, b1.norm_end, 0, 0.8, [r.reason]))

    items = [cands[i] for i in chosen.get("item", [])]
    in_regab_run = [i in regab_run for i in chosen.get("item", [])]
    parts = [cands[i] for i in chosen.get("part", [])]
    has15 = grammar.name == "form10k" and any(grammar.item_key(c.label_canon) in ("15", "16") for c in items)

    # Part nodes: explicit ones, plus synthesized for items whose part is missing
    part_nodes: dict[str, Node] = {}
    for c in parts:
        b = blocks[c.block_idx]
        n = Node(0, 0, 1, "part", c.label_canon, c.label_raw, c.title, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_end, c.norm_start, norm_len, c.order_key, min(1.0, c.score), list(c.rule_ids))
        part_nodes[c.label_canon] = n
    item_nodes: list[tuple[Node, str]] = []
    for c, by_regab_run in zip(items, in_regab_run):
        b = blocks[c.block_idx]
        want = grammar.part_of_item(c.label_canon, has_item_15=has15)
        # attach to the nearest preceding explicit Part if it matches; else synthesize
        explicit = part_nodes.get(want)
        if explicit is None or explicit.raw_start > c.head_raw_start:
            if explicit is None:
                explicit = Node(0, 0, 1, "part", want, None, None, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_start, c.norm_start, norm_len, grammar.order_key("part", want), 0.5, ["gram.synth_part"])
                part_nodes[want] = explicit
            else:
                # explicit Part heading typeset after its first item: open the Part at the item so ordinals stay stable
                c.rule_ids.append("gram.part_after_item")
                explicit.raw_start = min(explicit.raw_start, c.head_raw_start)
                explicit.norm_start = min(explicit.norm_start, c.norm_start)
                if "gram.part_opened_early" not in explicit.rule_ids:
                    explicit.rule_ids.append("gram.part_opened_early")
        n = Node(0, 0, 2, "item", c.label_canon, c.label_raw, c.title, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_end, c.norm_start, norm_len, c.order_key, min(1.0, c.score),
                 list(c.rule_ids) + (["seq.regab_run"] if by_regab_run else []))
        item_nodes.append((n, want))
    # Out-of-order items are placed under their statutory Part but must not disturb the
    # chain's own nodes: they never open an existing Part early (`gram.part_after_item`
    # above) and never extend one (see the Part-end rule below), so no accepted node's
    # anchor, parent or span moves because one of these was recovered.  `has15` is the
    # chain's, for the same reason: a recovered Item 15 must not move Item 14's Part.
    for c in [cands[i] for i in extra]:
        want = grammar.part_of_item(c.label_canon, has_item_15=has15)
        explicit = part_nodes.get(want)
        if explicit is None:
            explicit = Node(0, 0, 1, "part", want, None, None, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_start, c.norm_start, norm_len, grammar.order_key("part", want), 0.5, ["gram.synth_part", "seq.out_of_order"])
            part_nodes[want] = explicit
        n = Node(0, 0, 2, "item", c.label_canon, c.label_raw, c.title, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_end, c.norm_start, norm_len, c.order_key, min(1.0, c.score), list(c.rule_ids))
        item_nodes.append((n, want))

    if grammar.name == "form10q":
        _synth_item1(blocks, cands, toc_regions, toc_idx, set(live), grammar, part_nodes, item_nodes, doc_raw_end, norm_len)

    # Assign ids in document order: parts by start, items under their part
    ordered_parts = sorted(part_nodes.values(), key=lambda n: (n.raw_start, n.order_key))
    for p in ordered_parts:
        p.node_id = len(nodes)
        p.parent_id = 0
        nodes.append(p)
        kids = sorted((n for n, want in item_nodes if want == p.label_canon), key=lambda n: n.raw_start)
        for k in kids:
            k.node_id = len(nodes)
            k.parent_id = p.node_id
            nodes.append(k)
    # Ends: each node ends where the next node at depth <= its depth begins.
    # TOC nodes keep the end build_tree gave them (last member block) — extending
    # them to the next node inflates the region past its real rows (B2 hygiene).
    seq = sorted(nodes[1:], key=lambda n: (n.raw_start, n.depth))
    for i, n in enumerate(seq):
        if n.level_kind == "toc":
            continue
        for m in seq[i + 1 :]:
            if m.depth <= n.depth and m.raw_start > n.raw_start:
                n.raw_end = m.raw_start
                n.norm_end = m.norm_start
                break
    # A Part cannot end before its last child ends — except for an out-of-order child,
    # which is under its Part by statute and somewhere else entirely by typesetting.
    # Stretching Part II over an Item 8 bound after the exhibit index would swallow
    # Parts III and IV; the misplacement belongs to the one node that has it.
    # `seq.strong_duplicate` (Turn 9 B.4) is out of order in the same sense and for the
    # same reason — a 10-Q whose financial statements are bound behind Part II puts its
    # real Item 1 there, and Part I must not be stretched over Part II to follow it.
    for p in ordered_parts:
        kids = [n for n in nodes if n.parent_id == p.node_id
                and "seq.out_of_order" not in n.rule_ids and "seq.strong_duplicate" not in n.rule_ids]
        if kids:
            p.raw_end = max(p.raw_end, max(k.raw_end for k in kids))
            p.norm_end = max(p.norm_end, max(k.norm_end for k in kids))
    return nodes, rejected


def truncate_at_back_start(nodes: list[Node], back_start: int, back_norm_start: int) -> int:
    """tree.back_truncate — a main-body node stops at the back-matter boundary.

    `raw_end` is otherwise the start of the next node at the same or shallower depth,
    which for the last item is the end of the document: Item 15's span then contains
    the signature page, the exhibit index and the whole financial appendix
    (docs/turn7_decisions/a_appendix.md section 4). The meta digit demotes nodes that
    *begin* inside the appendix but cannot shorten a node that begins before it, so the
    span is truncated here. Nodes that begin at or after the boundary keep their spans.

    tree.back_truncate_descend (Turn 12 B.0, spec docs/turn12_decisions/a3_subheadings.md
    section (a)): the scan above clamps a Part, Item or heading whose OWN span straddles
    `back_start`, but never revisits that node's descendants. A depth>=3 heading entirely
    inside the back matter (its own `raw_start >= back_start`) is never touched by the
    scan above, so once its ancestor is cut short it "ends after its parent" — not because
    its own span is wrong, but because its tree parent no longer contains it.

    **Re-parent, do not re-span.** A heading whose immediate parent no longer contains it
    (`parent.raw_end <= heading.raw_start`) is walked up the ancestor chain to the nearest
    node that still does — which, for this population, is always the document root, since
    every intermediate ancestor was independently cut to the identical `back_start` by the
    scan above and therefore fails containment too. `parent_id` and `depth` are updated to
    match; `raw_start`, `raw_end`, `norm_start`, `norm_end` and `head_*` are never touched
    — clamping them would drive `raw_end` at or below the node's own `raw_start` for
    essentially the whole population (verified: 3,542,948 of 3,544,818 strictly, the other
    1,870 exactly at the boundary — `turn12-a3-backtrunc-inverted.parquet`), destroying
    byte addressing to satisfy a containment check the node's PATH already satisfies once
    it is correctly parented. The node's own (pre-existing) subtree is not touched
    structurally — its children keep the same `parent_id` they always had — but every
    node inside it has its `depth` shifted by the same delta the re-parented root moved,
    since `depth` must stay `parent.depth + 1` everywhere (an invariant the corpus already
    satisfies with zero exceptions, `turn12-leadsub-m5-integrity.parquet` section M5).
    Scope is `level_kind == "heading"` only: a separate, unrelated, pre-existing residual
    (3,814 depth<3 items whose Part was never stretched to cover an out-of-order child,
    `turn12-a3-backtrunc-le2.parquet`) also shows up as "ends after parent" and must not
    be touched here.

    Processes headings shallowest-first so a multi-level detach (a heading whose parent
    was itself independently truncated, several levels up) resolves against an
    already-updated ancestor. Returns the number of nodes changed (both re-parented roots
    and the depth-shifted members of their subtrees), not counting the direct clamps from
    the scan above.

    Callers: `pipeline.py` calls `assign_paths` again after this function changes any
    `parent_id`/`depth`, since `assign_paths`'s tree walk depends on both and was computed
    before this pass ran; the boundary computation itself (`back_start`/`back_norm_start`)
    does not depend on either, so the second call reproduces the same bounds.
    """
    if back_start is None or back_norm_start is None or back_norm_start < 0:
        return 0
    n_changed = 0
    for n in nodes:
        if n.node_id == 0 or n.level_kind == "toc":
            continue
        if n.raw_start < back_start < n.raw_end:
            n.raw_end = back_start
            n.norm_end = max(n.norm_start, back_norm_start)
            if "tree.back_truncate" not in n.rule_ids:
                n.rule_ids.append("tree.back_truncate")
            n_changed += 1

    by_id = {n.node_id: n for n in nodes}
    children: dict[int, list[Node]] = {}
    for n in nodes:
        if n.node_id == 0:
            continue
        children.setdefault(n.parent_id, []).append(n)

    headings_shallowest_first = sorted(
        (n for n in nodes if n.node_id != 0 and n.level_kind == "heading"),
        key=lambda n: n.depth,
    )
    for n in headings_shallowest_first:
        p = by_id.get(n.parent_id)
        if p is None or p.raw_end > n.raw_start:
            continue  # still contained by its parent (or an orphan -- not this pass's job)
        a = p
        while a.node_id != 0 and not (a.raw_start <= n.raw_start < a.raw_end):
            a = by_id[a.parent_id]
        old_depth = n.depth
        n.parent_id = a.node_id
        n.depth = a.depth + 1
        if "tree.back_truncate_descend" not in n.rule_ids:
            n.rule_ids.append("tree.back_truncate_descend")
        n_changed += 1
        delta = n.depth - old_depth
        if delta:
            stack = list(children.get(n.node_id, ()))
            while stack:
                c = stack.pop()
                c.depth += delta
                n_changed += 1
                stack.extend(children.get(c.node_id, ()))
    return n_changed
