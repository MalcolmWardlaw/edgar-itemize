"""Plain-text (pre-HTML) EDGAR documents -> Block stream.

Input is the <TEXT> payload of one document (latin-1 string) plus the byte
offset of that payload in the submission file.  Output blocks carry raw byte
offsets and a normalized text rendering.  Offsets are identity within a line;
the normalized text is the block lines joined by '\n', blocks joined by '\n\n'.
"""

from __future__ import annotations

import re

from .blocks import Block, caps_ratio
from .offsets import OffsetMap

_PAGE_RE = re.compile(r"^\s*<PAGE>\s*(\d+)?\s*$", re.IGNORECASE)
_SGML_MARK_RE = re.compile(r"^\s*<(/?TABLE|CAPTION|/?FN|S|C)>\s*$", re.IGNORECASE)
_TABLE_OPEN_RE = re.compile(r"^\s*<TABLE>", re.IGNORECASE)
_TABLE_CLOSE_RE = re.compile(r"^\s*</TABLE>", re.IGNORECASE)
_INLINE_TAG_RE = re.compile(r"<[^>\n]{1,40}>")
_UNDERLINE_RE = re.compile(r"^\s*[-=_]{3,}\s*$")
_HEADING_LINE_RE = re.compile(
    r"^\s{0,40}(?:PART\s+(?:I{1,3}|IV|[1-4])\b|ITEMS?\s*\d{1,2}\s*[A-Ca-c]?\b)(?![\d])", re.IGNORECASE
)
_ARTICLE_LINE_RE = re.compile(r"^\s{0,40}(?:ARTICLE\s+[IVXL\d]+|SECTION\s+\d+\.\d+|\d{1,2}\.\d{1,2}\s)", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _page_width(lines: list[str]) -> int:
    """Median length of long lines: the effective column width of the document."""
    lens = sorted(len(l.rstrip()) for l in lines if len(l.strip()) >= 50)
    if not lens:
        return 80
    return max(70, lens[len(lens) // 2] + 2)


def text_to_blocks(payload: str, raw_base: int, *, heading_split: bool = True) -> tuple[list[Block], str, OffsetMap]:
    blocks: list[Block] = []
    omap = OffsetMap()
    norm_parts: list[str] = []
    norm_pos = 0

    # Tokenize into lines with raw offsets.
    lines: list[tuple[int, str]] = []
    pos = 0
    for m in re.finditer(r"[^\n]*\n|[^\n]+$", payload):
        lines.append((raw_base + m.start(), m.group(0).rstrip("\r\n")))
        pos = m.end()
    width = _page_width([l for _, l in lines])

    in_table = False
    table_id = -1
    cur: list[tuple[int, str]] = []
    cur_in_table = False
    prev_blank = True
    pending_page: tuple[int, str] | None = None

    def flush(next_blank: bool) -> None:
        nonlocal cur, norm_pos, cur_in_table
        if not cur:
            return
        idx = len(blocks)
        stripped = [l.strip() for _, l in cur]
        text = "\n".join(_WS_RE.sub(" ", s) for s in stripped)
        first_raw = cur[0][0]
        # raw_end: end of last line content
        last_raw, last_line = cur[-1]
        raw_end = last_raw + len(last_line.rstrip())
        # leading whitespace offset of first line
        lead = len(cur[0][1]) - len(cur[0][1].lstrip())
        raw_start = first_raw + lead
        if norm_parts:
            norm_parts.append("\n\n")
            norm_pos += 2
        ns = norm_pos
        # map each line
        p = ns
        for k, (lr, l) in enumerate(cur):
            seg = _WS_RE.sub(" ", l.strip())
            q = 0  # position within seg
            for tm in re.finditer(r"\S+", l):
                omap.add(p + q, lr + tm.start(), len(tm.group(0)))
                q += len(tm.group(0)) + 1
            p += len(seg) + 1
        norm_parts.append(text)
        norm_pos += len(text)
        underline = False
        b = Block(
            idx=idx,
            text=text,
            raw_start=raw_start,
            raw_end=raw_end,
            norm_start=ns,
            norm_end=norm_pos,
            kind="para",
            lines=tuple(stripped),
            indent=float(lead),
            center=abs(lead - (width - len(stripped[0])) / 2) <= 4 and lead >= 4,
            caps_ratio=caps_ratio(text),
            underline=underline,
            in_table=cur_in_table,
            table_id=table_id if cur_in_table else -1,
            blank_before=prev_blank_at_start[0],
            blank_after=next_blank,
            line_raw_starts=tuple(lr + (len(l) - len(l.lstrip())) for lr, l in cur),
            line_raw_ends=tuple(lr + len(l.rstrip()) for lr, l in cur),
        )
        blocks.append(b)
        cur = []

    prev_blank_at_start = [True]

    def add_marker(raw_at: int, kind: str, text: str) -> None:
        nonlocal norm_pos
        idx = len(blocks)
        if norm_parts:
            norm_parts.append("\n\n")
            norm_pos += 2
        ns = norm_pos
        norm_parts.append(text)
        norm_pos += len(text)
        blocks.append(
            Block(idx=idx, text=text, raw_start=raw_at, raw_end=raw_at + len(text), norm_start=ns, norm_end=norm_pos, kind=kind, is_page_break=(kind == "page"))
        )

    for i, (lr, line) in enumerate(lines):
        if _PAGE_RE.match(line):
            flush(True)
            add_marker(lr, "page", "<PAGE>")
            prev_blank = True
            continue
        if _TABLE_OPEN_RE.match(line):
            flush(True)
            in_table = True
            table_id += 1
            prev_blank = True
            continue
        if _TABLE_CLOSE_RE.match(line):
            flush(True)
            in_table = False
            prev_blank = True
            continue
        if _SGML_MARK_RE.match(line):
            flush(True)
            prev_blank = True
            continue
        s = line.strip()
        if not s:
            flush(True)
            prev_blank = True
            continue
        if _UNDERLINE_RE.match(line) and cur:
            # underline for the previous line(s): mark and end the block
            cur_in_table = in_table
            flush(True)
            blocks[-1].underline = True
            prev_blank = True
            continue
        if _INLINE_TAG_RE.search(s) and not s.strip(" <>/").isalpha():
            # leave inline tags (rare in text era) as-is; they are part of text
            pass
        starts_heading = heading_split and (_HEADING_LINE_RE.match(line) or _ARTICLE_LINE_RE.match(line))
        if starts_heading and cur:
            flush(False)
            prev_blank = False
        if not cur:
            prev_blank_at_start[0] = prev_blank
            cur_in_table = in_table
        cur.append((lr, line))
        prev_blank = False
    flush(True)
    return blocks, "".join(norm_parts), omap
