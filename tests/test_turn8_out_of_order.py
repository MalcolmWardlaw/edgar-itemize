"""Turn 8 B.4 — items typeset out of the statutory order (`seq.out_of_order`).

The monotone item chain is one strictly-increasing run over the whole document, so a
label the filer typeset in the wrong place is dropped as `nonmonotone` even when it is
the document's only, unopposed, fully-titled copy (12,034 such candidates in 7,950
filings of runs/full_v17, runs/judge/turn8-ooo-census.parquet).  Each test names the
rule id it guards; the census and the judged windows behind them are described in
docs/turn8_decisions/a5_out_of_order.md.
"""

from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import build_tree

G = Form10KGrammar()


def parse(body: str):
    """Candidates and tree for a text-era document body."""
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, rejected = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return cands, nodes, rejected


def items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def cand_for(cands, label, at=None):
    hits = [c for c in cands if c.label_canon == label and (at is None or at in c.title or at in c.label_raw)]
    assert hits, f"no candidate for {label}"
    return hits[0]


# --- seq.out_of_order: Item 1B typeset after Item 2 ------------------------------
# The filer's habit the census is mostly made of: the short "N/A" items bundled together
# after Part I's long ones (4,134 of the 9,717-row accept pool are ITEM 1B).
#
# The chain's members are typeset as headings (caps) and the stray one is not, so the
# chain is decided on weight rather than on `max_weight_increasing`'s tie-break: a bare
# swap (drop Item 2, or drop Item 1B) is otherwise the same length either way.
BODY_1B = """PART I

ITEM 1. BUSINESS

The Company manufactures industrial fasteners at three plants.

ITEM 2. PROPERTIES

The Company owns a factory in Ohio and leases warehouse space.

Item 1B. Unresolved Staff Comments

None.

ITEM 3. LEGAL PROCEEDINGS

The Company is party to no material pending legal proceedings.
"""


def test_item_1b_after_item_2_is_placed_out_of_order():
    cands, nodes, rejected = parse(BODY_1B)
    it = items(nodes)
    assert set(it) == {"ITEM 1", "ITEM 2", "ITEM 1B", "ITEM 3"}
    ooo = it["ITEM 1B"]
    assert "seq.out_of_order" in ooo.rule_ids and "gram.item.title_match" in ooo.rule_ids
    # no nonmonotone rejection is left behind for the label the pass claimed
    assert [r.label_canon for r in rejected if r.reason == "nonmonotone"] == []
    # order_key is statutory, the parent is statutory, the position is the document's
    assert ooo.order_key == G.order_key("item", "ITEM 1B")
    assert ooo.parent_id == it["ITEM 1"].parent_id  # PART I, like every other Part I item
    assert it["ITEM 2"].raw_start < ooo.raw_start < it["ITEM 3"].raw_start
    # the chain's own nodes keep their anchors
    for lab in ("ITEM 1", "ITEM 2", "ITEM 3"):
        assert "seq.out_of_order" not in it[lab].rule_ids


def test_out_of_order_ordinal_follows_position_not_label():
    """paths by position, order_key by label (agenda.assign_paths sorts kids by raw_start)."""
    from edgar_itemize.agenda import assign_paths

    blocks, norm, _ = text_to_blocks(BODY_1B, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, _ = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(BODY_1B), norm_len=len(norm))
    paths = assign_paths(nodes, blocks, doc_start=0, doc_end=len(BODY_1B))
    it = items(nodes)
    ordinals = {lab: paths[n.node_id][2] for lab, n in it.items()}
    assert ordinals == {"ITEM 1": 1, "ITEM 2": 2, "ITEM 1B": 3, "ITEM 3": 4}


def test_front_matter_item_keeps_its_part_through_the_heading_merge():
    """merge_and_renumber re-derives parents from position; an out-of-order item that
    sits before any Part heading (51% of them do -- front-matter listings) would be
    handed to the document root at depth 2.  Its Part must survive the merge.
    """
    from edgar_itemize.agenda import assign_paths, path_str
    from edgar_itemize.headings import find_headings, merge_and_renumber

    body = (
        "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\n\n"
        "PART I\n\nITEM 1. BUSINESS\n\nThe Company manufactures industrial fasteners.\n\n"
        "PART II\n\nITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY\n\n"
        "The common stock trades on the New York Stock Exchange.\n\n"
        "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\nRevenues rose twelve percent.\n"
    )
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10k")
    nodes, _ = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    nodes = merge_and_renumber(nodes, find_headings(blocks, nodes, "text"))
    paths = assign_paths(nodes, blocks, doc_start=0, doc_end=len(body))
    by_id = {n.node_id: n for n in nodes}
    ooo = next(n for n in nodes if n.label_canon == "ITEM 7A")
    assert "seq.out_of_order" in ooo.rule_ids
    assert by_id[ooo.parent_id].label_canon == "PART II"  # not the document root
    # the Parts keep their own top-level ordinals; the recovered item is a Part II child
    assert path_str(paths[by_id[ooo.parent_id].node_id]).startswith("2.2.")
    assert path_str(paths[ooo.node_id]).startswith("2.2.1")


# --- seq.out_of_order: Item 8 bound behind the exhibit index ---------------------
BODY_ITEM_8 = """PART II

ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY

The common stock trades on the New York Stock Exchange.

ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS

Revenues rose twelve percent on higher unit volume across both segments.

PART IV

ITEM 14. EXHIBITS, FINANCIAL STATEMENT SCHEDULES AND REPORTS ON FORM 8-K

The exhibits listed below are filed as part of this report.

Item 8. Financial Statements and Supplementary Data

Report of Independent Auditors and the consolidated balance sheets follow.
"""


def test_item_8_after_item_14_is_placed_out_of_order():
    cands, nodes, rejected = parse(BODY_ITEM_8)
    it = items(nodes)
    assert "ITEM 8" in it and "seq.out_of_order" in it["ITEM 8"].rule_ids
    assert it["ITEM 14"].raw_start < it["ITEM 8"].raw_start
    # placed under its statutory Part, not the Part it physically sits in
    parts = {n.node_id: n for n in nodes if n.level_kind == "part"}
    assert parts[it["ITEM 8"].parent_id].label_canon == "PART II"
    # ... and Part II is NOT stretched over the tail to reach it: that would swallow Part IV
    assert parts[it["ITEM 8"].parent_id].raw_end <= it["ITEM 14"].raw_start
    assert [r.label_canon for r in rejected if r.reason == "nonmonotone"] == []


def test_out_of_order_never_competes_for_a_claimed_label():
    """A second copy of a label the chain already placed stays `nonmonotone`."""
    body = BODY_ITEM_8 + "\nItem 7. Management's Discussion and Analysis\n\nSee the discussion above.\n"
    cands, nodes, rejected = parse(body)
    assert sum(1 for n in nodes if n.label_canon == "ITEM 7") == 1
    assert "ITEM 7" in [r.label_canon for r in rejected if r.reason == "nonmonotone"]


# --- the TOC rows that must keep losing ------------------------------------------
BODY_TOC = """FORM 10-K

TABLE OF CONTENTS

Item 1. Business . . . . . . . . . . . . . . . . . . . 3

Item 2. Properties . . . . . . . . . . . . . . . . . . 9

Item 3. Legal Proceedings . . . . . . . . . . . . . . 11

Item 7. Management's Discussion and Analysis . . . . . 14

Item 8. Financial Statements and Supplementary Data . 21

PART I

Item 1. Business

The Company manufactures industrial fasteners at three plants.

Item 2. Properties

The Company owns a factory in Ohio and leases warehouse space.
"""


def test_toc_rows_are_not_recovered_out_of_order():
    cands, nodes, rejected = parse(BODY_TOC)
    it = items(nodes)
    # Items 3, 7 and 8 appear only in the table of contents: the pass must not mine a
    # condemned index for the labels the body never typesets.
    assert set(it) == {"ITEM 1", "ITEM 2"}
    assert not any("seq.out_of_order" in n.rule_ids for n in nodes)
    assert {r.label_canon for r in rejected if r.reason == "toc"} >= {"ITEM 3", "ITEM 7", "ITEM 8"}


# --- toc.vetoed_index: the vetoed region is a real index, not a stub listing ---------
# toc.veto_chain_completing drops a proposed region whose members are the document's only
# copies of their labels, which happens to every table of contents whose Part III rows are
# incorporated by reference.  A.5 read the tag as evidence FOR content on the strength of
# a 2-3 row stub listing; on the population it marks a twenty-row index nine times in ten
# (docs/turn8_decisions/b4_out_of_order.md).  Length and statutory order separate them.
_BODY_7 = (
    "PART I\n\nITEM 1. BUSINESS\n\nThe Company manufactures industrial fasteners at three plants.\n\n"
    "ITEM 1A. RISK FACTORS\n\nOur results depend on steel prices.\n\n"
    "ITEM 2. PROPERTIES\n\nThe Company owns a factory in Ohio.\n\n"
    "ITEM 3. LEGAL PROCEEDINGS\n\nNo material pending legal proceedings.\n\n"
    "PART II\n\nITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY\n\nThe stock trades on the NYSE.\n\n"
    "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\nRevenues rose twelve percent.\n\n"
    "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\n\nThe balance sheets follow.\n"
)


def _front(rows):
    """A front-matter listing above a seven-item body, the shape the veto acts on."""
    return ("ACME CORP\n\nTABLE OF CONTENTS\n\n"
            + "".join(f"{lab} {title}{page}\n\n" for lab, title, page in rows) + "\n" + _BODY_7)


# a full index: every item of the body plus the two Part III items that exist nowhere else,
# which is exactly why veto_chain_completing declines to condemn it
_INDEX = [("Item 1.", "Business", "  3"), ("Item 1A.", "Risk Factors", "  9"),
          ("Item 2.", "Properties", " 13"), ("Item 3.", "Legal Proceedings", " 15"),
          ("Item 5.", "Market for Registrant's Common Equity", " 17"),
          ("Item 7.", "Management's Discussion and Analysis", " 19"),
          ("Item 8.", "Financial Statements and Supplementary Data", " 21"),
          ("Item 10.", "Directors and Executive Officers", ""),
          ("Item 11.", "Executive Compensation", "")]
# A.5's case: a three-item stub listing, vetoed for the same reason
_STUB = [("PART III", "", ""), ("Item 10.", "Directors and Executive Officers", ""),
         ("Item 11.", "Executive Compensation", " 20"),
         ("Item 12.", "Security Ownership of Certain Beneficial Owners", " 21")]


def test_long_ordered_vetoed_index_is_not_mined_for_items():
    cands, nodes, _ = parse(_front(_INDEX))
    assert any("toc.vetoed_region" in c.rule_ids for c in cands)  # the veto fired
    c = cand_for(cands, "ITEM 11")
    assert "toc.vetoed_index" in c.rule_ids and "seq.out_of_order" not in c.rule_ids
    assert set(items(nodes)) == {"ITEM 1", "ITEM 1A", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 7", "ITEM 8"}


def test_a_short_vetoed_stub_run_is_still_recovered():
    cands, nodes, _ = parse(_front(_STUB))
    c = cand_for(cands, "ITEM 10")
    assert "toc.vetoed_region" in c.rule_ids and "toc.vetoed_index" not in c.rule_ids
    assert "seq.out_of_order" in items(nodes)["ITEM 10"].rule_ids
    # its neighbours in the run print page numbers, so they lose on index evidence as before
    assert "ITEM 11" not in items(nodes)


# --- toc.leader_or_pageno_wrapped: the leaders wrap onto the title's second line ---
# 0000062418-98-000003: "Item 12: Security Ownership of Certain Beneficial\nOwners and
# Management. . . . . 55" carried no index evidence at all, and gram.item.title_match then
# waived the prose penalties for it (A.5 limit 1; 2 of the 2 clean hand-read false accepts).
def test_wrapped_page_number_row_is_index_evidence():
    body = (
        "PART III\n\nItem 12: Security Ownership of Certain Beneficial\n"
        "Owners and Management. . . . . . . . . . 55\n\n"
        "Item 13: Certain Relationships and Related\nTransactions                    57\n"
    )
    cands, nodes, rejected = parse(body)
    c12 = cand_for(cands, "ITEM 12")
    assert "toc.leader_or_pageno_wrapped" in c12.rule_ids
    c13 = cand_for(cands, "ITEM 13")  # trailing page number, no leaders, also on line two
    assert "toc.leader_or_pageno_wrapped" in c13.rule_ids
    # scope: the wrapped tag is index evidence for seq.out_of_order only -- it must not
    # imply the first-line rule, whose absence is what the Turn 3 chain-completion veto
    # (toc.veto_chain_completing) reads to rescue a real stub listing
    assert "toc.leader_or_pageno" not in c12.rule_ids and not c12.toc_hint
    assert not any("seq.out_of_order" in n.rule_ids for n in nodes)


def test_underline_ruler_under_a_heading_is_not_a_dot_leader():
    """0000098362-04-000001: "Item 2. Properties\\n___________________" -- the ruler line
    matches the leader regex's underscore alternative, and _title_from_lines joins it
    because it does not start with a letter.  A wrapped line with no words is furniture."""
    body = "PART I\n\nItem 2. Properties\n___________________\n\nTimken has manufacturing facilities at multiple locations.\n"
    cands, nodes, _ = parse(body)
    c = cand_for(cands, "ITEM 2")
    assert "toc.leader_or_pageno_wrapped" not in c.rule_ids and "toc.leader_or_pageno" not in c.rule_ids


def test_wrapped_scan_stops_at_the_lines_the_title_owns():
    """A number in the body below a heading is not the heading's page number."""
    body = (
        "PART II\n\nItem 7. Management's Discussion and Analysis\n\n"
        "Revenues rose twelve percent, and unit volume in the fastener segment rose 14\n"
    )
    cands, _, _ = parse(body)
    c = cand_for(cands, "ITEM 7")
    assert "toc.leader_or_pageno" not in c.rule_ids


# --- toc.pageno_self_label: the label's own digits are not a page number ----------
# 0001437749-18-005827: "ITEM 8" alone, title on the next block, matched
# _TRAILING_PAGE_RE against its own item number and lost the chain (A.5 limit 2).
def test_bare_numeric_label_does_not_match_its_own_number():
    body = (
        "PART II\n\nItem 5. Market for Registrant's Common Equity\n\n"
        "The common stock trades on the New York Stock Exchange.\n\n"
        "Item 8\n\nFinancial Statements and Supplementary Data\n\n"
        "The consolidated balance sheets and the auditors' report follow this page.\n"
    )
    cands, nodes, _ = parse(body)
    c = cand_for(cands, "ITEM 8")
    # The tag names what the trailing-page regex matched.  It is deliberately a
    # description and not a behaviour: the hint and its penalty stand (taking them back
    # re-anchors whole documents onto their tables of contents, see candidates.py), and
    # the tag cannot reach the out-of-order rule anyway -- a label alone on its line has
    # no title on that line, so gram.item.title_match, which that rule requires, is absent.
    assert "toc.pageno_self_label" in c.rule_ids and "toc.leader_or_pageno" in c.rule_ids
    assert "gram.item.title_match" not in c.rule_ids
    assert "ITEM 8" in items(nodes)


def test_real_trailing_page_number_still_counts():
    body = "PART III\n\nItem 12. Security Ownership . . . . . . 55\n"
    cands, _, _ = parse(body)
    assert "toc.leader_or_pageno" in cand_for(cands, "ITEM 12").rule_ids
    body = "PART III\n\nItem 12 - Security Ownership of Certain Beneficial Owners    55\n"
    cands, _, _ = parse(body)
    c = cand_for(cands, "ITEM 12")
    assert "toc.leader_or_pageno" in c.rule_ids and "toc.pageno_self_label" not in c.rule_ids


# --- rej.xref_pointer: the pointer sentence that opens with the statutory title ----
# 0000709337-97-000004 (and three same-filer repeats 1998-99), 0000950152-06-002070:
# "Item 8., Financial Statements and Supplementary Data is set forth in the registrant's
# 1996 Annual Report to Shareholders and is incorporated by reference in Part II of this
# report."  gram.item.title_match fires by design, so the xref rule is what has to say no.
BODY_IBR = """PART II

Item 5. Market for Registrant's Common Equity

The common stock trades on the New York Stock Exchange.

Item 7. Management's Discussion and Analysis

Revenues rose twelve percent on higher unit volume across both segments.

PART IV

Item 15. Exhibits, Financial Statement Schedules and Reports on Form 8-K

(a)1. Financial Statements

Item 8., Financial Statements and Supplementary Data
is set forth in the registrant's 1996 Annual Report to
Shareholders and is incorporated by reference in Part II
of this report.
"""


def test_ibr_pointer_sentence_is_not_recovered_out_of_order():
    cands, nodes, rejected = parse(BODY_IBR)
    c = cand_for(cands, "ITEM 8")
    assert "gram.item.title_match" in c.rule_ids  # the waiver still fires, as documented
    assert "rej.xref_pointer" in c.rule_ids
    assert "ITEM 8" not in items(nodes)
    assert not any("seq.out_of_order" in n.rule_ids for n in nodes)
    # scope: the tag bars the out-of-order placement and changes nothing else, so a
    # pointer sentence the monotone chain does accept keeps its node and its score
    plain = BODY_IBR.replace("is set forth in the registrant's 1996 Annual Report to", "was restated for the 1996 fiscal year and")
    same = cand_for(parse(plain)[0], "ITEM 8")
    assert "rej.xref_pointer" not in same.rule_ids and same.score == c.score
    assert [r.reason for r in rejected if r.label_canon == "ITEM 8"] == ["nonmonotone"]


def test_run_in_heading_is_not_a_pointer_sentence():
    """The discriminator is the sentence break: a run-in heading closes its title first."""
    body = (
        "PART II\n\nItem 7. Management's Discussion and Analysis\n\n"
        "Revenues rose twelve percent on higher unit volume.\n\n"
        "Item 8. Financial Statements and Supplementary Data.  The information set forth on\n"
        "pages 13-14 to 13-41 of Exhibit 13 hereto is incorporated herein by reference.\n"
    )
    cands, nodes, _ = parse(body)
    c = cand_for(cands, "ITEM 8")
    assert "gram.item.title_match" in c.rule_ids and "rej.xref_pointer" not in c.rule_ids
    assert "ITEM 8" in items(nodes)


def test_heading_with_no_terminal_punctuation_is_not_a_pointer_sentence():
    """The determiner guard: a new subject after the title means a new sentence.

    0000007084-04-000364 typesets "Item 5." and its title in table cells and runs the
    body straight on with no punctuation at all -- "Item 5. MARKET FOR REGISTRANT'S
    COMMON EQUITY ... Information responsive to this Item is set forth in ..." -- and is
    a heading the judges accept.  Only the rest of the printed title may stand between
    the statutory title and the pointer verb.
    """
    body = (
        "PART II\n\nItem 5. MARKET FOR REGISTRANT'S COMMON EQUITY, RELATED STOCKHOLDER MATTERS\n"
        "Information responsive to this Item is set forth in the annual report to shareholders\n"
    )
    cands, nodes, _ = parse(body)
    c = cand_for(cands, "ITEM 5")
    assert "gram.item.title_match" in c.rule_ids and "rej.xref_pointer" not in c.rule_ids
    assert "ITEM 5" in items(nodes)


def test_pointer_verb_without_the_statutory_title_keeps_the_old_rule():
    """A plain cross-reference has no title match; rej.xref_phrase already handles it."""
    body = "PART II\n\nItem 6 is incorporated by reference to page 20 of the Annual Report.\n"
    cands, _, _ = parse(body)
    c = cand_for(cands, "ITEM 6")
    assert "gram.item.title_match" not in c.rule_ids and "rej.xref_phrase" in c.rule_ids
