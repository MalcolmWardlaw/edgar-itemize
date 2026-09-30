import pytest

from edgar_itemize.grammar.form10k import ITEM_RE, PART_RE, Form10KGrammar

G = Form10KGrammar()


def canon_item(s):
    m = ITEM_RE.match(s)
    return G.canonicalize("item", m) if m else None


def canon_part(s):
    m = PART_RE.match(s)
    return G.canonicalize("part", m) if m else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ITEM 1. BUSINESS", "ITEM 1"),
        ("Item 1A. Risk Factors", "ITEM 1A"),
        ("Item 1A.Risk Factors", "ITEM 1A"),
        ("Item 7A. Quantitative", "ITEM 7A"),
        ("ITEM 9A: CONTROLS", "ITEM 9A"),
        ("Item 7 — Management's Discussion", "ITEM 7"),
        ("Item No. 1 Business", "ITEM 1"),
        ("Item I— BUSINESS", "ITEM 1"),
        ("ITEMS 10 THROUGH 13", "ITEM 10"),
        ("Items 1 and 2. Business and Properties.", "ITEM 1"),
        ("Item 16. Form 10-K Summary", "ITEM 16"),
        ("Item1B. Unresolved Staff Comments.", "ITEM 1B"),
        ("Item 405 of Regulation S-K", None),
        ("Item 17", None),
        ("Item 1D", None),
        ("Items of product", None),
        ("Itemized deductions", None),
        ("ITEM 8", "ITEM 8"),
        ("Item 9.A. Controls and Procedures.", "ITEM 9A"),
        ("Item 9.B. Other Information.", "ITEM 9B"),
        ("ITEM 9A (T) - CONTROLS AND PROCEDURES", "ITEM 9A"),
        ("Item 1. Business", "ITEM 1"),
        ("Item 1, Business", "ITEM 1"),
        ("ITEM 2, PROPERTIES", "ITEM 2"),
        ("Item 3; Legal Proceedings", "ITEM 3"),
        ("ITEM 1(A). BUSINESS", "ITEM 1"),
        ("Item 1(A). Risk Factors", "ITEM 1A"),
        ("Item 1(B). Unresolved Staff Comments", "ITEM 1B"),
        ("Item 7(A). Quantitative and Qualitative Disclosures About Market Risk", "ITEM 7A"),
        ("Item 9(A). Controls and Procedures", "ITEM 9A"),
        ("Item 9(B). Other Information", "ITEM 9B"),
        ("Item 1(a) General Development of Business", "ITEM 1"),
        ("Item 1.A Risk Factors", "ITEM 1A"),
        ("Item 9.A. Controls and Procedures.", "ITEM 9A"),
        ("Item 7(a) Market Risk", "ITEM 7A"),
    ],
)
def test_item_labels(text, expected):
    assert canon_item(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("PART I", "PART I"),
        ("Part II.", "PART II"),
        ("PART III - OTHER", "PART III"),
        ("PART IV", "PART IV"),
        ("Part 1", "PART I"),
        ("Part One", "PART I"),
        ("PARTICIPANTS", None),
        ("PART 5", None),
        ("Party", None),
    ],
)
def test_part_labels(text, expected):
    assert canon_part(text) == expected


def test_order_keys_monotone():
    keys = ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16"]
    ks = [G.order_key("item", f"ITEM {k}") for k in keys]
    assert ks == sorted(ks) and len(set(ks)) == len(ks)


def test_part_of_item_14():
    assert G.part_of_item("ITEM 14", has_item_15=False) == "PART IV"
    assert G.part_of_item("ITEM 14", has_item_15=True) == "PART III"


def test_continued_page_header_is_penalized():
    from edgar_itemize.blocks import Block
    from edgar_itemize.candidates import find_candidates

    def blk(i, t):
        return Block(idx=i, text=t, raw_start=i * 100, raw_end=i * 100 + len(t), norm_start=i * 100, norm_end=i * 100 + len(t), lines=(t,), bold=True)

    cands = find_candidates([blk(0, "Item 15. Exhibits"), blk(1, "Item 15. Exhibits (continued)")], G, era="html_publisher")
    assert cands[0].score > cands[1].score and "rej.continued" in cands[1].rule_ids
