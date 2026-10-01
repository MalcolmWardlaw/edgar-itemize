"""MkDocs hooks for the manual.

Some pages pull a file from the development notebook (`docs/`) or the repository root in
verbatim: `--8<-- "path"` on a line of its own, the pymdownx.snippets syntax. The included
files link to turn reports and decision memos that are not part of the site, and to each
other by repository path, so this hook does the inclusion itself (before MkDocs validates
links) and turns such links into plain text. Links to the web and to pages of the manual are
left alone. pymdownx.snippets stays enabled for anything else.

The static demo (manual/demo/) runs the viewer's own front end: after the build this hook
copies src/edgar_itemize/viewer/static/index.html to <site>/demo/viewer.html with one line
added, the demo's fetch shim (demo/shim.js), so the demo never carries a fork of the viewer.
"""

from __future__ import annotations

import re
from pathlib import Path

VIEWER_SOURCE = "src/edgar_itemize/viewer/static/index.html"
SHIM_ANCHOR = '<meta charset="utf-8">\n'
SHIM_TAG = '<script src="shim.js"></script>\n'

_INCLUDE = re.compile(r'^--8<-- "([^"]+)"\s*$', re.M)
_LINK = re.compile(r"\[([^\]]+)\]\(((?:\.\./|docs/|conformance/|scripts/|src/|tests/|runs/)[^)]+)\)")


def _plain(m: re.Match) -> str:
    text = m.group(1)
    return text if text.startswith("`") else f"`{text}`"


def on_page_markdown(markdown: str, page, config, files) -> str:
    root = Path(config.config_file_path).parent

    def include(m: re.Match) -> str:
        path = root / m.group(1)
        return path.read_text(encoding="utf-8").rstrip("\n")

    markdown = _INCLUDE.sub(include, markdown)
    return _LINK.sub(_plain, markdown)


def demo_viewer_html(source: str) -> str:
    """The viewer page with the demo shim loaded first (before any of the viewer's scripts)."""
    if source.count(SHIM_ANCHOR) != 1:
        raise ValueError(f"{VIEWER_SOURCE}: expected exactly one {SHIM_ANCHOR!r} to anchor the demo shim")
    return source.replace(SHIM_ANCHOR, SHIM_ANCHOR + SHIM_TAG, 1)


def on_post_build(config) -> None:
    site = Path(config.site_dir)
    if not (site / "demo" / "index.html").exists():
        return
    root = Path(config.config_file_path).parent
    src = (root / VIEWER_SOURCE).read_text(encoding="utf-8")
    (site / "demo" / "viewer.html").write_text(demo_viewer_html(src), encoding="utf-8")
