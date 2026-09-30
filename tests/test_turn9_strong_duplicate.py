"""Turn 9 B.4 / B.4k — the same-order_key tiebreak (`seq.strong_duplicate`), 10-Q and 10-K.

`max_weight_increasing` maximizes the total weight of one strictly-increasing run, so a
weak early copy of a label can keep its slot because the rest of the chain follows it:
an undetected TOC row "Item 1." scoring 0.35 with Items 2 and 3 behind it outweighs the
real "ITEM 1. FINANCIAL STATEMENTS" scoring 1.05 that the filer bound after them
(docs/turn9_decisions/a5_10q_full.md section 4.2, cases 0000004962-25-000045 and
0001144204-08-027518).  The pass gives the slot to the strongest live copy when it beats
the chain's pick by at least 0.3, wherever in the document it sits.

Blocks and candidates are built directly (same shape as test_synth_item1.py) so the two
weights under test are exactly the ones in the assertions and not an artifact of
normalization or scoring.
"""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import Form10QGrammar
from edgar_itemize.tree import build_tree

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


def _doc(grammar, item1_labels, strong_score):
    """A 10-Q/10-K whose Item 1 appears twice: a weak index-shaped row at 100 and the
    real, fully styled heading at 400, behind Items 2 and 3.

    The chain keeps the row at 100 (0.35 + 0.9 + 0.9 = 2.15 beats 1.05 alone), which is
    what the tiebreak exists to overrule.
    """
    one, two, three = item1_labels
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 1.    Financial Statements .......... 3"),
        blk(2, 200, text="Item 2. Management's Discussion and Analysis"),
        blk(3, 300, text="Item 3. Quantitative and Qualitative Disclosures"),
        blk(4, 400, text="ITEM 1. FINANCIAL STATEMENTS"),
    ]
    cands = [
        cand(grammar, 0, "part", "PART I", 0, score=0.9),
        cand(grammar, 1, "item", one, 100, score=0.35),
        cand(grammar, 2, "item", two, 200, score=0.9),
        cand(grammar, 3, "item", three, 300, score=0.9),
        cand(grammar, 4, "item", one, 400, score=strong_score),
    ]
    if grammar.name == "form10k":
        cands = [c for c in cands if c.kind == "item"]
    return build_tree(blocks, cands, [], grammar, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)


Q_LABELS = ("ITEM I.1", "ITEM I.2", "ITEM I.3")
K_LABELS = ("ITEM 1", "ITEM 2", "ITEM 3")


def items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def test_strong_duplicate_takes_the_slot_from_the_weak_row():
    nodes, rejected = _doc(Q, Q_LABELS, 1.05)
    it = items(nodes)
    assert set(it) == {"ITEM I.1", "ITEM I.2", "ITEM I.3"}  # the chain's label set is preserved
    one = it["ITEM I.1"]
    assert one.raw_start == 400  # the real heading, not the index row at 100
    assert one.confidence == 1.0  # min(1.0, 1.05)
    assert "seq.strong_duplicate" in one.rule_ids
    # the displaced copy is rejected under its own name, not as a chain failure
    weak = [r for r in rejected if r.label_canon == "ITEM I.1" and r.raw_start == 100]
    assert len(weak) == 1 and weak[0].reason == "weak_duplicate"
    assert not [r for r in rejected if r.raw_start == 400]


def test_strong_duplicate_does_not_stretch_its_part_over_the_next_one():
    """Part I ends where Part II begins even when its Item 1 was promoted behind it."""
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 1.    Financial Statements .......... 3"),
        blk(2, 200, text="Item 2. Management's Discussion and Analysis"),
        blk(3, 300, text="PART II - OTHER INFORMATION"),
        blk(4, 400, text="ITEM 1. FINANCIAL STATEMENTS"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 1, "item", "ITEM I.1", 100, score=0.35),
        cand(Q, 2, "item", "ITEM I.2", 200, score=0.9),
        cand(Q, 3, "part", "PART II", 300, score=0.9),
        cand(Q, 4, "item", "ITEM I.1", 400, score=1.05),
    ]
    nodes, _ = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    p1 = next(n for n in nodes if n.level_kind == "part" and n.label_canon == "PART I")
    one = next(n for n in nodes if n.level_kind == "item" and n.label_canon == "ITEM I.1")
    assert one.raw_start == 400
    assert p1.raw_end == 300  # Part II's start, not its promoted child's end


def test_control_gap_under_the_margin_keeps_the_chain_pick():
    nodes, rejected = _doc(Q, Q_LABELS, 0.6)  # 0.6 - 0.35 = 0.25 < 0.3
    one = items(nodes)["ITEM I.1"]
    assert one.raw_start == 100
    assert "seq.strong_duplicate" not in one.rule_ids
    late = [r for r in rejected if r.label_canon == "ITEM I.1" and r.raw_start == 400]
    assert len(late) == 1 and late[0].reason == "nonmonotone"
    assert not [r for r in rejected if r.reason == "weak_duplicate"]


def test_form10k_also_promotes():
    """Turn 9 B.4k widened `_STRONG_DUPLICATE_GRAMMARS` to include `form10k`: the
    identical collision was measured separately on the 10-K corpus (539 pairs / 195 of
    228,803 filings at gap >= 0.3, 96.8% strong-copy-later -- docs/turn9_decisions/
    b4_10q_tiebreak.md section 6) and, after a full run_diff gate with zero item/part
    label deltas (docs/turn9_decisions/b4k_10k_widen.md), the 10-K now gets the same
    tiebreak as the 10-Q. EX-13 documents are parsed with this same grammar object, so
    the widening reaches them too."""
    nodes, rejected = _doc(K, K_LABELS, 1.05)
    it = items(nodes)
    assert set(it) == {"ITEM 1", "ITEM 2", "ITEM 3"}  # the chain's label set is preserved
    one = it["ITEM 1"]
    assert one.raw_start == 400  # the real heading, not the index row at 100
    assert "seq.strong_duplicate" in one.rule_ids
    weak = [r for r in rejected if r.label_canon == "ITEM 1" and r.raw_start == 100]
    assert len(weak) == 1 and weak[0].reason == "weak_duplicate"
    assert not [r for r in rejected if r.raw_start == 400]


def test_an_earlier_stronger_copy_cannot_take_the_slot():
    """Only a LATER copy is promoted (`_STRONG_DUPLICATE_LATER_ONLY`).

    Backwards promotions pull a real body heading onto the front-of-document index row
    that outscores it: 11 of 15 hand-read backwards moves on the full 10-Q corpus were
    wrong (runs/judge/turn9-b4-handread-earlier.txt), against 28 of 30 right forwards.

    The chain is made to pick the LATER, weaker copy of Item 2 by putting an Item 1
    between the two: [I.1@200 1.0, I.2@300 0.35] = 1.35 beats [I.2@100 1.05] = 1.05.
    The stronger copy at 100 is the front index row (0000895469-98-000002's shape), and
    it must not take the slot back.
    """
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 2. Management's Discussion and Analysis .......... 15 - 23"),
        blk(2, 200, text="Item 1. Financial Statements"),
        blk(3, 300, text="ITEM 2 - MANAGEMENT'S DISCUSSION AND ANALYSIS"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 1, "item", "ITEM I.2", 100, score=1.05),  # the index row, strong
        cand(Q, 2, "item", "ITEM I.1", 200, score=1.0),
        cand(Q, 3, "item", "ITEM I.2", 300, score=0.35),  # the body heading, weak
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    it = items(nodes)
    assert it["ITEM I.2"].raw_start == 300  # the chain's later pick stands
    assert "seq.strong_duplicate" not in it["ITEM I.2"].rule_ids
    assert not [r for r in rejected if r.reason == "weak_duplicate"]
    assert [r for r in rejected if r.label_canon == "ITEM I.2" and r.raw_start == 100][0].reason == "nonmonotone"


def test_running_page_header_cannot_take_the_slot():
    """A `rej.page_repeat`/`rej.continued` copy is a page header, never a section start."""
    blocks = [
        blk(0, 0, text="PART I - FINANCIAL INFORMATION"),
        blk(1, 100, text="Item 1.    Financial Statements .......... 3"),
        blk(2, 200, text="Item 2. Management's Discussion and Analysis"),
        blk(3, 300, text="Item 3. Quantitative and Qualitative Disclosures"),
        blk(4, 400, text="ITEM 1. FINANCIAL STATEMENTS (continued)"),
    ]
    cands = [
        cand(Q, 0, "part", "PART I", 0, score=0.9),
        cand(Q, 1, "item", "ITEM I.1", 100, score=0.35),
        cand(Q, 2, "item", "ITEM I.2", 200, score=0.9),
        cand(Q, 3, "item", "ITEM I.3", 300, score=0.9),
        cand(Q, 4, "item", "ITEM I.1", 400, score=1.05, rules=["rej.page_repeat"]),
    ]
    nodes, rejected = build_tree(blocks, cands, [], Q, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    assert items(nodes)["ITEM I.1"].raw_start == 100
    assert not [r for r in rejected if r.reason == "weak_duplicate"]
