"""The original-document pane: serve a <DOCUMENT>'s <TEXT> payload with sync markers.

Every block and node the viewer draws carries a byte offset into the raw
submission file, so the original can be seeded with zero-width marker
elements at those same offsets and the two panes locate each other by
offset. HTML payloads are served as-is (scripts are disabled by the iframe
sandbox and a CSP); text payloads are escaped into a <pre>.
"""

from __future__ import annotations

import bisect
import html
import os
import re

RENDER_CAP = int(os.environ.get("EDGAR_ITEMIZE_RENDER_CAP", str(15 * 1024 * 1024)))

# Injected into the payload's <head> (or prepended). Markers are empty spans; a
# flash is drawn on the marker's parent element by the viewer. Heading chips
# render from data attributes and are shown only when the viewer sets the
# `ea-chips` class on the document element.
STYLE = """<style id="ea-style">
.ea-m{display:inline;padding:0;margin:0;border:0}
.ea-flash{outline:2px solid #1E4F73 !important;outline-offset:1px;transition:outline-color 1.2s}
.ea-flash-off{outline-color:transparent !important}
.ea-h::before{content:attr(data-label);display:none;font:700 10px/1.4 Menlo,Consolas,monospace;color:#fff;background:#1E4F73;padding:0 .35em;border-radius:2px;margin-right:.4em;vertical-align:middle;white-space:nowrap}
.ea-chips .ea-h::before{display:inline-block}
.ea-h[data-depth="2"]::before{background:#2E7D5B}
.ea-h[data-depth="3"]::before{background:#B7791F}
.ea-h[data-depth="4"]::before{background:#A3502A}
.ea-h[data-depth="5"]::before{background:#6E4C9E}
.ea-h[data-depth="6"]::before{background:#2F8F9D}
</style>"""

_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_RAW_TEXT_RE = re.compile(r"<(script|style|title|textarea)\b.*?(?:</\1\s*>|$)", re.DOTALL)


def raw_text_spans(low: str) -> list[tuple[int, int]]:
    """[start, end) of every <script>/<style>/<title>/<textarea> element in the lower-cased payload."""
    return [(m.start(), m.end()) for m in _RAW_TEXT_RE.finditer(low)]


def safe_point(low: str, pos: int, spans: list[tuple[int, int]] | None = None) -> int | None:
    """Move an insertion offset out of a tag or comment; None if it sits in raw-text content.

    `low` is the lower-cased payload; `spans` is raw_text_spans(low) (computed
    here when omitted). Block offsets normally land at the first character of
    a text run, so the nudge is rarely exercised.
    """
    if pos < 0 or pos > len(low):
        return None
    lt = low.rfind("<", 0, pos)
    gt = low.rfind(">", 0, pos)
    if lt > gt:  # inside <...> (a tag or a comment)
        end = low.find(">", pos)
        if end < 0:
            return None
        pos = end + 1
    if spans is None:
        spans = raw_text_spans(low)
    i = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    if i >= 0 and spans[i][0] <= pos < spans[i][1]:
        return None
    return pos


def marker_tag(kind: str, offset: int, **data: object) -> str:
    """`<span class="ea-m" id="ea<offset>" data-...></span>` (absolute raw offset in the id)."""
    attrs = [f'class="ea-m{" ea-h" if kind == "head" else ""}"', f'id="ea{offset}"']
    for k, v in data.items():
        if v is None or v == "":
            continue
        attrs.append(f'data-{k.replace("_", "-")}="{html.escape(str(v), quote=True)}"')
    return f"<span {' '.join(attrs)}></span>"


def inject_markers(payload: str, base: int, is_html: bool, markers: list[tuple[int, str]]) -> str:
    """Return the payload with marker tags inserted at absolute raw offsets.

    `markers` is a list of (absolute_offset, tag_html); offsets outside the
    payload are dropped. For HTML the style block goes after <head> when there
    is one; text payloads become an escaped <pre>.
    """
    n = len(payload)
    if is_html:
        low = payload.lower()
        spans = raw_text_spans(low)
        ins: list[tuple[int, str]] = []
        m = _HEAD_RE.search(payload)
        ins.append((m.end() if m else 0, STYLE))
        for off, tag in markers:
            p = off - base
            if 0 <= p <= n:
                q = safe_point(low, p, spans)
                if q is not None:
                    ins.append((q, tag))
        ins.sort(key=lambda t: t[0])
        out: list[str] = []
        cur = 0
        for p, tag in ins:
            out.append(payload[cur:p])
            out.append(tag)
            cur = p
        out.append(payload[cur:])
        return "".join(out)
    # text era: escape and wrap; markers go between escaped pieces
    pts = sorted({off - base for off, _ in markers if 0 <= off - base <= n})
    by_pos: dict[int, list[str]] = {}
    for off, tag in markers:
        p = off - base
        if 0 <= p <= n:
            by_pos.setdefault(p, []).append(tag)
    out = [STYLE, "<pre style=\"margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.45 Menlo,Consolas,monospace\">"]
    cur = 0
    for p in pts:
        out.append(html.escape(payload[cur:p]))
        out.extend(by_pos[p])
        cur = p
    out.append(html.escape(payload[cur:]))
    out.append("</pre>")
    return "".join(out)
