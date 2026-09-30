"""HTML EDGAR documents -> Block stream, using the vendored HTMLParser.

HTMLParser reports the (line, column) of every token, which we convert to an
absolute byte offset; because the submission was decoded as latin-1, those are
byte offsets into the file on disk.  Tag soup from 2000s publishers is handled
by a permissive open-element stack.

The parser is ``edgar_itemize._html_parser`` (CPython 3.11.15's ``html.parser``,
vendored so the tokenisation of malformed markup does not change with the
interpreter's patch level; see that module's header). ``html.unescape`` is the
stdlib's: it is identical across the verified interpreters.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field

from ._html_parser import HTMLParser
from .blocks import Block, caps_ratio
from .classify import Profile
from .offsets import OffsetMap

_BLOCK_TAGS = {
    "p", "div", "td", "th", "tr", "table", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "title", "center",
    "blockquote", "pre", "dl", "dt", "dd", "form", "body", "html", "page", "section", "article", "header", "footer", "caption",
    "thead", "tbody", "tfoot", "address", "fieldset",
}
_SKIP_TAGS = {"script", "style", "head", "ix:header", "ix:hidden", "ix:references", "ix:resources"}
_VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr", "page"}
_TRANSPARENT_PREFIX = ("ix:", "xbrli:", "link:", "o:", "w:")

_STYLE_BOLD_RE = re.compile(r"font-weight\s*:\s*(bold|bolder|[6-9]00)", re.I)
_STYLE_ITALIC_RE = re.compile(r"font-style\s*:\s*italic", re.I)
_STYLE_UNDERLINE_RE = re.compile(r"text-decoration(?:-line)?\s*:\s*[^;]*underline", re.I)
_STYLE_CENTER_RE = re.compile(r"text-align\s*:\s*center", re.I)
_STYLE_SIZE_RE = re.compile(r"font-size\s*:\s*([\d.]+)\s*(pt|px|em|%)?", re.I)
_STYLE_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.I)
_STYLE_PAGEBREAK_RE = re.compile(r"page-break-(?:before|after)\s*:\s*always", re.I)
_STYLE_INDENT_RE = re.compile(r"(?:margin-left|padding-left|text-indent)\s*:\s*(-?[\d.]+)\s*(pt|px|em|%|in)?", re.I)
_WS_RE = re.compile(r"[\s ]+")
_TOKEN_RE = re.compile(r"[^\s\xa0\ufeff\u200b\u200c\u200d\u2060]+")
_CP1252 = {"\x91": "'", "\x92": "'", "\x93": '"', "\x94": '"', "\x95": "•", "\x96": "–", "\x97": "—", "\x85": "…", "\xa0": " "}
_PRE_HEADING_RE = re.compile(r"^\s{0,40}(?:PART\s+(?:I{1,3}|IV|[1-4])\b|ITEMS?\s*\d{1,2}\s*[A-Ca-c]?\b|ARTICLE\s+[IVXL\d]+|SECTION\s+\d+)(?!\d)", re.IGNORECASE)
_BLOB_WS_RE = re.compile(r"[ \t\xa0]{2,}")
_TOC_BACKLINK_RE = re.compile(r"^\s*(?:table of contents|index|back to (?:top|index|contents))\s*$", re.I)


@dataclass(slots=True)
class _Style:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    center: bool = False
    size: float = 0.0  # 0 = inherit/unknown, in points
    indent: float = 0.0
    hidden: bool = False


@dataclass(slots=True)
class _Open:
    tag: str
    style: _Style
    link: bool = False  # <a href=...>


@dataclass(slots=True)
class _Run:
    raw_start: int
    raw_end: int
    text: str  # decoded, whitespace as-is
    style: _Style
    link: bool = False  # inside <a href=...>


_RUNIN_STOP_RE = re.compile(r"^\s*(?:items?\b|part\b|note\s*\d|notes?\s+to\b|table of contents|see\b|refer to|exhibits?\b|schedules?\b|section\b|article\b)", re.I)
_RUNIN_SEP_RE = re.compile(r"^\s*(?:[.:]|[-\u2013\u2014]\s)")


def _strip_backlinks(runs: list[_Run]) -> tuple[list[_Run], bool]:
    """Drop a 'Table of Contents' back-link glued to the start or end of a block (C3 M2).
    Returns the runs kept and whether any link run remains among them."""
    def is_backlink(group: list[_Run]) -> bool:
        return bool(group) and bool(_TOC_BACKLINK_RE.match("".join(r.text for r in group).replace("\xa0", " ")))
    nb = [r for r in runs if r.text.strip(" \t\r\n\xa0\ufeff")]
    if not nb or all(r.link for r in nb):
        return runs, any(r.link for r in nb)
    lead = []
    for r in nb:
        if r.link:
            lead.append(r)
        else:
            break
    tail = []
    for r in reversed(nb):
        if r.link:
            tail.append(r)
        else:
            break
    tail.reverse()
    drop = set()
    if is_backlink(lead):
        drop.update(id(r) for r in lead)
    if is_backlink(tail):
        drop.update(id(r) for r in tail)
    if not drop:
        return runs, any(r.link for r in nb)
    kept = [r for r in runs if id(r) not in drop]
    return kept, any(r.link for r in kept if r.text.strip(" \t\r\n\xa0\ufeff"))


def _runin_lead(runs: list[_Run], text: str) -> tuple[str, int] | None:
    """A short bold/underlined lead-in that opens a prose paragraph (C2): returns
    (lead text as it appears in the block text, raw end of the lead) or None."""
    nb = [r for r in runs if r.text.strip(" \t\r\n\xa0\ufeff") and r.text != "\n"]
    if len(nb) < 2:
        return None
    lead_runs = []
    for r in nb:
        if r.style.bold or r.style.underline:
            lead_runs.append(r)
        else:
            break
    if not lead_runs or len(lead_runs) == len(nb):
        return None
    lead = _WS_RE.sub(" ", "".join(r.text for r in lead_runs)).strip()
    rest_runs = nb[len(lead_runs):]
    rest = _WS_RE.sub(" ", "".join(r.text for r in rest_runs))
    if not lead or len(lead) > 70 or len(lead.split()) > 8 or not re.search(r"[A-Za-z]{2,}", lead):
        return None
    if _RUNIN_STOP_RE.match(lead) or lead[:1].islower():
        return None
    raw_end = lead_runs[-1].raw_start + len(lead_runs[-1].text.rstrip())
    if lead.endswith((".", ":")):
        pass
    elif _RUNIN_SEP_RE.match(rest):
        m = _RUNIN_SEP_RE.match(rest)
        rest = rest[m.end():]
    else:
        return None
    body = rest.strip()
    if len(body) < 60 or not body[:1].isupper() and not body[:1].isdigit() and body[:1] not in "\"'\u201c(":
        return None
    if sum(1 for c in body[:200] if c.islower()) < 30:
        return None
    if not text.startswith(lead):
        return None
    return lead, raw_end


@dataclass(slots=True)
class _Pending:
    runs: list[_Run] = field(default_factory=list)
    kind: str = "para"
    in_table: bool = False
    table_id: int = -1
    cell_col: int = -1
    row_key: int = -1
    is_page_break: bool = False
    anchor_ids: list[str] = field(default_factory=list)
    href_targets: list[str] = field(default_factory=list)
    preceding_comment: str = ""
    newlines: int = 0  # <br> count inside the block


def _size_pt(val: str, unit: str | None) -> float:
    try:
        v = float(val)
    except ValueError:
        return 0.0
    u = (unit or "pt").lower()
    if u == "px":
        return v * 0.75
    if u == "em":
        return v * 10.0
    if u == "%":
        return v / 10.0
    return v


_FONT_SIZE_TAG = {"1": 7.5, "2": 10.0, "3": 12.0, "4": 13.5, "5": 18.0, "6": 24.0, "7": 36.0}


class _Walker(HTMLParser):
    split_blobs = False  # image_text: split OCR blobs on 2+ spaces into pseudo-lines (A6)
    def __init__(self, raw: str, raw_base: int) -> None:
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.raw_base = raw_base
        self.line_starts = [0]
        for m in re.finditer(r"\n", raw):
            self.line_starts.append(m.end())
        self.stack: list[_Open] = []
        self.blocks: list[Block] = []
        self.pending = _Pending()
        self.skip_depth = 0
        self.table_stack: list[int] = []
        self.table_counter = 0
        self.row_counter = 0
        self.cell_col = -1
        self.row_blocks: dict[int, list[int]] = {}
        self.last_comment = ""
        self.pending_page_break = False
        self.pending_anchor_ids: list[str] = []
        self.br_streak = 0
        self.norm_parts: list[str] = []
        self.norm_pos = 0
        self.omap = OffsetMap()
        self.size_counter: Counter = Counter()
        self.pending_row_break = False
        self.pre_depth = 0

    # ---- positions
    def _abs(self) -> int:
        line, col = self.getpos()
        return self.raw_base + self.line_starts[line - 1] + col

    def _cur_style(self) -> _Style:
        return self.stack[-1].style if self.stack else _Style()

    # ---- block management
    def _flush(self) -> None:
        p = self.pending
        runs = p.runs
        if p.kind == "para" and runs:
            runs, link_left = _strip_backlinks(runs)
            if not link_left:
                p.href_targets = []  # the only link was a back-link: no toc.href evidence (C3 M2)
        if p.kind == "para" and not any(r.text.strip(" \t\r\n\xa0\ufeff") for r in runs):
            # keep anchors / comments for the next block
            self.pending = _Pending(anchor_ids=p.anchor_ids, href_targets=[], preceding_comment=p.preceding_comment, is_page_break=p.is_page_break)
            return
        idx = len(self.blocks)
        if self.norm_parts:
            self.norm_parts.append("\n\n")
            self.norm_pos += 2
        ns = self.norm_pos
        pieces: list[str] = []
        line_starts: list[int] = []
        line_ends: list[int] = []
        nchar_bold = nchar_ul = nchar_it = nchar = 0
        first_style = runs[0].style if runs else _Style()
        sizes: Counter = Counter()
        cur_len = 0
        prev_ws = True  # whitespace seen since the last emitted token
        for r in runs:
            last_end = 0
            for tm in _TOKEN_RE.finditer(r.text):
                tok = tm.group(0)
                if tok == "\n":
                    continue
                if tm.start() > last_end:
                    prev_ws = True
                if pieces and not pieces[-1].endswith("\n") and prev_ws:
                    pieces.append(" ")
                    cur_len += 1
                if not pieces or pieces[-1].endswith("\n"):
                    line_starts.append(r.raw_start + tm.start())
                    line_ends.append(r.raw_start + tm.end())
                line_ends[-1] = r.raw_start + tm.end()
                self.omap.add(ns + cur_len, r.raw_start + tm.start(), len(tok))
                pieces.append(tok)
                cur_len += len(tok)
                last_end = tm.end()
                prev_ws = False
                n = len(tok)
                nchar += n
                if r.style.bold:
                    nchar_bold += n
                if r.style.underline:
                    nchar_ul += n
                if r.style.italic:
                    nchar_it += n
                if r.style.size:
                    sizes[r.style.size] += n
            if r.text and (r.text[-1].isspace() or r.text[-1] == "\xa0" or last_end < len(r.text)):
                prev_ws = True
            if r.text == "\n":
                if pieces and not pieces[-1].endswith("\n"):
                    pieces.append("\n")
                    cur_len += 1
        text = "".join(pieces).strip()
        if not text and p.kind == "para":
            self.pending = _Pending(anchor_ids=p.anchor_ids, preceding_comment=p.preceding_comment, is_page_break=p.is_page_break)
            return
        self.norm_parts.append(text)
        self.norm_pos += len(text)
        nonblank = [r for r in runs if r.text.strip(" \t\r\n \ufeff")]
        raw_start = nonblank[0].raw_start if nonblank else (runs[0].raw_start if runs else self._abs())
        raw_end = nonblank[-1].raw_end if nonblank else (runs[-1].raw_end if runs else raw_start)
        size = sizes.most_common(1)[0][0] if sizes else 0.0
        if size:
            self.size_counter[size] += nchar
        lines = tuple(l.strip() for l in text.split("\n"))
        runin = None
        # (table cells included: publishers wrap body paragraphs in one-cell layout tables;
        # headings.py keeps run-ins only from single-cell rows)
        if p.kind == "para" and nchar_bold < 0.5 * max(1, nchar) and nchar_ul < 0.5 * max(1, nchar):
            runin = _runin_lead(runs, text)
        blk = Block(
            idx=idx,
            text=text,
            raw_start=raw_start,
            raw_end=raw_end,
            norm_start=ns,
            norm_end=self.norm_pos,
            kind=p.kind,
            lines=lines,
            bold=(nchar_bold >= 0.5 * max(1, nchar)) or (first_style.bold and nchar <= 120),
            underline=(nchar_ul >= 0.5 * max(1, nchar)) or (first_style.underline and nchar <= 120),
            italic=nchar_it >= 0.5 * max(1, nchar),
            center=first_style.center,
            caps_ratio=caps_ratio(text),
            font_size_rel=size,  # absolute for now; normalized after the walk
            indent=first_style.indent,
            in_table=p.in_table,
            table_id=p.table_id,
            cell_col=p.cell_col,
            is_page_break=p.is_page_break,
            anchor_ids=tuple(p.anchor_ids),
            href_targets=tuple(p.href_targets),
            preceding_comment=p.preceding_comment,
            line_raw_starts=tuple(line_starts) or (raw_start,),
            line_raw_ends=tuple(line_ends) or (raw_end,),
            runin_text=runin[0] if runin else "",
            runin_raw_end=runin[1] if runin else -1,
        )
        self.blocks.append(blk)
        if p.row_key >= 0:
            self.row_blocks.setdefault(p.row_key, []).append(idx)
        self.pending = _Pending()

    def _begin_block_context(self) -> None:
        p = self.pending
        if self.table_stack:
            p.in_table = True
            p.table_id = self.table_stack[-1]
            p.cell_col = self.cell_col
            p.row_key = self.row_counter
        if self.pending_page_break:
            p.is_page_break = True
            self.pending_page_break = False
        if self.pending_anchor_ids:
            p.anchor_ids.extend(self.pending_anchor_ids)
            self.pending_anchor_ids = []
        if self.last_comment:
            p.preceding_comment = self.last_comment
            self.last_comment = ""

    # ---- handlers
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            self.stack.append(_Open(tag, self._cur_style()))
            return
        parent = self._cur_style()
        st = _Style(parent.bold, parent.italic, parent.underline, parent.center, parent.size, parent.indent, parent.hidden)
        style = a.get("style", "")
        if tag in ("b", "strong") or tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            st.bold = True
        if tag in ("i", "em"):
            st.italic = True
        if tag == "u":
            st.underline = True
        if tag == "center" or a.get("align", "").lower() == "center":
            st.center = True
        if tag == "font" and a.get("size"):
            st.size = _FONT_SIZE_TAG.get(a["size"].strip().lstrip("+"), st.size)
        if style:
            if _STYLE_BOLD_RE.search(style):
                st.bold = True
            elif re.search(r"font-weight\s*:\s*(normal|[1-5]00)", style, re.I):
                st.bold = False
            if _STYLE_ITALIC_RE.search(style):
                st.italic = True
            if _STYLE_UNDERLINE_RE.search(style):
                st.underline = True
            if _STYLE_CENTER_RE.search(style):
                st.center = True
            elif re.search(r"text-align\s*:\s*(left|right|justify)", style, re.I):
                st.center = False
            m = _STYLE_SIZE_RE.search(style)
            if m:
                st.size = _size_pt(m.group(1), m.group(2))
            if _STYLE_HIDDEN_RE.search(style):
                st.hidden = True
            if _STYLE_PAGEBREAK_RE.search(style):
                self.pending_page_break = True
            m = _STYLE_INDENT_RE.search(style)
            if m and tag in _BLOCK_TAGS:
                st.indent = _size_pt(m.group(1), m.group(2)) if m.group(2) != "%" else _size_pt(m.group(1), None) * 5
        if tag in ("h1", "h2", "h3"):
            st.size = max(st.size, 14.0)
        anchor = a.get("id") or (a.get("name") if tag == "a" else None)
        if anchor:
            self.pending_anchor_ids.append(anchor)
        is_link = tag == "a" and bool(a.get("href"))
        if tag == "a" and a.get("href", "").startswith("#") and len(a["href"]) > 1:
            # bare href="#" is an anchor target template, not a link out
            self.pending.href_targets.append(a["href"][1:])
        if tag == "br":
            self.br_streak += 1
            if self.br_streak >= 2:
                self._flush()
            else:
                self.pending.runs.append(_Run(self._abs(), self._abs() + 4, "\n", st))
            return
        self.br_streak = 0
        if tag == "hr":
            self._flush()
            self._begin_block_context()
            self.pending.kind = "hr"
            self.pending.runs.append(_Run(self._abs(), self._abs() + 4, "----", st))
            self._flush()
            return
        if tag == "page":
            self._flush()
            self._begin_block_context()
            self.pending.kind = "page"
            self.pending.is_page_break = True
            self.pending.runs.append(_Run(self._abs(), self._abs() + 6, "<PAGE>", st))
            self._flush()
            return
        if tag == "pre":
            self.pre_depth += 1
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag == "table":
                self.table_counter += 1
                self.table_stack.append(self.table_counter)
                self.cell_col = -1
            elif tag == "tr":
                self.row_counter += 1
                self.cell_col = -1
            elif tag in ("td", "th"):
                self.cell_col += 1
            self._begin_block_context()
        if tag not in _VOID_TAGS:
            self.stack.append(_Open(tag, st, is_link))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS or tag in _BLOCK_TAGS:
            self.handle_starttag(tag, attrs)
            if tag in _BLOCK_TAGS and tag not in _VOID_TAGS:
                self.handle_endtag(tag)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        # pop to the matching open tag if present
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                closed = self.stack[i:]
                del self.stack[i:]
                for o in closed:
                    if o.tag in _SKIP_TAGS:
                        self.skip_depth = max(0, self.skip_depth - 1)
                break
        else:
            return
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag == "table" and self.table_stack:
                self.table_stack.pop()
            elif tag == "tr":
                self._finish_row()
        self.br_streak = 0

    def _finish_row(self) -> None:
        idxs = self.row_blocks.pop(self.row_counter, [])
        if len(idxs) > 1:
            cells = [self.blocks[i].text for i in idxs]
            first = self.blocks[idxs[0]]
            first.row_text = " ".join(c for c in cells if c)
            first.row_raw_end = self.blocks[idxs[-1]].raw_end
            first.row_href = any(self.blocks[i].href_targets for i in idxs)
            first.row_bold = any(self.blocks[i].bold for i in idxs)

    def handle_data(self, data: str) -> None:
        if self.skip_depth or self._cur_style().hidden:
            return
        if not data:
            return
        start = self._abs()
        if self.pre_depth:
            self._pre_data(data, start)
            return
        if data.strip(" \t\r\n\xa0"):
            self.br_streak = 0
            self._begin_block_context()
        st = self._cur_style(); link = any(o.link for o in self.stack)
        if self.split_blobs and "\n" not in data and _BLOB_WS_RE.search(data):
            # scanned-image exhibits (A6): the hidden OCR layer collapses line breaks into
            # runs of 2+ spaces; restore pseudo-lines so labels on later lines are visible.
            # Offsets are preserved: each "\n" run spans the whitespace it replaces.
            pos = 0
            for m in _BLOB_WS_RE.finditer(data):
                if m.start() > pos:
                    self.pending.runs.append(_Run(start + pos, start + m.start(), data[pos:m.start()], st, link))
                self.pending.runs.append(_Run(start + m.start(), start + m.end(), "\n", st, link))
                pos = m.end()
            if pos < len(data):
                self.pending.runs.append(_Run(start + pos, start + len(data), data[pos:], st, link))
            return
        self.pending.runs.append(_Run(start, start + len(data), data, st, link))

    def _pre_data(self, data: str, start: int) -> None:
        """Preformatted text: blank lines end blocks, heading-like lines start blocks, <PAGE> is a page mark."""
        st = self._cur_style()
        for m in re.finditer(r"[^\n]*\n|[^\n]+$", data):
            line = m.group(0)
            raw_at = start + m.start()
            body = line.rstrip("\r\n")
            if not body.strip():
                self._flush()
            elif re.match(r"\s*<PAGE>", body, re.I):
                self._flush()
                self._begin_block_context()
                self.pending.kind = "page"
                self.pending.is_page_break = True
                self.pending.runs.append(_Run(raw_at, raw_at + len(body), "<PAGE>", st))
                self._flush()
            else:
                if _PRE_HEADING_RE.match(body) and self.pending.runs:
                    self._flush()
                self._begin_block_context()
                lead = len(body) - len(body.lstrip())
                self.pending.runs.append(_Run(raw_at + lead, raw_at + len(body), body.strip(), st))
                self.pending.runs.append(_Run(raw_at + len(body), raw_at + len(line), "\n", st))

    def _ref(self, ch: str) -> None:
        if self.skip_depth or self._cur_style().hidden:
            return
        start = self._abs()
        raw_len = len(self.rawdata) and 1
        # find the actual reference length in raw
        m = re.match(r"&#?[A-Za-z0-9]+;?", self.raw[start - self.raw_base : start - self.raw_base + 12])
        raw_len = m.end() if m else 1
        if ch.strip(" \xa0"):
            self._begin_block_context()
        self.pending.runs.append(_Run(start, start + raw_len, ch, self._cur_style()))

    def handle_entityref(self, name: str) -> None:
        ch = html.unescape(f"&{name};")
        if ch == f"&{name};":
            ch = "&" + name
        self._ref(_CP1252.get(ch, ch))

    def handle_charref(self, name: str) -> None:
        try:
            ch = chr(int(name[1:], 16)) if name[:1] in "xX" else chr(int(name))
        except ValueError:
            ch = ""
        ch = _CP1252.get(ch, ch)
        self._ref(ch or " ")

    def handle_comment(self, data: str) -> None:
        d = data.strip()
        if d:
            self.last_comment = d[:200]


def html_to_blocks(payload: str, raw_base: int, profile: Profile | None = None) -> tuple[list[Block], str, OffsetMap]:
    w = _Walker(payload, raw_base)
    w.split_blobs = bool(profile is not None and profile.era == "image_text")
    w.feed(payload)
    # a malformed charref ("&#" split by a line wrap, "&#151Cash") stalls HTMLParser's
    # goahead; without a resume the rest of the document lands in one giant data block.
    # feed("") re-enters goahead after each stall point; offsets are untouched.
    prev = None
    while w.rawdata and w.rawdata != prev:
        prev = w.rawdata
        w.feed("")
    w.close()
    w._flush()
    blocks = w.blocks
    modal = w.size_counter.most_common(1)[0][0] if w.size_counter else 0.0
    for b in blocks:
        b.font_size_rel = (b.font_size_rel / modal) if (modal and b.font_size_rel) else 1.0
        if b.kind == "para" and _TOC_BACKLINK_RE.match(b.text) and (b.href_targets or b.is_page_break):
            b.kind = "nav"
    return blocks, "".join(w.norm_parts), w.omap
