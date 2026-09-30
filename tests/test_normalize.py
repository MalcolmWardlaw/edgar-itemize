from edgar_itemize.normalize_html import html_to_blocks
from edgar_itemize.normalize_text import text_to_blocks


def test_text_blocks_roundtrip_offsets():
    payload = "\n<PAGE>\n                                    PART I\n\nITEM 1.  BUSINESS\n\nSome body text here.\nMore text.\n\n     ITEM 2. PROPERTIES\n"
    base = 1000
    blocks, norm, omap = text_to_blocks(payload, base)
    kinds = [b.kind for b in blocks]
    assert kinds[0] == "page"
    paras = [b for b in blocks if b.kind == "para"]
    assert [p.text.split("\n")[0] for p in paras] == ["PART I", "ITEM 1. BUSINESS", "Some body text here.", "ITEM 2. PROPERTIES"]
    for p in paras:
        raw = payload[p.raw_start - base : p.raw_end - base]
        assert raw.split() == p.text.split()
    assert paras[0].center
    # normalized offsets map back into the raw payload
    i = norm.index("BUSINESS")
    assert payload[omap.norm_to_raw(i) - base :].startswith("BUSINESS")


def test_html_blocks_style_and_offsets():
    html = (
        "<html><body><p align=center><b>PART I</b></p>"
        "<p><b>Item&nbsp;1.</b> Business</p>"
        "<table><tr><td>Item 7.</td><td>Management&#146;s Discussion</td></tr></table>"
        "<p>Body text.<br>Item 8. Financial Statements</p>"
        "<p style='display:none'>hidden</p>"
        "</body></html>"
    )
    base = 5000
    blocks, norm, omap = html_to_blocks(html, base)
    texts = [b.text for b in blocks]
    assert texts[0] == "PART I" and blocks[0].bold and blocks[0].center
    assert texts[1] == "Item 1. Business" and blocks[1].bold
    row = [b for b in blocks if b.row_text]
    assert row and row[0].row_text == "Item 7. Management's Discussion"
    assert "hidden" not in norm
    body = [b for b in blocks if b.text.startswith("Body")][0]
    assert body.lines == ("Body text.", "Item 8. Financial Statements")
    # line 2 raw offset points at 'Item 8'
    assert html[body.line_raw_starts[1] - base :].startswith("Item 8.")
    for b in blocks:
        assert html[b.raw_start - base : b.raw_end - base].strip() != ""
