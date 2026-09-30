"""Turn 13 B.4 -- `rej.section_xref`, the contract analogue of `rej.xref_pointer`.

docs/turn13_decisions/a3_lexnlp.md section 9 (the `xref3` proxy) and
docs/turn13_decisions/b4_section_xref.md.  A definitional cross-reference whose
`Section n.n` token wraps to the head of a line ("... has the meaning assigned to that term
in / Section 5.04.") is rejected before chaining, so the real body heading of that label
takes the slot.  The predicate is scripts/turn13/a3_proxy.py's `xref3`, verbatim; the
first group of tests pins each of its three conditions, the second the tree behaviour.
"""

from edgar_itemize.candidates import find_candidates, section_xref_pass, section_xref_test
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree_contract import build_contract_tree

G = ContractGrammar()


def at(text: str, needle: str) -> bool:
    return section_xref_test(text, text.index(needle))


# --- the predicate ---------------------------------------------------------------------

def test_definitional_reference_fires():
    t = "\"Protected Loans\" shall have the meaning assigned to that term in\n\nSection 5.04.\n"
    assert at(t, "Section 5.04")


def test_reference_followed_by_bracket_or_lower_case_fires():
    assert at("delivered in accordance with this\n\nSection 2.07 (a) and the Borrower\n", "Section 2.07")
    assert at("as provided in\n\nSection 6.04 hereof shall apply\n", "Section 6.04")


def test_comma_tail_fires():
    t = "subject to the terms hereof, including, without limitation,\n\nSection 9.02.\n"
    assert at(t, "Section 9.02")


def test_previous_line_ending_in_a_period_does_not_fire():
    assert not at("The Borrower shall repay the Loans in full.\n\nSection 2.05.\n", "Section 2.05")


def test_title_after_the_number_does_not_fire():
    # SECTION 9.05. Reinstatement. -- a real heading after a run-on line (A.3's xref sample)
    t = "the obligations of the Guarantor hereunder shall continue in full force and effect as\n\nSECTION 9.05. Reinstatement.\n"
    assert not at(t, "SECTION 9.05")


def test_title_case_run_on_is_the_accepted_recall_cost():
    # the one Turn 12 regression xref3 misses (a3_lexnlp.md s9 item 4): not tagged
    t = "the Administrative Agent shall promptly notify each Lender thereof, and\n\nSection 2.5. Upon receipt of any such notice from the Borrowers, the\n"
    assert not at(t, "Section 2.5")


def test_list_close_does_not_fire():
    t = "(c) any Subsidiary shall fail to pay any Indebtedness when due; or\n\nSection 10.04.\n"
    assert not at(t, "Section 10.04")


def test_short_capitalised_title_line_does_not_fire():
    assert not at("ARTICLE X\n\nCosts and expenses\n\nSection 10.01.\n", "Section 10.01")


def test_bare_numeral_label_does_not_fire():
    assert not at("in accordance with the terms of\n\n5.04.\n", "5.04")


def test_document_head_does_not_fire():
    assert not at("Section 1.01.\n", "Section 1.01")


# --- the tree --------------------------------------------------------------------------

BODY = (
    "ARTICLE I\n\nDEFINITIONS\n\n"
    "Section 1.01. Defined Terms. As used in this Agreement the following terms have the meanings specified.\n\n"
    "\"Interest Period\" shall have the meaning assigned to such term in\n\n"
    "Section 2.02.\n\n"
    "\"Loans\" means the loans made by the Lenders to the Borrower pursuant to this Agreement.\n\n"
    "ARTICLE II\n\nTHE CREDIT\n\n"
    "Section 2.01. Commitments. Each Lender agrees to make Loans to the Borrower from time to time.\n\n"
    "Section 2.02. Interest. The Loans shall bear interest at the rate provided herein for each day.\n\n"
    "Section 2.03. Prepayment. The Borrower may prepay the Loans at any time without premium.\n"
)


def parse(body: str, *, rule: bool = True):
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="contract")
    if rule:
        section_xref_pass(cands, blocks, norm)
    nodes, rejected = build_contract_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return dict(cands=cands, nodes=nodes, rejected=rejected)


def test_only_the_reference_is_tagged():
    r = parse(BODY)
    tagged = [c for c in r["cands"] if "rej.section_xref" in c.rule_ids]
    assert [(c.label_canon, c.head_raw_start) for c in tagged] == [("SECTION 2.02", BODY.index("Section 2.02.\n"))]


def test_reference_rejected_and_body_heading_holds_the_slot():
    r = parse(BODY)
    secs = {(n.label_canon, n.head_raw_start) for n in r["nodes"] if n.level_kind == "section"}
    assert ("SECTION 2.02", BODY.index("Section 2.02. Interest")) in secs
    assert ("SECTION 2.02", BODY.index("Section 2.02.\n")) not in secs
    rj = [x for x in r["rejected"] if x.reason == "section_xref"]
    assert [(x.label_canon, x.raw_start) for x in rj] == [("SECTION 2.02", BODY.index("Section 2.02.\n"))]
    assert {"SECTION 1.01", "SECTION 2.01", "SECTION 2.02", "SECTION 2.03"} == {s for s, _ in secs}


def test_without_the_rule_nothing_is_rejected_as_section_xref():
    r = parse(BODY, rule=False)
    assert not any(x.reason == "section_xref" for x in r["rejected"])


def test_reference_cannot_open_a_restart():
    # a reference to Section 1.01 late in the document used to be able to restart the
    # section numbering; tagged, it is not live and opens nothing
    body = BODY + ("\nThe Borrower shall observe the definitions set forth in\n\nSection 1.01.\n\n"
                   "and shall comply with each covenant set forth herein.\n")
    r = parse(body)
    late = body.rindex("Section 1.01.")
    assert not any(n.head_raw_start == late for n in r["nodes"])
    assert not any("chain.section_restart" in n.rule_ids for n in r["nodes"])
    assert any(x.reason == "section_xref" and x.raw_start == late for x in r["rejected"])


def test_r2_bare_conjunction_previous_line_does_not_fire():
    # Turn 13 B.4 R2: the list close split from its `;` onto a line of its own
    t = ("unless such interruptions are covered by business interruption insurance;\n\nor\n\n"
         "Section 6.1.11. (i) the Borrower shall lose, fail to keep in force\n")
    assert not at(t, "Section 6.1.11")


def test_r2b_bare_conjunction_after_prose_still_fires():
    # R2b: the bare `or` is prose split off by HTML bold runs -- the line before it does not
    # end in `;`.  The normalised shape of 0000949377-06-000535 seq 2 (html_generic) for
    # `pursuant to <b>Section 2.5, Section 2.6(e)</b> or <b>Section 9.2</b>.`
    t = ("\"\nTermination Date\n\" means the Maturity Date or such earlier date of termination of the "
         "Commitments\npursuant to\n\nSection 2.5, Section 2.6(e)\nor\n\nSection 9.2\n.\n\n"
         "\"\nTotal Capitalization\n\" means the sum.\n")
    assert at(t, "Section 9.2")
    # the same `or` after a `;` close is the R2 heading shape and does not fire
    t2 = t.replace("Section 2.6(e)\nor", "Section 2.6(e);\nor")
    assert not at(t2, "Section 9.2")


def test_r2_heading_kept_in_the_tree():
    body = ("ARTICLE VI\n\nEVENTS OF DEFAULT\n\n"
            "Section 6.1.10. The cable systems of the Borrower shall be interrupted for more than ten days "
            "unless such interruptions are covered by business interruption insurance;\n\nor\n\n"
            "Section 6.1.11. (i) the Borrower shall lose, fail to keep in force or terminate any license;\n\nor\n\n"
            "Section 6.1.12. The Borrower shall fail to pay any amount when due under this Agreement.\n")
    r = parse(body)
    assert not any(x.reason == "section_xref" for x in r["rejected"])
    labels = [n.label_canon for n in r["nodes"] if n.level_kind == "section"]
    assert any(l and "6.01.11" in l for l in labels), labels


# --- Turn 13 B.4b: `gram.synth_section_xref` -------------------------------------------

ORPHANS = (
    "ARTICLE I\n\nDEFINITIONS\n\n"
    "\"Closing Date\" has the meaning assigned to such term in\n\n"
    "Section 3.2.\n\n"
    "(a) The Borrower shall deliver the financial statements for each fiscal quarter.\n\n"
    "(b) The Borrower shall deliver the annual audit report within ninety days.\n\n"
    "(c) The Borrower shall deliver such other information as the Agent requests.\n\n"
    "ARTICLE II\n\nCOVENANTS\n\n"
    "(a) The Borrower shall maintain its corporate existence at all times hereafter.\n\n"
    "(b) The Borrower shall pay all taxes before they become delinquent hereunder.\n"
)


def test_orphaned_clauses_get_a_synthetic_section_per_article(monkeypatch):
    import edgar_itemize.tree_contract as tc
    monkeypatch.setattr(tc, "_SXREF_ORPHAN_ON", False)
    off = parse(ORPHANS)
    assert not any(n.level_kind.startswith("clause") for n in off["nodes"])
    assert sum(1 for x in off["rejected"] if x.reason == "clause_outside_section") == 5
    monkeypatch.setattr(tc, "_SXREF_ORPHAN_ON", True)
    on = parse(ORPHANS)
    synth = [n for n in on["nodes"] if "gram.synth_section_xref" in n.rule_ids]
    assert [(n.level_kind, n.label_canon, n.head_raw_start) for n in synth] == [
        ("section", None, ORPHANS.index("(a) The Borrower shall deliver")),
        ("section", None, ORPHANS.index("(a) The Borrower shall maintain")),
    ]
    by_id = {n.node_id: n for n in on["nodes"]}
    # each synthetic section sits under the article it is in, and holds that article's clauses
    assert by_id[synth[0].parent_id].label_canon == "ARTICLE 1" and by_id[synth[1].parent_id].label_canon == "ARTICLE 2"
    kids = {s.node_id: [n.label_canon for n in on["nodes"] if n.parent_id == s.node_id] for s in synth}
    assert list(kids.values()) == [["(a)", "(b)", "(c)"], ["(a)", "(b)"]]
    assert not any(x.reason == "clause_outside_section" for x in on["rejected"])
    # the reference itself stays rejected and is not a node
    assert any(x.reason == "section_xref" for x in on["rejected"])


def test_no_reference_no_change(monkeypatch):
    # without a vetoed reference the B.4b step never runs
    body = ORPHANS.replace("\"Closing Date\" has the meaning assigned to such term in\n\nSection 3.2.\n\n", "")
    import edgar_itemize.tree_contract as tc
    a = parse(body)
    monkeypatch.setattr(tc, "_SXREF_ORPHAN_ON", False)
    b = parse(body)
    sig = lambda r: [(n.level_kind, n.label_canon, n.head_raw_start, tuple(n.rule_ids)) for n in r["nodes"]]
    assert sig(a) == sig(b)
    assert [(x.label_canon, x.reason) for x in a["rejected"]] == [(x.label_canon, x.reason) for x in b["rejected"]]
