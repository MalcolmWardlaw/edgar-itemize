"""Turn 4 — grammar recall.

Each test names the rule id it guards and the judged window class it recovers
(runs/judge/turn4-consensus.jsonl, scored with scripts/judge_reeval.py).
"""

import pytest

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import ITEM_RE, Form10KGrammar
from edgar_itemize.pipeline import grammar_for
from edgar_itemize.sgml import DocumentBlock, conformed_type

G = Form10KGrammar()


def canon(s):
    m = ITEM_RE.match(s)
    return G.canonicalize("item", m) if m else None


def rules(s):
    m = ITEM_RE.match(s)
    return G.label_rules("item", m) if m else []


def blk(i, text, start, **kw):
    lines = tuple(text.split("\n"))
    ends, pos = [], start
    starts = []
    for ln in lines:
        starts.append(pos)
        pos += len(ln)
        ends.append(pos)
        pos += 1
    return Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start,
                 norm_end=start + len(text), lines=lines, line_raw_starts=tuple(starts),
                 line_raw_ends=tuple(ends), **kw)


# --- gram.item.word: spelled-ordinal item numbers -------------------------------
# 99 judged positives, 0 judged negatives (all e3 absent_exotic_form / beyond_core
# _extension / unclassified_core, era=text), e.g. 0000002601-95-000017 "ITEM ONE - BUSINESS".
@pytest.mark.parametrize(
    "text,expected",
    [
        ("ITEM ONE - BUSINESS", "ITEM 1"),
        ("ITEM TWO - PROPERTIES", "ITEM 2"),
        ("Item Seven. Management's Discussion and Analysis", "ITEM 7"),
        ("ITEM SIXTEEN. FORM 10-K SUMMARY", "ITEM 16"),
        ("ITEM FOURTEEN - EXHIBITS", "ITEM 14"),
        # not an item label
        ("ITEMONE - BUSINESS", None),
        ("ITEM SEVENTEEN - SOMETHING", None),
        ("Itemized deductions", None),
    ],
)
def test_spelled_ordinal_items(text, expected):
    assert canon(text) == expected


def test_spelled_ordinal_carries_its_rule_id():
    assert rules("ITEM ONE - BUSINESS") == ["gram.item.word"]
    assert rules("ITEM I. BUSINESS") == ["gram.item.roman"]
    assert rules("ITEM 1. BUSINESS") == []


def test_spelled_ordinal_becomes_a_candidate():
    blocks = [blk(0, "ITEM ONE - BUSINESS", 100), blk(1, "ITEM TWO - PROPERTIES", 400)]
    cands = find_candidates(blocks, G, era="text")
    assert [(c.label_canon, "gram.item.word" in c.rule_ids) for c in cands] == [("ITEM 1", True), ("ITEM 2", True)]


# --- pos.inline_after_part: comma between the part numeral and ITEM ---------------
# 85 judged positives recovered (E3 0000771726-05-000116, Dow 0000029915-09-000016).
@pytest.mark.parametrize("text", [
    "PART I, Item 1B. Unresolved Staff Comments.",
    "PART II, Item 7A. Quantitative and Qualitative Disclosures About Market Risk.",
    "Part III,Item 9.Directors, Executive Officers",
])
def test_part_then_item_with_comma(text):
    cands = find_candidates([blk(0, text, 100)], G, era="html_early")
    kinds = {c.kind: c for c in cands}
    assert "part" in kinds and "item" in kinds
    assert "pos.inline_after_part" in kinds["item"].rule_ids


def test_part_alone_still_emits_no_item():
    (c,) = find_candidates([blk(0, "PART I", 100)], G, era="html_early")
    assert c.kind == "part"


# --- gram.item.multi_repeat / gram.item.multi_singular: combined headings ---------
# 58 judged positives recovered; the construct is 78 positives / 27 negatives in the
# bank (E3 0001028269-00-000114, 0000702165-13-000042, 0000950129-03-001049).
@pytest.mark.parametrize("text,label,covers", [
    ("ITEM 1. BUSINESS AND ITEM 2. PROPERTIES", "ITEM 1", ["2"]),
    ("Item 1. Business and Item 2. Properties", "ITEM 1", ["2"]),
    ("ITEM 1. and ITEM 2. BUSINESS AND PROPERTIES", "ITEM 1", ["2"]),
    ("ITEM 1 and ITEM 2 - BUSINESS and PROPERTIES", "ITEM 1", ["2"]),
    ("ITEM 1. BUSINESS; ITEM 2. PROPERTIES; AND ITEM 3. LEGAL PROCEEDINGS.", "ITEM 1", ["2", "3"]),
    ("Item 7 and Item 7A. Management's Discussion", "ITEM 7", ["7A"]),
    # bare-number range, plural and singular
    ("Items 1 and 2. Business and Properties", "ITEM 1", ["2"]),
    ("Item 1 and 2. Business and Properties", "ITEM 1", ["2"]),
    ("ITEMS 10 THROUGH 13", "ITEM 10", ["11", "12", "13"]),
])
def test_combined_headings_credit_every_item(text, label, covers):
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert c.label_canon == label and c.multi
    assert [r.split(" ", 1)[1] for r in c.rule_ids if r.startswith("multi.ITEM ")] == covers


@pytest.mark.parametrize("text", [
    # a long comma-separated run of labels is a cross-reference list, not a heading
    "Item 2, Item 3, Item 5, Item 6, Item 7, Item 8, Item 13",
    # numeric noise: the "range" must be a connector followed by an item number
    "ITEM 3,165,082 2,916,286 467,474",
    "Item 1,150.52 - -",
])
def test_cross_reference_runs_get_no_multi_credit(text):
    cands = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert not any(r.startswith("multi.ITEM ") for c in cands for r in c.rule_ids)


# --- gram.item.title_match: run-in headings whose section body starts on the line ---
# 310 judged positives recovered (E3 rej_low_score, e.g. 0001104659-05-010273 and
# 0000076334-12-000064 "ITEM 8. Financial Statements and Supplementary Data. The
# information set forth ... is incorporated herein by reference.").
def test_title_match_waives_the_prose_and_xref_penalties():
    text = ("ITEM 8. Financial Statements and Supplementary Data. The information set forth on pages "
            "13-14 to 13-41 of Exhibit 13 hereto is incorporated herein by reference. It is a long line.")
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="html_publisher") if x.kind == "item"]
    assert "gram.item.title_match" in c.rule_ids
    assert not {"rej.xref_phrase", "rej.long_line", "rej.prose"} & set(c.rule_ids)
    assert c.score >= 0.35  # tree.build_tree min_score


@pytest.mark.parametrize("text", [
    "Item 8 of this report on Form 10-K for the year ended December 31, 1998.",
    'Item 6 is incorporated by reference to page 20 of items captioned "Selected Data".',
])
def test_cross_reference_without_the_statutory_title_stays_penalized(text):
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert "gram.item.title_match" not in c.rule_ids
    assert c.score < 0.35


@pytest.mark.parametrize("text", [
    "Item 6. Selected Financial Data. . . . . . . . . . . . . . . . . . 7",
    "Item 10. Directors and Executive Officers of the Registrant 7",
])
def test_index_evidence_blocks_the_title_match_waiver(text):
    # an index row opens with the statutory title too; dot leaders / a trailing page
    # number keep it out of the waiver so the chain still prefers the body copy
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert "gram.item.title_match" not in c.rule_ids and "toc.leader_or_pageno" in c.rule_ids


@pytest.mark.parametrize("text,label", [
    ("item 3. legal proceedings", "ITEM 3"),
    ("item 7. management's discussion and analysis of financial condition", "ITEM 7"),
    ("Item 5: market for registrant's common equity, related stockholder matters", "ITEM 5"),
])
def test_lowercase_statutory_title_is_not_prose(text, label):
    # 34 judged positives (0001493152-20-005159, 0001144204-12-017368): some filers
    # typeset the whole heading in lower case, which rej.lowercase_title read as prose
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="html_publisher") if x.kind == "item"]
    assert c.label_canon == label and "rej.lowercase_title" not in c.rule_ids and c.score >= 0.35


def test_lowercase_non_statutory_title_still_penalized():
    (c,) = [x for x in find_candidates([blk(0, "Item 5 and Item 7 of our revolving credit facility.", 100)], G, era="text") if x.kind == "item"]
    assert "rej.lowercase_title" in c.rule_ids and c.score < 0.35


# --- later-line scan: no 40-line cap, and the text era is scanned too ----------------
# 4 judged positives (0000002024-02-000008 "Item 2. Properties" is line 265 of a
# 268-line html_early block; "Item 7a." is line 121 of 141).
def test_label_past_the_old_forty_line_cap():
    lines = ["filler paragraph line %d" % i for i in range(120)]
    lines[100] = "Item 2. Properties"
    (c,) = [x for x in find_candidates([blk(0, "\n".join(lines), 100)], G, era="html_early") if x.kind == "item"]
    assert c.label_canon == "ITEM 2" and "pos.line100" in c.rule_ids


def test_text_era_later_line_is_scanned():
    text = "Some closing prose of the previous item.\nITEM 3. LEGAL PROCEEDINGS\nNone."
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert c.label_canon == "ITEM 3" and c.norm_start == 100 + len(text.split("\n")[0]) + 1


def test_later_line_norm_start_tracks_the_line():
    text = "aa\nbb\nItem 5. Market for Registrant's Common Equity"
    (c,) = [x for x in find_candidates([blk(0, text, 0)], G, era="html_early") if x.kind == "item"]
    assert c.norm_start == 6


def test_later_line_norm_start_uses_the_normalized_line_length():
    # b.lines are the raw (uncollapsed) lines in the text era while the normalized text
    # collapses internal runs of spaces; summing b.lines lengths mis-places norm_start
    b = Block(idx=0, text="a  b\nItem 3. Legal Proceedings", raw_start=0, raw_end=30, norm_start=0, norm_end=30,
              lines=("a  b", "Item 3. Legal Proceedings"), line_raw_starts=(0, 5), line_raw_ends=(4, 30))
    b.text = "a b\nItem 3. Legal Proceedings"  # what the text normalizer actually emits
    (c,) = [x for x in find_candidates([b], G, era="text") if x.kind == "item"]
    assert c.norm_start == 4  # len("a b") + 1, not len("a  b") + 1


# --- gram.form_from_header: EDGAR's CONFORMED SUBMISSION TYPE beats a wrong <TYPE> ---
# 46 judged positives over 6 filings (0000921111-99-000001, 0000943551-99-000001,
# 0001031002-02-000026, 0000946489-99-000004, 0000813920-00-000001,
# 0000928658-97-000001): 10-K submissions whose primary document is tagged 10-Q.
def _doc(t):
    return DocumentBlock(sequence=1, type=t, filename=None, description=None, is_html=False,
                         doc_start=0, doc_end=100, text_start=0, text_end=100)


def test_conformed_type_from_header():
    hdr = "ACCESSION NUMBER:\t0001031002-02-000026\nCONFORMED SUBMISSION TYPE:\t10-K\nPUBLIC DOCUMENT COUNT:\t3\n"
    assert conformed_type(hdr) == "10-K"
    assert conformed_type("no header here") == ""


@pytest.mark.parametrize("doc_type,submission_type,want", [
    ("10-Q", "10-K", "form10k"),      # the bug: filer tagged the annual report 10-Q
    ("10-Q", "10-K405", "form10k"),
    ("10-Q", "10-Q", "form10q"),      # a real 10-Q is untouched
    ("10-Q", "", "form10q"),          # no header: fall back to the document tag
    ("10-K", "10-K", "form10k"),
    ("EX-10", "10-K", "contract"),    # exhibits still route by the document tag
    ("EX-13", "10-K", "contract"),
])
def test_grammar_routed_by_submission_type(doc_type, submission_type, want):
    assert grammar_for(_doc(doc_type), submission_type).name == want


def test_title_match_keeps_the_clean_head_line_bonus():
    # regression: waiving the penalties must not also drop pos.head_line_only, which
    # weights the body chain against a vetoed TOC region (0000839945-05-000001)
    text = ("Item 2. PROPERTIES\nThe Partnership's executive and administrative offices are\n"
            "located in New York. They are adequate for its needs.\nNo other properties are held.")
    (c,) = [x for x in find_candidates([blk(0, text, 100)], G, era="text") if x.kind == "item"]
    assert "gram.item.title_match" in c.rule_ids and "pos.head_line_only" in c.rule_ids


# --- later-line scan: a label wrapped across OCR pseudo-lines ---------------------

def test_later_line_scan_sees_label_wrapped_onto_next_pseudo_line():
    """An OCR page blob (A6) puts "SECTION" and "1. Definitions" on separate
    pseudo-lines; the single-line prefilter must look one line ahead or the whole
    section chain of a scanned exhibit disappears (integration diff ex10 v9 -> v10)."""
    from edgar_itemize.grammar.contract import ContractGrammar
    text = "THIS AGREEMENT is made\nSECTION\n1. Definitions\nAs used herein the following terms shall have the meanings\nSECTION\n2. The Loans\nSubject to the terms hereof each Lender agrees"
    cands = find_candidates([blk(0, text, 0)], ContractGrammar(), era="image_text")
    labels = sorted(c.label_canon for c in cands if c.label_canon)
    assert "SECTION 1" in labels and "SECTION 2" in labels
