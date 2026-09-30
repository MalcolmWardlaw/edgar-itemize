"""Turn 12 B.1 / K1 — `rej.vetoed_index_row`, the index row the chain anchors on.

`docs/turn12_decisions/a1_index_anchor.md`.  `toc.veto_chain_completing` drops a proposed
region whose members are the document's only strong copies of their labels and tags every
member `toc.vetoed_region`; Turn 8's `tree.vetoed_index_run` calls a long ordered run of
those tags an index, but it is read only in `tree.out_of_order_items`, the SECOND placement
pass.  The monotone chain never sees it, so it anchors items on the rows of an index the
parser has already decided is an index and the real body heading loses as `nonmonotone` —
6,221 accepted 10-K item nodes in 547 documents and 8,537 10-Q nodes in 4,729.

This pass moves the same test to candidate time.  Each test below is one clause of the
predicate the banks cleared:

  the run test   >= `_VETOED_INDEX_MIN_ITEMS` item candidates whose order keys increase,
                 with the Form 10-Q `stretch` amendment for runs whose labels are still
                 unresolved `ITEM ?.k` (`gram.part_from_context` runs after the live/rejected
                 split, so a two-Part contents page descends at the Part boundary);
  the later-copy clause  the document must hold a LATER candidate of the same
                 (kind, label_canon) carrying `gram.item.title_match` — a second copy of this
                 very item for the chain to move to.  Without the clause the rule is 80.3%
                 index rows and deletes 3,978 of the 6,221; with it, 53/53 in sample and
                 71/75 and 75/75 out of sample.  Without the title match it is satisfied by a
                 LABEL COLLISION (an exhibit-index group row, an abbreviated-report filer
                 renumbering Part III/IV items from 1) and condemns a real heading — all four
                 re-bank errors (`runs/judge/turn12-a1v2-latercheck.txt`).
"""

from edgar_itemize.candidates import (
    Candidate,
    _vidx_run_qualifies,
    find_candidates,
    page_repeat_pass,
    vetoed_index_row_pass,
)
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import Form10QGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import _VETOED_INDEX_MIN_ITEMS, build_tree
from edgar_itemize.tree_contract import build_contract_tree

G10K = Form10KGrammar()
G10Q = Form10QGrammar()
GCTR = ContractGrammar()


def parse(body: str, *, grammar=G10K, gname: str = "form10k", rules: bool = True):
    """Candidates, tree and rejections in `pipeline.parse_document`'s order.

    `rules=False` runs the Turn 11 pipeline (the pass never called), which is how each test
    shows what the rule itself does.
    """
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, grammar, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar=gname)
    toc_idx = {i for t in toc for i in range(t.first_cand, t.last_cand + 1)}
    page_repeat_pass(cands, blocks, toc_idx)
    if rules:
        vetoed_index_row_pass(cands, min_items=_VETOED_INDEX_MIN_ITEMS)
    build = build_contract_tree if gname == "contract" else build_tree
    nodes, rejected = build(blocks, cands, toc, grammar, doc_raw_start=0,
                            doc_raw_end=len(body), norm_len=len(norm))
    return dict(blocks=blocks, cands=cands, toc=toc, nodes=nodes, rejected=rejected)


def items(r) -> dict[str, int]:
    return {n.label_canon: n.head_raw_start for n in r["nodes"] if n.level_kind == "item"}


def condemned(r) -> set[str]:
    return {c.label_canon for c in r["cands"] if "rej.vetoed_index_row" in c.rule_ids}


def vetoed(r) -> set[str]:
    return {c.label_canon for c in r["cands"] if "toc.vetoed_region" in c.rule_ids}


# ---------------------------------------------------------------------------------------
# The defect, in one document.  Six index rows: four whose item the body re-states, and two
# Part III rows incorporated by reference, which is what makes `veto_chain_completing` drop
# the region (it needs >= 2 members with no copy outside it, carrying no leader or page
# number of their own).  The body typesets its four headings run-in, so they score below the
# index rows and the monotone chain takes the index.
_INDEX_ROWS = (
    "ACME CORP\n\nTABLE OF CONTENTS\n\n"
    "ITEM 1. BUSINESS                                    3\n\n"
    "ITEM 2. PROPERTIES                                  9\n\n"
    "ITEM 3. LEGAL PROCEEDINGS                          13\n\n"
    "ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY      15\n\n"
    "ITEM 10. DIRECTORS AND EXECUTIVE OFFICERS\n\n"
    "ITEM 11. EXECUTIVE COMPENSATION\n\n"
)
_BODY = (
    "\nPART I\n\n"
    "Item 1. Business. The Company manufactures industrial fasteners at three plants in Ohio.\n\n"
    "Item 2. Properties. The Company owns a factory in Ohio and leases warehouse space nearby.\n\n"
    "Item 3. Legal Proceedings. No material pending legal proceedings other than routine ones.\n\n"
    "PART II\n\n"
    "Item 5. Market for Registrant's Common Equity. The stock trades on the New York Exchange.\n"
)
INDEX_THEN_BODY = _INDEX_ROWS + _BODY

# the same six-row index with no body at all: the incorporation-by-reference / General
# Instruction J(1) run, which is 26 + 11 of the first bank's 47 real-heading windows
INDEX_ONLY = _INDEX_ROWS + "\nThe information called for is incorporated by reference.\n"

# the same document with the later copies carrying a DIFFERENT item's title — the exhibit
# index group rows of `0001354488-13-002619` ("Item 3 / Articles of Incorporation and
# Bylaws"), which satisfy a bare later-copy clause and are not a second copy of the item
COLLIDING_LATER = _INDEX_ROWS + (
    "\nEXHIBIT INDEX\n\n"
    "Item 1. Underwriting Agreement. Filed as Exhibit 1.1 to the registration statement.\n\n"
    "Item 2. Plan of Acquisition. Filed as Exhibit 2.1 to the registration statement here.\n\n"
    "Item 3. Articles of Incorporation and Bylaws. Filed as Exhibit 3.1 to the statement.\n\n"
    "Item 5. Instruments Defining Rights. Filed as Exhibit 4.1 to the statement of record.\n"
)


def test_the_defect_itself_the_chain_anchors_on_the_index_rows():
    """Before the rule: every item is anchored on its contents row and the four real body
    headings are rejected `nonmonotone`."""
    off = parse(INDEX_THEN_BODY, rules=False)
    assert vetoed(off) >= {"ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 10", "ITEM 11"}
    assert off["toc"] == []  # the region was proposed and then vetoed
    assert items(off) == {"ITEM 1": 30, "ITEM 2": 85, "ITEM 3": 140, "ITEM 5": 195,
                          "ITEM 10": 250, "ITEM 11": 293}
    assert {r.label_canon for r in off["rejected"] if r.reason == "nonmonotone"} == \
           {"ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5"}


def test_a_vetoed_index_row_with_a_later_titled_copy_is_condemned_and_the_anchor_moves():
    on = parse(INDEX_THEN_BODY)
    assert condemned(on) == {"ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5"}
    # the four anchors move off the contents page and onto the body heading the chain had
    # been rejecting; every one lands strictly later than it was
    off = parse(INDEX_THEN_BODY, rules=False)
    for lab in ("ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5"):
        assert items(on)[lab] > items(off)[lab]
    assert {r.label_canon for r in on["rejected"] if r.reason == "toc"} == condemned(on)
    assert all("toc.vetoed_index" in c.rule_ids
               for c in on["cands"] if "rej.vetoed_index_row" in c.rule_ids)


def test_the_two_rows_with_no_later_copy_are_not_condemned():
    """ITEM 10 and ITEM 11 are named only on the index — incorporation by reference.  There
    is no better target to re-anchor on, so condemning them would convert a wrong offset
    into a missing item; the clause leaves them alone."""
    on = parse(INDEX_THEN_BODY)
    assert {"ITEM 10", "ITEM 11"} & condemned(on) == set()


def test_an_ibr_stub_run_with_no_later_copy_anywhere_is_untouched():
    """The whole `later=False` half: 47 of 147 first-bank windows are real headings — 26
    incorporation-by-reference item runs, 11 General Instruction J(1) omission lists, 10
    ordinary body headings.  The rule must not move a document like this at all."""
    on, off = parse(INDEX_ONLY), parse(INDEX_ONLY, rules=False)
    assert vetoed(off) >= {"ITEM 1", "ITEM 10"}   # the run is tagged, and qualifies
    assert condemned(on) == set()
    assert items(on) == items(off)
    assert [r.reason for r in on["rejected"]] == [r.reason for r in off["rejected"]]


def test_a_later_copy_without_a_title_match_does_not_satisfy_the_clause():
    """The narrowing the re-bank's four errors forced: a later row carrying the same label
    but a different item's title is a LABEL COLLISION, not a second copy."""
    on, off = parse(COLLIDING_LATER), parse(COLLIDING_LATER, rules=False)
    later = [c for c in on["cands"] if c.kind == "item" and c.head_raw_start > 320]
    assert later and not any("gram.item.title_match" in c.rule_ids for c in later)
    assert condemned(on) == set()
    assert items(on) == items(off)


def test_a_condemned_row_never_appears_in_the_tree():
    """It is rejected before the per-member rescue and before the chain, so neither the
    rescue nor `out_of_order_items` can re-admit it."""
    on = parse(INDEX_THEN_BODY)
    heads = {n.head_raw_start for n in on["nodes"]}
    for c in on["cands"]:
        if "rej.vetoed_index_row" in c.rule_ids:
            assert c.head_raw_start not in heads
            assert "seq.out_of_order" not in c.rule_ids


# --- the run test, on its own ------------------------------------------------------------
def _cand(label: str, key: int, kind: str = "item") -> Candidate:
    return Candidate(block_idx=0, kind=kind, label_raw=label, label_canon=label, title="",
                     score=0.8, rule_ids=["toc.vetoed_region"], order_key=key)


def _run(labels_keys, kind: str = "item"):
    cands = [_cand(l, k, kind) for l, k in labels_keys]
    return cands, _vidx_run_qualifies(cands, 0, len(cands) - 1, _VETOED_INDEX_MIN_ITEMS)


def test_the_run_test_needs_four_increasing_item_keys():
    assert _run([("ITEM 1", 10), ("ITEM 2", 20), ("ITEM 3", 30), ("ITEM 5", 50)])[1]
    assert not _run([("ITEM 1", 10), ("ITEM 2", 20), ("ITEM 3", 30)])[1]
    assert not _run([("ITEM 3", 30), ("ITEM 1", 10), ("ITEM 5", 50), ("ITEM 2", 20)])[1]


def test_an_unresolved_two_part_run_qualifies_under_the_stretch_clause():
    """`?.1 ?.2 ?.3 ?.4 ?.1 ?.2` — the two-Part 10-Q contents page.  The descent is the Part
    boundary `gram.part_from_context` has not resolved yet, and the sequence is a
    concatenation of ascending stretches of >= 2, so it qualifies (reach 3,106 -> 7,119 of
    the 8,537 tightest, `runs/judge/turn12-a1v2-pop.txt`)."""
    assert _run([("ITEM ?.1", 10), ("ITEM ?.2", 20), ("ITEM ?.3", 30), ("ITEM ?.4", 40),
                 ("ITEM ?.1", 10), ("ITEM ?.2", 20)])[1]


def test_a_scrambled_unresolved_run_does_not_qualify():
    """`?.3 ?.1 ?.4 ?.2` — isolated singletons, not two contents blocks."""
    assert not _run([("ITEM ?.3", 30), ("ITEM ?.1", 10), ("ITEM ?.4", 40), ("ITEM ?.2", 20)])[1]


def test_a_resolved_run_keeps_the_shipped_strictly_increasing_test():
    """The stretch clause is gated on an unresolved label, so the 10-K is untouched by it."""
    assert not _run([("ITEM 1", 10), ("ITEM 2", 20), ("ITEM 3", 30), ("ITEM 4", 40),
                     ("ITEM 1", 10), ("ITEM 2", 20)])[1]


# --- Form 10-Q, end to end ---------------------------------------------------------------
# A two-Part contents page over a Part I body.  Both Parts' rows are one vetoed run, and the
# four Part I rows have a later titled copy; the two Part II rows do not.
Q_INDEX = (
    "ACME CORP\n\nINDEX\n\n"
    "PART I. FINANCIAL INFORMATION\n\n"
    "ITEM 1. FINANCIAL STATEMENTS                          3\n\n"
    "ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS          9\n\n"
    "ITEM 3. QUANTITATIVE AND QUALITATIVE DISCLOSURES     13\n\n"
    "ITEM 4. CONTROLS AND PROCEDURES                      15\n\n"
    "PART II. OTHER INFORMATION\n\n"
    "ITEM 1. LEGAL PROCEEDINGS\n\n"
    "ITEM 6. EXHIBITS\n\n"
)
Q_BODY = (
    "\nPART I. FINANCIAL INFORMATION\n\n"
    "Item 1. Financial Statements. The condensed consolidated balance sheets are unaudited.\n\n"
    "Item 2. Management's Discussion and Analysis of Financial Condition and Results of\n"
    "Operations. Revenues rose twelve percent over the comparable quarter of the prior year.\n\n"
    "Item 3. Quantitative and Qualitative Disclosures About Market Risk. No material change.\n\n"
    "Item 4. Controls and Procedures. Disclosure controls were effective at period end here.\n"
)


def test_a_two_part_10q_contents_page_is_condemned_and_the_anchors_move():
    on = parse(Q_INDEX + Q_BODY, grammar=G10Q, gname="form10q")
    off = parse(Q_INDEX + Q_BODY, grammar=G10Q, gname="form10q", rules=False)
    assert vetoed(off) >= {"ITEM I.1", "ITEM I.2", "ITEM I.3", "ITEM I.4",
                           "ITEM II.1", "ITEM II.6"}
    assert condemned(on) == {"ITEM I.1", "ITEM I.2", "ITEM I.3", "ITEM I.4"}
    for lab in ("ITEM I.1", "ITEM I.2", "ITEM I.3", "ITEM I.4"):
        assert items(on)[lab] > items(off)[lab]
    assert {"ITEM II.1", "ITEM II.6"} & condemned(on) == set()  # no later copy


# --- composition and scope ---------------------------------------------------------------
CONTRACT_INDEX = (
    "SECTION 1.01.  Certain Defined Terms                                     1\n\n"
    "SECTION 1.02.  Other Interpretive Provisions                             5\n\n"
    "SECTION 1.03.  Accounting Terms                                          7\n\n"
    "SECTION 2.01.  The Commitments                                           9\n\n"
    "ARTICLE I\n\nDEFINITIONS\n\n"
    "The Borrower shall from time to time deliver to the Administrative Agent such\n"
    "certificates and other documents as the Lenders may reasonably request in connection\n"
    "with the transactions contemplated hereby and shall pay the fees of its counsel.\n\n"
)


def test_the_two_turn11_contract_tags_and_this_one_are_disjoint():
    """`rej.ctoc_row` is written only for the contract grammar and `ctoc.region_relief` only
    for a member of a LIVE region, while a condemned row here is a member of a VETOED one.
    Asserted so no later edit can make the passes fight over one candidate."""
    for doc, grammar, gname in ((INDEX_THEN_BODY, G10K, "form10k"),
                                (INDEX_ONLY, G10K, "form10k"),
                                (COLLIDING_LATER, G10K, "form10k"),
                                (Q_INDEX + Q_BODY, G10Q, "form10q"),
                                (CONTRACT_INDEX, GCTR, "contract")):
        r = parse(doc, grammar=grammar, gname=gname)
        for c in r["cands"]:
            assert not ("rej.vetoed_index_row" in c.rule_ids and "rej.ctoc_row" in c.rule_ids)
            assert not ("rej.vetoed_index_row" in c.rule_ids
                        and "ctoc.region_relief" in c.rule_ids)


def test_the_pass_moves_no_region_and_no_score():
    """Tags only: `detect_toc` runs before it and is never consulted, so every region and
    every score stays bit-identical — which is what keeps the contract and EX-13 paths out."""
    for doc, grammar, gname in ((INDEX_THEN_BODY, G10K, "form10k"),
                                (Q_INDEX + Q_BODY, G10Q, "form10q")):
        on = parse(doc, grammar=grammar, gname=gname)
        off = parse(doc, grammar=grammar, gname=gname, rules=False)
        assert [(t.first_cand, t.last_cand, t.reason) for t in on["toc"]] == \
               [(t.first_cand, t.last_cand, t.reason) for t in off["toc"]]
        assert [c.score for c in on["cands"]] == [c.score for c in off["cands"]]
        assert [c.head_raw_start for c in on["cands"]] == [c.head_raw_start for c in off["cands"]]
