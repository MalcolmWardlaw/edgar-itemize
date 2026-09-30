"""Turn 5: run-in sub-headings (C2), continuation suppression and back-link glue (C3)."""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import CONTINUED_RE, page_repeat_pass
from edgar_itemize.headings import _signature, find_headings
from edgar_itemize.normalize_html import html_to_blocks
from edgar_itemize.tree import Node

PROSE = "Net sales increased $168 million compared to the prior year because of higher volume in all regions and pricing actions."


def _blocks(html):
    blocks, norm, _ = html_to_blocks(html, 0)
    return [b for b in blocks if b.kind == "para"], norm


def test_runin_bold_leadin_is_tagged_with_offsets():
    html = f"<p><b>Net Sales.</b> {PROSE}</p>"
    (b,), norm = _blocks(html)
    assert b.runin_text == "Net Sales."
    assert html[b.raw_start:b.runin_raw_end] == "Net Sales."
    assert b.text.startswith("Net Sales. Net sales")
    # a short run-in paragraph keeps the block-level bold flag (candidate scoring relies on
    # it for run-in style contract sections) but is still emitted as a run-in, not a heading
    short = "<p><b>Net Sales:</b> Up 12% on volume gains across all segments and every region we serve.</p>"
    blocks, _, _ = html_to_blocks(short, 0)
    b = next(x for x in blocks if x.kind == "para")
    assert b.bold and b.runin_text == "Net Sales:"
    item = Node(1, 0, 2, "item", "ITEM 7", "Item 7", "MD&A", 0, 10000, 0, 5, 0, 10000, 0, 0.9, [])
    out = find_headings(blocks, [item], "html_publisher")
    assert [(n.title, "sty.runin" in n.rule_ids) for n in out] == [("Net Sales", True)]


def test_runin_styled_span_and_separator_outside_bold():
    html = f'<div><span style="font-weight:700">Use of Estimates</span> - {PROSE}</div>'
    (b,), _ = _blocks(html)
    assert b.runin_text == "Use of Estimates"
    html = f'<p><font style="font-weight:bold;font-style:italic">Gross Profit.</font><font>&nbsp;</font><font>{PROSE}</font></p>'
    (b,), _ = _blocks(html)
    assert b.runin_text == "Gross Profit."


def test_runin_rejects_cross_reference_long_lead_and_short_body():
    assert _blocks(f"<p><b>See Note 5.</b> {PROSE}</p>")[0][0].runin_text == ""
    assert _blocks(f"<p><b>Net sales increased sharply during the year because of volume.</b> {PROSE}</p>")[0][0].runin_text == ""
    assert _blocks("<p><b>Net Sales.</b> Up 10%.</p>")[0][0].runin_text == ""
    # a fully bold paragraph is a standalone heading candidate, not a run-in
    assert _blocks("<p><b>Results of Operations</b></p>")[0][0].runin_text == ""


def test_backlink_glue_is_stripped_and_href_waived():
    html = '<p><b>ITEM 1. DESCRIPTION OF BUSINESS.</b><a href="#V_00002">Table of Contents</a></p>'
    (b,), norm = _blocks(html)
    assert b.text == "ITEM 1. DESCRIPTION OF BUSINESS."
    assert b.href_targets == ()
    assert html[b.raw_start:b.raw_end] == "ITEM 1. DESCRIPTION OF BUSINESS."
    assert "Table of Contents" not in norm
    # a real link inside the heading keeps its href evidence
    html = '<p><a href="#i1">ITEM 1. BUSINESS</a></p>'
    (b,), _ = _blocks(html)
    assert b.href_targets == ("i1",)


def test_continuation_regex_and_signature_suppression():
    for t in ("RESULTS OF OPERATIONS (continued)", "Liquidity (Cont'd)", "ITEM 6. SELECTED DATA (Con't)", "Comparison of 1994 to 1993 - Continued"):
        assert CONTINUED_RE.search(t), t
    assert not CONTINUED_RE.search("Discontinued Operations")
    b = Block(idx=0, text="Liquidity and Capital Resources (Continued)", raw_start=0, raw_end=40, norm_start=0, norm_end=40, bold=True)
    assert _signature(b, "html_publisher") is None
    b.text = "Liquidity and Capital Resources"
    assert _signature(b, "html_publisher") is not None


def test_runin_nodes_nest_under_standalone_heading():
    item = Node(1, 0, 2, "item", "ITEM 7", "Item 7", "MD&A", 0, 1000, 0, 10, 0, 1000, 0, 0.9, [])
    blocks = [
        Block(idx=0, text="Item 7. MD&A", raw_start=0, raw_end=10, norm_start=0, norm_end=10),
        Block(idx=1, text="Results of Operations", raw_start=100, raw_end=120, norm_start=20, norm_end=40, bold=True),
        Block(idx=2, text="Net Sales. " + PROSE, raw_start=200, raw_end=400, norm_start=50, norm_end=250, runin_text="Net Sales.", runin_raw_end=210),
        Block(idx=3, text="Gross Profit. " + PROSE, raw_start=500, raw_end=700, norm_start=260, norm_end=460, runin_text="Gross Profit.", runin_raw_end=513),
        Block(idx=4, text="Liquidity", raw_start=800, raw_end=810, norm_start=470, norm_end=480, bold=True),
    ]
    out = find_headings(blocks, [item], "html_publisher")
    titles = [(n.title, n.depth, n.head_raw_end) for n in out]
    assert titles == [("Results of Operations", 3, 120), ("Net Sales", 4, 210), ("Gross Profit", 4, 513), ("Liquidity", 3, 810)]
    assert "sty.runin" in out[1].rule_ids and out[1].confidence < out[0].confidence


def test_forward_continuation_and_first_copy_relief():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.grammar.form10k import Form10KGrammar

    def blk(i, text, start):
        lines = tuple(text.split("\n"))
        return Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start, norm_end=start + len(text), lines=lines,
                     line_raw_starts=(start,), line_raw_ends=(start + len(lines[0]),))

    blocks = [
        blk(0, "Item 8. Financial Statements and Supplementary Data\n(continued on following page)", 1000),
        blk(1, "Item 9. Changes in and Disagreements\nwith Accountants", 2000),
        blk(2, "Item 9. Changes in and Disagreements (continued)", 3000),
    ]
    cands = find_candidates(blocks, Form10KGrammar(), era="text")
    by = {(c.label_canon, c.head_raw_start): c for c in cands}
    assert "rej.continued" not in by[("ITEM 8", 1000)].rule_ids  # forward pointer, real heading
    assert "rej.continued" in by[("ITEM 9", 3000)].rule_ids and "rej.continued_first" in by[("ITEM 9", 3000)].rule_ids
    assert by[("ITEM 9", 3000)].score < by[("ITEM 9", 2000)].score  # the clean copy still wins
    # an equally styled continued copy after a clean copy loses its relief once TOC regions are known
    blocks = [blk(0, "ITEM 6. SELECTED FINANCIAL DATA", 100), blk(1, "ITEM 6. SELECTED FINANCIAL DATA (CONTINUED)", 900)]
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks)
    assert cands[1].score < cands[0].score and "rej.continued_after_copy" in cands[1].rule_ids
    # a wrapped-line marker with no earlier copy keeps most of its score
    blocks = [blk(0, "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL\nCONDITION AND RESULTS OF OPERATIONS (continued)", 500)]
    (c,) = find_candidates(blocks, Form10KGrammar(), era="text")
    assert "rej.continued" in c.rule_ids and "rej.continued_first" in c.rule_ids


def test_page_repeat_without_marker_is_penalized():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.grammar.form10k import Form10KGrammar

    def blk(i, text, start, kind="para"):
        lines = tuple(text.split("\n"))
        return Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start, norm_end=start + len(text), lines=lines, kind=kind,
                     is_page_break=(kind == "page"), line_raw_starts=(start,), line_raw_ends=(start + len(lines[0]),))

    t = "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL\nCONDITION AND RESULTS OF OPERATIONS"
    blocks = [blk(0, "<PAGE>", 90, "page"), blk(1, t, 100), blk(2, "Revenues rose in the year for reasons explained below in detail.", 300),
              blk(3, "26", 900), blk(4, "<PAGE>", 950, "page"), blk(5, t, 1000), blk(6, "More prose follows the running header here.", 1200)]
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks)
    by = {c.head_raw_start: c for c in cands if c.label_canon == "ITEM 7"}
    assert "rej.page_repeat" not in by[100].rule_ids
    assert "rej.page_repeat" in by[1000].rule_ids and by[1000].score < by[100].score
    # the same repeat mid-page (no page marker) is left alone
    blocks[4] = blk(4, "Some prose that ends the previous page without a page marker.", 950)
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks)
    assert "rej.page_repeat" not in next(c for c in cands if c.head_raw_start == 1000).rule_ids
    # a TOC-region member never serves as the reference copy
    blocks[4] = blk(4, "<PAGE>", 950, "page")
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks, toc_idx={0})
    assert "rej.page_repeat" not in next(c for c in cands if c.head_raw_start == 1000).rule_ids


def test_index_rows_do_not_make_the_body_heading_a_page_repeat():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.grammar.form10k import Form10KGrammar

    def blk(i, text, start, kind="para", **kw):
        lines = tuple(text.split("\n"))
        b = Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start, norm_end=start + len(text), lines=lines, kind=kind,
                  is_page_break=(kind == "page"), line_raw_starts=tuple(start + sum(len(x) + 1 for x in lines[:k]) for k in range(len(lines))),
                  line_raw_ends=(start + len(lines[0]),))
        for k, v in kw.items():
            setattr(b, k, v)
        return b

    # a TOC row whose dot leader sits on the wrapped second line, then the real heading at a page start
    blocks = [blk(0, "Item 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY\nDATA . . . . . . . . 47", 100), blk(1, "<PAGE>", 900, "page"),
              blk(2, "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA", 1000)]
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks)
    assert "rej.page_repeat" not in next(c for c in cands if c.head_raw_start == 1000).rule_ids
    # an HTML index cell (in a table) is not a reference copy either
    blocks = [blk(0, "ITEM 1. BUSINESS", 100, in_table=True), blk(1, "<PAGE>", 900, "page"), blk(2, "ITEM 1. BUSINESS", 1000)]
    cands = find_candidates(blocks, Form10KGrammar(), era="html_publisher"); page_repeat_pass(cands, blocks)
    assert "rej.page_repeat" not in next(c for c in cands if c.head_raw_start == 1000).rule_ids


def test_runin_inside_single_cell_layout_table():
    html = f"<table><tr><td><b>Borrowings:</b> {PROSE}</td></tr></table>"
    blocks, _, _ = html_to_blocks(html, 0)
    b = next(x for x in blocks if x.kind == "para")
    assert b.in_table and b.runin_text == "Borrowings:"
    item = Node(1, 0, 2, "item", "ITEM 7", "Item 7", "MD&A", 0, 10000, 0, 5, 0, 10000, 0, 0.9, [])
    assert [n.title for n in find_headings(blocks, [item], "html_early")] == ["Borrowings"]
    # a two-cell row (label cell + text cell) is a real table, not a paragraph wrapper
    html = f"<table><tr><td><b>Borrowings:</b> {PROSE}</td><td>12.5</td></tr></table>"
    blocks, _, _ = html_to_blocks(html, 0)
    assert find_headings(blocks, [item], "html_early") == []


def test_hinted_body_copy_outranks_relieved_continuation():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.grammar.form10k import Form10KGrammar

    def blk(i, text, start):
        lines = tuple(text.split("\n"))
        return Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start, norm_end=start + len(text), lines=lines,
                     line_raw_starts=(start,), line_raw_ends=(start + len(lines[0]),))

    # a bare "Item 7" label block (its title on the next block) followed by the continuation copy
    blocks = [blk(0, "Item 7", 100), blk(1, "MANAGEMENT'S DISCUSSION AND ANALYSIS", 110), blk(2, "Item 7 (Continued)", 900)]
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks)
    by = {c.head_raw_start: c for c in cands if c.label_canon == "ITEM 7"}
    # Turn 8 names what the hint is really reading here -- the bare label's own item
    # number, not a page number (toc.pageno_self_label) -- but keeps the hint and its
    # penalty, so this test's ranking is unchanged.
    assert by[100].toc_hint and "toc.pageno_self_label" in by[100].rule_ids
    assert "rej.continued_after_copy" in by[900].rule_ids
    assert by[900].score < by[100].score - 0.2  # below the chain's index-row weight of the earlier copy
    # when the only earlier copy sits inside a TOC region the relief stays whole
    cands = find_candidates(blocks, Form10KGrammar(), era="text"); page_repeat_pass(cands, blocks, toc_idx={0})
    assert "rej.continued_after_copy" not in next(c for c in cands if c.head_raw_start == 900).rule_ids


def test_running_header_before_the_styled_heading_is_not_the_reference():
    from edgar_itemize.candidates import find_candidates
    from edgar_itemize.grammar.form10k import Form10KGrammar

    def blk(i, text, start, kind="para", **kw):
        lines = tuple(text.split("\n"))
        b = Block(idx=i, text=text, raw_start=start, raw_end=start + len(text), norm_start=start, norm_end=start + len(text), lines=lines, kind=kind,
                  is_page_break=(kind == "page"), line_raw_starts=(start,), line_raw_ends=(start + len(lines[0]),))
        for k, v in kw.items():
            setattr(b, k, v)
        return b

    # modern template: an unstyled "Item 8 | Financial Statements..." running header opens the page,
    # the bold heading follows; the bold copy must not be penalised as a repeat of the header
    blocks = [blk(0, "<PAGE>", 90, "page"), blk(1, "Item 8 | Financial Statements and Supplementary Data", 100),
              blk(2, "Index to the financial statements follows on this page.", 200), blk(3, "<PAGE>", 390, "page"),
              blk(4, "Item 8. Financial Statements and Supplementary Data.", 400, bold=True)]
    cands = find_candidates(blocks, Form10KGrammar(), era="html_publisher"); page_repeat_pass(cands, blocks)
    assert "rej.page_repeat" not in next(c for c in cands if c.head_raw_start == 400).rule_ids
    # the same unstyled repeat at a page start is still a page header
    blocks[4] = blk(4, "Item 8 | Financial Statements and Supplementary Data", 400)
    cands = find_candidates(blocks, Form10KGrammar(), era="html_publisher"); page_repeat_pass(cands, blocks)
    assert "rej.page_repeat" in next(c for c in cands if c.head_raw_start == 400).rule_ids
