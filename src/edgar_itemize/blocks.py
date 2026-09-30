"""Era-agnostic block stream: the unit that heading detection operates on."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Block:
    idx: int
    text: str  # normalized text (whitespace collapsed within lines, entities decoded)
    raw_start: int  # byte offset in the submission file
    raw_end: int
    norm_start: int  # offset in the normalized document text
    norm_end: int
    kind: str = "para"  # para | page | hr | table | comment
    lines: tuple[str, ...] = ()  # text-era: original lines (stripped) of this block
    bold: bool = False
    underline: bool = False
    italic: bool = False
    center: bool = False
    caps_ratio: float = 0.0
    font_size_rel: float = 1.0
    indent: float = 0.0
    in_table: bool = False
    table_id: int = -1
    cell_col: int = -1
    row_text: str = ""  # set on the first cell block of a table row: all cell texts joined
    row_raw_end: int = -1
    row_href: bool = False
    row_bold: bool = False
    is_page_break: bool = False
    anchor_ids: tuple[str, ...] = ()
    href_targets: tuple[str, ...] = ()
    preceding_comment: str = ""
    blank_before: bool = True
    blank_after: bool = True
    # raw offset of each line start (text era) or text run (html), for sub-block addressing
    line_raw_starts: tuple[int, ...] = field(default=())
    line_raw_ends: tuple[int, ...] = field(default=())
    # run-in sub-heading (C2): a short styled lead-in that opens a prose paragraph
    runin_text: str = ""
    runin_raw_end: int = -1

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @property
    def n_lines(self) -> int:
        return max(1, len(self.lines))


def caps_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)
