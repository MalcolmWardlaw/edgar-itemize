"""Kind classifier for EX-13 (Annual Report to Shareholders) headings.

Post-`find_headings` only, not by the LLM: a small regex classifier over each
heading node's title, mirroring the category boundaries the Turn 7 `ars` judge
prompt encodes (`src/edgar_itemize/llm.py` ARS_SYSTEM / ARS_KINDS) and starting
from `scripts/judge_batch.py`'s `_FIN_CAPTION_RE`. Mapping is not 1:1 with
Item 7/8 (`docs/turn7_decisions/b_ex13.md` section 4b):

  mdna                          -> ITEM 7
  financial_statements          -> ITEM 8
  notes_to_financial_statements -> ITEM 8
  auditors_report               -> ITEM 8
  selected_financial_data       -> ITEM 6

letter_to_shareholders / business_description / other carry no 10-K item to
satisfy and are not classified here (satisfies_item stays null).

Turn 8 (Phase B.5, `docs/turn8_decisions/a4_ex13.md`): the 200-window judge
bank found six alias-phrase families the four base patterns above miss (91/100
classifier precision, 36/100 null-side aliases). Each family's fix is added as
its own `<family>_alias` pattern/kind/rule id (`kind.mdna_alias`,
`kind.selected_alias`, `kind.finstmt_alias`, `kind.auditors_alias`) rather than
edited into the base pattern, so the base `kind.<family>` rule id keeps its
Turn 7 meaning on every title it already matched and the new alias hits are
separately auditable. Each `_alias` pattern sits immediately after its base
pattern (still before the generic `finstmt` catch-all, per the existing
precedence rule below) so a title matched by the strict base pattern is never
re-tagged under the looser alias one. A title with a `_alias` kind is not
double-counted: `classify()` still returns on the first match, in list order.
"""

from __future__ import annotations

import regex

# Turn 8 (a4 section 3a): a table-of-contents row -- a dot-leader or underscore
# leader run followed by a trailing page number -- gets no kind at all, checked
# before any pattern below. Catches e.g. "Financial Highlights .......... 1"
# gpt-oss/qwen both mis-read as a real section heading (a4_ex13.md section 2).
_TOC_ROW_RE = regex.compile(r"(?:\.\s?){3,}\s*\d+\s*$|(?:_\s?){3,}\s*\d+\s*$")

# Checked in order; the first match wins. "notes"/"auditors"/"selected"/"mdna"
# (base and alias) are checked before the more generic "finstmt" catch-all so a
# note title or an auditors' report caption is never mis-read as a bare
# financial statement.
_KIND_PATTERNS: tuple[tuple[str, regex.Pattern], ...] = (
    ("mdna", regex.compile(r"management'?s?\s+discussion\s+and\s+analysis", regex.IGNORECASE)),
    # a4 3b: "&" for "and", and a bare "discussion of ... condition" with no
    # "analysis" at all -- e.g. "MANAGEMENT'S DISCUSSION & ANALYSIS OF FINANCIAL
    # CONDITION...", "MANAGEMENT'S DISCUSSION OF CONSOLIDATED FINANCIAL CONDITION
    # AND RESULTS...". Does *not* match a bare "MANAGEMENT DISCUSSION" with no
    # qualifier (too generic to alias safely, per the memo).
    ("mdna_alias", regex.compile(
        r"management'?s?\s+discussion\s*(?:and|&)?\s*(?:analysis|of\s+(?:consolidated\s+)?financial\s+condition)",
        regex.IGNORECASE)),
    ("auditors", regex.compile(
        r"report\s+of\s+(?:independent|management)|"
        r"independent\s+(?:registered\s+public\s+accounting\s+firm|auditors?'?s?\s+report|accountants?'?\s+report)",
        regex.IGNORECASE)),
    # a4 3e: front-matter management-representation captions conventionally
    # placed next to the auditors' opinion letter -- "RESPONSIBILITY FOR
    # FINANCIAL STATEMENTS", "MANAGEMENT'S REPORT ON FINANCIAL STATEMENTS".
    ("auditors_alias", regex.compile(
        r"(?:responsibility\s+for|management'?s?\s+report\s+on)\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements",
        regex.IGNORECASE)),
    ("selected", regex.compile(r"selected\s+(?:consolidated\s+)?financial\s+data", regex.IGNORECASE)),
    # a4 3c: "information" as well as "data", extra qualifying words in between
    # ("quarterly", "and other"), and a separate "financial highlights" phrasing
    # with no "selected" at all. Known accepted false-positive risk: "SELECTED
    # QUARTERLY FINANCIAL DATA" is genuinely ambiguous (section-opening in 2 of
    # 3 bank instances, a nested subheading in the third) -- see a4_ex13.md 3c.
    ("selected_alias", regex.compile(
        r"selected\s+(?:consolidated\s+)?(?:quarterly\s+)?financial\s+(?:[a-z]+\s+){0,3}(?:data|information)|"
        r"financial\s*(?:&|and)?\s*(?:operating\s+)?highlights",
        regex.IGNORECASE)),
    ("notes", regex.compile(r"notes\s+to\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements", regex.IGNORECASE)),
    ("finstmt", regex.compile(
        r"consolidated\s+balance\s+sheets?|"
        r"consolidated\s+statements?\s+of\s+(?:income|operations|cash\s+flows?|stockholders.?\s+equity|changes\s+in)|"
        r"index\s+to\s+(?:consolidated\s+)?financial\s+statements|"
        r"financial\s+statements\s+and\s+supplementary\s+data|"
        r"consolidated\s+financial\s+statements",
        regex.IGNORECASE)),
    # a4 3d: "consolidating" (not just "consolidated"), "shareholders" (not just
    # "stockholders"), and "changes on" (a filer typo/variant of "changes in").
    # Deliberately does *not* add "condensed" as an alternative to "consolidated"
    # -- "CONDENSED STATEMENT(S) OF INCOME" is very likely the Reg S-X
    # parent-company-only note pattern, not a primary Item 8 statement; see
    # a4_ex13.md section 2.
    ("finstmt_alias", regex.compile(
        r"consolidat(?:ed|ing)\s+balance\s+sheets?|"
        r"consolidat(?:ed|ing)\s+statements?\s+of\s+(?:income|operations|cash\s+flows?|"
        r"(?:stock|share)holders.?\s+equity|changes\s+(?:in|on))",
        regex.IGNORECASE)),
)

# kind -> (10-K item this kind satisfies, rule id appended to the node)
KIND_ITEM: dict[str, str] = {
    "mdna": "ITEM 7",
    "mdna_alias": "ITEM 7",
    "finstmt": "ITEM 8",
    "finstmt_alias": "ITEM 8",
    "notes": "ITEM 8",
    "auditors": "ITEM 8",
    "auditors_alias": "ITEM 8",
    "selected": "ITEM 6",
    "selected_alias": "ITEM 6",
}
KIND_RULE: dict[str, str] = {k: f"kind.{k}" for k in KIND_ITEM}


def classify(title: str | None) -> tuple[str, str] | None:
    """Return (kind, satisfies_item) for a heading title in an EX-13 document, or None."""
    if not title:
        return None
    if _TOC_ROW_RE.search(title):
        return None
    for kind, pat in _KIND_PATTERNS:
        if pat.search(title):
            return kind, KIND_ITEM[kind]
    return None


def block_in_toc_region(block_idx: int | None, toc_block_ranges: "list[tuple[int, int]]") -> bool:
    """Turn 8 (a4_ex13.md): the other half of the TOC-row guard -- a heading whose
    block sits inside a detected TOC region (`toc.py`'s cross-window evidence: dense
    duplicated/leader-hinted runs, not visible from title text alone) gets no
    `satisfies_item` regardless of what its title says. `toc_block_ranges` is
    `[(region.block_start, region.block_end), ...]`; `block_idx` is None when the
    heading's block could not be matched (never suppressed in that case)."""
    if block_idx is None:
        return False
    return any(lo <= block_idx <= hi for lo, hi in toc_block_ranges)
