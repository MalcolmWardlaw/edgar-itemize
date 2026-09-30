"""Form 10-Q agenda: Part I (financial information) and Part II (other information).

Item numbering restarts in Part II, so the order key encodes the Part: Part I
Items 1-4 -> 10..40, Part II Items 1-6 -> 110..160.  Because the label alone is
ambiguous ("Item 1" appears in both Parts), canonicalization needs the current
Part, which the tree builder supplies via `set_context`.
"""

from __future__ import annotations

import regex

from .base import LevelSpec
from .form10k import PART_RE, _ROMAN, _ITEM_WORD

# Turn 12 B.4 (A8): the 10-Q's own word-spelled item numbers never exceed Item 6, so the
# word alternation is a filtered copy of form10k's ONE..SIXTEEN table, not the whole thing
# -- restricting the alternation keeps a stray "SEVEN"/"EIGHT" elsewhere on a heading line
# from ever being mistaken for a 10-Q item number, which cannot exist.
_ITEM_WORD_10Q = {w: n for w, n in _ITEM_WORD.items() if n <= 6}
_ITEM_WORD_ALT_10Q = "|".join(sorted(_ITEM_WORD_10Q, key=len, reverse=True))
# Roman numerals the 10-Q's own items actually use (I-IV; census turn12-a4-10q-census-
# corrected.parquet found zero real V/VI romans) -- kept as its own set, distinct from
# `_ITEM_WORD_10Q`, so label_rules can tell "II" (gram.item.roman) from "TWO"
# (gram.item.word) without ambiguity.
_ROMAN_NUMS_10Q = {"I", "II", "III", "IV"}

ITEM_RE = regex.compile(
    # gram.item.4t (Turn 8): the SEC's 2007-2010 transitional internal-control
    # numbering ("Item 4T", Release 33-8934) adds a "T" to the suffix alternation
    # alongside the ordinary A-C letters; canonicalize() below folds it back into
    # the Item 4 slot (it is not a distinct item the way 1A/7A/9A are on the 10-K).
    #
    # Turn 12 B.4 (A8, docs/turn12_decisions/a4_label_family.md section 6.1): the `num`
    # group gains the roman and spelled-word alternatives form10k.ITEM_RE already has
    # (10,128 positive roman hits / 8,235 documents, 250/54 word, seed-12 hand read
    # 98/40 real -- turn12-a4-rulings.txt), and the separator between `num` and `suf`
    # widens from bare whitespace to also accept a dot, dash or parenthesis
    # ("Item 1.A", "Item 1-A", "Item 4(T)" -- gram.item.suffix_sep, 1,272 positive hits
    # / 1,130 documents, 95% real, folding in the `4T` correction from section 1.1).
    rf"^[\s\"'\(\[\*•\-]*I\s?T\s?E\s?M(?P<plural>S)?(?:\s*No\.?)?\s*"
    rf"(?P<num>\d{{1}}|(?=[IV]+\b)(?:IV|I{{1,3}})|(?<=\s)(?:{_ITEM_WORD_ALT_10Q})(?![A-Za-z]))"
    r"(?:[\s.\-–—]*\(?(?P<suf>[A-CTa-ct])\)?)?(?![\d\w])"
    r"(?P<sep>\s*[\.:\-–—\)]+\s*|\s+|$)(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)

PART1_TITLES = {"1": "Financial Statements", "2": "Management's Discussion and Analysis", "3": "Quantitative and Qualitative Disclosures About Market Risk", "4": "Controls and Procedures"}
PART2_TITLES = {"1": "Legal Proceedings", "1A": "Risk Factors", "2": "Unregistered Sales of Equity Securities", "3": "Defaults Upon Senior Securities",
                "4": "Mine Safety Disclosures / Submission of Matters to a Vote", "5": "Other Information", "6": "Exhibits"}
# Turn 12 B.4 re-gate (R-B4-6, docs/turn12_decisions/b4_labels_build.md section 6): the two
# substring hints are the SECOND tier of the Part decision, between the anchored statutory
# table (`_decide_part`) and the tree's positional fallback (`gram.part_from_context`). The
# first 10-Q gate read (v7 -> v8) retired them outright and lost 887 real Part I headings
# whose title the anchored table misses ("Controls and Procedures" as a 10-QSB Item 3,
# "Qualititive" and the other typos, "Management's Discussions", "Defaults under Senior
# Securities", "Security-Holders") to the positional fallback, which resolves against the
# contents page's own PART II row when B.1 leaves it live.
_PART2_HINT = regex.compile(r"legal|risk factors|unregistered|defaults|mine safety|submission of matters|other information|exhibits", regex.IGNORECASE)
_PART1_HINT = regex.compile(r"financial statements|management|discussion|quantitative|controls and procedures", regex.IGNORECASE)

# The statutory title of each 10-Q item, anchored: same contract as
# Form10KGrammar._ITEM_TITLE_RE (form10k.py) -- used by title_matches()/title_match_end()
# to tell a run-in heading ("ITEM 1A. Risk Factors. The Company is subject to...") from a
# sentence that merely mentions the item ("Item 1A of Form 10-K.").  Keys are the
# Part-qualified item key (`canon.split(" ", 1)[1]`, e.g. "I.1", "II.4") that
# Form10QGrammar.canonicalize already produces, so no extra lookup is needed to match a
# candidate's `canon` against this table.
#
# Source of the patterns: runs/judge/turn10-10qtitle-table.json (Turn 10 A.2,
# docs/turn10_decisions/a2_10q_titles.md), built from the form's own instructions and
# widened from a corpus census of 5,787,846 accepted 10-Q item nodes in runs/full_10q_v3
# (96.8% title-match recall against that population, section 3 of the decision doc).
# Embedded here as code, not loaded from the JSON at import, so the grammar carries no
# runtime file dependency -- the same choice form10k.py made for its own table.
#
# Era: the JSON also records from_year/to_year per item (I.3 from 1998, I.4 from 2003,
# II.1A from 2006 -- Form10QGrammar.ERA_REQUIRED already gates these for
# expected_items()/completeness).  title_matches() below does NOT re-apply that gate,
# matching Form10KGrammar's precedent (its own repurposed-item alternations for 4/6/14
# carry no era gate either): the census that built this table found a real pre-mandate
# population using the later wording voluntarily (e.g. "Controls and Procedures" before
# 2003's mandate) which an era gate here would wrongly refuse to match, and the item's
# existence as a label is not itself year-gated in canonicalize() -- only its title
# stops appearing, which the alternation set already tracks by including only wordings
# the form has actually used, not by a from_year test at match time.
_AP = r"['‘’ʼ`]?s?"
_ITEM_TITLE_RE = {k: regex.compile(v, regex.IGNORECASE) for k, v in {
    "I.1": r"(?:condensed\s+|consolidated\s+|unaudited\s+|interim\s+|index\s+to\s+)*financial\s+statements?\b|financial\s+information\b",
    # 10-QSB wording ("...or Plan of Operation") outlived the form's 2008 retirement in
    # filer boilerplate (n=7,463, 1995-2026) -- included here, not as a separate table.
    "I.2": rf"management{_AP}\s+discussion\s+and\s+analysis\b|management{_AP}\s+discussion\s+and\s+analysis\s+or\s+plan\s+of\s+operations?\b",
    "I.3": r"qu?antitative\s+and\s+qualitative\s+disclosures?\b|qualitative\s+and\s+quantitative\s+disclosures?\b",
    "I.4": r"(?:disclosure\s+|evaluation\s+of\s+disclosure\s+)?controls\s+and\s+procedures\b",
    "II.1": r"legal\s+proceedings?\b|litigation\b",
    "II.1A": r"risks?\s+factors?\b",
    "II.2": r"unregistered\s+sales?\s+of\s+(?:equity\s+)?securities\b|changes?\s+in\s+securities\b|recent\s+sales?\s+of\s+unregistered\s+securities\b",
    "II.3": r"defaults?\s+(?:upon|on|in|by)\s+.{0,20}?senior\s+securit(?:y|ies)\b",
    # II.4's three-era wording change (submission of matters -> removed and reserved ->
    # mine safety, 2010/2012) is covered by alternation, not a from_year split -- the
    # item itself never stopped existing, only its statutory title did.
    "II.4": r"submissions?\s+of\s+matters?\s+to\s+(?:a\s+)?vote\s+of\s+securit(?:y|ies)\s+holders\b|mine\s+safety\s+disclosures?\b|\(?\s*removed\s+and\s+reserved\s*\)?\b|\(?\s*reserved\s*\)?\b",
    "II.5": r"other\s+information\b|other\s+events\b|other\s+matters\b",
    "II.6": r"exhibits?\b",
}.items()}


def _suffix_via_separator(m: regex.Match) -> bool:
    """True when the `suf` letter was reached through the Turn 12 B.4 widened separator (a
    dot, dash or parenthesis between `num` and `suf`) rather than glued / space-separated."""
    if not m.group("suf"):
        return False
    between = m.string[m.end("num"):m.start("suf")]
    return any(ch in between for ch in ".-\u2013\u2014(")


def _decide_part(key: str, title: str) -> str | None:
    """Turn 12 B.4 (A9, docs/turn12_decisions/a4_label_family.md section 6.3): replaces
    the two substring `_PART1_HINT`/`_PART2_HINT` alternations, which were never updated
    for the Turn 10 title table and disagreed with it on 2,552 documents (594 already-
    accepted nodes, 5/5 hand-read real; 1,958 more among the rejected population). Takes
    the Part whose statutory title consumes the LONGER anchored prefix of `title` --
    exactly `scripts/turn12/a4_wrongpart.py`'s `decide_part()` reference implementation,
    which this ships unchanged. `title_match_end` only matches a combo that exists in
    `_ITEM_TITLE_RE` (e.g. a PART2-only key like "1A" never has an "I.1A" entry), so an
    unambiguous key naturally returns exactly one of e1/e2 and never both."""
    e1 = Form10QGrammar.title_match_end("item", f"ITEM I.{key}", title)
    e2 = Form10QGrammar.title_match_end("item", f"ITEM II.{key}", title)
    if e1 is None and e2 is None:
        return None
    if e1 is not None and e2 is not None:
        return None if e1 == e2 else ("I" if e1 > e2 else "II")
    return "I" if e1 is not None else "II"


class Form10QGrammar:
    name = "form10q"
    levels = (
        LevelSpec(kind="part", depth=1, pattern=PART_RE, canonical_only=True),
        LevelSpec(kind="item", depth=2, pattern=ITEM_RE, canonical_only=True),
    )

    def canonicalize(self, kind: str, m: regex.Match) -> str | None:
        if kind == "part":
            n = _ROMAN.get(m.group("n").upper())
            return f"PART {['I', 'II'][n - 1]}" if n in (1, 2) else None
        if kind == "item":
            raw_num = m.group("num")
            # Turn 12 B.4 (A8, section 6.1): `num` may now be a roman numeral ("II") or a
            # spelled word ("TWO") instead of a bare digit -- map it through the same
            # tables form10k uses for its own item label before anything below runs.
            if raw_num.isdigit():
                num = raw_num
            else:
                mapped = _ROMAN.get(raw_num.upper()) or _ITEM_WORD.get(raw_num.upper())
                if mapped is None:
                    return None
                num = str(mapped)
            suf = (m.group("suf") or "").upper()
            if suf == "T":
                # "Item 4T. Controls and Procedures" only ever numbered Part I's Item 4
                # (non-accelerated filers, fiscal periods 2007 through the 2010 sunset);
                # Part II never used a "4T" — reject rather than guess for any other num.
                # Now reachable via "4.T"/"4-T"/"4(T)" too (section 6.1), not only "4T"/"4 T".
                return "ITEM I.4" if num == "4" else None
            key = f"{num}{suf}"
            if key not in PART1_TITLES and key not in PART2_TITLES:
                # Turn 12 B.4 re-gate (R-B4-5, memo section 6): a lettered sub-caption reached
                # through the widened separator -- "Item 6. (a) Exhibits", "ITEM 6.(B) - REPORTS",
                # "Item 2. B. Other Developments", "ITEM 2 (C). CHANGES IN SECURITIES" -- is not
                # a suffix of a 10-Q item. The first gate read refused the whole line here
                # (2,752 real headings lost, 2,511 of them Item 6, the II.6 tier down 2,474
                # documents, 133 single-item trees emptied); refuse the SUFFIX instead and read
                # the line as the bare item number, exactly as the pre-Turn-12 regex did.
                if suf and _suffix_via_separator(m) and (num in PART1_TITLES or num in PART2_TITLES):
                    key = num
                else:
                    return None
            title = (m.group("title") or "")[:80]
            # Turn 12 B.4 (A9, section 6.3): decide the Part from the item's own statutory
            # title via `_decide_part` (title_match_end, anchored) rather than the retired
            # substring hints. A key valid in only one Part resolves to it directly and
            # unconditionally -- unchanged from the pre-Turn-12 behavior, and needed so a
            # bare label with no title text on its line ("Item 1A" with the title on the
            # next block) still resolves instead of falling back to the ambiguous marker
            # for a key that was never actually ambiguous. Only a key valid in BOTH Parts
            # (1/2/3/4) needs the title to decide, and only that case falls through to
            # "ITEM ?.<key>" when the title decides nothing -- the tree builder's own
            # position-based resolution (`gram.part_from_context`) still runs on it.
            part = _decide_part(key, title)
            if part is not None:
                return f"ITEM {part}.{key}"
            if key in PART2_TITLES and key not in PART1_TITLES:
                return f"ITEM II.{key}"
            if key in PART1_TITLES and key not in PART2_TITLES:
                return f"ITEM I.{key}"
            # Turn 12 B.4 re-gate (R-B4-6, memo section 6): the pre-Turn-12 substring hints as
            # the second tier, in their original order (Part II first), for a key valid in both
            # Parts whose title the anchored table does not open with. Only a title neither hint
            # touches reaches the positional marker below.
            if _PART2_HINT.search(title):
                return f"ITEM II.{key}"
            if _PART1_HINT.search(title):
                return f"ITEM I.{key}"
            return f"ITEM ?.{key}"  # ambiguous: resolved by the tree builder from the enclosing Part
        return None

    @staticmethod
    def label_rules(kind: str, m: regex.Match) -> list[str]:
        """gram.item.4t (Turn 8): records that a label was spelled with the SEC's
        transitional "4T" suffix -- canonicalize() already folds it straight into
        "ITEM I.4" (the same slot as an ordinary "Item 4"), so this hook is the only
        place the distinction survives, mirroring Form10KGrammar.label_rules'
        gram.item.word/gram.item.roman (same item, different spelling, not a
        different label_canon).

        Turn 12 B.4 (A8, section 6.1) adds three more spelling tags, mirroring
        Form10KGrammar's own gram.item.word/gram.item.roman precedent: gram.item.roman
        when `num` matched a roman numeral, gram.item.word when it matched a spelled
        word, and gram.item.suffix_sep when the text between `num` and `suf` was a dot,
        dash or parenthesis rather than bare whitespace ("Item 1.A", "Item 4(T)")."""
        if kind != "item":
            return []
        rules: list[str] = []
        raw = (m.group("num") or "").upper()
        if raw in _ROMAN_NUMS_10Q:
            rules.append("gram.item.roman")
        elif raw in _ITEM_WORD_10Q:
            rules.append("gram.item.word")
        if (m.group("suf") or "").upper() == "T":
            rules.append("gram.item.4t")
        if m.group("suf") and _suffix_via_separator(m):
            rules.append("gram.item.suffix_sep")
            # R-B4-5: the suffix was refused and the line read as the bare number (see
            # canonicalize) -- recorded so the diff can count the sub-caption headings.
            num = raw if raw.isdigit() else str(_ROMAN.get(raw) or _ITEM_WORD.get(raw) or "")
            key = f"{num}{(m.group('suf') or '').upper()}"
            if key not in PART1_TITLES and key not in PART2_TITLES and key[-1:] != "T":
                rules.append("gram.item.suffix_refused")
        # R-B4-6: the Part was decided by the substring hint tier, not the anchored table
        # or position -- recorded for the same reason.
        num = raw if raw.isdigit() else str(_ROMAN.get(raw) or _ITEM_WORD.get(raw) or "")
        suf = (m.group("suf") or "").upper()
        key = f"{num}{suf}"
        if key not in PART1_TITLES and key not in PART2_TITLES and suf and suf != "T" and _suffix_via_separator(m):
            key = num
        if key in PART1_TITLES and key in PART2_TITLES and suf != "T":
            title = (m.group("title") or "")[:80]
            if _decide_part(key, title) is None and (_PART2_HINT.search(title) or _PART1_HINT.search(title)):
                rules.append("gram.item.part_hint")
        return rules

    @staticmethod
    def title_matches(kind: str, canon: str, title: str) -> bool:
        """gram.item.title_match (Turn 10 B.1): does `title` OPEN with this item's own
        statutory title?  Same contract and same rationale as
        Form10KGrammar.title_matches -- a run-in heading opens with the title and its
        section body continues on the same line; a genuine cross-reference never does.
        candidates.py reads this via getattr, so it fires only once this method exists."""
        return Form10QGrammar.title_match_end(kind, canon, title) is not None

    @staticmethod
    def title_match_end(kind: str, canon: str, title: str) -> int | None:
        """Where the statutory title `title_matches` found ENDS inside `title`, or None.
        Same contract as Form10KGrammar.title_match_end -- candidates.py's
        `rej.xref_pointer` reads the offset to tell a run-in heading from an
        incorporation-by-reference pointer sentence that happens to open with the title.

        `canon` is already Part-qualified ("ITEM I.1", "ITEM II.4", ...) so the table
        key is `canon.split(" ", 1)[1]` directly -- no further split needed the way
        `item_key()` does for order_key/part_of_item.  An ambiguous canon ("ITEM ?.3")
        has no table entry and correctly returns None: the Part is not yet resolved."""
        if kind != "item" or " " not in canon:
            return None
        pat = _ITEM_TITLE_RE.get(canon.split(" ", 1)[1])
        if pat is None:
            return None
        lead = len(title) - len(title.lstrip(" .:-–—\t"))
        m = pat.match(title[lead:])
        return lead + m.end() if m else None

    @staticmethod
    def item_key(canon: str) -> str:
        return canon.split(" ", 1)[1].split(".", 1)[1]

    @staticmethod
    def item_part(canon: str) -> str:
        return canon.split(" ", 1)[1].split(".", 1)[0]

    def order_key(self, kind: str, canon: str) -> int:
        if kind == "part":
            return ["PART I", "PART II"].index(canon) + 1
        key = self.item_key(canon)
        num = int("".join(ch for ch in key if ch.isdigit()))
        suf = {"": 0, "A": 1, "B": 2, "C": 3}["".join(ch for ch in key if ch.isalpha())]
        base = {"I": 0, "II": 100, "?": 0}[self.item_part(canon)]
        return base + num * 10 + suf

    def parent_kind(self, kind: str) -> str | None:
        return {"part": None, "item": "part"}[kind]

    def part_of_item(self, canon: str, *, has_item_15: bool = False) -> str:
        p = self.item_part(canon)
        return "PART II" if p == "II" else "PART I"

    # Turn 7 (e): three-tier expected-items table, replacing the single flat
    # base list. REQUIRED items are never legitimately absent from a 10-Q;
    # ERA_REQUIRED items become required once the SEC mandated them; OMITTABLE
    # items (all of Part II except Exhibits) are answered "None"/omitted from
    # the printed form whenever inapplicable in the quarter, by the form's own
    # instructions — they should not fail completeness by themselves.
    REQUIRED = ("I.1", "I.2", "II.6")
    ERA_REQUIRED = (("I.3", 1998), ("I.4", 2003), ("II.1A", 2006))
    OMITTABLE = ("II.1", "II.2", "II.3", "II.4", "II.5")

    @classmethod
    def expected_items(cls, year: int) -> tuple[str, ...]:
        return tuple(cls.REQUIRED) + tuple(k for k, y0 in cls.ERA_REQUIRED if year >= y0)

    @classmethod
    def omittable_items(cls, year: int) -> tuple[str, ...]:
        return cls.OMITTABLE  # all five omittable from 1994 forward; no era gate
