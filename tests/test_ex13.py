"""Turn 7 (b): EX-13 (Annual Report to Shareholders) as a parsed document.

Covers the document-root heading pass (headings.py/pipeline.py, `synth_root`),
the ars kind -> satisfies_item mapping (ars_kind.py), and the manifest builder
(scripts/full_manifest_ex13.py), per docs/turn7_decisions/b_ex13.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgar_itemize import ars_kind
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.pipeline import parse_document, result_rows
from edgar_itemize.sgml import Submission, header_text, split_documents

pytest.importorskip("scripts.full_manifest_ex13", reason="lab script scripts/full_manifest_ex13.py not present (private notebook only)")
from scripts.full_manifest_ex13 import (  # noqa: E402
    _EX13_TYPE_RE,
    UUE_RE,
    classify,
    is_binary_payload,
    matched_form,
    other_form_flags,
    trigger_population,
)


# --- an EX-13 body with no "ITEM N" text at all: build_tree must find zero
# item/part candidates, so find_headings has nothing to nest under without the
# document-root anchor (docs/turn7_decisions/b_ex13.md, "blocking fact") ---
EX13_SUB = (
    "<SEC-HEADER>\nACCESSION NUMBER: 0000000002-96-000001\n"
    "COMPANY CONFORMED NAME:\t\tTEST ARS CO\n</SEC-HEADER>\n"
    "<DOCUMENT>\n<TYPE>EX-13\n<SEQUENCE>2\n<DESCRIPTION>ANNUAL REPORT\n<TEXT>\n\n<PAGE>\n"
    "TO OUR SHAREHOLDERS\n\n"
    + "We had a good year across every one of our markets and segments. " * 8 + "\n\n"
    "SELECTED FINANCIAL DATA\n\n"
    + "Five year summary figures appear below in the accompanying table. " * 8 + "\n\n"
    "MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n"
    + "Results improved this year on volume growth across all of our markets. " * 8 + "\n\n"
    "REPORT OF INDEPENDENT ACCOUNTANTS\n\n"
    + "We have audited the accompanying consolidated financial statements listed here. " * 8 + "\n\n"
    "CONSOLIDATED BALANCE SHEETS\n\n"
    + "Assets and liabilities are detailed in the accompanying tables below here. " * 8 + "\n\n"
    "NOTES TO FINANCIAL STATEMENTS\n\n"
    + "Summary of significant accounting policies follows in detail below here. " * 8 + "\n\n"
    "</TEXT>\n</DOCUMENT>\n"
)

# A normal 10-K primary document (real Item/Part text) for the regression check
# that synth_root is a no-op whenever items are actually found.
TENK_SUB = (
    "<SEC-HEADER>\nACCESSION NUMBER: 0000000001-96-000001\n"
    "COMPANY CONFORMED NAME:\t\tTEST PACKING CO\n</SEC-HEADER>\n"
    "<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<TEXT>\n\n<PAGE>\n"
    "                                PART I\n\n"
    "ITEM 1. BUSINESS\n\nThe company packs things. " + "Plants are owned. " * 40 + "\n\n"
    "ITEM 2. PROPERTIES\n\nSeveral plants. " + "More plants are described here. " * 20 + "\n\n"
    "</TEXT>\n</DOCUMENT>\n"
)


# Turn 8 (a4_ex13.md section 2): a dot-leader table-of-contents row that the style
# scanner over-promotes to a heading candidate ("Financial Highlights .... 1", the
# real bank case, not synthetic) sitting ahead of the real body section it points to.
EX13_TOC_SUB = (
    "<SEC-HEADER>\nACCESSION NUMBER: 0000000003-96-000001\n"
    "COMPANY CONFORMED NAME:\t\tTEST ARS TOC CO\n</SEC-HEADER>\n"
    "<DOCUMENT>\n<TYPE>EX-13\n<SEQUENCE>2\n<DESCRIPTION>ANNUAL REPORT\n<TEXT>\n\n<PAGE>\n"
    "Financial Highlights .................... 1\n\n"
    "FINANCIAL HIGHLIGHTS\n\n"
    + "Revenue and earnings both grew again this year across our markets. " * 8 + "\n\n"
    "</TEXT>\n</DOCUMENT>\n"
)


def _ex13_doc(synth_root: bool, sub_text: str = EX13_SUB, accession: str = "0000000002-96-000001"):
    sub = Submission(path=Path(f"{accession}.txt"), text=sub_text, header=header_text(sub_text), documents=split_documents(sub_text))
    doc = sub.documents[0]
    assert (doc.type or "").upper() == "EX-13"
    return parse_document(sub, doc, "12345", grammar=Form10KGrammar(), synth_root=synth_root)


def _tenk():
    sub = Submission(path=Path("0000000001-96-000001.txt"), text=TENK_SUB, header=header_text(TENK_SUB), documents=split_documents(TENK_SUB))
    return sub, sub.documents[0]


# --- document-root heading detection -------------------------------------------------


def test_ex13_without_synth_root_yields_no_headings():
    # the blocking fact the section file records: Form10KGrammar() on an EX-13
    # today produces zero nodes beyond the document root.
    r = _ex13_doc(synth_root=False)
    assert [n.level_kind for n in r.nodes] == ["document"]


def test_ex13_build_tree_finds_zero_item_candidates():
    r = _ex13_doc(synth_root=True)
    assert not any(n.level_kind in ("item", "part") for n in r.nodes)


def test_ex13_synth_root_recovers_headings():
    r = _ex13_doc(synth_root=True)
    headings = [n for n in r.nodes if n.level_kind == "heading"]
    assert len(headings) >= 5
    titles = {n.title for n in headings}
    assert "MANAGEMENT'S DISCUSSION AND ANALYSIS" in titles
    assert "CONSOLIDATED BALANCE SHEETS" in titles
    # headings nest directly under the document root (no synthetic node leaks into the tree)
    root = next(n for n in r.nodes if n.level_kind == "document")
    assert all(n.parent_id == root.node_id for n in headings)


def test_ex13_paths_assign_without_error():
    r = _ex13_doc(synth_root=True)
    nodes, doc, _rej = result_rows(r, 1996, keep_text=False)
    assert doc["doc_type"] == "EX-13"
    assert doc["n_nodes"] == len(r.nodes)


def test_synth_root_is_noop_when_items_exist():
    sub, doc = _tenk()
    a = parse_document(sub, doc, "12345", synth_root=False)
    b = parse_document(sub, doc, "12345", synth_root=True)
    assert [(n.level_kind, n.label_canon, n.raw_start, n.raw_end) for n in a.nodes] == \
           [(n.level_kind, n.label_canon, n.raw_start, n.raw_end) for n in b.nodes]


# --- satisfies_item / kind.* mapping -------------------------------------------------


def test_ars_kind_classify_mapping():
    assert ars_kind.classify("MANAGEMENT'S DISCUSSION AND ANALYSIS") == ("mdna", "ITEM 7")
    assert ars_kind.classify("Report of Independent Accountants") == ("auditors", "ITEM 8")
    assert ars_kind.classify("Report of Independent Registered Public Accounting Firm") == ("auditors", "ITEM 8")
    assert ars_kind.classify("Consolidated Balance Sheets") == ("finstmt", "ITEM 8")
    assert ars_kind.classify("Notes to Consolidated Financial Statements") == ("notes", "ITEM 8")
    assert ars_kind.classify("Selected Financial Data") == ("selected", "ITEM 6")
    assert ars_kind.classify("Letter to Shareholders") is None
    assert ars_kind.classify("To Our Shareholders") is None
    assert ars_kind.classify(None) is None
    assert ars_kind.classify("") is None


# --- Turn 8 (a4_ex13.md): alias families, one kind.<family>_alias rule id each ---------


def test_ars_kind_mdna_alias_family():
    # "&" for "and" (six of the nine bank "discussion" aliases)
    assert ars_kind.classify("MANAGEMENT'S DISCUSSION & ANALYSIS OF FINANCIAL CONDITION & RESULTS OF\nOPERATION") \
        == ("mdna_alias", "ITEM 7")
    assert ars_kind.classify("MANAGEMENT'S DISCUSSION & ANALYSIS") == ("mdna_alias", "ITEM 7")
    # "discussion of ... condition" with no "analysis" at all
    assert ars_kind.classify("MANAGEMENT'S DISCUSSION OF CONSOLIDATED FINANCIAL\nCONDITION AND RESULTS OF OPERA") \
        == ("mdna_alias", "ITEM 7")
    # base "mdna" (exact "discussion and analysis") still wins over the alias, unchanged
    assert ars_kind.classify("MANAGEMENT'S DISCUSSION AND ANALYSIS") == ("mdna", "ITEM 7")
    # a bare "MANAGEMENT DISCUSSION" with no "analysis"/"condition" qualifier is
    # deliberately too generic to alias (a4_ex13.md section 3b) -- stays null
    assert ars_kind.classify("MANAGEMENT DISCUSSION\nINTERNATIONAL BUSINESS MACHINES CORPORATION AND SUBSIDIARY") is None


def test_ars_kind_selected_alias_family():
    assert ars_kind.classify("SELECTED CONSOLIDATED FINANCIAL INFORMATION") == ("selected_alias", "ITEM 6")
    assert ars_kind.classify("Selected Quarterly Financial Data (Unaudited)") == ("selected_alias", "ITEM 6")
    assert ars_kind.classify("SELECTED CONSOLIDATED FINANCIAL AND OTHER DATA") == ("selected_alias", "ITEM 6")
    assert ars_kind.classify("FINANCIAL & OPERATING HIGHLIGHTS") == ("selected_alias", "ITEM 6")
    assert ars_kind.classify("Financial Highlights") == ("selected_alias", "ITEM 6")
    assert ars_kind.classify("MISSISSIPPI CHEMICAL CORPORATION\nFINANCIAL HIGHLIGHTS") == ("selected_alias", "ITEM 6")
    # base "selected" (exact "selected [consolidated] financial data") still wins, unchanged
    assert ars_kind.classify("Selected Financial Data") == ("selected", "ITEM 6")


def test_ars_kind_finstmt_alias_family():
    assert ars_kind.classify("CONSOLIDATING BALANCE SHEET") == ("finstmt_alias", "ITEM 8")
    assert ars_kind.classify("CONSOLIDATED STATEMENT OF SHAREHOLDERS' EQUITY") == ("finstmt_alias", "ITEM 8")
    assert ars_kind.classify("CONSOLIDATED STATEMENTS OF CHANGES ON SHAREHOLDERS' EQUITY") == ("finstmt_alias", "ITEM 8")
    # base "finstmt" (exact "consolidated"/"stockholders") still wins, unchanged
    assert ars_kind.classify("Consolidated Balance Sheets") == ("finstmt", "ITEM 8")
    assert ars_kind.classify("Consolidated Statements of Stockholders' Equity") == ("finstmt", "ITEM 8")
    # deliberately NOT aliased: "condensed" (parent-company-only Reg S-X note
    # pattern, not a primary Item 8 statement -- a4_ex13.md section 2/3d)
    assert ars_kind.classify("CONDENSED STATEMENT OF INCOME") is None


def test_ars_kind_auditors_alias_family():
    assert ars_kind.classify("RESPONSIBILITY FOR FINANCIAL STATEMENTS") == ("auditors_alias", "ITEM 8")
    assert ars_kind.classify("MANAGEMENT'S REPORT ON\nFINANCIAL STATEMENTS") == ("auditors_alias", "ITEM 8")
    # base "auditors" still wins, unchanged
    assert ars_kind.classify("Report of Independent Accountants") == ("auditors", "ITEM 8")


def test_ars_kind_toc_row_guard():
    # dot-leader + trailing page number: both known bank cases (a4_ex13.md section 2)
    assert ars_kind.classify("Financial Highlights .................... 1") is None
    assert ars_kind.classify("Selected Financial Data .......................... 1") is None
    # underscore leader variant
    assert ars_kind.classify("Management's Discussion and Analysis ______ 24") is None
    # a title carrying digits that are NOT a dot-leader page number still classifies
    assert ars_kind.classify("Selected Financial Data (1993-2002)") == ("selected", "ITEM 6")


def test_ars_kind_block_in_toc_region():
    assert ars_kind.block_in_toc_region(5, [(2, 8)]) is True
    assert ars_kind.block_in_toc_region(1, [(2, 8)]) is False
    assert ars_kind.block_in_toc_region(9, [(2, 8), (10, 12)]) is False
    assert ars_kind.block_in_toc_region(11, [(2, 8), (10, 12)]) is True
    assert ars_kind.block_in_toc_region(None, [(2, 8)]) is False
    assert ars_kind.block_in_toc_region(5, []) is False


def test_ex13_toc_row_heading_end_to_end_carries_no_satisfies_item():
    # the real bank case (a4_ex13.md): the style scanner still finds the dot-leader
    # row a heading candidate, but ars_kind.classify's TOC-row guard (applied in
    # pipeline.parse_document's synth_root loop) must leave it untagged, while the
    # real section heading it points to is unaffected and classifies normally.
    r = _ex13_doc(synth_root=True, sub_text=EX13_TOC_SUB, accession="0000000003-96-000001")
    headings = [n for n in r.nodes if n.level_kind == "heading"]
    by_title = {n.title: n for n in headings}
    assert "Financial Highlights .................... 1" in by_title
    toc_row = by_title["Financial Highlights .................... 1"]
    assert toc_row.satisfies_item is None
    assert not any(rid.startswith("kind.") for rid in toc_row.rule_ids)
    real = by_title["FINANCIAL HIGHLIGHTS"]
    assert real.satisfies_item == "ITEM 6"
    assert "kind.selected_alias" in real.rule_ids


def test_ex13_headings_carry_satisfies_item_and_kind_rule():
    r = _ex13_doc(synth_root=True)
    by_title = {n.title: n for n in r.nodes if n.level_kind == "heading"}
    assert by_title["MANAGEMENT'S DISCUSSION AND ANALYSIS"].satisfies_item == "ITEM 7"
    assert "kind.mdna" in by_title["MANAGEMENT'S DISCUSSION AND ANALYSIS"].rule_ids
    assert by_title["SELECTED FINANCIAL DATA"].satisfies_item == "ITEM 6"
    assert "kind.selected" in by_title["SELECTED FINANCIAL DATA"].rule_ids
    assert by_title["REPORT OF INDEPENDENT ACCOUNTANTS"].satisfies_item == "ITEM 8"
    assert "kind.auditors" in by_title["REPORT OF INDEPENDENT ACCOUNTANTS"].rule_ids
    assert by_title["CONSOLIDATED BALANCE SHEETS"].satisfies_item == "ITEM 8"
    assert "kind.finstmt" in by_title["CONSOLIDATED BALANCE SHEETS"].rule_ids
    assert by_title["NOTES TO FINANCIAL STATEMENTS"].satisfies_item == "ITEM 8"
    assert "kind.notes" in by_title["NOTES TO FINANCIAL STATEMENTS"].rule_ids
    # front matter: no item to satisfy, no kind.* rule id
    assert by_title["TO OUR SHAREHOLDERS"].satisfies_item is None
    assert not any(rid.startswith("kind.") for rid in by_title["TO OUR SHAREHOLDERS"].rule_ids)


def test_result_rows_carries_satisfies_item_column():
    r = _ex13_doc(synth_root=True)
    nodes, _doc, _rej = result_rows(r, 1996, keep_text=False)
    by_title = {n["title"]: n for n in nodes if n["level_kind"] == "heading"}
    assert by_title["MANAGEMENT'S DISCUSSION AND ANALYSIS"]["satisfies_item"] == "ITEM 7"
    assert by_title["TO OUR SHAREHOLDERS"]["satisfies_item"] is None


def test_tenk_nodes_never_carry_satisfies_item():
    # 10-K path (synth_root=False, the CLI default for kind="10k") must never
    # populate satisfies_item -- it is an EX-13-only column.
    sub, doc = _tenk()
    r = parse_document(sub, doc, "12345")
    nodes, _doc, _rej = result_rows(r, 1996, keep_text=False)
    assert all(n["satisfies_item"] is None for n in nodes)


# --- manifest builder -----------------------------------------------------------------


def test_ex13_type_regex():
    for t in ("EX-13", "EX-13.1", "EX-13.01", "EX-13.A", "EX-13.(A)", "EX-13."):
        assert _EX13_TYPE_RE.match(t), t
    for t in ("EX-13G", "EX-132", "EX-10.1", "EX-27", "SC 13G"):
        assert not _EX13_TYPE_RE.match(t), t


def test_trigger_population_any_ibr_item(tmp_path):
    # one accession flagged via ITEM 7, one only via a non-7/8 item (ITEM 6), one
    # with the flags individually true but not both -- all three belong to the
    # population (any item's IBR-in-submission flag, not only Item 7/8; this
    # reconciles the memo's 12,306 total against its 10,161-both-7-and-8 figure,
    # see the manifest builder module docstring).
    rows = [
        dict(accession_number="A", label_canon="ITEM 7", item_incorporated_by_reference=True, ibr_target_in_submission=True),
        dict(accession_number="A", label_canon="ITEM 1", item_incorporated_by_reference=False, ibr_target_in_submission=None),
        dict(accession_number="B", label_canon="ITEM 6", item_incorporated_by_reference=True, ibr_target_in_submission=True),
        dict(accession_number="C", label_canon="ITEM 8", item_incorporated_by_reference=True, ibr_target_in_submission=False),
        dict(accession_number="D", label_canon="ITEM 8", item_incorporated_by_reference=None, ibr_target_in_submission=None),
    ]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "nodes-10k-1994.parquet")
    assert trigger_population(tmp_path) == {"A", "B"}


# --- Turn 12 B.5 (docs/turn12_decisions/a5_ex13.md section 4): the manifest-build
# binary-payload filter and the contains_other_form flag ------------------------------


def test_uuencoded_ex13_payload_is_flagged_binary_at_manifest_build(tmp_path):
    """161 of 13,140 EX-13-tagged documents are a uuencoded <PDF>/<XML> wrapper, not
    an annual report -- 142 of those carry the plain EX-13 tag with no distinguishing
    <FILENAME> extension, so this is a content test on the document's own <TEXT>
    span, not a filename test."""
    sub_text = (
        "<SEC-HEADER>\nACCESSION NUMBER: 0000000009-96-000001\n"
        "COMPANY CONFORMED NAME:\t\tTEST BINARY CO\n</SEC-HEADER>\n"
        "<DOCUMENT>\n<TYPE>EX-13\n<SEQUENCE>2\n<DESCRIPTION>ANNUAL REPORT\n<FILENAME>ex13.txt\n<TEXT>\n"
        "<PDF>\n%PDF-1.4 binary garbage follows here that is not readable text\n"
        "</TEXT>\n</DOCUMENT>\n"
    )
    p = tmp_path / "0000000009-96-000001.txt"
    p.write_text(sub_text)
    assert is_binary_payload(str(p), 2) is True
    # a genuine (text) EX-13 at the same shape is not flagged
    normal_text = sub_text.replace("<PDF>\n%PDF-1.4 binary garbage follows here that is not readable text\n",
                                   "TO OUR SHAREHOLDERS\n\nWe had a good year across our markets.\n")
    p2 = tmp_path / "0000000010-96-000001.txt"
    p2.write_text(normal_text.replace("0000000009", "0000000010"))
    assert is_binary_payload(str(p2), 2) is False
    # a uuencode `begin 644 ...` line within the first 300 bytes is the other shape
    uue_text = sub_text.replace("<PDF>\n%PDF-1.4 binary garbage follows here that is not readable text\n",
                                 "begin 644 exhibit13.pdf\nM4$1&+3$N-`H`````````````````````````````````````\n")
    p3 = tmp_path / "0000000011-96-000001.txt"
    p3.write_text(uue_text.replace("0000000009", "0000000011"))
    assert is_binary_payload(str(p3), 2) is True
    # a nonexistent sequence on an otherwise-real submission is not flagged (has_text False)
    assert is_binary_payload(str(p2), 99) is False


def test_uue_regex_matches_the_a5_binary_shapes():
    assert UUE_RE.search("<PDF>\nbinary junk")
    assert UUE_RE.search("<XML>\n<foo/>")
    assert UUE_RE.search("begin 644 foo.pdf\nM4$1&")
    assert UUE_RE.search("some preamble line\nbegin 666 bar.pdf\nM4$1&")
    assert not UUE_RE.search("TO OUR SHAREHOLDERS\n\nWe had a good year.")


def test_ex13_contains_other_form_flags_the_e2_population(tmp_path):
    """class E2 (docs/turn12_decisions/a5_ex13.md section 3): a genuinely different
    form's item reproduced under the 10-K grammar's numeral-only ITEM_RE. Computed
    from an already-parsed EX-13 run's own nodes/rejected tables -- no re-parse."""
    # accession X: an old-style Form 8-K "ITEM 3. BANKRUPTCY OR RECEIVERSHIP" reproduced
    # and rejected nonmonotone by the 10-K chain -- a clean E2 case with one form only
    rej_rows = [
        dict(accession_number="X", sequence=2, label_canon="ITEM 3", text="ITEM 3. BANKRUPTCY OR RECEIVERSHIP."),
        # accession Y: a 10-Q Item 1 "Financial Statements" caption, accepted (rare
        # but possible if it slots into the chain) -- still E2, from the nodes table
    ]
    node_rows = [
        dict(accession_number="Y", sequence=3, level_kind="item", label_canon="ITEM 1",
            title="FINANCIAL STATEMENTS"),
        # a genuine 10-K Item 1 "Business" heading elsewhere: E1, must not be flagged
        dict(accession_number="Z", sequence=4, level_kind="item", label_canon="ITEM 1", title="BUSINESS"),
    ]
    pq.write_table(pa.Table.from_pylist(rej_rows), tmp_path / "rejected-ex13-1996.parquet")
    pq.write_table(pa.Table.from_pylist(node_rows), tmp_path / "nodes-ex13-1996.parquet")

    flags = other_form_flags(str(tmp_path))
    assert flags[("X", 2)] == "8-K"
    assert flags[("Y", 3)] == "10-Q"
    assert ("Z", 4) not in flags  # E1, not E2 -- no flag at all

    # classify()/matched_form() agree with the E2/E1 split directly
    bankruptcy = "ITEM 3. BANKRUPTCY OR RECEIVERSHIP."
    assert classify("3", bankruptcy) == "E2" and matched_form("3", bankruptcy) == "8-K"
    assert classify("1", "FINANCIAL STATEMENTS") == "E2" and matched_form("1", "FINANCIAL STATEMENTS") == "10-Q"
    assert classify("1", "BUSINESS") == "E1"


def test_other_form_flags_empty_when_no_prior_run(tmp_path):
    assert other_form_flags(str(tmp_path / "does_not_exist")) == {}
