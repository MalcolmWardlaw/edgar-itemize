"""Turn 7 Phase C.2: contract TOC condemnation (`toc.section_run`,
`toc.dup_later_contract`) and the contract-side member rescue.

A credit agreement's table of contents carries none of the 10-K evidence: no
caption, no dot leaders, no page numbers, no hrefs.  The evidence is the run as a
whole — dense, prose-free section rows whose labels are re-found later spread over
a much longer stretch of document (docs/TURN7_PLAN.md Phase C item 2).
"""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree_contract import build_contract_tree

G = ContractGrammar()


def build(rows):
    """rows: (kind, label, norm_start, score, rules, text). Blocks are contiguous."""
    blocks, cands = [], []
    for i, (kind, label, pos, score, rules, text) in enumerate(rows):
        blocks.append(Block(idx=i, text=text, raw_start=pos, raw_end=pos + len(text),
                            norm_start=pos, norm_end=pos + len(text)))
        cands.append(Candidate(block_idx=i, kind=kind, label_raw=label, label_canon=label, title="T",
                               score=score, rule_ids=list(rules), order_key=G.order_key(kind, label),
                               toc_hint=any(r.startswith("toc.") for r in rules),
                               head_raw_start=pos, head_raw_end=pos + 12, norm_start=pos))
    return blocks, cands


def toc_rows(labels, start=0, step=45):
    """A leaderless index: one short row per section, no prose between them."""
    return [("section", lab, start + i * step, 0.65, [], f"{lab} Title") for i, lab in enumerate(labels)]


def body_rows(labels, start=5000, step=9000):
    """Body headings: the label runs straight into the section's own prose."""
    return [("section", lab, start + i * step, 0.45, [],
             f"{lab} Title. The Borrower shall from time to time deliver to the Administrative "
             "Agent such certificates and other documents as the Lenders may reasonably request.")
            for i, lab in enumerate(labels)]


LABELS = ["SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01",
          "SECTION 2.02", "SECTION 2.03", "SECTION 3.01", "SECTION 3.02"]


def test_leaderless_contract_toc_is_condemned():
    blocks, cands = build(toc_rows(LABELS) + body_rows(LABELS))
    regions = detect_toc(blocks, cands, norm_len=80000, grammar="contract")
    assert len(regions) == 1 and regions[0].reason == "toc.dup_later_contract"
    assert regions[0].first_cand == 0 and regions[0].last_cand == len(LABELS) - 1
    assert all("toc.section_run" in cands[i].rule_ids for i in range(len(LABELS)))
    # ... and the body copies, not the rows, become the accepted sections
    nodes, rejected = build_contract_tree(blocks, cands, regions, G, doc_raw_start=0, doc_raw_end=80000, norm_len=80000)
    starts = {n.label_canon: n.head_raw_start for n in nodes if n.level_kind == "section"}
    assert starts == {lab: 5000 + i * 9000 for i, lab in enumerate(LABELS)}
    assert sum(1 for r in rejected if r.reason == "toc") == len(LABELS)


def test_same_rows_without_a_body_are_not_condemned():
    # the identical run with no later copies is a terse body, not an index
    blocks, cands = build(toc_rows(LABELS))
    assert detect_toc(blocks, cands, norm_len=80000, grammar="contract") == []


def test_body_only_agreement_is_not_condemned():
    # sections at body spacing: no run forms at all, so nothing is proposed
    blocks, cands = build(body_rows(LABELS, start=0, step=9000))
    assert detect_toc(blocks, cands, norm_len=80000, grammar="contract") == []


def test_compound_exhibit_repeat_is_not_condemned():
    # two attached agreements with the same section numbering and equally terse
    # sections: the "later copies" span no more than the first list does, so the
    # spread test stands the run down
    a = [("section", lab, i * 300, 0.50, [], f"{lab} Title") for i, lab in enumerate(LABELS)]
    b = [("section", lab, 4000 + i * 300, 0.50, [], f"{lab} Title") for i, lab in enumerate(LABELS)]
    blocks, cands = build(a + b)
    assert detect_toc(blocks, cands, norm_len=80000, grammar="contract") == []


def test_leadered_contract_toc_still_condemned():
    rows = [("section", lab, i * 45, 0.50, ["toc.leader_or_pageno"], f"{lab} Title . . . . . {i + 3}")
            for i, lab in enumerate(LABELS[:4])]
    blocks, cands = build(rows + body_rows(LABELS[:4]))
    regions = detect_toc(blocks, cands, norm_len=80000, grammar="contract")
    assert len(regions) == 1 and regions[0].reason == "toc.hints"  # too short for the row-run rule
    nodes, _ = build_contract_tree(blocks, cands, regions, G, doc_raw_start=0, doc_raw_end=80000, norm_len=80000)
    starts = {n.label_canon: n.head_raw_start for n in nodes if n.level_kind == "section"}
    assert starts == {lab: 5000 + i * 9000 for i, lab in enumerate(LABELS[:4])}


def test_ten_k_path_unchanged_by_the_contract_rule():
    # the same shape under the default grammar name proposes nothing new
    blocks, cands = build(toc_rows(LABELS) + body_rows(LABELS))
    assert detect_toc(blocks, cands, norm_len=80000) == []


def test_member_rescue_keeps_a_section_with_no_body_copy():
    # SECTION 1.01 is typeset in the body in a form the grammar misses: its only
    # copy is the condemned index row, so it must survive as a node rather than be
    # lost with the region.  Its order key still has to fit the accepted chain --
    # a rescued row sits before every body copy, so one whose key is mid-sequence
    # loses the max-weight chain to the body copies it would displace, and the
    # label is genuinely absent from the body.
    keep = LABELS[1:]
    blocks, cands = build(toc_rows(LABELS) + body_rows(keep))
    regions = detect_toc(blocks, cands, norm_len=80000, grammar="contract")
    assert len(regions) == 1
    nodes, _ = build_contract_tree(blocks, cands, regions, G, doc_raw_start=0, doc_raw_end=80000, norm_len=80000)
    secs = {n.label_canon: n for n in nodes if n.level_kind == "section"}
    assert set(secs) == set(LABELS)  # nothing lost
    assert "toc.member_rescued" in secs["SECTION 1.01"].rule_ids
    assert secs["SECTION 1.01"].head_raw_start == 0  # the index row, the only copy there is
    assert all("toc.member_rescued" not in secs[lab].rule_ids for lab in keep)
