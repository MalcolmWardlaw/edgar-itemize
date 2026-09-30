"""Turn 13 B.1 -- Regulation AB items as their own PART V run.

Spec: docs/turn13_decisions/a6a_regab.md. The candidate half is Turn 12 B.4's withdrawn
`_REGAB_RE` level (docs/turn12_decisions/b4_labels_build.md); this build adds R-B4-4 (the
`(b)(2)` double sub-item), R-B4-1 (the seven Reg-AB titles in `_ITEM_TITLE_RE` behind an
optional "of Regulation AB" lead-in) and R-B4-3 (`seq.regab_run`: the Reg-AB items are
chained apart from the ordinary items and reunited before strong_duplicate_pass).
"""

from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import ITEM_RE, _REGAB_RE, Form10KGrammar
from edgar_itemize.grammar.form10q import Form10QGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import build_tree

G = Form10KGrammar()


def parse(body: str):
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, rejected = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return cands, nodes, rejected


def canon(line: str) -> str | None:
    m = _REGAB_RE.match(line)
    return G.canonicalize("item", m) if m else None


# --- candidate generation -----------------------------------------------------------


def test_regab_label_canonicalizes_only_the_seven_hand_read_numbers():
    assert canon("Item 1117 of Regulation AB, Legal Proceedings.") == "ITEM 1117"
    assert canon("ITEM 1123. SERVICER COMPLIANCE STATEMENT") == "ITEM 1123"
    assert canon("Item 1121. Distribution and Pool Performance Information") is None
    assert _REGAB_RE.match("Item 1124. Something") is None  # outside 1100-1123


def test_double_sub_item_tokenises_r_b4_4():
    # turn12-b4-10k-losses.txt (8e): 10,225 census rows open "(x)(n)"; the Turn 12
    # pattern refused them outright.
    assert canon("Item 1114(b)(2) of Regulation AB, Credit Enhancement") == "ITEM 1114"
    m = _REGAB_RE.match("ITEM 1114(B)(2).  SIGNIFICANT ENHANCEMENT PROVIDER INFORMATION.")
    assert m and m.group("psuf") == "B" and m.group("title").startswith("SIGNIFICANT")
    # a three-level cite is still not a heading label
    assert _REGAB_RE.match("Item 1122(d)(2)(iii) of Regulation AB in connection") is None


def test_ordinary_item_re_still_never_reaches_four_digits():
    assert ITEM_RE.match("ITEM 1112. Significant Obligor(s) of Pool Assets") is None


def test_label_rule_and_part_v():
    m = _REGAB_RE.match("Item 1119. Affiliations and Certain Relationships")
    assert G.label_rules("item", m) == ["gram.item.regab"]
    assert G.part_of_item("ITEM 1119", has_item_15=True) == "PART V"
    assert G.order_key("part", "PART V") == 5
    assert G.is_regab_item("ITEM 1119") and not G.is_regab_item("ITEM 15")


# --- R-B4-1: title evidence -----------------------------------------------------------


def test_title_match_with_and_without_the_lead_in():
    assert G.title_matches("item", "ITEM 1117", "of Regulation AB, Legal Proceedings.")
    assert G.title_matches("item", "ITEM 1117", "Legal Proceedings")
    assert G.title_matches("item", "ITEM 1114", "of Regulation AB, Significant Enhancement")  # wrapped line
    assert G.title_matches("item", "ITEM 1122", "of Regulation AB, Compliance with Applicable")
    assert G.title_matches("item", "ITEM 1117", "of Regulation AB. (Legal Proceedings)")


def test_exhibit_index_row_gets_no_title_evidence_r_b4_2():
    # 0001193125-12-143030 @44984, past the old back_start (turn12-b4-10k-losses.txt (4))
    assert not G.title_matches("item", "ITEM 1122",
                               "Report on Assessment of Compliance with Applicable Servicing Criteria 33.1")
    assert not G.title_matches("item", "ITEM 1117", "of Regulation AB")


def test_form10q_has_no_regab_hook():
    assert getattr(Form10QGrammar(), "is_regab_item", None) is None


# --- R-B4-3: the second run -------------------------------------------------------------

# The dominant layout (7,520 of 9,169, turn12-b4-10k-regab-layout.parquet): the Reg-AB
# block printed before Part IV. In one chain, ITEM 15 (key 150) after ITEM 1123 (11230)
# lost as `nonmonotone`, or the Reg-AB block lost to it.
BODY_BEFORE = """PART I

ITEM 1. BUSINESS

Omitted.

ITEM 2. PROPERTIES

Omitted.

ITEM 3. LEGAL PROCEEDINGS

Omitted.

PART II

ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY

Omitted.

PART III

ITEM 10. DIRECTORS AND EXECUTIVE OFFICERS

Omitted.

SUBSTITUTE INFORMATION PROVIDED IN ACCORDANCE WITH GENERAL INSTRUCTION J TO FORM 10-K

ITEM 1112(B). SIGNIFICANT OBLIGOR FINANCIAL INFORMATION

No single obligor represents 10% or more of the pool assets.

ITEM 1114(B)(2). CREDIT ENHANCEMENT AND OTHER SUPPORT

No entity provides credit enhancement for 10% or more of the pool.

ITEM 1117. LEGAL PROCEEDINGS

None.

ITEM 1119. AFFILIATIONS AND CERTAIN RELATIONSHIPS AND RELATED TRANSACTIONS

None.

ITEM 1122. COMPLIANCE WITH APPLICABLE SERVICING CRITERIA

See Exhibit 33.

ITEM 1123. SERVICER COMPLIANCE STATEMENT

See Exhibit 35.

PART IV

ITEM 15. EXHIBITS AND FINANCIAL STATEMENT SCHEDULES

(a) Exhibits are listed in the exhibit index.
"""


def _items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def test_regab_block_before_part_iv_keeps_both_runs():
    cands, nodes, rejected = parse(BODY_BEFORE)
    items = _items(nodes)
    for lab in ("ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 10", "ITEM 15",
                "ITEM 1112", "ITEM 1114", "ITEM 1117", "ITEM 1119", "ITEM 1122", "ITEM 1123"):
        assert lab in items, lab
    assert "seq.out_of_order" not in items["ITEM 15"].rule_ids
    parts = {n.node_id: n.label_canon for n in nodes if n.level_kind == "part"}
    assert parts[items["ITEM 1114"].parent_id] == "PART V"
    assert parts[items["ITEM 15"].parent_id] == "PART IV"
    assert "gram.item.regab" in items["ITEM 1114"].rule_ids


def test_regab_run_is_itself_monotone():
    # a stray earlier-numbered Reg-AB mention after the block competes only inside the
    # Reg-AB run, never with the ordinary items
    body = BODY_BEFORE.replace("PART IV\n", "ITEM 1117. LEGAL PROCEEDINGS\n\nRepeated.\n\nPART IV\n")
    cands, nodes, rejected = parse(body)
    items = _items(nodes)
    assert "ITEM 15" in items and "ITEM 1123" in items
    assert sum(1 for n in nodes if n.label_canon == "ITEM 1117") == 1


def test_plain_10k_tree_unchanged_by_the_hook():
    body = """PART I

ITEM 1. BUSINESS

We make widgets.

ITEM 2. PROPERTIES

A plant.

PART IV

ITEM 15. EXHIBITS AND FINANCIAL STATEMENT SCHEDULES

See index.
"""
    _, nodes, _ = parse(body)
    assert [n.label_canon for n in nodes if n.level_kind in ("part", "item")] == [
        "PART I", "ITEM 1", "ITEM 2", "PART IV", "ITEM 15"]
