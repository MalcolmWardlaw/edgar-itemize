"""Turn 9 B.2 — the cover-page caption guard (`rej.caption_omission` / `_ibr` / `_xref`).

Families 1-3 of `docs/turn8_decisions/b4_out_of_order.md` ("Every new false accept,
read"): 26 of the 39 judged false accepts `seq.out_of_order` introduced are rows of a
Rule 12b-25 omission notice, of a "documents incorporated by reference" table, or of a
filer agent's item-to-page cross-reference table.  Each of those rows says the item is
NOT in this document, so it cannot open one.

The guard's shape and the bank behind it are `docs/turn9_decisions/a4_caption_guard.md`;
the membership test is list/table-scoped, ported from
`scripts/turn9/caption_census.py`.  The last test here is the one the scoping exists for:
the Instruction J follower, the single item a filer does NOT omit, printed after the
omitted-items list — a 6-line-above window catches 11 of 27 sampled followers and the
list-scoped test catches none.
"""

from edgar_itemize.candidates import caption_guard_pass, find_candidates
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import build_tree

G = Form10KGrammar()


def parse(body: str, *, guard: bool = True):
    """Candidates and tree for a text-era 10-K body, in `pipeline.parse_document`'s order."""
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    if guard:
        caption_guard_pass(cands, norm)
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, rejected = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return cands, nodes, rejected


def items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def cand_for(cands, label, at=None):
    hits = [c for c in cands if c.label_canon == label and (at is None or at in c.title or at in c.label_raw)]
    assert hits, f"no candidate for {label}"
    return hits[0]


BODY = """PART I

ITEM 1. BUSINESS

The Company manufactures industrial fasteners at three plants in Ohio and Indiana.

ITEM 2. PROPERTIES

The Company owns a factory in Ohio and leases warehouse space in Indiana.

ITEM 3. LEGAL PROCEEDINGS

The Company is party to no material pending legal proceedings.

PART II

ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY

The common stock trades on the New York Stock Exchange under the symbol FAST.

ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION

Revenues rose twelve percent on higher unit volume across both reporting segments.
"""


# --- family 1: the Rule 12b-25 omission notice ------------------------------------
# 0000950152-01-503037 (items 6, 7, 8) and 0000950152-02-005312 (6, 7): "Pursuant to
# Rule 12b-25(b), this Form 10-K does not include the following: 1. Item 6, Selected
# Financial Data."  The list entries carry the statutory title and no index furniture at
# all, so they pass every one of `out_of_order_items`'s other tests.
OMISSION = """Pursuant to Rule 12b-25(b), this Form 10-K does not include the following:

Item 6. Selected Financial Data

Item 7A. Quantitative and Qualitative Disclosures About Market Risk

""" + BODY


def test_omission_notice_rows_are_not_recovered_out_of_order():
    cands, nodes, rejected = parse(OMISSION)
    for lab in ("ITEM 6", "ITEM 7A"):
        c = cand_for(cands, lab)
        assert "gram.item.title_match" in c.rule_ids  # every other test still passes
        assert "rej.caption_omission" in c.rule_ids
        assert lab not in items(nodes)
        assert [r.reason for r in rejected if r.label_canon == lab] == ["nonmonotone"]
    # without the guard these are exactly the false accepts b4 catalogued
    _, nodes_off, _ = parse(OMISSION, guard=False)
    assert {"ITEM 6", "ITEM 7A"} <= set(items(nodes_off))
    assert all("seq.out_of_order" in items(nodes_off)[lab].rule_ids for lab in ("ITEM 6", "ITEM 7A"))
    # scope: the tag carries no score and the chain's own nodes are untouched
    assert cand_for(cands, "ITEM 6").score == cand_for(parse(OMISSION, guard=False)[0], "ITEM 6").score
    assert set(items(nodes)) == {"ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 7"}


def test_omission_caption_does_not_reach_past_the_list_end():
    """The scope is the list, not the block: a non-item line closes it.

    (The intervening sentence must not itself say "omitted" — the family-1 caption
    pattern would then open a fresh list under it, which is the correct reading of a
    second notice and is what the first draft of this test tripped over.)
    """
    body = OMISSION.replace(
        "Item 7A. Quantitative and Qualitative Disclosures About Market Risk",
        "The registrant expects to file these items by amendment within fifteen days.\n\n"
        "Item 7A. Quantitative and Qualitative Disclosures About Market Risk",
    )
    cands, nodes, _ = parse(body)
    assert "rej.caption_omission" in cand_for(cands, "ITEM 6").rule_ids
    assert "rej.caption_omission" not in cand_for(cands, "ITEM 7A").rule_ids
    assert "ITEM 7A" in items(nodes) and "ITEM 6" not in items(nodes)


# --- family 2: the "documents incorporated by reference" table --------------------
# 0000078778-97-000019 (5, 6, 8), 0000014846-99-000018 (11, 12, 13): a two-column cover
# table whose left cell is the item label and title and whose right cell names the proxy
# statement section.  Normalisation interleaves the columns, so the row reads as a
# heading followed by prose.
IBR = """DOCUMENTS INCORPORATED BY REFERENCE

Item 10. Directors and Executive Officers of the Registrant

Item 11. Executive Compensation

Item 12. Security Ownership of Certain Beneficial Owners and Management

""" + BODY


def test_incorporated_by_reference_table_rows_are_not_recovered_out_of_order():
    cands, nodes, rejected = parse(IBR)
    for lab in ("ITEM 10", "ITEM 11", "ITEM 12"):
        assert "rej.caption_ibr" in cand_for(cands, lab).rule_ids
        assert lab not in items(nodes)
        assert [r.reason for r in rejected if r.label_canon == lab] == ["nonmonotone"]
    _, nodes_off, _ = parse(IBR, guard=False)
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes_off))


def test_ibr_caption_with_no_item_list_under_it_reaches_nothing():
    """Almost every 10-K cover page carries this caption; only a list under it is a table."""
    body = ("DOCUMENTS INCORPORATED BY REFERENCE\n\n"
            "Portions of the registrant's definitive proxy statement are incorporated by\n"
            "reference into Part III of this Annual Report on Form 10-K.\n\n"
            "Item 11. Executive Compensation\n\n") + BODY
    cands, nodes, _ = parse(body)
    assert "rej.caption_ibr" not in cand_for(cands, "ITEM 11").rule_ids
    assert "ITEM 11" in items(nodes)


# --- family 3: the filer agent's item-to-page cross-reference table ---------------
# 0000892569-* (seven filings), 0000950137-06-013769, 0001095811-00-005447: "ITEM 8:
# FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA | SEE NOTE (B) BELOW", an all-caps
# two-column map from items to annual-report pages.
XREF_TABLE = """CROSS-REFERENCE SHEET

Item 8. Financial Statements and Supplementary Data

Item 9. Changes in and Disagreements with Accountants on Accounting

""" + BODY


def test_cross_reference_table_rows_are_not_recovered_out_of_order():
    cands, nodes, rejected = parse(XREF_TABLE)
    for lab in ("ITEM 8", "ITEM 9"):
        assert "rej.caption_xref" in cand_for(cands, lab).rule_ids
        assert lab not in items(nodes)
        assert [r.reason for r in rejected if r.label_canon == lab] == ["nonmonotone"]
    _, nodes_off, _ = parse(XREF_TABLE, guard=False)
    assert {"ITEM 8", "ITEM 9"} <= set(items(nodes_off))


def test_same_line_page_pointer_needs_no_caption():
    """0000852807-94-000004 ITEM 11: "ITEM 11. Executive Compensation  Page 5 under
    caption ...".  Sub-test (b) reads only the candidate's own line, from the label
    forward, so it needs no scoping at all (a4_caption_guard.md section 2)."""
    body = ("Item 11. Executive Compensation  Page 5 under the caption Executive Compensation\n\n") + BODY
    cands, nodes, _ = parse(body)
    c = cand_for(cands, "ITEM 11")
    assert "rej.caption_xref" in c.rule_ids and "ITEM 11" not in items(nodes)
    assert "ITEM 11" in items(parse(body, guard=False)[1])
    # the same row without the pointer is left alone
    plain = body.replace("  Page 5 under the caption Executive Compensation", "")
    assert "rej.caption_xref" not in cand_for(parse(plain)[0], "ITEM 11").rule_ids
    assert "ITEM 11" in items(parse(plain)[1])


# --- Instruction J: the follower the list-end test exists to protect --------------
# The asset-backed filer's "PART I -- The following Items have been omitted in accordance
# with General Instruction J to Form 10-K: Item 1. Business ... Item 3. Legal
# Proceedings", followed by the one item that is NOT omitted.  26 of the 29 real body
# headings in b4's post-gate hand read are this pattern; a 6-line-above window catches 11
# of 27 sampled followers and would reject them (a4_caption_guard.md section 4).
INSTRUCTION_J = """PART I

The following Items have been omitted in accordance with General Instruction J to Form 10-K:

Item 1. Business

Item 2. Properties

Item 3. Legal Proceedings

Reference is made to the Prospectus Supplement dated March 1 filed with the Commission.

Item 1B. Unresolved Staff Comments

Nothing to report.

PART II

Item 5. Market for Registrant's Common Equity

There is no established public trading market for the certificates.

Item 7. Management's Discussion and Analysis of Financial Condition

The pool balance declined in line with the servicer's collection report.
"""


def test_instruction_j_follower_is_never_tagged_and_stays_accepted():
    cands, nodes, _ = parse(INSTRUCTION_J)
    follower = cand_for(cands, "ITEM 1B")
    assert not any(r.startswith("rej.caption_") for r in follower.rule_ids)
    it = items(nodes)
    assert "ITEM 1B" in it and "seq.out_of_order" in it["ITEM 1B"].rule_ids
    # the omitted list above it IS tagged, and the guard's arrival changes nothing else
    assert "rej.caption_omission" in cand_for(cands, "ITEM 2").rule_ids
    assert set(it) == set(items(parse(INSTRUCTION_J, guard=False)[1]))


def test_instruction_j_follower_survives_a_part_heading_list_end():
    """One of the 27 sampled followers' lists is closed by a Part heading, not a
    non-item line; the stop rule has both."""
    body = INSTRUCTION_J.replace(
        "Reference is made to the Prospectus Supplement dated March 1 filed with the Commission.\n\n"
        "Item 1B. Unresolved Staff Comments",
        "PART I-A\n\nItem 1B. Unresolved Staff Comments",
    )
    cands, nodes = parse(body)[:2]
    assert not any(r.startswith("rej.caption_") for r in cand_for(cands, "ITEM 1B").rule_ids)
    assert "ITEM 1B" in items(nodes)


# The shape the corpus actually prints (0000929638-21-000484, 0001104659-18-020339 and
# 257 more in B.2's 1,500-filing pre-gate probe): the item the filer does NOT omit is
# typeset INSIDE the run of item lines, not after it.  The list end never separates it,
# so the enumeration's ascending order has to: Item 1B sits below Item 3 statutorily, and
# that break is both what makes it a real heading and why the monotone chain dropped it.
INSTRUCTION_J_INLINE = """PART I

The following Items have been omitted in accordance with General Instruction J to Form 10-K:

Item 1. Business.

Item 1A. Risk Factors.

Item 2. Properties.

Item 3. Legal Proceedings.

Item 1B. Unresolved Staff Comments.

None.

Item 4. Mine Safety Disclosures.

Not applicable.

PART II

Item 5. Market for Registrant's Common Equity

There is no established public trading market for the certificates.

Item 7. Management's Discussion and Analysis of Financial Condition

The pool balance declined in line with the servicer's collection report.
"""


def test_instruction_j_item_inside_the_list_run_is_not_a_list_entry():
    cands, nodes, _ = parse(INSTRUCTION_J_INLINE)
    follower = cand_for(cands, "ITEM 1B")
    assert not any(r.startswith("rej.caption_") for r in follower.rule_ids)
    it = items(nodes)
    assert "ITEM 1B" in it and "seq.out_of_order" in it["ITEM 1B"].rule_ids
    # the entries above and below it in the enumeration are still list members
    for lab in ("ITEM 1", "ITEM 1A", "ITEM 2", "ITEM 3"):
        assert "rej.caption_omission" in cand_for(cands, lab).rule_ids
    assert set(it) == set(items(parse(INSTRUCTION_J_INLINE, guard=False)[1]))


def test_enumeration_resumes_after_the_out_of_order_entry():
    """The order break excludes one entry; it does not end the list."""
    body = INSTRUCTION_J_INLINE.replace("None.\n\nItem 4.", "Item 4.")
    cands = parse(body)[0]
    assert "rej.caption_omission" not in cand_for(cands, "ITEM 1B").rule_ids
    assert "rej.caption_omission" in cand_for(cands, "ITEM 4").rule_ids
