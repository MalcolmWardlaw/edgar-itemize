"""MkDocs hooks for the manual.

Some pages pull a file from the development notebook (`docs/`) or the repository root in
verbatim: `--8<-- "path"` on a line of its own, the pymdownx.snippets syntax. The included
files link to turn reports and decision memos that are not part of the site, and to each
other by repository path, so this hook does the inclusion itself (before MkDocs validates
links) and turns such links into plain text. Links to the web and to pages of the manual are
left alone. pymdownx.snippets stays enabled for anything else.
"""

from __future__ import annotations

import re
from pathlib import Path

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
