"""Turn 12 B.4 -- the label family (docs/turn12_decisions/a4_label_family.md section 6).

Four independent changes, one test class each:

  1. Form10QGrammar.ITEM_RE gains roman numerals, spelled words and a dot/dash/paren
     suffix separator (including "4T"'s dotted/dashed/parenthesized spellings).
  2. (WITHDRAWN at the Turn 12 B.4 gate, b4_labels_build.md section 5: the bounded
     Regulation-AB alternate on Form10KGrammar returns as a Turn 13 build.)
  3. Form10QGrammar.canonicalize decides an ambiguous item's Part via
     `title_match_end` against both Parts' title tables instead of the retired
     substring hints.
  4. The `multi.ITEM <k>` credit is Part-qualified on form10q (pipeline.py's
     `items_found` and tree.py's `out_of_order_items` claimed set), and a rejected
     combined-label ("Items N and M") candidate can be promoted through that same
     mechanism when every key it would cover is unclaimed and its (already
     range-stripped) title opens with one of their statutory titles.
"""

from types import SimpleNamespace

import pytest

from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.form10k import Form10KGrammar, ITEM_RE as K_ITEM_RE
from edgar_itemize.grammar.form10q import ITEM_RE, Form10QGrammar
from edgar_itemize.pipeline import result_rows
from edgar_itemize.tree import Node, out_of_order_items

Q = Form10QGrammar()
K = Form10KGrammar()


def canon(s):
    m = ITEM_RE.match(s)
    return Q.canonicalize("item", m) if m else None


def rules(s):
    m = ITEM_RE.match(s)
    return Q.label_rules("item", m) if m else []


# --- 1. ITEM_RE: roman, word, suffix_sep (with the 4T special case) ----------------


@pytest.mark.parametrize(
    "text,expected_canon,expected_rule",
    [
        ("Item I. Financial Statements", "ITEM I.1", "gram.item.roman"),
        ("Item I. Legal Proceedings", "ITEM II.1", "gram.item.roman"),
        ("Item III. Defaults Upon Senior Securities", "ITEM II.3", "gram.item.roman"),
        ("ITEM IV. Mine Safety Disclosures", "ITEM II.4", "gram.item.roman"),
        ("Item One. Financial Statements", "ITEM I.1", "gram.item.word"),
        ("ITEM TWO. Management's Discussion and Analysis", "ITEM I.2", "gram.item.word"),
        ("Item Six. Exhibits", "ITEM II.6", "gram.item.word"),
        ("Item 1.A Risk Factors", "ITEM II.1A", "gram.item.suffix_sep"),
        ("Item 1-A Risk Factors", "ITEM II.1A", "gram.item.suffix_sep"),
        ("Item 4.T Controls and Procedures", "ITEM I.4", "gram.item.suffix_sep"),
        ("Item 4-T Controls and Procedures", "ITEM I.4", "gram.item.suffix_sep"),
        ("Item 4(T) Controls and Procedures", "ITEM I.4", "gram.item.suffix_sep"),
    ],
)
def test_roman_word_suffix_sep_positive(text, expected_canon, expected_rule):
    assert canon(text) == expected_canon
    assert expected_rule in rules(text)


def test_4t_dotted_dashed_parenthesized_all_carry_gram_item_4t_too():
    for text in ("Item 4T. Controls and Procedures", "Item 4.T Controls and Procedures",
                 "Item 4-T Controls and Procedures", "Item 4(T) Controls and Procedures"):
        assert canon(text) == "ITEM I.4"
        assert "gram.item.4t" in rules(text)


def test_ordinary_glued_suffix_is_not_tagged_suffix_sep():
    # "Item 1A." (no separator between the digit and the letter) predates this build
    # and must not pick up a tag that means something else happened.
    assert canon("Item 1A. Risk Factors") == "ITEM II.1A"
    assert "gram.item.suffix_sep" not in rules("Item 1A. Risk Factors")
    assert "gram.item.roman" not in rules("Item 1A. Risk Factors")
    assert "gram.item.word" not in rules("Item 1A. Risk Factors")


@pytest.mark.parametrize(
    "text",
    [
        "ITEMONE - BUSINESS",  # glued word, not a label
        "Item Seven. Exhibits",  # SEVEN is out of the 10-Q's ONE..SIX range
        "Item VII. Exhibits",  # roman numeral out of the 10-Q's I..IV range
    ],
)
def test_roman_word_out_of_range_do_not_match(text):
    assert ITEM_RE.match(text) is None


def test_roman_in_cross_reference_sentence_gets_no_title_match():
    """"Item I of this Form 10-Q is incorporated..." tokenises (the label itself is
    real) but must not carry `gram.item.title_match`, which is what the pipeline's
    prose/xref penalties and `out_of_order_items`'s admission gate both key on."""
    m = ITEM_RE.match("Item I of this Form 10-Q is incorporated herein by reference")
    assert m is not None
    c = Q.canonicalize("item", m)
    assert c == "ITEM ?.1"  # ambiguous: no statutory title on the line to decide the Part
    assert Q.title_matches("item", c, m.group("title")) is False


def test_lowercase_roman_in_prose_enumeration_does_not_look_like_a_heading():
    """"(items i and ii referred to as...)" -- a raw-line match on "item i" mid-sentence
    is not block-initial in the real pipeline (candidates.py only tries a block's own
    first line/next physical line), but even matched in isolation it must not resolve
    to a titled item: no statutory title follows it on the line."""
    m = ITEM_RE.match("item i and ii referred to as the Reporting Items")
    assert m is not None
    c = Q.canonicalize("item", m)
    assert Q.title_matches("item", c, m.group("title")) is False


def test_table_row_with_leader_gets_no_title_match_waiver():
    """A TOC-shaped row (dot leader + page number) is still index evidence regardless
    of the label shape -- this is candidates.py's own toc_hint mechanism, unaffected
    by the widened regex; confirmed here for a roman-numeral row."""
    from edgar_itemize.blocks import Block
    from edgar_itemize.candidates import find_candidates

    text = "Item I. Financial Statements . . . . . . . . . . . 3"
    b = Block(idx=0, text=text, raw_start=0, raw_end=len(text), norm_start=0, norm_end=len(text),
              lines=(text,), line_raw_starts=(0,), line_raw_ends=(len(text),))
    cands = find_candidates([b], Q, era="text")
    assert len(cands) == 1
    c = cands[0]
    assert c.label_canon == "ITEM I.1"
    assert "toc.leader_or_pageno" in c.rule_ids
    assert "gram.item.title_match" not in c.rule_ids  # toc_hint blocks the waiver


# --- 2. Form10KGrammar's bounded Regulation-AB alternate: WITHDRAWN (Turn 12 B.4 gate,
# docs/turn12_decisions/b4_labels_build.md section 5) -- the level, its title table, the
# synthetic PART V and the gram.item.regab tag were removed from form10k.py after the 10-K
# corpus read (1,279 real Item 15/16 headings displaced, 329 anchors moved onto contents
# rows, 201 back_start boundaries pushed into the exhibits). It returns as a Turn 13 build
# with its own run under PART V; its tests were deleted with it. The shadow-risk finding
# below (section 2.3) is about the ORDINARY pattern and stays.


def test_ordinary_item_re_never_matches_a_four_digit_regab_label():
    # the shadow-risk finding this build relies on (section 2.3): `\d{1,2}` plus the
    # `(?![\d\w])` lookahead structurally cannot reach a third digit.
    assert K_ITEM_RE.match("ITEM 1112. Significant Obligor(s) of Pool Assets") is None


# --- 3. canonicalize by title table (retires the substring hints) -----------------


def test_ambiguous_key_decided_by_the_carrying_items_own_title():
    # Item 3's title in both PART1/PART2's shared key space: the "market risk" item's
    # own statutory wording (unwidened -- the market-risk phrase ALONE is a separate,
    # unshipped change) already opens with "quantitative and qualitative disclosures".
    assert canon("Item 3. Quantitative and Qualitative Disclosures About Market Risk") == "ITEM I.3"
    assert canon("Item 1A. Risk Factors") == "ITEM II.1A"


def test_market_risk_widening_is_not_shipped():
    # the bare phrase "Market Risk" with none of "quantitative"/"qualitative" must NOT
    # open-match I.3's title table in this build -- confirms the widening named in
    # section 6.3 was deliberately left out.
    assert Q.title_match_end("item", "ITEM I.3", "Market Risk. The Company is exposed to...") is None


def test_ambiguous_key_with_neither_title_falls_through_to_marker():
    # key "2" exists in both Parts (MD&A / Unregistered Sales); neither title opens
    # this line, so the ambiguous marker stands exactly as it did before this build.
    assert canon("Item 2. Some Unrelated Heading Text") == "ITEM ?.2"


def test_unambiguous_key_resolves_even_with_no_title_on_the_line():
    # "1A" only exists in Part II -- a bare label with the title on the next block
    # (no text to match) must still resolve directly, not fall to the ambiguous marker.
    assert canon("Item 1A.") == "ITEM II.1A"
    assert canon("Item 5.") == "ITEM II.5"


# --- 4. multi.ITEM <k> Part qualification + the combined-label credit -------------


def _fake_10q_result(nodes):
    return SimpleNamespace(
        accession="0000000000-00-000000", cik="1",
        doc=SimpleNamespace(sequence=1, type="10-Q", text_start=0, text_end=10_000),
        grammar="form10q", profile=SimpleNamespace(era="text", publisher=None, agent_cik=None, signals={}),
        ibr={}, paths={"_bounds": {"front_end": 0, "back_start": 10_000}}, nodes=nodes,
        candidates=[], toc=[], profile_levels=[], normalized_text="", blocks=[], rejected=[],
    )


def _item_node(node_id, label_canon, rule_ids):
    return Node(node_id=node_id, parent_id=0, depth=2, level_kind="item", label_canon=label_canon,
                label_raw=label_canon, title="T", raw_start=0, raw_end=10, head_raw_start=0, head_raw_end=10,
                norm_start=0, norm_end=10, order_key=0, confidence=0.9, rule_ids=list(rule_ids))


def test_items_found_part_qualifies_the_multi_credit():
    """A10, section 6.4: the carrying node is "Items 2 and 3." accepted as ITEM I.2;
    the bare "multi.ITEM 3" rule id must surface as the Part-qualified "ITEM I.3", not
    the unqualified "ITEM 3" that no completeness table can ever match."""
    n = _item_node(1, "ITEM I.2", ["lbl.item", "lbl.item.multi", "multi.ITEM 3"])
    r = _fake_10q_result([n])
    nodes, doc, _ = result_rows(r, 2020, keep_text=False)
    assert doc["items_found"] == ["ITEM I.2", "ITEM I.3"]


def test_items_found_unqualified_off_form10q():
    n = _item_node(1, "ITEM 2", ["lbl.item.multi", "multi.ITEM 3"])
    r = _fake_10q_result([n])
    r.grammar = "form10k"
    nodes, doc, _ = result_rows(r, 2020, keep_text=False)
    assert doc["items_found"] == ["ITEM 2", "ITEM 3"]


def _multi_cand(label_canon, title, rule_ids, head_raw_start=1000):
    return Candidate(block_idx=0, kind="item", label_raw=label_canon, label_canon=label_canon, title=title,
                      score=0.5, rule_ids=list(rule_ids), order_key=0,
                      head_raw_start=head_raw_start, head_raw_end=head_raw_start + 10, multi=True)


def test_combined_label_promoted_when_all_covered_keys_are_unclaimed():
    c = _multi_cand("ITEM I.2", "Management's Discussion and Analysis",
                     ["lbl.item", "lbl.item.multi", "multi.ITEM 3"])
    cands = [c]
    extra = out_of_order_items(cands, [], [0], set(), Q)
    assert extra == [0]
    assert "seq.out_of_order_multi" in cands[0].rule_ids
    assert "gram.item.title_match" not in cands[0].rule_ids  # promoted via the multi path, not the ordinary one


def test_combined_label_not_promoted_when_a_covered_key_is_already_claimed():
    chosen = _multi_cand("ITEM I.3", "Quantitative and Qualitative Disclosures About Market Risk",
                          [], head_raw_start=500)
    dropped = _multi_cand("ITEM I.2", "Management's Discussion and Analysis",
                           ["lbl.item", "lbl.item.multi", "multi.ITEM 3"], head_raw_start=1000)
    cands = [chosen, dropped]
    extra = out_of_order_items(cands, [0], [1], set(), Q)
    assert extra == []  # "ITEM I.3" is already claimed, so ALL-unclaimed fails


def test_combined_label_not_promoted_without_a_title_match_on_either_key():
    c = _multi_cand("ITEM I.2", "Some Unrelated Text With No Statutory Title",
                     ["lbl.item", "lbl.item.multi", "multi.ITEM 3"])
    cands = [c]
    extra = out_of_order_items(cands, [], [0], set(), Q)
    assert extra == []


def test_combined_label_credit_scoped_to_form10q_only():
    c = _multi_cand("ITEM 2", "Some Text", ["lbl.item", "lbl.item.multi", "multi.ITEM 3"])
    cands = [c]
    extra = out_of_order_items(cands, [], [0], set(), K)  # form10k grammar
    assert extra == []


# --- 5. Turn 12 B.4 re-gate: the two roots of the first 10-Q gate read (memo section 6) ---
# R-B4-5: a lettered sub-caption reached through the widened separator is refused as a
# SUFFIX, not as an item -- "Item 6. (a) Exhibits" is Item 6, exactly as before Turn 12.
@pytest.mark.parametrize(
    "text, expected_canon",
    [
        ("Item 6. (a) Exhibits", "ITEM II.6"),
        ("ITEM 6.(B) - REPORTS ON FORM 8-K", "ITEM II.6"),
        ("Item 6 (b.) No reports on Form 8-K were filed", "ITEM II.6"),
        ("ITEM 2 (C). CHANGES IN SECURITIES AND USE OF PROCEEDS", "ITEM II.2"),
        ("Item 2. B. Other Developments", "ITEM ?.2"),
    ],
)
def test_paren_subcaption_refuses_the_suffix_not_the_item(text, expected_canon):
    assert canon(text) == expected_canon
    assert "gram.item.suffix_refused" in rules(text)
    # the valid suffixed keys still read as suffixed labels through the same separator
    assert canon("Item 1.A Risk Factors") == "ITEM II.1A"
    assert canon("Item 4(T). Controls and Procedures") == "ITEM I.4"
    # a suffix that is glued (no separator) to an invalid key is still refused outright
    assert canon("Item 6A. Exhibits") is None


# R-B4-6: the pre-Turn-12 substring hints are the second tier of the Part decision, between
# the anchored statutory table and the tree's positional fallback -- a title the anchored
# table misses but a hint catches resolves to its own Part and never reaches "ITEM ?.k"
# (which the tree resolves against the contents page's live PART II row).
@pytest.mark.parametrize(
    "text, expected_canon",
    [
        ("Item 3. Controls and Procedures", "ITEM I.3"),                       # 10-QSB Item 3
        ("Item 3. Quantitative and Qualititive Disclosure About Market Risk", "ITEM I.3"),
        ("Item 2. Managements' Discussion and Analysis of Financial Condition", "ITEM I.2"),
        ("Item 4. Submission of Matters to a Vote of Security-Holders", "ITEM II.4"),
        ("Item 3. Defaults by the Corporation on its Senior Securities", "ITEM II.3"),
    ],
)
def test_substring_hint_is_the_second_tier_before_position(text, expected_canon):
    assert canon(text) == expected_canon
    assert "gram.item.part_hint" in rules(text)
    # the anchored table still decides first when it can, without the hint tag
    assert canon("Item 3. Quantitative and Qualitative Disclosures About Market Risk") == "ITEM I.3"
    assert "gram.item.part_hint" not in rules("Item 3. Quantitative and Qualitative Disclosures About Market Risk")
    # a title neither tier touches still falls through to the positional marker
    assert canon("Item 3. Not applicable") == "ITEM ?.3"

