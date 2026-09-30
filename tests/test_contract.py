import pytest

from edgar_itemize.grammar.contract import ARTICLE_RE, CLAUSE_RE, SECTION_RE, ContractGrammar, roman_to_int

G = ContractGrammar()


@pytest.mark.parametrize("text,expected", [
    ("ARTICLE I", "ARTICLE 1"), ("ARTICLE VII DEFAULTS", "ARTICLE 7"), ("Article 2. The Credits", "ARTICLE 2"),
    ("ARTICLE ONE", "ARTICLE 1"), ("Articles of incorporation", None), ("ARTICLE 99", None),
])
def test_article(text, expected):
    m = ARTICLE_RE.match(text)
    assert (G.canonicalize("article", m) if m else None) == expected


@pytest.mark.parametrize("text,expected", [
    ("Section 1.01. Defined Terms.", "SECTION 1.01"), ("SECTION 2.1 Commitments", "SECTION 2.01"),
    ("7.01 Events of Default.", "SECTION 7.01"), ("1.5% per annum", None), ("Section 1.Defined Terms. As used", "SECTION 1"),
    ("1. DEFINITIONS. As used in this", "SECTION 1"), ("3. the borrower shall", None), ("2.5 million dollars", "SECTION 2.05"),
    ("Section 10.11(a)", None), ("Section 3.02.1 Sub", "SECTION 3.02.1"),
])
def test_section(text, expected):
    m = SECTION_RE.match(text)
    assert (G.canonicalize("section", m) if m else None) == expected


@pytest.mark.parametrize("text,expected", [
    ("(a) The Borrower", "(a)"), ("(iv) any", "(iv)"), ("(A) with", "(A)"), ("(1) first", "(1)"), ("(xii) x", "(xii)"),
    ("(a)Consolidated Net Income", "(a)"), ("a) no", None), ("(2004) was", None),
])
def test_clause(text, expected):
    m = CLAUSE_RE.match(text)
    assert (G.canonicalize("clause", m) if m else None) == expected


def test_roman():
    assert roman_to_int("XIV") == 14 and roman_to_int("iv") == 4 and roman_to_int("IIII") is None


def test_clause_values():
    assert ContractGrammar.clause_value("i", "alpha") == 9 and ContractGrammar.clause_value("i", "roman") == 1
    assert ContractGrammar.clause_value("aa", "alpha") == 27 and ContractGrammar.clause_value("ab", "alpha") is None


def test_roman_with_d_and_m_does_not_crash():
    assert roman_to_int("d") == 500 and roman_to_int("MCD") == 1400
    assert ContractGrammar.clause_value("d", "roman") == 500


def _cands(*texts, era="text"):
    """find_candidates over one hand-built block per text (each block is its own line)."""
    from edgar_itemize.blocks import Block, caps_ratio
    from edgar_itemize.candidates import find_candidates

    blocks = []
    p = 0
    for i, t in enumerate(texts):
        ls = tuple(t.split("\n"))
        blocks.append(Block(idx=i, text=t, raw_start=p, raw_end=p + len(t), norm_start=p,
                            norm_end=p + len(t), lines=ls, caps_ratio=caps_ratio(ls[0]),
                            line_raw_starts=tuple(range(len(ls))), line_raw_ends=tuple(range(len(ls)))))
        p += len(t) + 2
    return find_candidates(blocks, ContractGrammar(), era=era)


@pytest.mark.parametrize("line", [
    "1.00 - Eurocurrency Reserve Requirements",
    "1.00 – Eurodollar Reserve Percentage",          # en dash
    "1.00 — Eurocurrency Reserve Requirements",      # em dash
    "1.00-Eurodollar Reserve Percentage",            # no spaces
    "1.00 - - Eurocurrency Reserve Requirements",    # the bar drawn twice
    "(1.00 - LIBOR Reserve Percentage)",             # parenthesised
    "1.00 - Euro-Rate Reserve Percentage",
    "1.00 - C/D Reserve Percentage",
    "1.00 - Canadian Eurodollar Reserve Percentage",
    "1.00 - Reserve Percentage Rate",
    "1.00 - Eurocurrency Reserve Requirements.",
    "5.00 - Statutory Reserve Rate",
    "1.00 minus the Euro-Rate Reserve Percentage",
])
def test_the_eurodollar_fractions_denominator_is_not_a_section(line):
    """rej.formula_denominator (Turn 9 B.1b): the normalizers drop the rule that draws the
    fraction bar, so the denominator of

        Eurodollar Base Rate
        1.00 - Eurocurrency Reserve Requirements

    arrives as a line of its own and reads as `SECTION 1.00`.  It is a candidate the chain
    always threw away until B.1's section restart promoted it to a chain head (468 of
    6,708 restart heads in runs/full_ex10_v16), so it is condemned at the candidate."""
    c = next(c for c in _cands("Eurodollar Base Rate", line) if c.kind == "section")
    assert "rej.formula_denominator" in c.rule_ids
    assert c.score < 0.35  # below build_contract_tree's min_score: not a live candidate


@pytest.mark.parametrize("line", [
    "1.00 AFFIRMATIVE COVENANTS",                    # a real n.00-numbered heading
    "1.00 Reserve Requirements",                     # ... even one about reserves: no minus sign
    "SECTION 1.00 - Reserve Requirements. The Borrower shall maintain reserves as required "
    "by the Board of Governors of the Federal Reserve System in accordance with Regulation D.",
    "2.00:1.00",                                     # a covenant-ratio table cell
    "1.01 - Eurocurrency Reserve Requirements",      # not an .00 ordinal
    "1.00 - Applicable Margin",                      # a minus, but not the reserve family
])
def test_the_formula_guard_leaves_everything_else_alone(line):
    for c in _cands("Eurodollar Base Rate", line):
        assert "rej.formula_denominator" not in c.rule_ids


def test_image_text_blob_splits_into_pseudo_lines_with_offsets():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.classify import Profile
    from edgar_itemize.grammar.contract import ContractGrammar
    from edgar_itemize.normalize_html import html_to_blocks

    blob = ("ARTICLE I   DEFINITIONS   SECTION 1.01. Defined Terms.   As used in this Agreement the following terms have the meanings set forth below.   "
            "SECTION 1.02. Other Definitional Provisions.   The definitions of terms herein shall apply equally to the singular and plural forms.")
    html = f'<div><img src="p1.jpg"><font style="font-size:1pt;color:white">{blob}</font></div>'
    prof = Profile(era="image_text", publisher="other", agent_cik="0")
    blocks, norm, _ = html_to_blocks(html, 0, prof)
    b = next(x for x in blocks if x.kind == "para")
    assert len(b.lines) == 6 and b.lines[2].startswith("SECTION 1.01")
    assert html[b.line_raw_starts[2]:b.line_raw_starts[2] + 12] == "SECTION 1.01"
    cands = find_candidates(blocks, ContractGrammar(), era="image_text")
    assert [c.label_canon for c in cands] == ["ARTICLE 1", "SECTION 1.01", "SECTION 1.02"]
    # without the image_text profile the blob stays one line
    blocks, _, _ = html_to_blocks(html, 0, Profile(era="html_publisher", publisher="other", agent_cik="0"))
    assert len(next(x for x in blocks if x.kind == "para").lines) == 1
