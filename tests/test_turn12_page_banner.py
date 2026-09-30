"""Turn 12 B.3: rej.page_banner (docs/turn12_decisions/a3_subheadings.md section (b)).

A later copy of a heading title repeated three or more times under one parent, sitting
within 40 visible characters (PAGE_BANNER_VISIBLE) of the preceding page break, is a running page header and is
dropped from the heading layer with a `Rejected(kind="heading", reason="rej.page_banner")`.
The four must-not-condemn cases from the bank are each pinned here: the first copy of any
run, any run of two, any copy with body text between it and the page break, and any
single-occurrence heading.
"""

from edgar_itemize.blocks import Block
from edgar_itemize.headings import find_headings, merge_and_renumber, page_break_adjacent
from edgar_itemize.normalize_html import html_to_blocks
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.tree import Node, Rejected

PROSE = ("Net sales increased compared to the prior year because of higher volume in all regions "
         "and pricing actions taken during the second half of the year.")


def _item(raw_end: int) -> Node:
    return Node(1, 0, 2, "item", "ITEM 8", "Item 8", "Financial Statements", 0, raw_end, 0, 5, 0, raw_end, 0, 0.9, [])


def _doc(raw_end: int) -> Node:
    return Node(0, -1, 0, "document", None, None, None, 0, raw_end, 0, 0, 0, raw_end, 0, 1.0, [])


def _page(body: str, banner: str = "<p><b>Notes to Consolidated Financial Statements</b></p>", pre: str = "") -> str:
    # a CSS page break, a page-number line (the first block after the break carries the
    # `is_page_break` flag and is never a heading itself), then the banner and a body paragraph
    return f'<div style="page-break-before:always"></div><p>12</p>{pre}{banner}<p>{body}</p>'


def _run(html: str, era: str = "html_publisher"):
    blocks, _norm, _ = html_to_blocks(html, 0)
    item = _item(len(html))
    rejected: list[Rejected] = []
    out = find_headings(blocks, [item], era, rejected=rejected)
    return blocks, out, rejected


def test_page_banner_drops_later_copies_of_a_run_of_three():
    html = "<p>Item 8. Financial Statements</p>" + _page(PROSE) + _page(PROSE) + _page(PROSE)
    blocks, out, rejected = _run(html)
    assert [n.title for n in out] == ["Notes to Consolidated Financial Statements"]  # the first copy survives
    assert [r.reason for r in rejected] == ["rej.page_banner", "rej.page_banner"]
    assert all(r.kind == "heading" for r in rejected)
    # the rejected rows address the dropped blocks: raw_start is the block's, text is its text
    for r in rejected:
        b = blocks[r.block_idx]
        assert r.raw_start == b.raw_start and r.text == b.text[:120]
        assert html[b.raw_start:b.raw_end] == "Notes to Consolidated Financial Statements"
    # the surviving copy is the first by position
    assert out[0].raw_start == min(b.raw_start for b in blocks if b.text == "Notes to Consolidated Financial Statements")


def test_page_banner_never_condemns_the_first_copy():
    html = "<p>Item 8. Financial Statements</p>" + _page(PROSE) * 5
    _blocks, out, rejected = _run(html)
    assert len(out) == 1 and len(rejected) == 4
    assert out[0].raw_start < min(r.raw_start for r in rejected)


def test_page_banner_leaves_a_run_of_two_alone():
    html = "<p>Item 8. Financial Statements</p>" + _page(PROSE) + _page(PROSE)
    _blocks, out, rejected = _run(html)
    assert [n.title for n in out] == ["Notes to Consolidated Financial Statements"] * 2
    assert rejected == []


def test_page_banner_leaves_a_copy_with_body_text_before_it_alone():
    # copies 2 and 3 each open a genuinely different section: well over 40 visible characters
    # of body text sit between the page break and the heading, so they are not adjacent
    html = "<p>Item 8. Financial Statements</p>" + _page(PROSE) + _page(PROSE, pre=f"<p>{PROSE}</p>") + _page(PROSE, pre=f"<p>{PROSE}</p>")
    _blocks, out, rejected = _run(html)
    assert [n.title for n in out] == ["Notes to Consolidated Financial Statements"] * 3
    assert rejected == []
    # and a short registrant banner (under 40 visible characters) between the break and the
    # copy does NOT rescue it -- that is the shape the bank's furniture windows have
    html = "<p>Item 8. Financial Statements</p>" + _page(PROSE) * 1 + _page(PROSE, pre="<p>ACME HOLDINGS INC.</p>") * 2
    _blocks, out, rejected = _run(html)
    assert len(out) == 1 and len(rejected) == 2


def test_page_banner_leaves_a_single_occurrence_heading_alone():
    html = ("<p>Item 8. Financial Statements</p>" + _page(PROSE)
            + _page(PROSE, banner="<p><b>Report of Independent Auditors</b></p>")
            + _page(PROSE, banner="<p><b>Consolidated Balance Sheets</b></p>"))
    _blocks, out, rejected = _run(html)
    assert [n.title for n in out] == ["Notes to Consolidated Financial Statements", "Report of Independent Auditors",
                                      "Consolidated Balance Sheets"]
    assert rejected == []


def test_page_banner_runs_are_per_parent_and_title_normalised():
    # the same title under two different parents is two runs; whitespace and case do not
    # split a run
    banners = ["<p><b>Notes to Consolidated Financial Statements</b></p>",
               "<p><b>Notes   to Consolidated\nFinancial Statements</b></p>",
               "<p><b>Notes To Consolidated Financial Statements</b></p>"]
    html = "<p>Item 8. Financial Statements</p>" + "".join(_page(PROSE, banner=b) for b in banners)
    _blocks, out, rejected = _run(html)
    assert len(out) == 1 and len(rejected) == 2
    # (an all-caps copy would be a stronger signature, and later copies would nest under
    # it -- a different parent, hence a different run, by the rule's own terms)
    # the three copies share the item as parent, and a bold sub-heading between them (a
    # different title) does not break the run
    html = ("<p>Item 8. Financial Statements</p>" + _page(PROSE)
            + "<p><b>Basis of Presentation</b></p><p>%s</p>" % PROSE + _page(PROSE) + _page(PROSE))
    _blocks, out, rejected = _run(html)
    assert [n.title for n in out] == ["Notes to Consolidated Financial Statements", "Basis of Presentation"]
    assert len(rejected) == 2


def test_page_banner_text_era_page_marker_and_hr():
    # text era: <PAGE> lines become "page" blocks; the copy right after one is adjacent
    page = "\n<PAGE>\n\n          NOTES TO CONSOLIDATED FINANCIAL STATEMENTS\n\n" + PROSE + "\n"
    text = "ITEM 8. FINANCIAL STATEMENTS\n" + page * 3
    blocks, _norm, _ = text_to_blocks(text, 0)
    rejected: list[Rejected] = []
    out = find_headings(blocks, [_item(len(text))], "text", rejected=rejected)
    assert [n.title for n in out] == ["NOTES TO CONSOLIDATED FINANCIAL STATEMENTS"]
    assert len(rejected) == 2 and all(r.reason == "rej.page_banner" for r in rejected)
    # html: <hr> is a page break too
    html = "<p>Item 8. Financial Statements</p>" + ("<hr/><p><b>Notes to Consolidated Financial Statements</b></p><p>%s</p>" % PROSE) * 3
    _blocks, out, rejected = _run(html)
    assert len(out) == 1 and len(rejected) == 2


def test_page_break_adjacent_counts_visible_text_only():
    def blk(i, text, kind="para", pb=False):
        return Block(idx=i, text=text, raw_start=i * 100, raw_end=i * 100 + len(text), norm_start=0, norm_end=0, kind=kind, is_page_break=pb)
    blocks = [blk(0, "<PAGE>", "page", True), blk(1, "x" * 40), blk(2, "Heading")]
    assert page_break_adjacent(blocks, 2)  # exactly 40 is within the limit
    blocks[1].text = "x" * 41
    assert not page_break_adjacent(blocks, 2)
    blocks[1].text = "  x   " * 20  # whitespace collapses: 20 x's plus 19 separators = 39, inside 40
    assert page_break_adjacent(blocks, 2)
    assert not page_break_adjacent([blk(0, "x" * 10), blk(1, "Heading")], 1)  # no break at all
    # a CSS break flagged on a para block: that block's own text is after the break
    blocks = [blk(0, "y" * 200), blk(1, "ACME HOLDINGS INC.", pb=True), blk(2, "Heading")]
    assert page_break_adjacent(blocks, 2)
    blocks[1].text = "z" * 41
    assert not page_break_adjacent(blocks, 2)


def test_page_banner_children_of_a_dropped_copy_reattach_to_the_survivor():
    # a note heading nested under banner copy 2 (weaker style) re-attaches under copy 1
    # once copy 2 is dropped; the survivor's span extends over the dropped copy's page
    note = "<p><u>Summary of Significant Accounting Policies</u></p><p>%s</p>" % PROSE
    html = ("<p>Item 8. Financial Statements</p>" + _page(PROSE)
            + "<p><u>Organization</u></p><p>%s</p>" % PROSE + _page(PROSE) + note + _page(PROSE))
    blocks, _norm, _ = html_to_blocks(html, 0)
    rejected: list[Rejected] = []
    item = _item(len(html))
    heads = find_headings(blocks, [item], "html_publisher", rejected=rejected)
    nodes = merge_and_renumber([_doc(len(html)), item], heads)
    by_title = {n.title: n for n in nodes if n.level_kind == "heading"}
    assert set(by_title) == {"Notes to Consolidated Financial Statements", "Organization",
                             "Summary of Significant Accounting Policies"}
    survivor = by_title["Notes to Consolidated Financial Statements"]
    assert by_title["Summary of Significant Accounting Policies"].parent_id == survivor.node_id
    assert by_title["Organization"].parent_id == survivor.node_id
    assert survivor.depth == 3 and by_title["Organization"].depth == 4
    assert survivor.raw_end == item.raw_end  # the survivor now runs to the item's end
    assert len(rejected) == 2
