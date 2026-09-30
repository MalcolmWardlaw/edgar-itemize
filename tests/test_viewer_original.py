"""Marker injection for the viewer's original-document pane."""

from edgar_itemize.viewer.original import STYLE, inject_markers, marker_tag, safe_point


def test_safe_point_text_run_unchanged():
    low = "<p>hello <b>world</b></p>"
    assert safe_point(low, 3) == 3          # 'h' of hello
    assert safe_point(low, 12) == 12        # 'w' of world


def test_safe_point_nudges_out_of_tag_and_comment():
    low = "<p class='x'>hi</p><!-- a comment -->tail"
    assert safe_point(low, 5) == 13         # inside <p ...> -> after '>'
    assert safe_point(low, 22) == 37        # inside the comment -> after '-->'


def test_safe_point_refuses_raw_text_content():
    low = "<style>p{}</style><script>var a=1;</script><p>ok</p>"
    assert safe_point(low, 8) is None       # inside <style>
    assert safe_point(low, 30) is None      # inside <script>
    assert safe_point(low, 46) == 46        # 'o' of ok
    assert safe_point(low, -1) is None and safe_point(low, len(low) + 1) is None


def test_marker_tag_shapes():
    assert marker_tag("block", 120, idx=3) == '<span class="ea-m" id="ea120" data-idx="3"></span>'
    t = marker_tag("head", 7, label='ITEM 1', depth=2, path="2.1.1", node=None)
    assert t.startswith('<span class="ea-m ea-h" id="ea7"') and 'data-label="ITEM 1"' in t and "data-node" not in t


def test_inject_html_puts_style_in_head_and_markers_at_offsets():
    payload = "<html><head><title>t</title></head><body><p>Alpha</p><p>Beta</p></body></html>"
    base = 1000
    a = base + payload.index("Alpha")
    b = base + payload.index("Beta")
    out = inject_markers(payload, base, True, [(a, marker_tag("block", a, idx=0)), (b, marker_tag("head", b, label="X", depth=1))])
    assert out.startswith("<html><head>" + STYLE)
    assert '<p><span class="ea-m" id="ea%d" data-idx="0"></span>Alpha</p>' % a in out
    assert 'id="ea%d"' % b in out and out.index('id="ea%d"' % b) < out.index("Beta")
    # an offset outside the payload is dropped, the rest of the text is untouched
    out2 = inject_markers(payload, base, True, [(base + len(payload) + 5, "<span></span>")])
    assert out2 == "<html><head>" + STYLE + payload[len("<html><head>"):]


def test_inject_html_without_head_prepends_style():
    payload = "<p>x</p>"
    out = inject_markers(payload, 0, True, [])
    assert out == STYLE + payload


def test_inject_text_escapes_and_wraps():
    payload = "ITEM 1. BUSINESS\n<not a tag> & co\n"
    out = inject_markers(payload, 50, False, [(50, marker_tag("head", 50, label="ITEM 1", depth=1)), (67, marker_tag("block", 67, idx=1))])
    assert out.startswith(STYLE + "<pre")
    assert "&lt;not a tag&gt; &amp; co" in out
    assert out.index('id="ea50"') < out.index("ITEM 1. BUSINESS") < out.index('id="ea67"') < out.index("&lt;not a tag")
