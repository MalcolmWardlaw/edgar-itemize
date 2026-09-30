"""Turn 10 B.1: Form10QGrammar's title table (`gram.item.title_match` /
`seq.out_of_order` on 10-Q).  Mirrors the 32-case self-check A.2 built against the
standalone JSON loader (scripts/turn10/a2_load_table.py) but runs it against the
grammar's own `title_matches`/`title_match_end` now that the table is wired into
src/, plus a pipeline-level test that `seq.out_of_order` actually fires on a
synthetic 10-Q (the mechanism the table exists to unlock, per
docs/turn10_decisions/a2_10q_titles.md section 5, rule 2), and a check that
Form10KGrammar's own title-match behaviour is untouched by this change.
"""

from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import ITEM_RE, Form10QGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree import build_tree

G = Form10QGrammar()
GK = Form10KGrammar()

# --- self-check: the same 32 cases as scripts/turn10/a2_load_table.py, ported to the
# grammar's own methods (22 real titles per item, 10 non-titles that must not match).
_POSITIVE_CASES = [
    ("I.1", "Financial Statements"),
    ("I.1", "Condensed Consolidated Financial Statements (Unaudited)"),
    ("I.1", "Financial Information"),
    ("I.2", "Management's Discussion and Analysis of Financial Condition and Results of Operations"),
    ("I.2", "Management's Discussion and Analysis or Plan of Operation"),
    ("I.3", "Quantitative and Qualitative Disclosures About Market Risk"),
    ("I.3", "Qualitative and Quantitative Disclosure About Market Risks"),
    ("I.4", "Controls and Procedures"),
    ("I.4", "Disclosure Controls and Procedures"),
    ("II.1", "Legal Proceedings"),
    ("II.1", "Litigation"),
    ("II.1A", "Risk Factors"),
    ("II.1A", "Risks Factors"),
    ("II.2", "Unregistered Sales of Equity Securities and Use of Proceeds"),
    ("II.2", "Changes in Securities and Use of Proceeds"),
    ("II.3", "Defaults Upon Senior Securities"),
    ("II.4", "Submission of Matters to a Vote of Security Holders"),
    ("II.4", "Mine Safety Disclosures"),
    ("II.4", "(Removed and Reserved)"),
    ("II.5", "Other Information"),
    ("II.6", "Exhibits and Reports on Form 8-K"),
    ("II.6", "Exhibits"),
]

_NEGATIVE_CASES = [
    ("I.1", "is incorporated herein by reference to Exhibit 13."),
    ("I.2", "The following discussion should be read in conjunction with"),
    ("I.3", "not applicable to the Company at this time because"),
    ("I.4", "of Form 10-K sets forth our internal control structure."),
    ("II.1", "None. The Company is not currently a party to"),
    ("II.1A", "In addition to the other information set forth in this report,"),
    ("II.2", "of Form 8-K filed during the quarter ended"),
    ("II.3", "See Note 7 to the accompanying financial statements for"),
    ("II.4", "of the Company's 2005 Annual Meeting of Stockholders was held on"),
    ("II.6", "index to exhibits filed with this report is incorporated"),
]


def test_positive_titles_match():
    for key, title in _POSITIVE_CASES:
        assert G.title_matches("item", f"ITEM {key}", title), (key, title)


def test_negative_titles_do_not_match():
    for key, title in _NEGATIVE_CASES:
        assert not G.title_matches("item", f"ITEM {key}", title), (key, title)


def test_self_check_count():
    assert len(_POSITIVE_CASES) + len(_NEGATIVE_CASES) == 32


def test_title_match_end_offset_and_ambiguous_canon():
    end = G.title_match_end("item", "ITEM II.6", "Exhibits. See the index below.")
    assert end == len("Exhibits")
    # an unresolved Part ("ITEM ?.3") has no table entry -- correctly no match, not a KeyError
    assert G.title_match_end("item", "ITEM ?.3", "Defaults Upon Senior Securities") is None
    assert G.title_matches("part", "ITEM I.1", "Financial Statements") is False


def test_getattr_interface_is_exposed_like_candidates_py_expects():
    # candidates.py calls these via getattr(grammar, "title_matches", None) /
    # getattr(grammar, "title_match_end", lambda *a: None) -- both must exist and be
    # plain callables usable off the class or an instance, same as Form10KGrammar's.
    assert callable(getattr(Form10QGrammar, "title_matches", None))
    assert callable(getattr(Form10QGrammar, "title_match_end", None))
    assert callable(getattr(G, "title_matches", None))


# --- pipeline-level: seq.out_of_order now reachable on a 10-Q -------------------------
# Same shape as tests/test_turn8_out_of_order.py's 10-K case: an item typeset after a
# later one in Part II, fully titled and unopposed, dropped as `nonmonotone` by the
# monotone chain and recovered only once `gram.item.title_match` can fire.

BODY_OOO = """PART I

ITEM 1. FINANCIAL STATEMENTS

The unaudited condensed financial statements follow.

ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS

Results of operations are discussed below.

PART II

ITEM 2. UNREGISTERED SALES OF EQUITY SECURITIES

None.

Item 1A. Risk Factors

There have been no material changes to our risk factors.

ITEM 6. EXHIBITS

See the exhibit index.
"""


def _parse_10q(body: str):
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era="text")
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="form10q")
    nodes, rejected = build_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=len(body), norm_len=len(norm))
    return cands, nodes, rejected


def _items(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "item"}


def test_out_of_order_item_recovered_on_10q():
    cands, nodes, rejected = _parse_10q(BODY_OOO)
    it = _items(nodes)
    assert "ITEM II.1A" in it, sorted(it)
    ooo = it["ITEM II.1A"]
    assert "seq.out_of_order" in ooo.rule_ids
    assert "gram.item.title_match" in ooo.rule_ids
    # no nonmonotone rejection is left behind for the label the pass claimed
    assert "ITEM II.1A" not in [r.label_canon for r in rejected if r.reason == "nonmonotone"]
    assert it["ITEM II.2"].raw_start < ooo.raw_start < it["ITEM II.6"].raw_start


def test_without_title_table_candidate_would_have_no_title_match_rule():
    # sanity: the candidate itself carries gram.item.title_match only because the
    # grammar now exposes title_matches -- confirms the wiring, not just the outcome.
    cands, nodes, rejected = _parse_10q(BODY_OOO)
    hit = next(c for c in cands if c.label_canon == "ITEM II.1A" and "Risk Factors" in c.title)
    assert "gram.item.title_match" in hit.rule_ids


# --- Form10KGrammar untouched ----------------------------------------------------------


def test_form10k_title_match_untouched():
    # spot-check a handful of Form10KGrammar._ITEM_TITLE_RE behaviours that predate this
    # turn's 10-Q work, to confirm the 10-K grammar (a separate class, separate table)
    # was not accidentally modified.
    assert GK.title_matches("item", "ITEM 8", "Financial Statements and Supplementary Data.")
    assert not GK.title_matches("item", "ITEM 8", "is incorporated herein by reference to Exhibit 13.")
    assert GK.title_matches("item", "ITEM 1A", "Risk Factors")
    end = GK.title_match_end("item", "ITEM 3", "Legal Proceedings. See Note 12.")
    assert end == len("Legal Proceedings")


def test_form10k_item_re_and_10q_item_re_are_distinct_objects():
    assert ITEM_RE is not None
    from edgar_itemize.grammar.form10k import ITEM_RE as K_ITEM_RE
    assert ITEM_RE is not K_ITEM_RE
