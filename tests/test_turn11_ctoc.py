"""Turn 11 B.1/B.2 — the contract table-of-contents boundary.

`docs/turn11_decisions/a1_ctoc_boundary.md`.  `toc._contract_regions` both under-reaches
and over-reaches, and Turn 10 named live instances on each side: 137 index rows
`runs/full_ex10_v19` still accepts as sections, and 19 real body headings the parser
condemns as `toc`.  The two defects are mirror images, so the two rules are built together
and gated on one diff:

  rej.ctoc_row        an index row in a run of >= `_CTOC_MIN_RUN` such rows, followed by no
                      prose, whose OWN second column is a page pointer, where
                      `_contract_regions` proposed nothing.
  ctoc.region_relief  a member of a proposed region that is not an index row, is followed
                      by prose within one block, and carries no page or leader evidence.

Each test below is one clause: the four row arms (`cell`, `gap`, `wrap`, and the
title-only cell that is NOT condemned), the run-length floor, the member-level page-pointer
clause, the two `_fm_prose_cell` shapes of a body section typeset in two cells, the
per-member prose guard, the relief and the index rows it must not relieve, the
`image_text` scope exclusion, the disjointness of the two predicates, and the promise that
`detect_toc` and every score are untouched.
"""

from edgar_itemize.candidates import (
    _CTOC_MIN_RUN,
    _ctoc_row_test,
    _fm_kind2,
    ctoc_row_pass,
    find_candidates,
)
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.normalize_html import html_to_blocks
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree_contract import build_contract_tree

G = ContractGrammar()


def parse(body: str, *, era: str = "text", rules: bool = True, scope_era: str | None = None):
    """Candidates, regions and tree for a synthetic contract, in `parse_document`'s order.

    `rules=False` runs the Turn 10 pipeline (the pass never called), which is how each test
    shows what the rule itself does.  `scope_era` overrides the era handed to the pass only,
    so the `image_text` scope clause can be tested on a document whose blocks are readable.
    """
    blocks, norm, _ = text_to_blocks(body, 0) if era == "text" else html_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era=era)
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="contract")
    if rules:
        ctoc_row_pass(cands, blocks, toc, norm, era=scope_era or era)
    nodes, rejected = build_contract_tree(blocks, cands, toc, G, doc_raw_start=0,
                                          doc_raw_end=len(body), norm_len=len(norm))
    return dict(blocks=blocks, cands=cands, toc=toc, nodes=nodes, rejected=rejected)


def condemned(r) -> set[str]:
    return {c.label_canon for c in r["cands"] if "rej.ctoc_row" in c.rule_ids}


def relieved(r) -> set[str]:
    return {c.label_canon for c in r["cands"] if "ctoc.region_relief" in c.rule_ids}


def sections(r) -> dict[str, int]:
    return {n.label_canon: n.head_raw_start for n in r["nodes"] if n.level_kind == "section"}


def rejected_toc(r) -> set[str]:
    return {x.label_canon for x in r["rejected"] if x.reason == "toc"}


# --- the body every fixture ends with: an article heading, then the agreement ----------
ARTICLE_AND_PROSE = (
    "ARTICLE I\n\nDEFINITIONS\n\n"
    "The Borrower shall from time to time deliver to the Administrative Agent such\n"
    "certificates and other documents as the Lenders may reasonably request in connection\n"
    "with the transactions contemplated hereby and shall pay the fees of its counsel.\n\n"
)
HTML_ARTICLE_AND_PROSE = (
    "<p>ARTICLE I</p><p>DEFINITIONS</p>"
    "<p>The Borrower shall from time to time deliver to the Administrative Agent such "
    "certificates and other documents as the Lenders may reasonably request in connection "
    "with the transactions contemplated hereby and shall pay the fees of its counsel.</p>"
)

# --- B.1 row shape 1: the text era's column gap ----------------------------------------
# "SECTION 1.01.  Certain Defined Terms                      1" (0000891092-05-001193 seq 2
# carries 17 of the 137 in this shape).  `_contract_regions` proposes nothing over it: the
# run's labels are not re-found later as candidates at all, so its dup share is far below
# `_CTOC_MIN_DUP` (memo section 2; census median 0.00, p90 0.09).
GAP_INDEX = (
    "SECTION 1.01.  Certain Defined Terms                                     1\n\n"
    "SECTION 1.02.  Other Interpretive Provisions                             5\n\n"
    "SECTION 1.03.  Accounting Terms                                          7\n\n"
    "SECTION 2.01.  The Commitments                                           9\n\n"
) + ARTICLE_AND_PROSE


def test_text_era_gap_index_run_is_condemned():
    r = parse(GAP_INDEX)
    assert r["toc"] == []  # the existing detector proposes nothing — this is the 137's shape
    assert condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01"}
    assert all("ctoc.row_gap" in c.rule_ids for c in r["cands"] if c.kind == "section")
    assert rejected_toc(r) == condemned(r) and sections(r) == {}
    off = parse(GAP_INDEX, rules=False)
    assert set(sections(off)) == condemned(r)  # every one of them was accepted before


# --- B.1 row shape 2: the HTML table cell ----------------------------------------------
# "Section 11.23" | "USA Patriot Act" | "101" (0001481832-12-000024 seq 2 carries 76 of the
# 137 in this shape).  The first cell holds the label ALONE, which is why `_fm_row_test`'s
# `_fm_first_col_carries_label_and_title` guard cannot be reused here (memo section 4).
CELL_INDEX = (
    "<table>"
    "<tr><td>Section 11.21</td><td>Waiver of Jury Trial</td><td>101</td></tr>"
    "<tr><td>Section 11.22</td><td>Confidentiality</td><td>101</td></tr>"
    "<tr><td>Section 11.23</td><td>USA Patriot Act</td><td>102</td></tr>"
    "<tr><td>Section 11.24</td><td>Governing Law</td><td>103</td></tr>"
    "</table>"
) + HTML_ARTICLE_AND_PROSE


def test_html_cell_index_run_is_condemned():
    r = parse(CELL_INDEX, era="html_generic")
    assert condemned(r) == {"SECTION 11.21", "SECTION 11.22", "SECTION 11.23", "SECTION 11.24"}
    assert all("ctoc.row_cell" in c.rule_ids for c in r["cands"] if c.kind == "section")
    assert rejected_toc(r) == condemned(r)
    off = parse(CELL_INDEX, era="html_generic", rules=False)
    assert set(sections(off)) == condemned(r)


# --- B.1 row shape 3: the wrapped title ------------------------------------------------
# "SECTION 2.03. Issuance of and Drawings and Reimbursement" / "Under Letters of Credit  14":
# the label line carries no gap and the continuation line does.  `_fm_row_test`'s
# `interleave` arm requires a gap on the label line first and so cannot see it.
WRAP_INDEX = (
    "SECTION 2.01.  The Commitments                                           9\n\n"
    "SECTION 2.02.  Making the Loans                                         11\n\n"
    "SECTION 2.03.  Issuance of and Drawings and Reimbursement\n"
    "               Under Letters of Credit                                  14\n\n"
    "SECTION 2.04.  Repayment of Loans                                       17\n\n"
) + ARTICLE_AND_PROSE


def test_wrapped_index_row_is_condemned_by_the_wrap_arm():
    r = parse(WRAP_INDEX)
    assert condemned(r) == {"SECTION 2.01", "SECTION 2.02", "SECTION 2.03", "SECTION 2.04"}
    wrapped = next(c for c in r["cands"] if c.label_canon == "SECTION 2.03")
    assert "ctoc.row_wrap" in wrapped.rule_ids


# --- the run-length floor ---------------------------------------------------------------
def test_run_shorter_than_the_floor_is_not_condemned():
    """`_CTOC_MIN_RUN = 3` (memo section 4): it costs nothing on the 137 and drops 3,717
    census rows relative to 2.  Two rows are not a table of contents."""
    two = (
        "<table>"
        "<tr><td>Section 5.1</td><td>Indemnification</td><td>51</td></tr>"
        "<tr><td>Section 5.2</td><td>Confidentiality</td><td>52</td></tr>"
        "</table>"
    ) + HTML_ARTICLE_AND_PROSE
    r = parse(two, era="html_generic")
    assert _CTOC_MIN_RUN == 3
    assert all(_ctoc_row_test(r["blocks"], c) is not None
               for c in r["cands"] if c.kind == "section")  # both ARE index rows
    assert condemned(r) == set()  # the run is one short


# --- the member-level page-pointer clause ------------------------------------------------
def test_a_second_column_that_is_not_a_page_pointer_is_not_condemned():
    """The amendment of memo section 7e, and the whole difference between 0.780 and 1.000
    precision on the `flagged_accepted` stratum: the evidence test `rej.fm_table` applies to
    the RUN is applied here to the MEMBER.  A leaderless index whose second cell is a bare
    title is released — those rows are the Turn 12 lead of memo section 8c, not this rule's."""
    titles = (
        "<table>"
        "<tr><td>Section 5.1</td><td>Indemnification</td></tr>"
        "<tr><td>Section 5.2</td><td>Confidentiality</td></tr>"
        "<tr><td>Section 5.3</td><td>Governing Law</td></tr>"
        "<tr><td>Section 5.4</td><td>Counterparts</td></tr>"
        "</table>"
    ) + HTML_ARTICLE_AND_PROSE
    r = parse(titles, era="html_generic")
    assert all(_ctoc_row_test(r["blocks"], c)[0] == "cell"
               for c in r["cands"] if c.kind == "section")
    assert condemned(r) == set()
    assert set(sections(r)) == {"SECTION 5.01", "SECTION 5.02", "SECTION 5.03", "SECTION 5.04"}


# --- a body section whose second cell is its own operative text ---------------------------
OPERATIVE = ("Each Obligor shall ensure that at all times the aggregate amount of its Financial "
             "Indebtedness does not exceed the limit set out in the schedule and shall promptly "
             "notify the Agent of any breach of this covenant.")
OPERATIVE_DOC = ("Each Obligor shall ensure that the documents listed in Exhibit A are delivered "
                 "to the Agent within five Business Days and shall promptly notify the Agent of "
                 "any breach of the covenant described in Part I.")


def _uk_table(cell: str) -> str:
    return ("<table>"
            + "".join(f"<tr><td>4.1{i}</td><td>{cell}</td></tr>" for i in range(4, 8))
            + "</table>") + HTML_ARTICLE_AND_PROSE


def test_operative_text_in_the_next_cell_is_not_an_index_row():
    """The UK/European shape in which the section NUMBER sits alone in one cell and the
    TITLE AND OPERATIVE TEXT sit in the next: every one of the 22 `flagged_accepted` misses
    of the first bank (memo section 7d).  Two guards stop it, and the tests take one each:
    `_fm_prose_cell` when the cell is plain prose, and the member-level page clause when the
    cell names an Exhibit or a Part and `_fm_kind2` therefore calls it `document`."""
    plain = parse(_uk_table(OPERATIVE), era="html_generic")
    assert all(_ctoc_row_test(plain["blocks"], c) is None
               for c in plain["cands"] if c.kind == "section")
    assert condemned(plain) == set()
    assert len(sections(plain)) == 4

    doc = parse(_uk_table(OPERATIVE_DOC), era="html_generic")
    assert all(_ctoc_row_test(doc["blocks"], c)[0] == "cell"
               for c in doc["cands"] if c.kind == "section")  # the guard does NOT fire here
    assert condemned(doc) == set()  # ... and the member's cell is not a page pointer
    assert len(sections(doc)) == 4


# --- the per-member prose guard -----------------------------------------------------------
def test_a_row_followed_by_its_own_body_is_not_condemned():
    """Memo section 4 clause 6: 0 of the 137 are followed by prose within one block, and a
    row that is is a heading that happens to sit beside a number."""
    body = (
        "SECTION 1.01.  Certain Defined Terms                                     1\n\n"
        "SECTION 1.02.  Other Interpretive Provisions                             5\n\n"
        "SECTION 1.03.  Accounting Terms                                          7\n\n"
        "SECTION 2.01.  The Commitments                                           9\n\n"
        "The Borrower shall from time to time deliver to the Administrative Agent such\n"
        "certificates and other documents as the Lenders may reasonably request in\n"
        "connection with the transactions contemplated hereby and shall pay all fees.\n\n"
    )
    r = parse(body)
    assert condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03"}
    assert "SECTION 2.01" in sections(r)


# --- B.2: the region relief ---------------------------------------------------------------
# A leaderless contract index `_contract_regions` condemns (`toc.dup_later_contract`), whose
# FIRST member is not an index row at all but the agreement's real SECTION 1.01 heading
# running straight into its own prose — the shape of the 19 (memo section 5).  Its label is
# re-found later in a schedule, so the existing `toc.member_rescued` test cannot save it:
# that rescue fires only for a label with no live copy outside the region.
_EOD = ("Any one or more of the following events shall constitute an Event of Default and the "
        "Administrative Agent may declare the unpaid principal amount of all outstanding Loans "
        "immediately due and payable, whereupon the same shall become forthwith due and payable "
        "without presentment or demand of any kind.")
_TITLES = {"1.02": "Other Interpretive Provisions", "1.03": "Accounting Terms",
           "1.04": "Remedies", "1.05": "Notices", "2.01": "The Commitments",
           "2.02": "Making the Loans", "2.03": "Repayment of Loans"}
_FILL = ("Filler paragraph with enough ordinary words to separate the sections of the body "
         "from one another and keep the blocks apart in the normalized stream.\n\n")
REGION_DOC = (
    f"SECTION 1.01. Events of Default.  {_EOD}\n\n"
    + "".join(f"SECTION {lab}. {t}\n\n" for lab, t in _TITLES.items())
    + "ARTICLE I\n\nDEFINITIONS\n\n"
    + "".join(f"SECTION {lab}. {t}.  The Borrower shall from time to time deliver to the "
              "Administrative Agent such certificates and other documents as the Lenders may "
              "reasonably request in connection with the transactions hereby contemplated.\n\n"
              + _FILL * 3 for lab, t in _TITLES.items())
    + "SCHEDULE 1\n\n" + _FILL * 2 + "SECTION 1.01 Events of Default\n\n" + _FILL * 2
)


def test_region_relief_restores_a_body_heading_the_region_swallowed():
    off = parse(REGION_DOC, rules=False)
    assert [t.reason for t in off["toc"]] == ["toc.dup_later_contract"]
    assert "SECTION 1.01" in rejected_toc(off)  # condemned with the index it sits in
    assert "SECTION 1.01" not in sections(off)

    on = parse(REGION_DOC)
    assert relieved(on) == {"SECTION 1.01"}
    assert "SECTION 1.01" not in rejected_toc(on)
    assert sections(on)["SECTION 1.01"] == 0
    node = next(n for n in on["nodes"] if n.label_canon == "SECTION 1.01")
    assert "ctoc.region_relief" in node.rule_ids


def test_the_index_rows_of_the_same_region_are_not_relieved():
    """Clause 4 and clause 2 together: the other seven members are index rows followed by
    another index row, and they stay condemned.  The relief cannot empty a region."""
    on = parse(REGION_DOC)
    assert rejected_toc(on) == {f"SECTION {lab}" for lab in _TITLES}
    assert relieved(on) & rejected_toc(on) == set()


def test_the_relief_does_not_move_the_regions_or_any_score():
    """The relief is applied at `build_contract_tree`'s per-member rescue point and
    `toc.py` is untouched, so every region and every candidate score is bit-identical with
    the pass on and off — which is what keeps the 10-K, 10-Q and EX-13 paths out of it."""
    for doc, era in ((REGION_DOC, "text"), (GAP_INDEX, "text"), (CELL_INDEX, "html_generic")):
        on, off = parse(doc, era=era), parse(doc, era=era, rules=False)
        assert [(t.first_cand, t.last_cand, t.reason) for t in on["toc"]] == \
               [(t.first_cand, t.last_cand, t.reason) for t in off["toc"]]
        assert [c.score for c in on["cands"]] == [c.score for c in off["cands"]]
        assert [c.head_raw_start for c in on["cands"]] == [c.head_raw_start for c in off["cands"]]


# --- scope: image_text ---------------------------------------------------------------------
def test_image_text_is_out_of_scope_for_both_rules():
    """The `image_text` profile recovers structure from a hidden OCR layer and emits one
    undifferentiated block, so there is no table structure for the row test to read and
    "prose follows within one block" is vacuously true (census section 3b; Sonnet's verdict
    on the bank's 9 `image_text` windows went the flag's way 0 of 9)."""
    for doc, era in ((GAP_INDEX, "text"), (CELL_INDEX, "html_generic"), (REGION_DOC, "text")):
        r = parse(doc, era=era, scope_era="image_text")
        assert condemned(r) == set() and relieved(r) == set()


# --- the disjointness the build spec requires ----------------------------------------------
def test_the_two_rules_are_disjoint():
    """Memo section 10.3.  Mechanically true three times over — B.1 fires only where no
    region covers the row and B.2 only inside one; B.2 requires the row test to FAIL and B.1
    requires it to pass; B.1 requires no prose within one block and B.2 requires prose — and
    asserted here so no later edit can make the two rules fight over one candidate."""
    for doc, era in ((GAP_INDEX, "text"), (WRAP_INDEX, "text"), (CELL_INDEX, "html_generic"),
                     (REGION_DOC, "text"), (_uk_table(OPERATIVE_DOC), "html_generic")):
        r = parse(doc, era=era)
        assert condemned(r) & relieved(r) == set()
        for c in r["cands"]:
            assert not ("rej.ctoc_row" in c.rule_ids and "ctoc.region_relief" in c.rule_ids)


# ===========================================================================================
# Turn 12 B.1 / C1 — the leader arm (docs/turn12_decisions/a1_index_anchor.md sections 4b,
# 5 "C1", 9.2).  The four column arms all split on a whitespace gap, so a text-era index row
# whose column separator IS the leader reads as no row at all: 504 of 504 alarm candidates in
# runs/judge/turn12-leadctr-vetoprobe.txt come out `row_test=None`.  The arm splits on the
# leader run instead and inherits every one of B.1's guards unchanged.
# ===========================================================================================
LEADER_INDEX = (
    "SECTION 1.01. Certain Defined Terms.......................................   1\n\n"
    "SECTION 1.02. Other Interpretive Provisions...............................   5\n\n"
    "SECTION 1.03. Accounting Terms............................................   7\n\n"
    "SECTION 2.01. The Commitments.............................................   9\n\n"
) + ARTICLE_AND_PROSE


def test_dot_leader_index_run_is_condemned_by_the_leader_arm():
    r = parse(LEADER_INDEX)
    assert r["toc"] == []  # as in the 137's shape, the detector proposes nothing
    assert condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01"}
    assert all("ctoc.row_leader" in c.rule_ids for c in r["cands"] if c.kind == "section")
    assert rejected_toc(r) == condemned(r) and sections(r) == {}
    off = parse(LEADER_INDEX, rules=False)
    assert set(sections(off)) == condemned(r)  # every one of them was accepted before


def test_the_underscore_leader_is_the_same_row():
    """`_{3,}` is the other separator the arm splits on (the rule-ruled index)."""
    doc = LEADER_INDEX.replace(".......................................", "_______________________________")
    doc = doc.replace("...............................", "_______________________").replace(
        "............................", "____________________")
    r = parse(doc)
    assert len(condemned(r)) >= _CTOC_MIN_RUN


# The known counterexample, 0000072020-03-000021 seq 3: a document whose BODY headings are
# typeset with leader dots ("Section 8.3.....Bankruptcy Defaults. When any Event of Default
# ...").  The arm reads the row, and then the guards it inherits refuse it: the second column
# is the section's own prose, not a page cell (`_fm_kind2 != "page"`), and prose follows
# within one block.  13 of the 26 `ctr_alarm` heading windows are this shape and C1 condemns
# none of them (runs/judge/turn12-a1-bank-headings-rulings.txt).
LEADER_BODY = (
    "SECTION 8.01.....Notices, Etc. All notices and other communications provided for\n"
    "hereunder shall be in writing and shall be delivered by hand or sent by certified\n"
    "mail to the address of the party set forth on the signature pages hereof.\n\n"
    "SECTION 8.02.....Amendments, Etc. No amendment or waiver of any provision of this\n"
    "Agreement shall be effective unless the same shall be in writing and signed by the\n"
    "Administrative Agent and the Required Lenders and the Borrower.\n\n"
    "SECTION 8.03.....Governing Law. This Agreement shall be governed by, and construed\n"
    "in accordance with, the laws of the State of New York without regard to the conflict\n"
    "of laws principles of that or any other jurisdiction.\n\n"
) + ARTICLE_AND_PROSE


def test_a_leader_typeset_body_heading_followed_by_prose_is_not_condemned():
    r = parse(LEADER_BODY)
    assert condemned(r) == set()
    assert set(sections(r)) == {"SECTION 8.01", "SECTION 8.02", "SECTION 8.03"}
    assert sections(r) == sections(parse(LEADER_BODY, rules=False))


def test_the_leader_arm_is_reached_only_after_the_four_column_arms():
    """Spec 9.2: the fifth arm fires only where `cell` / `gap` / `interleave` / `wrap`
    return None, so no row that already had a column answer can change its `row_kind`."""
    for doc, era in ((GAP_INDEX, "text"), (CELL_INDEX, "html_generic"), (WRAP_INDEX, "text")):
        r = parse(doc, era=era)
        for c in r["cands"]:
            assert not any(rid.startswith("ctoc.row_leader") for rid in c.rule_ids)


def test_the_leader_arm_keeps_the_two_rules_disjoint_and_moves_no_region_or_score():
    for doc in (LEADER_INDEX, LEADER_BODY):
        on, off = parse(doc), parse(doc, rules=False)
        assert condemned(on) & relieved(on) == set()
        assert [(t.first_cand, t.last_cand, t.reason) for t in on["toc"]] == \
               [(t.first_cand, t.last_cand, t.reason) for t in off["toc"]]
        assert [c.score for c in on["cands"]] == [c.score for c in off["cands"]]


def test_image_text_is_out_of_scope_for_the_leader_arm_too():
    assert condemned(parse(LEADER_INDEX, scope_era="image_text")) == set()


# ===========================================================================================
# Turn 12 B.1 / C1 -- the leader arm (docs/turn12_decisions/a1_index_anchor.md sections 4b,
# 5 "C1", 9.2).  The four column arms all split on a WHITESPACE gap, so a text-era index row
# whose column separator IS the leader reads as no row at all: 504 of 504 alarm candidates in
# runs/judge/turn12-leadctr-vetoprobe.txt come out `row_test=None`, `toc.veto_chain_completing`
# drops their region, and Turn 11's B.1 therefore never reaches them.  The arm splits on the
# leader run instead and inherits every one of B.1's guards unchanged.
# ===========================================================================================
# Two rows with no leader and no body copy are what makes `veto_chain_completing` drop the
# region, which is the state the whole defect lives in: the rows are tagged
# `toc.vetoed_region`, no region covers them, and B.1 is free to test them.
LEADER_INDEX_ROWS = (
    "SECTION 1.01. Certain Defined Terms.........................................1\n\n"
    "SECTION 1.02. Other Interpretive Provisions.................................5\n\n"
    "SECTION 1.03. Accounting Terms..............................................7\n\n"
    "SECTION 2.01. The Commitments...............................................9\n\n"
    "SECTION 9.01. Notices and Other Communications\n\n"
    "SECTION 9.02. Successors and Assigns\n\n"
)
LEADER_INDEX = LEADER_INDEX_ROWS + ARTICLE_AND_PROSE

# The known counterexample, `0000072020-03-000021` seq 3: a document whose BODY headings are
# typeset with leader dots ("Section 8.3.....Bankruptcy Defaults. When any Event of Default
# ...").  13 of the 26 `ctr_alarm` heading windows are this shape and C1 condemns none of them
# (runs/judge/turn12-a1-bank-headings-rulings.txt).
LEADER_BODY = (
    "SECTION 8.01. Notices, Etc......All notices and other communications provided for\n"
    "hereunder shall be in writing and shall be delivered by hand or sent by certified\n"
    "mail to the address of the party set forth on the signature pages hereof.\n\n"
    "SECTION 8.02. Amendments, Etc......No amendment or waiver of any provision of this\n"
    "Agreement shall be effective unless the same shall be in writing and signed by the\n"
    "Administrative Agent and the Required Lenders and the Borrower of record.\n\n"
    "SECTION 8.03. Governing Law......This Agreement shall be governed by, and construed\n"
    "in accordance with, the laws of the State of New York without regard to conflict\n"
    "of laws principles of that or any other jurisdiction of the United States.\n\n"
) + ARTICLE_AND_PROSE
# both in one document, so every row is outside a region and only the guards separate them
LEADER_MIXED = LEADER_INDEX_ROWS + LEADER_BODY


def _row_kinds(r) -> dict[str, tuple[str, str] | None]:
    return {c.label_canon: _ctoc_row_test(r["blocks"], c) for c in r["cands"] if c.kind == "section"}


def test_dot_leader_index_run_is_condemned_by_the_leader_arm():
    r = parse(LEADER_INDEX)
    assert r["toc"] == []  # the region was proposed and then VETOED -- the defect's state
    assert all("toc.vetoed_region" in c.rule_ids for c in r["cands"] if c.kind == "section")
    assert condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01"}
    assert all("ctoc.row_leader" in c.rule_ids
               for c in r["cands"] if c.label_canon in condemned(r))
    assert rejected_toc(r) >= condemned(r)
    off = parse(LEADER_INDEX, rules=False)
    assert set(sections(off)) >= condemned(r)  # every one of them was accepted before
    assert set(sections(r)) & condemned(r) == set()


def test_a_leader_typeset_body_heading_followed_by_prose_is_not_condemned():
    """The arm READS these rows -- it is the guards it inherits that refuse them: the second
    column is the section's own prose, not a page cell (`_fm_kind2 != "page"`)."""
    r = parse(LEADER_MIXED)
    rk = _row_kinds(r)
    for lab in ("SECTION 8.01", "SECTION 8.02", "SECTION 8.03"):
        assert rk[lab] is not None and rk[lab][0] == "leader"
        assert _fm_kind2(rk[lab][1]) != "page"
    assert condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01"}
    for lab in ("SECTION 1.01", "SECTION 2.01"):
        assert _fm_kind2(rk[lab][1]) == "page"


def test_a_row_with_no_leader_and_no_gap_is_still_read_by_nothing():
    """The two leaderless rows of the vetoed run carry no second column at all, so no arm
    reads them and B.1 leaves them where they are."""
    r = parse(LEADER_INDEX)
    rk = _row_kinds(r)
    assert rk["SECTION 9.01"] is None and rk["SECTION 9.02"] is None
    assert {"SECTION 9.01", "SECTION 9.02"} & condemned(r) == set()


def test_the_leader_arm_is_reached_only_after_the_four_column_arms():
    """Spec 9.2: the fifth arm fires only where `cell` / `gap` / `interleave` / `wrap` return
    None, so no row that already had a column answer can change its `row_kind`."""
    for doc, era in ((GAP_INDEX, "text"), (CELL_INDEX, "html_generic"), (WRAP_INDEX, "text")):
        r = parse(doc, era=era)
        for c in r["cands"]:
            assert not any(rid.startswith("ctoc.row_leader") for rid in c.rule_ids)


def test_the_leader_arm_keeps_the_two_rules_disjoint_and_moves_no_region_or_score():
    for doc in (LEADER_INDEX, LEADER_BODY, LEADER_MIXED):
        on, off = parse(doc), parse(doc, rules=False)
        assert condemned(on) & relieved(on) == set()
        for c in on["cands"]:
            assert not ("rej.ctoc_row" in c.rule_ids and "ctoc.region_relief" in c.rule_ids)
        assert [(t.first_cand, t.last_cand, t.reason) for t in on["toc"]] == \
               [(t.first_cand, t.last_cand, t.reason) for t in off["toc"]]
        assert [c.score for c in on["cands"]] == [c.score for c in off["cands"]]


def test_image_text_is_out_of_scope_for_the_leader_arm_too():
    assert condemned(parse(LEADER_MIXED, scope_era="image_text")) == set()
