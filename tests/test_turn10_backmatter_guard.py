"""Turn 10 Phase B.0 (fixed by Phase C's t10-b0-fix) -- the Part-restart guard on
`seq.strong_duplicate`.

docs/turn9_decisions/b4k_10k_widen.md sections 2-3 found the B.4k 10-K widening's two
`core_complete` losses and its one hand-read miss are all the same shape: a later,
higher-scoring same-canonical-label rival that is NOT a genuine second occurrence of
the chain's section because it sits inside a later stretch that reuses a Part number
the document's own numbering already used (Sun River Energy, 0001010549-11-000633;
Crimson, 0000813779-10-000010, the hand-read miss). `strong_duplicate_pass` (tree.py)
refuses to promote such a rival and tags it `seq.strong_duplicate_backmatter`.

The first shipped version (fdff040) ALSO refused a rival past a bare back-matter marker
heading (SIGNATURES / EXHIBIT INDEX / ...). The full-corpus gate
(runs/judge/turn10-b2-gate.txt section 2, runs/judge/turn10-b0-regression.txt) found
that condition never once right and responsible for 15 of 19 wrong anchor moves on the
complete 228,786-document 10-K corpus (mostly Fifth Third Bancorp, 13 consecutive
years, where a real, later "ITEM 2. PROPERTIES" legitimately sits past a genuine
mid-document "SIGNATURES" heading in these multi-million-byte filings) -- removed
entirely rather than re-tuned; there is no test for it in this file any more.

The remaining 4 of the 19 wrong moves were Part-restart false positives from a SECOND,
undetected front-matter cross-reference table whose own tightly-packed "PART I"..
"PART IV" row collided with the real body's -- fixed with two more filters,
`_ADJACENT_TOC_GAP` (a dense run of Part labels close together is a listing, not a
document's own Part transition) and `_MAX_REPEATED_LABELS` (more than one label
repeating as a second full I..IV cycle is a different, unresolved shape, not the "one
foreign Part" shape this guard exists for) -- both covered below.

Blocks and candidates are built directly (same shape as test_turn9_strong_duplicate.py)
so the guard boundaries under test are exactly the ones the assertions check and not an
artifact of normalization or scoring.
"""

import pytest

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.form10k import ITEM_RE, Form10KGrammar
from edgar_itemize.tree import build_tree

K = Form10KGrammar()


def blk(idx, pos, text="x", **kw):
    return Block(idx=idx, text=text, raw_start=pos, raw_end=pos + 50, norm_start=pos, norm_end=pos + 50, **kw)


def cand(grammar, block_idx, kind, label, pos, score=0.8, rules=()):
    return Candidate(
        block_idx=block_idx, kind=kind, label_raw=label, label_canon=label, title="T", score=score,
        rule_ids=list(rules), order_key=grammar.order_key(kind, label),
        head_raw_start=pos, head_raw_end=pos + 10,
    )


def items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def test_rival_inside_a_later_part_restart_is_not_promoted():
    """(a) A later rival that sits after a Part label the document already used earlier
    is inside a later segment with its own Part numbering -- Sun River / Crimson's
    shape. The repeated "PART I" is 10000 bytes from the first use (well past both the
    30%-of-document gap floor and the 2000-byte adjacency floor -- see the next two
    tests for what those catch)."""
    blocks = [
        blk(0, 0, text="PART I"),
        blk(1, 100, text="Item 1.    Business .......... 3"),
        blk(2, 200, text="Item 2. Properties"),
        blk(3, 300, text="Item 3. Legal Proceedings"),
        blk(4, 10000, text="PART I-FINANCIAL INFORMATION"),
        blk(5, 10100, text="ITEM 1. FINANCIAL STATEMENTS"),
    ]
    cands = [
        cand(K, 0, "part", "PART I", 0, score=0.9),
        cand(K, 1, "item", "ITEM 1", 100, score=0.35),
        cand(K, 2, "item", "ITEM 2", 200, score=0.9),
        cand(K, 3, "item", "ITEM 3", 300, score=0.9),
        cand(K, 4, "part", "PART I", 10000, score=0.4),  # repeats the label -- a restart
        cand(K, 5, "item", "ITEM 1", 10100, score=1.05),
    ]
    nodes, rejected = build_tree(blocks, cands, [], K, doc_raw_start=0, doc_raw_end=12000, norm_len=12000)
    it = items(nodes)
    assert it["ITEM 1"].raw_start == 100  # the chain's pick stands
    assert "seq.strong_duplicate" not in it["ITEM 1"].rule_ids
    blocked = next(c for c in cands if c.kind == "item" and c.head_raw_start == 10100)
    assert "seq.strong_duplicate_backmatter" in blocked.rule_ids
    assert not [r for r in rejected if r.reason == "weak_duplicate"]


def test_dense_front_cluster_is_not_a_restart_reference():
    """(b) A SECOND, undetected front-matter cross-reference table whose own Part I..IV
    row sits within a few hundred bytes of each other (0000702808-00-000001's real
    shape, runs/judge/turn10-b0-regression.txt) must not seed "already used" for the
    real, later Part II -- every genuine Part-to-Part gap measured on the acceptance
    filings was at minimum ~5,000 bytes; this cluster's largest internal gap is 300.
    `_ADJACENT_TOC_GAP` drops every member of the cluster from the pool, so the real,
    later "PART II" -- promoted here because it is the document's own only tracked
    occurrence -- has no earlier reference to repeat."""
    blocks = [
        blk(0, 0, text="PART I"),
        blk(1, 100, text="PART II"),
        blk(2, 300, text="PART III"),
        blk(3, 600, text="PART IV"),
        blk(4, 1000, text="Item 6.    Selected Financial Data .......... 30"),
        blk(5, 20000, text="PART II"),
        blk(6, 20100, text="Item 6. Selected Financial Data"),
    ]
    cands = [
        cand(K, 0, "part", "PART I", 0, score=0.6),
        cand(K, 1, "part", "PART II", 100, score=0.65),
        cand(K, 2, "part", "PART III", 300, score=0.65),
        cand(K, 3, "part", "PART IV", 600, score=0.65),
        cand(K, 4, "item", "ITEM 6", 1000, score=0.45, rules=["toc.leader_or_pageno"]),
        cand(K, 5, "part", "PART II", 20000, score=0.75),
        cand(K, 6, "item", "ITEM 6", 20100, score=0.65, rules=["gram.item.title_match"]),
    ]
    nodes, rejected = build_tree(blocks, cands, [], K, doc_raw_start=0, doc_raw_end=25000, norm_len=25000)
    it = items(nodes)
    assert it["ITEM 6"].raw_start == 20100  # promoted: the front cluster is not a restart reference
    assert "seq.strong_duplicate_backmatter" not in it["ITEM 6"].rule_ids
    late = next(c for c in cands if c.kind == "item" and c.head_raw_start == 20100)
    assert "seq.strong_duplicate_backmatter" not in late.rule_ids


def test_whole_document_repeated_is_not_a_restart():
    """(c) A document where every Part label repeats as a second full I..IV cycle
    (0000950124-07-001895's real shape, runs/judge/turn10-b0-regression.txt) is a
    different, unresolved shape from Sun River / Crimson's ONE reused label --
    `_MAX_REPEATED_LABELS` disables restart detection for the whole document rather
    than guess which cycle is real. Both cycles here clear `_ADJACENT_TOC_GAP`
    (3000-byte internal spacing) and the second Part I's gap from the first clears the
    30% floor too, so only the repeated-labels filter is under test."""
    blocks = [
        blk(0, 0, text="PART I"),
        blk(1, 100, text="Item 1B.    Unresolved Staff Comments .......... 2"),
        blk(2, 3000, text="PART II"),
        blk(3, 6000, text="PART III"),
        blk(4, 9000, text="PART IV"),
        blk(5, 12000, text="PART I"),
        blk(6, 12100, text="ITEM 1B. Unresolved Staff Comments"),
        blk(7, 15000, text="PART II"),
        blk(8, 18000, text="PART III"),
        blk(9, 21000, text="PART IV"),
    ]
    cands = [
        cand(K, 0, "part", "PART I", 0, score=0.85),
        cand(K, 1, "item", "ITEM 1B", 100, score=0.55, rules=["toc.leader_or_pageno"]),
        cand(K, 2, "part", "PART II", 3000, score=0.85),
        cand(K, 3, "part", "PART III", 6000, score=0.85),
        cand(K, 4, "part", "PART IV", 9000, score=0.85),
        cand(K, 5, "part", "PART I", 12000, score=0.95),
        cand(K, 6, "item", "ITEM 1B", 12100, score=1.05),
        cand(K, 7, "part", "PART II", 15000, score=0.95),
        cand(K, 8, "part", "PART III", 18000, score=0.95),
        cand(K, 9, "part", "PART IV", 21000, score=0.95),
    ]
    nodes, rejected = build_tree(blocks, cands, [], K, doc_raw_start=0, doc_raw_end=24000, norm_len=24000)
    it = items(nodes)
    assert it["ITEM 1B"].raw_start == 12100  # promoted: repeated-labels disables the restart guard
    late = next(c for c in cands if c.kind == "item" and c.head_raw_start == 12100)
    assert "seq.strong_duplicate_backmatter" not in late.rule_ids


@pytest.mark.xfail(reason=(
    "letter-spaced multi-digit item numbers (ITEM_RE's num group only reaches the "
    "first contiguous digit run) mis-tokenize as a truncated ITEM 1 candidate -- a "
    "tokenizer defect, not a Part-restart scope defect, so Turn 10 Phase B.0's guard "
    "does not and should not catch it (docs/TURN10_PLAN.md Phase B item 0; "
    "docs/turn9_decisions/b4k_10k_widen.md section 2 case 1, 0000950129-05-003051). "
    "A real fix needs the tokenizer to refuse a single digit immediately followed, "
    "after only whitespace with no intervening punctuation, by another digit -- left "
    "for a future turn."
))
def test_letter_spaced_item13_mistokenizes_as_item1():
    """0000950129-05-003051 (McDermott International): the real "Item 13. CERTAIN
    RELATIONSHIPS AND RELATED TRANSACTIONS" heading is rendered letter-spaced --
    "I t e m 1 3. C E R T A I N ..." -- and ITEM_RE's number capture stops at the first
    contiguous digit run ("1"), handing "3. C E R T A I N ..." to the title. The
    resulting candidate carries `label_canon="ITEM 1"` and inherits Item 13's own bold
    / caps / center formatting, which is what let it out-score and displace the
    filing's real Item 1 under the pre-guard `seq.strong_duplicate` (the sole cause of
    one of the B.4k widening's two `core_complete` losses)."""
    text = "I t e m 1 3. C E R T A I N R E L A T I O N S H I P S A N D R E L A T E D T R A N S A C T I O N S"
    m = ITEM_RE.match(text)
    assert m is not None
    assert K.canonicalize("item", m) == "ITEM 13"  # today: "ITEM 1" -- see xfail reason


def test_later_promotion_still_happens_with_no_restart():
    """(d) The ordinary case the B.4k widening exists for is unaffected: a later,
    stronger same-label rival that sits in plain body text with no Part restart in
    front of it is still promoted (same shape as
    test_turn9_strong_duplicate.py::test_form10k_also_promotes, re-asserted here against
    the new guard so a regression in the restart boundary computation would show up as
    a fresh failure in this file, not just a silent no-op in the old one)."""
    blocks = [
        blk(0, 0, text="PART I"),
        blk(1, 100, text="Item 1.    Business .......... 3"),
        blk(2, 200, text="Item 2. Properties"),
        blk(3, 300, text="Item 3. Legal Proceedings"),
        blk(4, 400, text="ITEM 1. BUSINESS"),
    ]
    cands = [
        cand(K, 1, "item", "ITEM 1", 100, score=0.35),
        cand(K, 2, "item", "ITEM 2", 200, score=0.9),
        cand(K, 3, "item", "ITEM 3", 300, score=0.9),
        cand(K, 4, "item", "ITEM 1", 400, score=1.05),
    ]
    nodes, rejected = build_tree(blocks, cands, [], K, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    it = items(nodes)
    assert it["ITEM 1"].raw_start == 400  # promoted: no restart in this document
    assert "seq.strong_duplicate" in it["ITEM 1"].rule_ids
    weak = [r for r in rejected if r.label_canon == "ITEM 1" and r.raw_start == 100]
    assert len(weak) == 1 and weak[0].reason == "weak_duplicate"
