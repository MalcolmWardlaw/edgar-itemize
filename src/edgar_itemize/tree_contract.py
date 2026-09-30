"""Tree construction for contract-style documents (Article / Section / clauses)."""

from __future__ import annotations

from bisect import bisect_right

import regex

from .agenda import chain_restarts
from .blocks import Block, caps_ratio
from .candidates import Candidate, _clean_title
from .grammar.contract import ARTICLE_RE, CLAUSE_RE, SECTION_RE, ContractGrammar
from .toc import TocRegion
from .tree import Node, Rejected, max_weight_increasing

_FAMILIES = ("alpha", "roman", "upper", "upper_roman", "num")
_KIND = {"alpha": "clause_alpha", "roman": "clause_roman", "upper": "clause_upper", "upper_roman": "clause_upper_roman", "num": "clause_num",
         "alt": "clause_alpha"}

# --- Turn 12 B.2: the clause-layer reliefs (docs/turn12_decisions/a2_clause_layer.md s8) --
# Each relief is a module-level switch, as Turn 10 B.3's `_TITLE_ATTACH_ON` is, so the
# acceptance census can attribute rows to one relief at a time and so that "all off" is
# provably the Turn 11 tree.  Nothing reads them but this module and the tests.
_BOOKKEEPING_ON = True   # B.2.4  every dropped clause candidate gets a rejected row
_R6_ON = True            # B.2.0  seq.open_midrun (in sequence.py; this flag gates the call)
_R3_ON = True            # B.2.1  gram.clause_under_article / gram.synth_section
_R2_ON = True            # B.2.2  gram.clause_alpha_bijective
_R4_ON = True            # B.2.3  seq.alt_readings
# Turn 13 B.4 (docs/turn13_decisions/b4_section_xref.md): reject `rej.section_xref` rows
_SXREF_ON = True
# Turn 13 B.4b: clauses a vetoed reference leaves without a section parent get a synthetic one
_SXREF_ORPHAN_ON = True

# --- Turn 10 B.3: bare-title attachment (docs/turn10_decisions/a4_bare_titles.md s5a/s5b) --
# The whole pass, on or off in one place: it attaches titles and moves spans but creates and
# deletes no node, so turning it off returns the tree to its Turn 9 shape exactly (the gate's
# G3, runs/judge/turn10-b3-g3-attribution.txt: 0 path churn and 0 moved head_raw_start over
# both corpora).  Set False to measure a run without it; the tests flip it with monkeypatch.
_TITLE_ATTACH_ON = True
_TITLE_MAX_WORDS = 8
_TITLE_MAX_CHARS = 200
# the census's C3, verbatim from scripts/turn10/a4_shape.py so the corpus tables the rule
# was designed against (s3f: title_of_prev 17.8% / 30.6%) describe the same line set
_LETTER_WORD_RE = regex.compile(r"[A-Za-z][A-Za-z'’\-]*")


_ALT_SURCHARGE = 0.35  # = sequence()'s own restart_cost (B.2.3)


def _families(c: str, *, alt: bool = False) -> list[tuple]:
    """The (family, value) readings of a clause marker, with Turn 12 B.2's two additions.

    B.2.2 `gram.clause_alpha_bijective`: a two-letter marker gets a SECOND reading in the
    same family under the bijective convention ((aa)=27, (ab)=28, (dk)=115), so a list
    drafted past (z) can continue.  The doubled-letter reading ((aa)=27, (bb)=28) is kept,
    so the sequencer's own cost minimisation picks the convention the document uses.

    B.2.3 `seq.alt_readings`: when the caller says so (the span carries an alternatives
    PAIR), x/y/z also read as the `alt` family at values 1/2/3, carrying a surcharge as a
    third tuple element so a legal continuation of a lettered list always beats it.
    """
    out: list[tuple] = []
    for f in _FAMILIES:
        v = ContractGrammar.clause_value(c, f)
        if v:
            out.append((f, v))
        if _R2_ON:
            b = ContractGrammar.clause_value_bijective(c, f)
            if b and (f, b) not in out:
                out.append((f, b))
    if alt and _R4_ON:
        v = ContractGrammar.clause_value(c, "alt")
        if v:
            out.append(("alt", v, _ALT_SURCHARGE))
    return out


def _bare_title(b: Block) -> str | None:
    """The first line of block `b` when it is shaped like a bare title, else None.

    Conditions 1, 2, 4, 5, 6 and 7 of a4_bare_titles.md s5a (the census's C0/C7/C1/C2/C3/C6,
    scripts/turn10/a4_shape.py): >= 2 letters, not itself an Article/Section/Clause label,
    <= 8 words, no terminal period, all-caps or title-case, not in a table.
    """
    if b.in_table:
        return None
    t = (b.lines[0] if b.lines else b.text.split("\n")[0]).strip()
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 2:
        return None
    if ARTICLE_RE.match(t) or SECTION_RE.match(t) or CLAUSE_RE.match(t):
        return None
    words = t.split()
    if not words or len(words) > _TITLE_MAX_WORDS or t.endswith("."):
        return None
    all_caps = caps_ratio(t) >= 0.85 and len(letters) >= 4
    lw = [w for w in _LETTER_WORD_RE.findall(t) if len(w) > 2 or w[:1].isupper()]
    title_case = bool(lw) and sum(1 for w in lw if w[:1].isupper()) >= 0.6 * len(lw) and t[:1].isupper()
    if not (all_caps or title_case):
        return None
    return t


# --- Turn 12 B.2.1 (R3): the outside-section region relief --------------------------------
# The classifier below is `scripts/turn12/a2_classify.py:classify_outside` and the context
# scans of `scripts/turn12/a2_census.py`, ported verbatim, because the bank that gated this
# relief (docs/turn12_decisions/a2_clause_layer.md s5c, s7a) was drawn and scored on those
# mechanism labels: O1 a definitions article or a defined term, O2 a recital, O3 front
# matter, O4 a schedule / annex / charter / rider, O5 a chain-boundary gap, O7 an accepted
# article whose Section layer was never found.  O0 (a document with no accepted section at
# all) cannot arise here -- with no section there is no gap to relieve -- and its population
# is the silent drop B.2.4 writes out, which no relief this turn touches.
_SCHED_RE = regex.compile(
    r"^\s*(?:SCHEDULE|ANNEX|APPENDIX|EXHIBIT|RIDER|ADDENDUM|ATTACHMENT|SUPPLEMENT)\b"
    r"|^\s*(?:AMENDED\s+AND\s+RESTATED\s+)?(?:ARTICLES?|CERTIFICATE)\s+OF\s+"
    r"(?:INCORPORATION|ORGANIZATION|ASSOCIATION|FORMATION|DESIGNATION|AMENDMENT)\b"
    r"|^\s*(?:AMENDED\s+AND\s+RESTATED\s+)?BY-?LAWS\b|^\s*OPERATING\s+AGREEMENT\b",
    regex.IGNORECASE)
_RECITAL_RE = regex.compile(r"^\s*(?:W\s?I\s?T\s?N\s?E\s?S\s?S\s?E\s?T\s?H\b|WHEREAS\b|RECITALS?\b|"
                            r"PRELIMINARY\s+STATEMENTS?\b|BACKGROUND\b)", regex.IGNORECASE)
_MEANS_RE = regex.compile(r"[\"\u201c\'\u2019\u201d]\s*(?:shall\s+(?:mean|have\s+the\s+meaning)|means\b|has\s+the\s+meaning)"
                          r"|\bshall\s+mean\b", regex.IGNORECASE)
_DEFN_RE = regex.compile(r"\bDEFINITION|\bDEFINED\s+TERMS?\b|\bINTERPRETATION\b", regex.IGNORECASE)
_BACK_BLOCKS = 60        # how far back a context marker is looked for (a2_census.py)
_MEANS_NEAR = 3          # a defined term this close counts as a definitions marker
_ALT_MARKERS = ("x", "y", "z")
_REGION_MIN = 3          # >= 3 block-initial members in distinct blocks, monotone run >= 3
# `prose_after`'s own word regex, verbatim from scripts/turn12/a2_bank_score.py (WORD_RE
# plus its len >= 2 filter), so the clause fires on exactly the line set the bank measured
_PROSE_WORD_RE = regex.compile(r"[A-Za-z][A-Za-z'\-]+")


def _outside_context(blocks: list[Block]) -> tuple[list[bool], list[bool], list[bool], list[bool]]:
    """Per block: schedule heading, recital opener, defined term, definitions heading."""
    n = len(blocks)
    sched, recital, means, defn = [False] * n, [False] * n, [False] * n, [False] * n
    for i, b in enumerate(blocks):
        t = b.text or ""
        head = (b.lines[0] if b.lines else t.split("\n", 1)[0])[:120]
        sched[i] = bool(_SCHED_RE.match(head))
        recital[i] = bool(_RECITAL_RE.search(t[:400]))
        means[i] = bool(_MEANS_RE.search(t[:400]))
        defn[i] = bool(_DEFN_RE.search(head))
    return sched, recital, means, defn


def _back_scan(bi: int, flags: list[bool], n_b: int) -> int:
    """Blocks back to the nearest block carrying `flags`, -1 if none within _BACK_BLOCKS."""
    for j in range(min(bi, n_b - 1), max(0, bi - _BACK_BLOCKS) - 1, -1):
        if flags[j]:
            return bi - j
    return -1


def _outside_mechanism(c: Candidate, ctx, blocks: list[Block], first_sec: int | None,
                       arts: list[Node]) -> str:
    sched, recital, means, defn = ctx
    if first_sec is not None and c.head_raw_start > first_sec:
        return "O5"
    n_b = len(blocks)
    bi = c.block_idx
    near = [(_back_scan(bi, sched, n_b), "O4"), (_back_scan(bi, defn, n_b), "O1"),
            (_back_scan(bi, recital, n_b), "O2")]
    mb = _back_scan(bi, means, n_b)
    if 0 <= mb <= _MEANS_NEAR:
        near.append((mb, "O1"))
    near = [(d, k) for d, k in near if d >= 0]
    if near:
        best = min(d for d, _ in near)
        for pref in ("O4", "O1", "O2"):
            if any(k == pref and d == best for d, k in near):
                return pref
    art = next((a for a in reversed(arts) if a.raw_start <= c.head_raw_start), None)
    if art is not None and _DEFN_RE.search((art.title or "") + " " + (art.label_canon or "")):
        return "O1"
    return "O7" if art is not None else "O3"


def _prose_after(c: Candidate) -> bool:
    """`prose_after`: the marked line carries prose after the marker -- the candidate's own
    title has >= 3 letter-words (scripts/turn12/a2_bank_score.py's clause, verbatim).  It is
    what keeps a page-number footer, a `(NY)` docket stamp, a blank fill-in and a table cell
    out of O3 and O4, whose unrestricted bank halves failed at 81.2% and 87.5%."""
    return len(_PROSE_WORD_RE.findall(c.title or "")) >= 3


def _cut_regions(members: list[Candidate], art_starts: list[int]) -> list[list[Candidate]]:
    """A region is a maximal run of consecutive outside-section clause candidates, cut
    wherever an accepted article opens (a2_census.py's own region construction)."""
    out: list[list[Candidate]] = []
    prev: int | None = None
    for c in members:
        if prev is None or any(prev < a <= c.head_raw_start for a in art_starts):
            out.append([])
        out[-1].append(c)
        prev = c.head_raw_start
    return out


def _monotone_run(markers: list[str]) -> int:
    """Longest run of consecutive values in one family, in order, that starts at 1 or 2 --
    R3's "this really is an enumerated list" test (a2_census.py:monotone_run)."""
    best = 0
    for f in _FAMILIES:
        cur, prev = 0, None
        for m in markers:
            v = ContractGrammar.clause_value(m, f)
            if v is None:
                continue
            if prev is None or not (v == prev + 1 and cur):
                cur = 1 if v in (1, 2) else 0
            else:
                cur += 1
            prev = v
            best = max(best, cur)
    return best


def _region_ok(members: list[Candidate]) -> bool:
    """The discriminating clause: >= 3 live clause candidates that are block-initial in
    distinct blocks AND a monotone run of >= 3 in one family starting at 1 or 2.  A run-in
    enumeration inside one sentence contributes one block-initial candidate; a schedule row
    list is a table whose markers do not form a monotone run."""
    if not _R3_ON or len(members) < _REGION_MIN:
        return False
    binit = {c.block_idx for c in members
             if not any(r.startswith("pos.line") for r in c.rule_ids)}
    if len(binit) < _REGION_MIN:
        return False
    return _monotone_run([(c.label_canon or "").strip("()") for c in members]) >= _REGION_MIN


def _segment_back_starts(blocks: list[Block], nodes: list[Node], doc_raw_start: int, doc_raw_end: int) -> tuple[list[int], list[int]]:
    """(segment starts, each segment's back_start) -- assign_paths' own computation, run
    here because the attachment pass has to know back matter before `assign_paths` does
    (condition 8 of s5a)."""
    from .agenda import MAX_SEGMENTS, _back_matter_boundary, compound_restarts

    starts = [doc_raw_start] + [a.head_raw_start for a in compound_restarts(nodes, blocks)][: MAX_SEGMENTS - 1]
    backs: list[int] = []
    for si, lo in enumerate(starts):
        if len(starts) == 1:
            backs.append(_back_matter_boundary(blocks, nodes, doc_raw_end)[0])
            continue
        hi = starts[si + 1] if si + 1 < len(starts) else doc_raw_end
        sn = [n for n in nodes if n.node_id != 0 and lo <= n.raw_start < hi]
        sb = [b for b in blocks if lo <= b.raw_start < hi]
        backs.append(_back_matter_boundary(sb, sn, hi)[0])
    return starts, backs


def attach_bare_titles(nodes: list[Node], blocks: list[Block], cands: list[Candidate], toc_regions: list[TocRegion], *,
                       doc_raw_start: int, doc_raw_end: int) -> None:
    """Turn 10 B.3, docs/turn10_decisions/a4_bare_titles.md s5a/s5b.

    Contract typography prints `ARTICLE V` / blank / `NEGATIVE COVENANTS` / blank /
    `Section 5.01 ...` as three blocks, and `candidates._title_from_lines` only joins title
    lines *inside one* block, so the ARTICLE node comes out titleless and the title line is
    invisible to the tree.  26.0% (contracts text) / 40.7% (EX-10) of the A.4 census is that class
    (s3f).  Two rules, run in that order on each titleless accepted article/section:

      gram.title_adjacent_line  the title is the line BELOW the label: `title` and
                                `head_raw_end` move, nothing else.
      gram.title_above_label    the title is the line ABOVE it: `title`, `raw_start` and
                                `norm_start` move; `head_raw_start`/`head_raw_end` do not.

    No node is created or removed and no `head_raw_start` ever moves -- every judge bank
    since Turn 4 and the contracts-text crosswalk are keyed on `head_raw_start`, and ordinal
    paths must not churn (gate G3).  Ends are closed after this pass, so a `raw_start` that
    moves up takes the previous node's `raw_end` with it.
    """
    if not _TITLE_ATTACH_ON:
        return
    targets = [n for n in nodes if n.level_kind in ("article", "section")
               and not (n.title or "").strip() and "gram.synth_article" not in n.rule_ids
               and "gram.synth_section" not in n.rule_ids]
    if not targets:
        return
    block_starts = [b.raw_start for b in blocks]
    cand_blocks = {c.block_idx for c in cands}
    toc_ranges = [(r.block_start, r.block_end) for r in toc_regions]
    head_blocks = {max(0, bisect_right(block_starts, n.head_raw_start) - 1)
                   for n in nodes if n.level_kind in ("article", "section")}
    seg_starts, seg_backs = _segment_back_starts(blocks, nodes, doc_raw_start, doc_raw_end)

    def back_start_of(off: int) -> int:
        return seg_backs[max(0, bisect_right(seg_starts, off) - 1)]

    def in_toc(i: int) -> bool:
        return any(lo <= i <= hi for lo, hi in toc_ranges)

    claimed: set[int] = set()  # a title line is attached to at most one node

    def usable(i: int) -> str | None:
        """Conditions 1-8 on block `i` (3: the block proposes no label of its own)."""
        if i in claimed or i in cand_blocks or in_toc(i):
            return None
        return _bare_title(blocks[i])

    for n in sorted(targets, key=lambda x: (x.head_raw_start, x.node_id)):
        b0 = max(0, bisect_right(block_starts, n.head_raw_start) - 1)
        if n.head_raw_start >= back_start_of(n.head_raw_start):
            continue  # condition 8: back matter
        done = False
        for k in (1, 2):
            j = b0 + k
            if j >= len(blocks) or (k == 2 and blocks[b0 + 1].kind == "para"):
                break  # condition 9: only a blank / page / hr marker may stand between
            t = usable(j)
            if t is None:
                continue
            title = _clean_title(t)[:_TITLE_MAX_CHARS]
            if not title:
                continue
            n.title = title
            ends = blocks[j].line_raw_ends
            n.head_raw_end = ends[0] if ends else blocks[j].raw_end
            n.rule_ids.append("gram.title_adjacent_line")
            claimed.add(j)
            done = True
            break
        if done or "seg.instrument_boundary" in n.rule_ids or "chain.restart" in n.rule_ids:
            # a chain/segment boundary keeps its raw_start: assign_paths segments on the
            # node's raw_start, so moving it across the boundary would churn ordinals
            continue
        for k in (1, 2):
            j = b0 - k
            if j < 0 or (k == 2 and blocks[b0 - 1].kind == "para"):
                break
            b = blocks[j]
            if not (b.blank_before and b.blank_after and len(b.text.split("\n")) == 1):
                continue  # condition 10: standalone
            if any(m in head_blocks for m in range(j, b0)):
                continue  # condition 11: another accepted heading opens in between
            t = usable(j)
            if t is None:
                continue
            title = _clean_title(t)[:_TITLE_MAX_CHARS]
            if not title:
                continue
            n.title = title
            n.raw_start = b.raw_start
            n.norm_start = b.norm_start
            n.rule_ids.append("gram.title_above_label")
            claimed.add(j)
            break


def build_contract_tree(
    blocks: list[Block], cands: list[Candidate], toc_regions: list[TocRegion], grammar: ContractGrammar, *,
    doc_raw_start: int, doc_raw_end: int, norm_len: int, min_score: float = 0.35,
) -> tuple[list[Node], list[Rejected]]:
    rejected: list[Rejected] = []
    toc_idx: set[int] = set()
    for r in toc_regions:
        toc_idx.update(range(r.first_cand, r.last_cand + 1))

    def rej(c: Candidate, reason: str) -> None:
        rejected.append(Rejected(c.block_idx, c.kind, c.label_canon, c.score, reason, c.head_raw_start, blocks[c.block_idx].text[:120]))

    # Numbering restarts (`chain.restart`), decided on the candidate list before anything
    # is chained: a compound exhibit glues several instruments into one EX-10 document
    # and each restarts its own numbering, so the article chain and the section chain
    # below run once per restart (`seq.per_segment`) instead of once per document.  One
    # document-wide monotone chain can hold only one order-key-1 article, which is why
    # the second instrument's body used to lose its sections to the first instrument's
    # chain (the 664 chain casualties of the Turn 7 C2 gate, docs/TURN7_REPORT.md s6).
    # Condemnation stays document-wide (`toc.dup_later_contract`): a later instrument's
    # index can sit at the front of the file, so `toc_idx` is computed above, once.
    #
    # These are the CHAIN's stretches, not `agenda`'s segments.  Only a restart that also
    # carries instrument evidence (`seg.instrument_boundary`) becomes a segment; the rest
    # chain separately inside the enclosing segment.  The chain partition therefore
    # refines the segment partition, which is what the article keying below needs: two
    # ARTICLE 1 nodes can share a segment, so `articles` is keyed by the chain that built
    # them and a section still finds the article of its own chain.
    # Turn 11 B.1: rows `candidates.ctoc_row_pass` condemned as index rows of a leaderless
    # contract table of contents (`rej.ctoc_row`).  They are read HERE, before chaining, so a
    # condemned row can neither open a numbering restart nor win a slot in a chain; they are
    # rejected with reason `toc` below, which keeps the rejected table's reason column a
    # closed vocabulary (the viewer, the crosswalk and the queue builders read it).  By
    # construction (`ctoc_row_pass`) no candidate carries both `rej.ctoc_row` and
    # `ctoc.region_relief`, and a condemned row is never a member of a proposed region.
    ctoc_idx = {i for i, c in enumerate(cands) if "rej.ctoc_row" in c.rule_ids}
    # Turn 13 B.4: rows `candidates.section_xref_pass` tagged as definitional `Section n.n`
    # cross-references (`rej.section_xref`).  Read here for the same reason as `rej.ctoc_row`:
    # a reference must neither open a numbering restart nor win the chain slot of the real
    # body heading of its label.  Rejected below with reason `section_xref`, after the toc and
    # low_score branches so those rows keep their reasons.
    sxref_idx = {i for i, c in enumerate(cands) if _SXREF_ON and "rej.section_xref" in c.rule_ids}
    restart_cands = chain_restarts(blocks, cands, toc_idx | ctoc_idx | sxref_idx, min_score=min_score)
    chain_starts = [doc_raw_start] + [cands[i].head_raw_start for i in restart_cands]

    def chain_of(off: int) -> int:
        return max(0, bisect_right(chain_starts, off) - 1)

    # per-member rescue (`toc.member_rescued`, tree.py's rule for the 10-K path): a
    # condemned-region row that is the document's only live copy of its label is the
    # only place that section can be anchored — dropping it loses the label outright.
    # The "outside" bar is the tree's own min_score, not a style bar: a contract body
    # heading runs into its prose and routinely scores below its index row (Turn 7 C.2),
    # so a stricter bar would rescue rows that DO have a body copy and re-anchor the
    # section on the index.  A rescued row still has to win its slot in the chain below:
    # it sits before every body copy, so one whose order key is mid-sequence yields to
    # the body copies it would displace and the label is genuinely absent from the body.
    # The "outside" test stays document-wide even though the chains do not.  Scoping it
    # per segment was tried and reverted: a later instrument's index sits in front of that
    # instrument's own ARTICLE 1, i.e. in the PREVIOUS segment, so the per-segment set asks
    # the previous agreement whether it has a body copy, it does not, and the index row is
    # rescued and accepted.  On the corpus that put 14,218 extra `toc.member_rescued` rows
    # into segment 0 of the 867 segmented documents (rescued rows 4,283 -> 22,235) — index
    # rows re-accepted as headings, which is exactly what the C2 condemnation removed.
    outside = {(c.kind, c.label_canon) for i, c in enumerate(cands) if i not in toc_idx and c.score >= min_score}
    live = []
    sxref_live: list[int] = []  # B.4b: the vetoed references, in document order
    for i, c in enumerate(cands):
        if i in ctoc_idx:
            rej(c, "toc")  # Turn 11 B.1, `rej.ctoc_row`: an index row of a leaderless TOC
        elif i in toc_idx:
            if c.score >= 0.5 and "toc.leader_or_pageno" not in c.rule_ids and (c.kind, c.label_canon) not in outside:
                c.rule_ids.append("toc.member_rescued")
                live.append(i)
            elif "ctoc.region_relief" in c.rule_ids:
                # Turn 11 B.2: the region relief.  A member that is NOT an index row, IS
                # followed by prose within one block, and carries no page or leader evidence
                # anywhere on its row is a real body heading the region swallowed -- the 19
                # of docs/turn11_decisions/a1_ctoc_boundary.md section 5.  It is spared here,
                # at the rescue point, rather than by changing `toc.py`'s proposal, so every
                # region stays bit-identical and no other grammar's path can move.  The
                # existing `toc.member_rescued` test runs first: a member it already rescues
                # keeps that id and nothing changes for it.
                live.append(i)
            else:
                rej(c, "toc")
        elif c.score < min_score:
            rej(c, "low_score")
        elif i in sxref_idx:
            rej(c, "section_xref")  # Turn 13 B.4, `rej.section_xref`
            sxref_live.append(i)
        else:
            live.append(i)

    def mk(c: Candidate, depth: int, kind: str) -> Node:
        b = blocks[c.block_idx]
        return Node(0, 0, depth, kind, c.label_canon, c.label_raw, c.title, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_end,
                    c.norm_start, norm_len, c.order_key, min(1.0, c.score), list(c.rule_ids))

    # Articles and sections: max-weight increasing chains, one pair of chains per
    # numbering restart (`seq.per_segment`).  Each restart numbers from the top, so a
    # document-wide chain has to choose between them; between restarts it is monotone again.
    art_idx = [i for i in live if cands[i].kind == "article"]
    sec_idx = [i for i in live if cands[i].kind == "section"]

    def chain(idxs: list[int]) -> set[int]:
        return {idxs[p] for p in max_weight_increasing([(cands[i].order_key, cands[i].score) for i in idxs])}

    restart_set = set(restart_cands)
    art_pick: set[int] = set()
    sec_pick: set[int] = set()
    for s in range(len(chain_starts)):
        arts_s = [i for i in art_idx if chain_of(cands[i].head_raw_start) == s]
        secs_s = [i for i in sec_idx if chain_of(cands[i].head_raw_start) == s]
        head = next((i for i in arts_s if i in restart_set), None)
        if s > 0 and head is not None:
            # the candidate that opened the chain IS the chain: it is the stretch's
            # first article and carries order key 1, so it heads the chain by definition
            # and the rest of the chain is built above it.  Without this the chain could
            # prefer a second, heavier ARTICLE 1 later inside the same instrument and
            # the node carrying `chain.restart` would not exist.
            rest = [i for i in arts_s if i != head and cands[i].order_key > 1
                    and cands[i].head_raw_start > cands[head].head_raw_start]
            art_pick.add(head)
            art_pick |= chain(rest)
        else:
            art_pick |= chain(arts_s)
        sec_pick |= chain(secs_s)
    for i in art_idx:
        if i not in art_pick:
            rej(cands[i], "nonmonotone")
    for i in sec_idx:
        if i not in sec_pick:
            rej(cands[i], "nonmonotone")
    # Turn 13 B.4b: which vetoed references would have held a chain slot without the veto --
    # the section chain of each stretch re-run with them put back (the counterfactual Turn 12
    # tree).  Only those "displaced" references left clauses without a parent; one that would
    # have lost the chain anyway parented nothing before and gets no synthetic section now.
    displaced: set[int] = set()
    if _SXREF_ORPHAN_ON and sxref_live:
        for s in range(len(chain_starts)):
            xs = [i for i in sxref_live if chain_of(cands[i].head_raw_start) == s]
            if xs:
                secs_s = sorted([i for i in sec_idx if chain_of(cands[i].head_raw_start) == s] + xs)
                displaced |= chain(secs_s) & set(xs)

    nodes: list[Node] = [Node(0, -1, 0, "document", None, None, None, doc_raw_start, doc_raw_end, doc_raw_start, doc_raw_start, 0, norm_len, 0, 1.0, ["doc"])]
    for r in toc_regions:
        b0, b1 = blocks[r.block_start], blocks[r.block_end]
        nodes.append(Node(len(nodes), 0, 1, "toc", "TOC", None, None, b0.raw_start, b1.raw_end, b0.raw_start, b0.raw_end, b0.norm_start, b1.norm_end, 0, 0.8, [r.reason]))

    # Articles and sections are addressed by (chain, order key): two glued agreements
    # both have an ARTICLE 1 and a SECTION 1.01, and a section only ever nests under an
    # article of its own chain — an unmatched one gets a synthetic parent inside its own
    # chain rather than borrowing the previous stretch's article.  The key is the chain
    # and not the segment because an unsegmented restart puts two ARTICLE 1 nodes in one
    # segment, and they must not collide.
    articles: dict[tuple[int, int], Node] = {}
    for i in sorted(art_pick):
        c = cands[i]
        n = mk(c, 1, "article")
        s = chain_of(c.head_raw_start)
        if s:
            n.rule_ids.append("seq.per_segment")
        articles[(s, n.order_key)] = n
    sections: list[Node] = []
    for i in sorted(sec_pick):
        c = cands[i]
        n = mk(c, 2, "section")
        s = chain_of(c.head_raw_start)
        if s:
            n.rule_ids.append("seq.per_segment")
        a_num = c.order_key // 10000
        art = articles.get((s, a_num))
        if art is None or art.raw_start > n.raw_start:
            if art is None:
                art = Node(0, 0, 1, "article", f"ARTICLE {a_num}", None, None, c.head_raw_start, doc_raw_end, c.head_raw_start, c.head_raw_start, c.norm_start, norm_len, a_num, 0.5, ["gram.synth_article"] + (["seq.per_segment"] if s else []))
                articles[(s, a_num)] = art
            else:
                n.rule_ids.append("gram.article_after_section")
        sections.append(n)

    # Clauses: global minimum-cost sequencing within each section span (see sequence.py)
    from .sequence import SeqItem, learn_family_prior, sequence

    clause_idx = [i for i in live if cands[i].kind == "clause"]
    sec_sorted = sorted(sections, key=lambda n: n.raw_start)
    sec_starts = [s.raw_start for s in sec_sorted]
    # clause groups by the id() of the node they hang from: a section (the ordinary case)
    # or, under B.2.1's `gram.clause_under_article`, an article with no Section layer
    clause_by_parent: dict[int, list[list[Node]]] = {}
    synth_secs: list[Node] = []
    orphans: list[Candidate] = []  # B.4b: would-be `clause_outside_section` rows, when a reference was displaced
    # document-wide family prior from all clause candidates.  The `alt` readings are NOT
    # offered here: the prior asks which family dominates each depth, and a two-member
    # alternatives list is not a vote about that.
    all_items = [SeqItem(cands[i].score, _families(cands[i].label_canon.strip("()"))) for i in clause_idx]
    doc_prior = learn_family_prior(all_items, open_high=_R6_ON) if all_items else {}
    chain_end = [chain_starts[s + 1] if s + 1 < len(chain_starts) else doc_raw_end for s in range(len(chain_starts))]

    # --- 1. partition the live clause candidates into section spans and the gaps between
    spans: list[list[Candidate]] = []
    gaps: list[list[Candidate]] = []
    ci = 0
    for si in range(len(sec_sorted)):
        lo = sec_starts[si]
        hi = sec_starts[si + 1] if si + 1 < len(sec_sorted) else doc_raw_end
        # a section's span stops at its own chain's end: the clauses in the next
        # instrument's preamble belong to no section of this one (`seq.per_segment`)
        hi = min(hi, chain_end[chain_of(lo)])
        gap = []
        while ci < len(clause_idx) and blocks[cands[clause_idx[ci]].block_idx].raw_start < lo:
            gap.append(cands[clause_idx[ci]])
            ci += 1
        gaps.append(gap)
        span = []
        while ci < len(clause_idx) and blocks[cands[clause_idx[ci]].block_idx].raw_start < hi:
            span.append(cands[clause_idx[ci]])
            ci += 1
        spans.append(span)

    def place(unit: list[Candidate], parent: Node | None) -> list[Node]:
        """Sequence one unit of live clause candidates and make its nodes."""
        alt = _R4_ON and sum(1 for c in unit if (c.label_canon or "").strip("()").lower() in _ALT_MARKERS) >= 2
        res = sequence([SeqItem(c.score, _families(c.label_canon.strip("()"), alt=alt)) for c in unit],
                       family_prior=doc_prior, open_high=_R6_ON)
        out: list[Node] = []
        for c, pl in zip(unit, res.placements):
            if pl is None:
                rej(c, "clause_nonmonotone")
                continue
            n = mk(c, 3 + pl.depth, _KIND[pl.family])
            if pl.gap:
                n.rule_ids.append(f"seq.gap{pl.gap}")
            if pl.restart:
                n.rule_ids.append("seq.restart")
            if pl.open_high:
                n.rule_ids.append("seq.open_midrun")
            if pl.family == "alt":
                n.rule_ids.append("seq.alt_readings")
            if parent is not None:
                n.rule_ids.append("gram.clause_under_article" if parent.level_kind == "article"
                                  else "gram.synth_section")
            out.append(n)
        return out

    # --- 2. the gaps: B.2.1's region relief, and otherwise `clause_outside_section` ------
    arts_sorted = sorted(articles.values(), key=lambda n: n.raw_start)
    art_starts = [a.raw_start for a in arts_sorted]
    first_sec = sec_starts[0] if sec_starts else None
    ctx = _outside_context(blocks) if (_R3_ON and any(gaps)) else None
    for gap in gaps:
        for region in _cut_regions(gap, art_starts):
            keep: list[Candidate] = []
            kept: set[int] = set()
            if ctx is not None:
                # scope (s8 B.2.1): O1 / O2 / O5 / O7 unconditionally, O3 and O4 only where
                # `prose_after` -- the candidate's own title carries >= 3 letter-words, which
                # is what keeps the page-number footer, the `(NY)` docket stamp and the blank
                # fill-in cell out of the relief
                for c in region:
                    m = _outside_mechanism(c, ctx, blocks, first_sec, arts_sorted)
                    if m in ("O1", "O2", "O5", "O7") or (m in ("O3", "O4") and _prose_after(c)):
                        keep.append(c)
            if _region_ok(keep):
                parent = next((a for a in reversed(arts_sorted)
                               if a.raw_start <= keep[0].head_raw_start), None)
                synth = None
                if parent is None:
                    # no article either (the short-form instrument, the attached schedule):
                    # synthesize a Section at the region start, the mirror of the existing
                    # `gram.synth_article`, so the depth convention article/section/clause holds
                    c0 = keep[0]
                    synth = parent = Node(
                        0, 0, 2, "section", None, None, None, c0.head_raw_start, doc_raw_end,
                        c0.head_raw_start, c0.head_raw_start, c0.norm_start, norm_len, 0, 0.5,
                        ["gram.synth_section"] + (["seq.per_segment"] if chain_of(c0.head_raw_start) else []))
                # `place` writes its own rejected rows for whatever it cannot place, so a
                # relieved region's members are never rejected `clause_outside_section` too
                out = place(keep, parent)
                kept = {id(c) for c in keep}
                if out:
                    clause_by_parent.setdefault(id(parent), []).append(out)
                    if synth is not None:
                        synth_secs.append(synth)
                        sections.append(synth)
            else:
                kept = set()
            for c in region:
                if id(c) not in kept:
                    if displaced:
                        orphans.append(c)  # B.4b decides below
                    else:
                        rej(c, "clause_outside_section")

    # --- 3. the section spans ------------------------------------------------------------
    for si, span in enumerate(spans):
        if span:
            out = place(span, None)
            if out:
                clause_by_parent.setdefault(id(sec_sorted[si]), []).append(out)

    # --- 4. B.2.4, the bookkeeping fix: every live clause candidate the span loop never
    # consumed is written to the rejected table.  Two ways one is reached: the document has
    # no accepted section at all, so the loop above never ran (90,518 candidates in 1,191
    # documents); or it sits past the last section's own chain end, a trailing gap no span
    # covers (969 in 32).  91,487 in all were previously dropped with no node AND no
    # rejected row, which breaks "every rejected candidate is written with its reason".
    # No node changes: these candidates never reached `mk()` before this fix either.
    if _BOOKKEEPING_ON:
        while ci < len(clause_idx):
            if displaced:
                orphans.append(cands[clause_idx[ci]])
            else:
                rej(cands[clause_idx[ci]], "clause_outside_section")
            ci += 1

    # --- 5. Turn 13 B.4b (docs/turn13_decisions/b4_section_xref.md): `gram.synth_section_xref`.
    # In the Turn 12 tree a DISPLACED reference (one that would have held the chain slot,
    # above) was an accepted section whose span ran to the next accepted section or the chain
    # end, and every clause in that span was its child.  With the reference vetoed those
    # clauses are `clause_outside_section`.  The reference is not a heading, but the clauses
    # are real (the bank, runs/judge/turn13-b4b-*), so each would-be-rejected clause that lies
    # in a displaced reference's old span is kept: the span is cut at every accepted article
    # head (an article's clauses do not belong to a section of the previous article), and each
    # piece gets a synthetic Section parent anchored at its first clause, under the article it
    # sits in -- exactly B.2.1's `gram.synth_section` node -- and is sequenced on its own.
    if displaced:
        bounds = sorted([(h, -1) for h in sec_starts] + [(h, -1) for h in chain_starts]
                        + [(cands[i].head_raw_start, i) for i in displaced])
        bstarts = [b[0] for b in bounds]
        runs: dict[tuple[int, int], list[Candidate]] = {}
        for c in orphans:
            j = bisect_right(bstarts, c.head_raw_start - 1) - 1
            xi = bounds[j][1] if j >= 0 else -1
            if xi >= 0:
                s = chain_of(c.head_raw_start)
                art = next((a for a in reversed(arts_sorted)
                            if a.raw_start <= c.head_raw_start and chain_of(a.raw_start) == s), None)
                piece = art.raw_start if art is not None and art.raw_start > cands[xi].head_raw_start else -1
                runs.setdefault((xi, piece), []).append(c)
            else:
                rej(c, "clause_outside_section")
        for key in sorted(runs, key=lambda k: runs[k][0].head_raw_start):
            unit = runs[key]
            c0 = unit[0]
            s = chain_of(c0.head_raw_start)
            art = next((a for a in reversed(arts_sorted)
                        if a.raw_start <= c0.head_raw_start and chain_of(a.raw_start) == s), None)
            synth = Node(0, 0, 2, "section", None, None, None, c0.head_raw_start, doc_raw_end,
                         c0.head_raw_start, c0.head_raw_start, c0.norm_start, norm_len,
                         art.order_key * 10000 if art is not None else 0, 0.5,
                         ["gram.synth_section", "gram.synth_section_xref"] + (["seq.per_segment"] if s else []))
            out = place(unit, synth)
            if out:
                clause_by_parent.setdefault(id(synth), []).append(out)
                synth_secs.append(synth)
                sections.append(synth)

    # Assign ids: articles in order, sections under them, clauses under sections (or, for
    # B.2.1's no-node variant, directly under the article, interleaved with its sections)
    ordered_arts = sorted(articles.values(), key=lambda n: (n.raw_start, n.order_key))
    sec_by_art: dict[tuple[int, int], list[Node]] = {}
    for sn in sections:
        sec_by_art.setdefault((chain_of(sn.raw_start), sn.order_key // 10000), []).append(sn)
    for s in synth_secs:
        a_num = s.order_key // 10000
        k = (chain_of(s.raw_start), a_num)
        if k not in articles:
            articles[k] = Node(0, 0, 1, "article", None, None, None, s.raw_start, doc_raw_end,
                               s.raw_start, s.raw_start, s.norm_start, norm_len, a_num, 0.5,
                               ["gram.synth_article", "gram.synth_section"]
                               + (["seq.per_segment"] if k[0] else []))
    ordered_arts = sorted(articles.values(), key=lambda n: (n.raw_start, n.order_key))

    def emit(group: list[Node], parent_id: int) -> None:
        parents: list[int] = []  # node ids by depth
        for cn in group:
            d = cn.depth
            cn.node_id = len(nodes)
            cn.parent_id = parents[d - 4] if d >= 4 and len(parents) >= d - 3 else parent_id
            parents = parents[: d - 3] + [cn.node_id]
            nodes.append(cn)

    for a in ordered_arts:
        a.node_id = len(nodes); a.parent_id = 0; nodes.append(a)
        kids: list[tuple[int, int, object]] = [
            (s.raw_start, 0, s) for s in sec_by_art.get((chain_of(a.raw_start), a.order_key), [])]
        kids += [(g[0].raw_start, 1, g) for g in clause_by_parent.get(id(a), [])]
        for _off, tag, obj in sorted(kids, key=lambda t: (t[0], t[1])):
            if tag:
                emit(obj, a.node_id)  # type: ignore[arg-type]
                continue
            s = obj  # type: ignore[assignment]
            s.node_id = len(nodes); s.parent_id = a.node_id; nodes.append(s)
            for g in clause_by_parent.get(id(s), []):
                emit(g, s.node_id)
    # Turn 10 B.3: bare-title attachment, after clause sequencing and node ids (nothing
    # below reads `title`, and ends are closed after it so a moved `raw_start` carries the
    # previous node's `raw_end` with it)
    attach_bare_titles(nodes, blocks, cands, toc_regions, doc_raw_start=doc_raw_start, doc_raw_end=doc_raw_end)
    # ends
    seq = sorted(nodes[1:], key=lambda n: (n.raw_start, n.depth))
    for i, n in enumerate(seq):
        for m in seq[i + 1:]:
            if m.depth <= n.depth and m.raw_start > n.raw_start:
                n.raw_end = m.raw_start; n.norm_end = m.norm_start
                break
    return nodes, rejected
