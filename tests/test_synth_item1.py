"""Turn 8: gram.synth_item1 -- synthesize ITEM I.1 at the financial-statement
caption when a 10-Q's Part I has no real Item 1 candidate anywhere in its span
(runs/overnight/G1/report.md class 2; docs/turn8_decisions/a6_10q.md section 4).

Uses the same low-level Block/Candidate/TocRegion construction as
test_toc_turn3.py so the TOC-only-heading shape (a real "Item 1." row that
exists only inside a detected, correctly-rejected TOC region) can be built
directly without depending on HTML/text normalization or TOC detection.
"""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import Form10QGrammar
from edgar_itemize.toc import TocRegion
from edgar_itemize.tree import _FIN_CAPTION_RE, build_tree

Q = Form10QGrammar()
K = Form10KGrammar()


def blk(idx, pos, text="x", **kw):
    return Block(idx=idx, text=text, raw_start=pos, raw_end=pos + 50, norm_start=pos, norm_end=pos + 50, **kw)


def cand(grammar, block_idx, kind, label, pos, score=0.8, rules=()):
    return Candidate(
        block_idx=block_idx, kind=kind, label_raw=label, label_canon=label, title="T", score=score,
        rule_ids=list(rules), order_key=grammar.order_key(kind, label),
        head_raw_start=pos, head_raw_end=pos + 10,
    )


def _base_blocks_cands():
    """PART I heading (block 0), a TOC row mentioning "Item 1" (block 1, condemned),
    a financial-statement caption with no matching candidate (block 2), Item 2 (block 3)."""
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 1.    Financial Statements .......... 3"),
        blk(2, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(3, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 1, "item", "ITEM I.1", 100, score=0.5, rules=["toc.leader_or_pageno"]),
        cand(Q, 3, "item", "ITEM I.2", 300, score=0.9),
    ]
    regions = [TocRegion(first_cand=1, last_cand=1, block_start=1, block_end=1, reason="toc.hints")]
    return blocks, cands, regions


def test_synth_item1_fires_on_toc_only_heading():
    blocks, cands, regions = _base_blocks_cands()
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    synth = [n for n in nodes if n.level_kind == "item" and "gram.synth_item1" in n.rule_ids]
    assert len(synth) == 1
    n = synth[0]
    assert n.label_canon == "ITEM I.1"
    assert n.raw_start == 200  # anchored at the caption block, not the TOC row or Part I heading
    assert n.confidence == 0.5
    # the correctly-rejected TOC row is still rejected reason=toc, not rescued
    assert any(r.label_canon == "ITEM I.1" and r.reason == "toc" for r in rejected)
    # and it is nested under Part I like any other item
    part1 = next(x for x in nodes if x.level_kind == "part" and x.label_canon == "PART I")
    assert n.parent_id == part1.node_id


def test_synth_item1_does_not_fire_when_real_item1_exists_in_part_i():
    blocks, cands, regions = _base_blocks_cands()
    # a real (non-toc) Item 1 candidate elsewhere in Part I's span -- e.g. rejected
    # low_score or nonmonotone, but a genuine attempt, not a TOC row
    blocks.append(blk(4, 150, text="Item 1"))
    cands.append(cand(Q, 4, "item", "ITEM I.1", 150, score=0.2))
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_does_not_duplicate_a_rescued_toc_row():
    # a TOC-region Item 1 that IS member-rescued (score >= 0.6, no leader/pageno
    # tag, no strong outside copy) becomes a real live node -- synthesizing a
    # second "ITEM I.1" at the caption would duplicate the label under Part I
    # (the anchor-move bug this test guards: the rescued node's own raw_start
    # must be the only "ITEM I.1" in the tree).
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 1. Financial Statements"),  # rescued, not toc-rejected
        blk(2, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(3, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 1, "item", "ITEM I.1", 100, score=0.7),  # no toc.leader_or_pageno -> rescuable
        cand(Q, 3, "item", "ITEM I.2", 300, score=0.9),
    ]
    regions = [TocRegion(first_cand=1, last_cand=1, block_start=1, block_end=1, reason="toc.duplicated_later")]
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    item1_nodes = [n for n in nodes if n.label_canon == "ITEM I.1"]
    assert len(item1_nodes) == 1
    assert item1_nodes[0].raw_start == 100
    assert "toc.member_rescued" in item1_nodes[0].rule_ids
    assert "gram.synth_item1" not in item1_nodes[0].rule_ids
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_does_not_duplicate_on_sub_block_item_position():
    # Part and Item 1 both found on later lines of the SAME multi-line block
    # (pos.line2/pos.line3, or a table row) -- the block's own raw_start (line 1)
    # can sit well before either heading's real position. Comparing against the
    # candidate's own head_raw_start, not its block's raw_start, is what keeps
    # this real Item 1 inside the search span (35 of the Turn 8 gate's remaining
    # duplicates, e.g. 0000950152-02-008317, were this shape).
    blocks = [
        blk(0, 0, text="line1\nPART I\nItem 1. Financial Statements"),
        blk(1, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(2, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 10, score=0.9),
        cand(Q, 0, "item", "ITEM I.1", 20, score=0.8),  # same block_idx=0, block raw_start=0 < 10
        cand(Q, 2, "item", "ITEM I.2", 300, score=0.9),
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    item1_nodes = [n for n in nodes if n.label_canon == "ITEM I.1"]
    assert len(item1_nodes) == 1
    assert item1_nodes[0].raw_start == 20
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_does_not_duplicate_when_part_opened_early():
    # Part I's own heading is typeset AFTER its first item (gram.part_after_item):
    # the Part node's raw_start gets pulled back to the item, but head_raw_end still
    # reflects the later heading text -- the span's lower bound must track raw_start,
    # not head_raw_end, or the real Item 1 falls outside the search window and gets
    # a duplicate synthesized alongside it (the Turn 8 gate caught 312 of these).
    blocks = [
        blk(0, 150, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 50, text="Item 1. Financial Statements"),
        blk(2, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(3, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 150, score=0.9),
        cand(Q, 1, "item", "ITEM I.1", 50, score=0.8),
        cand(Q, 3, "item", "ITEM I.2", 300, score=0.9),
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    part1 = next(n for n in nodes if n.level_kind == "part" and n.label_canon == "PART I")
    assert part1.raw_start == 50  # pulled back to the item that precedes the heading
    item1_nodes = [n for n in nodes if n.label_canon == "ITEM I.1"]
    assert len(item1_nodes) == 1
    assert item1_nodes[0].raw_start == 50
    assert "gram.part_after_item" in item1_nodes[0].rule_ids
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_does_not_duplicate_when_inline_after_part():
    # Item 1 runs into the same block as the Part heading (pos.inline_after_part):
    # its own head_raw_start can precede the Part's head_raw_end even with no
    # part_after_item adjustment at all (18 of the Turn 8 gate's 342 duplicates).
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION Item 1. Financial Statements"),
        blk(1, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(2, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 0, "item", "ITEM I.1", 5, score=0.8),  # inline: starts before Part's head_raw_end (10)
        cand(Q, 2, "item", "ITEM I.2", 300, score=0.9),
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    item1_nodes = [n for n in nodes if n.label_canon == "ITEM I.1"]
    assert len(item1_nodes) == 1
    assert item1_nodes[0].raw_start == 5
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_skips_a_caption_line_right_after_toc_furniture():
    # An undetected index/TOC run (docs/turn8_decisions/a6_10q.md section 5 finding 2)
    # can list a caption entry that itself lacks a trailing page number (0001064435-98-
    # 000002's "Notes to Condensed Consolidated Financial Statements", wrapped past its
    # own page reference) -- it still matches _FIN_CAPTION_RE as a bare line, but the
    # block right before it is unmistakably TOC furniture (dot leaders + page number).
    # The real, later caption must win instead.
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Condensed Consolidated Statements of Cash Flows..................5"),
        blk(2, 150, text="Notes to Condensed Consolidated Financial Statements"),  # TOC entry, no own page #
        blk(3, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),  # real body caption
        blk(4, 300, text="Item 2. Management's Discussion and Analysis"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 4, "item", "ITEM I.2", 300, score=0.9),
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    synth = [n for n in nodes if "gram.synth_item1" in n.rule_ids]
    assert len(synth) == 1
    assert synth[0].raw_start == 200  # the real caption, not the TOC-adjacent one at 150


def test_synth_item1_no_fire_without_caption_text():
    blocks, cands, regions = _base_blocks_cands()
    blocks[2] = blk(2, 200, text="Some unrelated paragraph of prose text.")
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_no_fire_without_item2():
    blocks, cands, regions = _base_blocks_cands()
    cands = [c for c in cands if c.label_canon != "ITEM I.2"]
    blocks = blocks[:3]
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_no_fire_when_part1_itself_synthesized():
    blocks, cands, regions = _base_blocks_cands()
    cands = [c for c in cands if c.label_canon != "PART I"]  # no explicit Part I heading
    nodes, rejected = build_tree(blocks, cands, regions, Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


def test_synth_item1_never_fires_on_10k():
    # identical shape (a TOC-only "Item 1" row, an unheaded caption, then Item 2),
    # but under the 10-K grammar: build_tree must never call the 10-Q-only hook.
    blocks = [
        blk(0, 0, text="PART I"),
        blk(1, 100, text="Item 1.    Business .......... 3"),
        blk(2, 200, text="CONDENSED CONSOLIDATED BALANCE SHEETS\n(Unaudited)"),
        blk(3, 300, text="Item 2. Properties"),
    ]
    cands = [
        cand(K, 0, "part", "PART I", 0, score=0.9),
        cand(K, 1, "item", "ITEM 1", 100, score=0.5, rules=["toc.leader_or_pageno"]),
        cand(K, 3, "item", "ITEM 2", 300, score=0.9),
    ]
    regions = [TocRegion(first_cand=1, last_cand=1, block_start=1, block_end=1, reason="toc.hints")]
    nodes, rejected = build_tree(blocks, cands, regions, K, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert not any("gram.synth_item1" in n.rule_ids for n in nodes)


# --- _FIN_CAPTION_RE: caption-shaped lines match, prose sentences do not -----------


def test_fin_caption_matches_bare_captions():
    for line in (
        "CONDENSED CONSOLIDATED BALANCE SHEETS",
        "Condensed Consolidated Balance Sheets (Unaudited)",
        "CONSOLIDATED STATEMENTS OF OPERATIONS",
        "Statements of Cash Flows",
        "Financial Statements",
        "Notes to Condensed Consolidated Financial Statements",
        "XYZ Corp. and Subsidiaries Condensed Balance Sheets",
    ):
        assert _FIN_CAPTION_RE.search(line), line


def test_fin_caption_does_not_match_prose():
    for line in (
        "The accompanying condensed consolidated balance sheets have been prepared in accordance with GAAP.",
        "See Note 3 to the financial statements for further discussion of the balance sheets.",
        "Item 1. Financial Statements .......... 3",
    ):
        assert not _FIN_CAPTION_RE.search(line), line
