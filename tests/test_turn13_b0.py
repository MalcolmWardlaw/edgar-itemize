"""Turn 13 B.0 -- ready-now leads from Turn 12 B.4 (docs/TURN12_REPORT.md section 7,
docs/turn12_decisions/b4_labels_build.md sections 5.2/5.8/6/6.1/6.2), no probe:

  1. Form10KGrammar._ITEM_TITLE_RE["15"] widened to the singular "Exhibit and ..."
     (983 of the 1,058 lost ITEM 15 rows in the Reg-AB read never matched the
     plural-only `exhibits\\b`) and ["16"] to the en dash "Form 10-K Summary"
     (125 of the 221 lost ITEM 16 rows never matched the hyphen-only `10-?k`).
     Both feed `out_of_order_items`'s title-match admission gate; TURN12_REPORT.md
     line 1350-1353.
  2. candidates.py's `_try_match`, gated on `gram.item.suffix_refused` (Form10QGrammar
     only -- Form10KGrammar never tags it, so this is inert for the 10-K):
     - R-B4-5's 63-row `low_score` residual: the title is re-cut directly against the
       raw physical line (`first`) instead of sliced out of the normalized `text` at an
       offset native to `text`, which used to leak the refused "(a)" (and the run of
       typewriter spaces around it) into the title and read as lowercase prose.
  R-B4-7 (a line-level demotion of suffix-refused lines with an empty or
  exhibit-reference title) was built here and withdrawn after the 10-Q gate
  (docs/turn13_decisions/b0_ready.md): it deleted the only Item 6 heading in 182
  documents. It goes to Turn 14 as a sequence-level rule.
"""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import ITEM_RE, Form10QGrammar

K = Form10KGrammar()
Q = Form10QGrammar()


# --- 1. Form10KGrammar._ITEM_TITLE_RE["15"]/["16"] widened -----------------------------


def test_item_15_title_match_accepts_singular_exhibit_and():
    assert K.title_matches("item", "ITEM 15", "Exhibit and Financial Statement Schedules")
    assert K.title_matches("item", "ITEM 15", "Exhibit and Financial Statement Schedules.")


def test_item_15_title_match_still_accepts_plural_exhibits():
    # pre-existing behaviour must survive the widening
    assert K.title_matches("item", "ITEM 15", "Exhibits and Financial Statement Schedules.")
    assert K.title_matches("item", "ITEM 15", "Financial Statement Schedules")


def test_item_15_title_match_word_boundary_still_excludes_unrelated_prose():
    # "Exhibited" must not satisfy the singular/plural alternation via a partial match
    assert not K.title_matches("item", "ITEM 15", "Exhibited materials are on file with the SEC.")


def test_item_16_title_match_accepts_en_dash_and_em_dash():
    assert K.title_matches("item", "ITEM 16", "Form 10–K Summary")  # en dash
    assert K.title_matches("item", "ITEM 16", "Form 10—K Summary")  # em dash


def test_item_16_title_match_still_accepts_hyphen_and_no_separator():
    # pre-existing behaviour must survive the widening
    assert K.title_matches("item", "ITEM 16", "Form 10-K Summary")
    assert K.title_matches("item", "ITEM 16", "Form 10K Summary.")


def test_item_15_and_16_are_form10k_only_keys():
    # Form10QGrammar's own title table (test_form10q_titles.py) has no "15"/"16" keys at
    # all -- the 10-Q only goes up to Item 6 -- so this build's widening of
    # Form10KGrammar._ITEM_TITLE_RE cannot reach the 10-Q grammar; the 10-Q gate is
    # expected to show no change from this half of the build.
    assert Q.title_matches("item", "ITEM I.5", "Exhibit and Financial Statement Schedules") is False


# --- 2. candidates.py: the suffix_refused title re-cut (R-B4-5) -----------------------


def _block(raw_first: str, norm_first: str) -> Block:
    """A single-line text-era block whose normalized `text` collapses the raw line's
    internal whitespace, the way the real normalizer does (candidates.py's own
    docstring note: 'the text era collapses runs of spaces inside a line') -- the shape
    that exposed the leaked-suffix bug (raw typewriter spacing, e.g. 'Item 6.    (a)
    Exhibits', collapsed to single spaces in `text` but not in `lines`)."""
    return Block(
        idx=0, text=norm_first, raw_start=0, raw_end=len(raw_first),
        norm_start=0, norm_end=len(norm_first),
        lines=(raw_first,), line_raw_starts=(0,), line_raw_ends=(len(raw_first),),
    )


def test_suffix_refused_title_recut_against_raw_line_not_leaked_from_normalized_offset():
    raw = "Item 6.    (a)      Exhibits and Reports on Form 8-K."
    norm = "Item 6. (a) Exhibits and Reports on Form 8-K."
    # sanity: the naive old approach (slice `first` at `text`'s title offset) really
    # would have leaked the refused "(a)" and its trailing spaces into the title --
    # confirms the test fixture actually exercises the bug this build fixes.
    m_norm = ITEM_RE.match(norm)
    assert m_norm is not None
    naive = raw[m_norm.start("title"):]
    assert naive.lstrip().startswith("a)")

    b = _block(raw, norm)
    cands = find_candidates([b], Q, era="text")
    assert len(cands) == 1
    c = cands[0]
    assert c.label_canon == "ITEM II.6"
    assert "gram.item.suffix_refused" in c.rule_ids
    assert c.title == "Exhibits and Reports on Form 8-K"
    assert "rej.lowercase_title" not in c.rule_ids
    assert "gram.item.title_match" in c.rule_ids


def test_suffix_refused_none_sub_caption_title_recut():
    # R-B4-5's own 63-row low_score residual, memo section (9): "Item 6 (a) Exhibits -
    # None (b) Reports - None" style sub-caption where the parenthesis is consumed as
    # the separator; simplified here to the bare "(a) None" shape that empties a title.
    raw = "Item 6 (a)      None"
    norm = "Item 6 (a) None"
    b = _block(raw, norm)
    cands = find_candidates([b], Q, era="text")
    assert len(cands) == 1
    c = cands[0]
    assert c.label_canon == "ITEM II.6"
    assert "gram.item.suffix_refused" in c.rule_ids
    assert c.title == "None"


def test_suffix_refused_plural_exhibits_sub_caption_keeps_title_match():
    # the real plural sub-caption heading ("Item 6. (a) Exhibits", 42 of the 63-row
    # residual are headings) keeps its title evidence after the re-cut.
    raw = "Item 6. (a) Exhibits"
    norm = raw
    b = _block(raw, norm)
    cands = find_candidates([b], Q, era="text")
    assert len(cands) == 1
    c = cands[0]
    assert c.label_canon == "ITEM II.6"
    assert "gram.item.suffix_refused" in c.rule_ids
    assert "gram.item.title_match" in c.rule_ids


def test_suffix_refused_is_inert_on_form10k():
    # Form10KGrammar never tags gram.item.suffix_refused (it has no `label_rules` case
    # for it), so the candidates.py re-cut is dormant there -- confirms the 10-K
    # gate should show no change from this half of the build.
    from edgar_itemize.grammar.form10k import ITEM_RE as K_ITEM_RE
    m = K_ITEM_RE.match("Item 6(a) - Exhibit 10 - Material Contracts")
    assert m is not None
    assert "gram.item.suffix_refused" not in Form10KGrammar.label_rules("item", m)
