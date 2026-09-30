from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.normalize_html import html_to_blocks


def test_pre_wrapped_text_splits_on_blank_lines_and_headings():
    html = "<html><body><pre>\nPART I\n\nITEM 1.  BUSINESS\nWe make things.\nITEM 2.  PROPERTIES\n\nWe own a plant.\n</pre></body></html>"
    blocks, norm, _ = html_to_blocks(html, 0)
    texts = [b.text.split("\n")[0] for b in blocks if b.kind == "para"]
    assert texts == ["PART I", "ITEM 1. BUSINESS", "ITEM 2. PROPERTIES", "We own a plant."]
    for b in blocks:
        assert html[b.raw_start : b.raw_end].split() == b.text.split()


def test_table_row_heading_and_split_label():
    html = ("<html><body><table><tr><td>Item</td><td>1. Business</td></tr></table>"
            "<table><tr><td>Item 8.</td><td>Financial Statements and Supplementary Data</td></tr></table></body></html>")
    blocks, _, _ = html_to_blocks(html, 0)
    cands = find_candidates(blocks, Form10KGrammar(), era="html_publisher")
    assert [(c.label_canon, c.title) for c in cands] == [("ITEM 1", "Business"), ("ITEM 8", "Financial Statements and Supplementary Data")]
    assert all("pos.table_row" in c.rule_ids for c in cands)
    assert html[cands[1].head_raw_start : cands[1].head_raw_end].startswith("Item 8.")


def test_malformed_css_numbers_do_not_crash():
    html = '<p style="margin-left: ..5in; font-size: ..25pt">Item 1. Business</p>'
    blocks, _, _ = html_to_blocks(html, 0)
    assert blocks[0].text == "Item 1. Business"


def test_part_and_item_on_one_line():
    from edgar_itemize.normalize_text import text_to_blocks
    payload = "\nPART I Item 1. Business Background The first restaurant was opened in 1972 and\nthe company grew quickly thereafter.\n\nItem 2. Properties\n"
    blocks, _, _ = text_to_blocks(payload, 100)
    cands = find_candidates(blocks, Form10KGrammar(), era="text")
    labels = [(c.kind, c.label_canon) for c in cands]
    assert ("part", "PART I") in labels and ("item", "ITEM 1") in labels and ("item", "ITEM 2") in labels
    c1 = next(c for c in cands if c.label_canon == "ITEM 1")
    assert payload[c1.head_raw_start - 100:].startswith("Item 1.")


def test_bom_before_label_is_ignored():
    html = "<p>﻿Item 5. Market for Equity</p><p>&#65279;Item 7. MD&amp;A</p>"
    blocks, _, _ = html_to_blocks(html, 0)
    assert [b.text for b in blocks] == ["Item 5. Market for Equity", "Item 7. MD&A"]
    cands = find_candidates(blocks, Form10KGrammar(), era="html_early")
    assert [c.label_canon for c in cands] == ["ITEM 5", "ITEM 7"]
