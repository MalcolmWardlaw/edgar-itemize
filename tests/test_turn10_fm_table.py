"""Turn 10 B.2 — the front-matter table detector (`rej.fm_table`).

`docs/turn10_decisions/a3_fmtable.md`.  The caption guard of Turn 9 B.2 leaves 15 of the
26 b4-catalogued `seq.out_of_order` false accepts standing: the cover-page "documents
incorporated by reference" tables (family 2) and a filer agent's item-to-page
cross-reference tables (family 3).  Their rows are separated by a column header or a bare
`PART <roman>` line, and the pointer that says the item is not in this document sits in
the SECOND column, never on the label's own line.  This detector reads the second column
itself and needs no caption.

Each test below is one clause of the design: the three row shapes, the two guards on the
row test, the furniture and the stop rule of the run test, the run-level evidence test,
and the two scope promises — `detect_toc` is bit-identical with the pass on and off, and
the tag moves no score and changes no other node.
"""

from edgar_itemize.candidates import find_candidates, fm_table_pass
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.normalize_html import html_to_blocks
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import build_tree

G = Form10KGrammar()


def parse(body: str, *, guard: bool = True, era: str = "text"):
    """Candidates and tree for a synthetic 10-K, in `pipeline.parse_document`'s order."""
    if era == "text":
        blocks, norm, _ = text_to_blocks(body, 0)
    else:
        blocks, norm, _ = html_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era=era)
    if guard:
        fm_table_pass(cands, blocks)
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, rejected = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return cands, nodes, rejected


def items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def cand_for(cands, label):
    hits = [c for c in cands if c.label_canon == label]
    assert hits, f"no candidate for {label}"
    return hits[0]


def tagged(cands):
    return {c.label_canon for c in cands if "rej.fm_table" in c.rule_ids}


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


def as_html(body: str, prefix: str = "") -> str:
    paras = "\n".join(f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip())
    return f"<html><body>{prefix}{paras}</body></html>"


# --- row shape 1: the text era's column gap ---------------------------------------
# 0000078778-97-000019 (family 2) and 0000014846-99-000018: the left column holds the
# item's label and title, the right column names the proxy statement section.  The gap is
# invisible in `normalized_text` — the normalizer collapses runs of spaces inside a line —
# and survives in `Block.lines`, which is why the pass takes `blocks`.
GAP_TABLE = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement
Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement
Item 12. Security Ownership of Certain Beneficial     "Security Ownership" in the Proxy Statement

""" + BODY


def test_text_era_gap_row_is_not_recovered_out_of_order():
    cands, nodes, rejected = parse(GAP_TABLE)
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    for lab in ("ITEM 10", "ITEM 11", "ITEM 12"):
        c = cand_for(cands, lab)
        assert "gram.item.title_match" in c.rule_ids  # every other out-of-order test passes
        assert "fm.row_gap" in c.rule_ids
        assert lab not in items(nodes)
        assert [r.reason for r in rejected if r.label_canon == lab] == ["nonmonotone"]
    # without the pass these are exactly the false accepts b4 catalogued
    _, nodes_off, _ = parse(GAP_TABLE, guard=False)
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes_off))
    assert all("seq.out_of_order" in items(nodes_off)[lab].rule_ids for lab in ("ITEM 10", "ITEM 11", "ITEM 12"))


def test_the_normalized_text_alone_cannot_see_the_gap():
    """The reason the pass reads `Block.lines`: the row arrives in `normalized_text` as a
    heading followed by prose, which is what b4 read it as."""
    blocks, norm, _ = text_to_blocks(GAP_TABLE, 0)
    assert '"Election of Directors" in the Proxy Statement' in norm
    assert "Item 10. Directors and Executive Officers             " not in norm
    assert any("Item 10. Directors and Executive Officers             " in ln
               for b in blocks for ln in (b.lines or (b.text,)))


def test_gap_before_column_twelve_is_not_a_second_column():
    """A run of spaces near the left margin is typesetting, not a column boundary."""
    body = ("DOCUMENTS INCORPORATED BY REFERENCE\n\n"
            "Item 10.    Directors and Executive Officers of the Registrant\n"
            "Item 11.    Executive Compensation\n"
            "Item 12.    Security Ownership of Certain Beneficial Owners\n\n") + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == set()
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes))


# --- row shape 2: the html/ixbrl table cell ---------------------------------------
# 0000892569-* (seven filings), 0000950137-06-013769: "ITEM 8: FINANCIAL STATEMENTS AND
# SUPPLEMENTARY DATA" | "SEE NOTE (B) BELOW".
CELL_TABLE = (
    "<p>CROSS REFERENCE SHEET</p>"
    "<table>"
    "<tr><td>Item</td><td>Page in Annual Report</td></tr>"
    "<tr><td>Item 10. Directors and Executive Officers of the Registrant</td><td>SEE NOTE (B) BELOW</td></tr>"
    "<tr><td>Item 11. Executive Compensation</td><td>SEE NOTE (B) BELOW</td></tr>"
    "<tr><td>Item 12. Security Ownership of Certain Beneficial Owners</td><td>SEE NOTE (B) BELOW</td></tr>"
    "</table>"
)


def test_html_cell_row_is_not_recovered_out_of_order():
    cands, nodes, _ = parse(as_html(BODY, CELL_TABLE), era="html_generic")
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    assert all("fm.row_cell" in cand_for(cands, lab).rule_ids for lab in ("ITEM 10", "ITEM 11", "ITEM 12"))
    assert not ({"ITEM 10", "ITEM 11", "ITEM 12"} & set(items(nodes)))
    _, nodes_off, _ = parse(as_html(BODY, CELL_TABLE), guard=False, era="html_generic")
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes_off))


# --- row shape 3: the wrapped row the normalizer interleaves ----------------------
# The left column wraps and the right column continues beside it at the same tab stop, so
# the row's continuation line carries a gap at the same column (+-3).
INTERLEAVE_TABLE = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                        Document of Reference

Item 10. Directors and Executive Officers   "Election of Directors" from the
of the Registrant                           1997 Proxy Statement
Item 11. Executive Compensation             "Executive Compensation" from the
                                            1997 Proxy Statement
Item 12. Security Ownership of Certain      "Security Ownership" from the
Beneficial Owners and Management            1997 Proxy Statement

""" + BODY


def test_interleaved_wrapped_row_is_not_recovered_out_of_order():
    cands, nodes, _ = parse(INTERLEAVE_TABLE)
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    assert any("fm.row_interleave" in c.rule_ids for c in cands if c.label_canon in tagged(cands))
    assert not ({"ITEM 10", "ITEM 11", "ITEM 12"} & set(items(nodes)))


# --- guard 1: the first column must carry the label AND some of its title ---------
def test_label_only_first_cell_is_a_heading_typeset_in_two_cells():
    """"ITEM 4." | "(REMOVED AND RESERVED)" and "Item" | "1B. Unresolved Staff Comments"
    (0001193125-21-203334) are headings a filer split across cells, not table rows.  This
    guard alone takes the short-body false-catch rate from 1.1168% to 0.1304%
    (runs/judge/turn10-fmtable-stoprule.txt, configurations A -> B)."""
    split = (
        "<table>"
        "<tr><td>Item 10.</td><td>Directors and Executive Officers of the Registrant</td></tr>"
        "<tr><td>Item 11.</td><td>Executive Compensation</td></tr>"
        "<tr><td>Item</td><td>12. Security Ownership of Certain Beneficial Owners</td></tr>"
        "</table>"
    )
    cands, nodes, _ = parse(as_html(BODY, split), era="html_generic")
    assert tagged(cands) == set()
    assert {"ITEM 10", "ITEM 11"} <= set(items(nodes))


def test_label_only_first_column_in_the_text_era_is_not_a_row():
    """The same shape at a tab stop: "Item 11.        Executive Compensation"."""
    body = ("DOCUMENTS INCORPORATED BY REFERENCE\n\n"
            "Item 10.                Directors and Executive Officers of the Registrant\n"
            "Item 11.                Executive Compensation\n"
            "Item 12.                Security Ownership of Certain Beneficial Owners\n\n") + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == set()
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes))


# --- guard 2: a second cell that is the item's own body ---------------------------
def test_body_prose_in_the_next_cell_is_not_a_pointer_column():
    """0001169232-07-001444 typesets Part I as two cells: the heading on the left and its
    own body on the right.  A pointer cell names a page, a note, a document or a status;
    a body cell is prose of >= 150 characters and names none of them."""
    prose = (
        "<table>"
        "<tr><td>Item 10. Directors and Executive Officers of the Registrant</td>"
        "<td>The company had no changes in its board of directors during the year, and each "
        "of the persons named below has served continuously since the last annual meeting "
        "of the shareholders of the company.</td></tr>"
        "<tr><td>Item 11. Executive Compensation</td>"
        "<td>The compensation committee reviewed the salaries of each of the named officers "
        "during the year and made no changes to the schedule that had been adopted at the "
        "beginning of the preceding fiscal year.</td></tr>"
        "</table>"
    )
    cands, nodes, _ = parse(as_html(BODY, prose), era="html_generic")
    assert tagged(cands) == set()
    assert {"ITEM 10", "ITEM 11"} <= set(items(nodes))


# --- the run test: furniture, and PART transparency -------------------------------
def test_furniture_between_rows_does_not_end_the_run():
    """A column header, a rule line, a `<PAGE>` break, an SGML tag, a bare page number and
    a bare enumeration ordinal are all furniture of the table itself."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

- ------------------------------------------------------------------------------
Item                                                  Document of Reference
- ------------------------------------------------------------------------------
<S>                                                   <C>
Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement

(1)

Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement
<PAGE>
                                       3
Item 12. Security Ownership of Certain Beneficial     "Security Ownership" in the Proxy Statement

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    assert not ({"ITEM 10", "ITEM 11", "ITEM 12"} & set(items(nodes)))


def test_a_bare_part_line_between_two_rows_is_transparent():
    """The one stop rule the caption guard may not relax (b2 section 4): family 2's tables
    print `PART II` and `PART IV` between their rows."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

PART III

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement

PART IV

Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == {"ITEM 10", "ITEM 11"}
    assert not ({"ITEM 10", "ITEM 11"} & set(items(nodes)))


def test_a_real_part_heading_ends_the_run_on_its_first_body_item():
    """Why the Part line may be transparent here: every member must pass the row test
    first, so the run ends on the MEMBER (stop rule 3), not on the Part line.  Here the
    two-column table is followed by a real `PART III` and a real one-column body heading,
    which is tagged by nothing and keeps its node."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement
Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement

PART III

Item 12. Security Ownership of Certain Beneficial Owners and Management

The following table sets forth the beneficial ownership of the registrant's common stock.

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == {"ITEM 10", "ITEM 11"}
    assert "ITEM 12" in items(nodes) and "seq.out_of_order" in items(nodes)["ITEM 12"].rule_ids


def test_a_one_row_table_reaches_nothing():
    """MIN_RUN = 2: a single two-column line is not a table."""
    body = ("DOCUMENTS INCORPORATED BY REFERENCE\n\n"
            "Item                                                  Document of Reference\n\n"
            'Item 11. Executive Compensation                       "Executive Compensation" in the Proxy\n\n') + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == set()
    assert "ITEM 11" in items(nodes)


# --- the stop rule ----------------------------------------------------------------
def test_a_prose_block_ends_the_run():
    """Stop rule 1: a non-table paragraph of >= 200 characters with three consecutive
    lower-case words is the document's body, and the table ended above it."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement
Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement

The registrant is a Delaware corporation whose common stock is registered under section
twelve of the Securities Exchange Act of 1934, and the following discussion should be read
together with the consolidated financial statements appearing elsewhere in this report.

Item 12. Security Ownership of Certain Beneficial     "Security Ownership" in the Proxy Statement
Item 13. Certain Relationships and Related            "Certain Transactions" in the Proxy Statement

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12", "ITEM 13"}  # two runs of two


def test_two_blank_lines_and_a_non_furniture_line_end_the_run():
    """Stop rule 2, and what it protects: the second pair below is a run of its own only
    because it is still two rows; a single leftover row reaches nothing."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement
Item 11. Executive Compensation                       "Executive Compensation" in the Proxy Statement

The registrant will file a definitive proxy statement.

Item 12. Security Ownership of Certain Beneficial     "Security Ownership" in the Proxy Statement

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == {"ITEM 10", "ITEM 11"}
    assert "ITEM 12" in items(nodes)


def test_an_item_that_fails_the_row_test_ends_the_run():
    """Stop rule 3, the rule that does the work: a one-column item line between two rows
    is a heading, and the run ends on it."""
    body = """DOCUMENTS INCORPORATED BY REFERENCE

Item                                                  Document of Reference

Item 10. Directors and Executive Officers             "Election of Directors" in the Proxy Statement

Item 11. Executive Compensation

The compensation committee of the board of directors sets the salary of each officer.

Item 12. Security Ownership of Certain Beneficial     "Security Ownership" in the Proxy Statement

""" + BODY
    cands, nodes, _ = parse(body)
    assert tagged(cands) == set()  # neither row has a neighbour that passes the row test
    assert "ITEM 11" in items(nodes)


# --- the run-level evidence test --------------------------------------------------
STATUS_RUN = (
    "<table>"
    "<tr><td>Item 10. Directors and Executive Officers of the Registrant</td><td>Not Applicable.</td></tr>"
    "<tr><td>Item 11. Executive Compensation</td><td>Not Applicable.</td></tr>"
    "<tr><td>Item 12. Security Ownership of Certain Beneficial Owners</td><td>None.</td></tr>"
    "</table>"
)


def test_a_run_of_pure_status_cells_with_no_header_is_not_condemned():
    """0001193125-12-140190 typesets the whole of Part I in two columns — "Item 1B.
    Unresolved Staff Comments." | "Not Applicable." — and those are real headings with
    real (empty) bodies.  A front-matter table says WHERE the item's content is."""
    cands, nodes, _ = parse(as_html(BODY, STATUS_RUN), era="html_generic")
    assert tagged(cands) == set()
    assert {"ITEM 10", "ITEM 11", "ITEM 12"} <= set(items(nodes))


def test_the_same_run_under_a_column_header_is_condemned():
    """The evidence may come from the header above the run instead of from the cells."""
    with_header = STATUS_RUN.replace(
        "<table>", "<table><tr><td>Item</td><td>Page in the Annual Report</td></tr>", 1)
    cands, nodes, _ = parse(as_html(BODY, with_header), era="html_generic")
    assert tagged(cands) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    assert not ({"ITEM 10", "ITEM 11", "ITEM 12"} & set(items(nodes)))


# --- the must-not-catch class: short-body items -----------------------------------
SHORT_BODY = """PART I

ITEM 1. BUSINESS

The Company manufactures industrial fasteners at three plants in Ohio and Indiana.

ITEM 3. LEGAL PROCEEDINGS. None.

ITEM 4. SUBMISSION OF MATTERS TO A VOTE OF SECURITY HOLDERS. None.

PART II

ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY

The common stock trades on the New York Stock Exchange under the symbol FAST.
"""


def test_one_line_items_are_not_tagged():
    """A front-matter table row and a one-line item have exactly the same body length,
    which is why no body-length threshold is used anywhere: only the second column tells
    them apart (a3_fmtable.md section 3)."""
    cands, nodes, _ = parse(SHORT_BODY)
    assert tagged(cands) == set()
    assert {"ITEM 1", "ITEM 3", "ITEM 4", "ITEM 5"} <= set(items(nodes))


# --- scope ------------------------------------------------------------------------
TOC_BODY = """FORM 10-K

TABLE OF CONTENTS

                                                                            Page

Item 1. Business........................................................      1
Item 2. Properties......................................................      4
Item 3. Legal Proceedings...............................................      6
Item 5. Market for Registrant's Common Equity...........................      8
Item 7. Management's Discussion and Analysis of Financial Condition.....     11

""" + GAP_TABLE + """PART III

ITEM 10. DIRECTORS AND EXECUTIVE OFFICERS OF THE REGISTRANT

The information required by this item is set out under the caption Election of Directors.

ITEM 11. EXECUTIVE COMPENSATION

The information required by this item is set out under the caption Executive Compensation.

ITEM 12. SECURITY OWNERSHIP OF CERTAIN BENEFICIAL OWNERS AND MANAGEMENT

The information required by this item is set out under the caption Security Ownership.
"""


def test_detect_toc_is_bit_identical_with_the_pass_on_and_off():
    """The pass runs BEFORE `detect_toc`, which consults none of its ids
    (`toc.leader_or_pageno`, `Candidate.toc_hint`, `Candidate.score`, `rej.continued`),
    so every region and every toc rule id is unchanged (a3_fmtable.md section 4)."""
    blocks, norm, _ = text_to_blocks(TOC_BODY, 0)

    def toc_state(guard: bool):
        cands = find_candidates(blocks, G, era="text")
        if guard:
            fm_table_pass(cands, blocks)
        toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
        rules = [[r for r in c.rule_ids if not (r.startswith("fm.") or r == "rej.fm_table")] for c in cands]
        return repr(toc), rules, [c.score for c in cands], [c.toc_hint for c in cands]

    on, off = toc_state(True), toc_state(False)
    assert on[0] == off[0]  # the regions themselves
    assert on[1:] == off[1:]  # and every other id, score and hint on every candidate
    assert "TABLE OF CONTENTS" in norm and on[0] != "[]"  # a live region, not an empty list


def test_the_tag_carries_no_score_and_changes_no_other_node():
    cands_on, nodes_on, _ = parse(GAP_TABLE)
    cands_off, nodes_off, _ = parse(GAP_TABLE, guard=False)
    assert [c.score for c in cands_on] == [c.score for c in cands_off]
    on, off = items(nodes_on), items(nodes_off)
    assert set(off) - set(on) == {"ITEM 10", "ITEM 11", "ITEM 12"}
    for lab in on:
        assert (on[lab].head_raw_start, on[lab].norm_start) == (off[lab].head_raw_start, off[lab].norm_start)


def test_the_pass_is_form10k_only_in_the_pipeline():
    """Scope, exactly like the caption guard: `pipeline.parse_document` (its `parse_prepared` half) calls it under
    `if grammar.name == "form10k"`, so the 10-Q baseline and the contract grammar are
    untouched."""
    import inspect

    from edgar_itemize import pipeline

    src = inspect.getsource(pipeline.parse_prepared)  # the post-normalise half of parse_document
    head = src.split("fm_table_pass(cands, blocks)")[0]
    assert 'if grammar.name == "form10k":' in head
    assert "detect_toc(" in src.split("fm_table_pass(cands, blocks)")[1]
