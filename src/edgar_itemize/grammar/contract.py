"""Credit/loan agreement agenda: ARTICLE -> Section a.bb -> (a) -> (i) -> (A) -> (1).

Clause labels are ambiguous ("(i)" is alpha-i or roman-i; "(v)", "(x)" likewise).
The tree builder resolves them by monotone numbering within the enclosing level;
here we only classify and give order keys.
"""

from __future__ import annotations

import regex

from .base import LevelSpec

_ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_WORD_NUM = {w: i + 1 for i, w in enumerate(
    ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN",
     "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN", "TWENTY"])}
_ROMAN_RE = regex.compile(r"^(?=[IVXLCDM])M*(C[MD]|D?C{0,3})(X[CL]|L?X{0,3})(I[XV]|V?I{0,3})$")


def roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not _ROMAN_RE.match(s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_VAL[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


ARTICLE_RE = regex.compile(
    r"^[\s\"'\(\[\*•\-]*A\s?R\s?T\s?I\s?C\s?L\s?E\s+(?P<n>[IVXLC]+|\d{1,2}|[A-Z]+)(?![\w])"
    r"(?P<sep>\s*[\.:\-–—]+\s*|\s+|$)(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)
# SECTION_RE's separator group has no `$` alternative, so a Section label alone on a line
# ("SECTION 5", "Section 5.01") proposes nothing while "ARTICLE V" does.  Turn 10 B.3 built
# that alternative (`gram.section_label_eol`, a4_bare_titles.md s5c), measured it and did
# NOT ship it: 53% precision on the accepted half of a 40-window hand-read, half of the new
# candidates being numerals in pricing grids (a4_bare_titles.md s5f).
SECTION_RE = regex.compile(
    r"^[\s\"'\(\[\*•\-]*(?P<kw>S\s?E\s?C\s?T\s?I\s?O\s?N\s+|§\s*)?(?P<a>\d{1,2})(?:\.(?P<s>\d{1,2}))?(?P<sub>(?:\.\d{1,2})?)(?![\d%])"
    r"(?P<sep>\s*[\.:\-–—]+\s*|\s+|(?=[A-Z\"“]))(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)
CLAUSE_RE = regex.compile(
    r"^[\s\"'\*•\-]*\((?P<c>[a-z]{1,2}|[ivxl]{1,6}|[A-Z]{1,2}|[IVXL]{1,6}|\d{1,2})\)(?P<sep>\s*)(?=[\sA-Za-z\"“'$\(\[]|$)(?P<title>.*)$",
    regex.DOTALL,
)


class ContractGrammar:
    name = "contract"
    levels = (
        LevelSpec(kind="article", depth=1, pattern=ARTICLE_RE),
        LevelSpec(kind="section", depth=2, pattern=SECTION_RE, inline_ok=True),
        LevelSpec(kind="clause", depth=3, pattern=CLAUSE_RE, inline_ok=True),
    )

    def canonicalize(self, kind: str, m: regex.Match) -> str | None:
        if kind == "article":
            n = m.group("n").upper()
            val = int(n) if n.isdigit() else (roman_to_int(n) or _WORD_NUM.get(n))
            if not val or val > 40:
                return None
            return f"ARTICLE {val}"
        if kind == "section":
            a = int(m.group("a"))
            if a > 40 or a == 0:
                return None
            title = (m.group("title") or "").strip()
            if m.group("s") is None:
                # flat numbering: require the word "Section" or a caps/title-case heading ("1. DEFINITIONS.")
                if title[:1].isdigit() or title[:1] == "(":
                    return None
                if not m.group("kw"):
                    head = title.split(".")[0][:60]
                    letters = [c for c in head if c.isalpha()]
                    if not letters or not (m.group("sep") or "").strip().startswith(".") or sum(c.isupper() for c in letters) < 0.6 * len(letters):
                        return None
                return f"SECTION {a}"
            s = int(m.group("s"))
            sub = m.group("sub") or ""
            return f"SECTION {a}.{s:02d}{sub}"
        if kind == "clause":
            c = m.group("c")
            # ambiguous forms are canonicalized as written; tree.py resolves alpha vs roman
            return f"({c})"
        return None

    def order_key(self, kind: str, canon: str) -> int:
        if kind == "article":
            return int(canon.split()[1])
        if kind == "section":
            body = canon.split()[1]
            parts = body.split(".")
            a = int(parts[0])
            s = int(parts[1]) if len(parts) > 1 else 0
            sub = int(parts[2]) if len(parts) > 2 else 0
            return a * 10000 + s * 100 + sub
        return 0  # clauses are ordered by the tree builder

    def parent_kind(self, kind: str) -> str | None:
        return {"article": None, "section": "article", "clause": "section"}[kind]

    @staticmethod
    def clause_value(c: str, family: str) -> int | None:
        """Ordinal of a clause label under an assumed family: alpha|roman|upper|upper_roman|num|alt."""
        if family == "num":
            return int(c) if c.isdigit() else None
        if family == "alt":
            # Turn 12 B.2.3 (`seq.alt_readings`, a2_clause_layer.md R4): "(x) ... ; (y) ... ;
            # or (z) ..." is a standalone alternatives list, not the 24th-26th member of a
            # lettered sequence.  The reading is offered only where the span carries a PAIR
            # (tree_contract._families' `alt` argument) and always with a surcharge, so a
            # legal continuation of a real lettered list beats it.
            return {"x": 1, "y": 2, "z": 3}.get(c.lower()) if len(c) == 1 and c.isalpha() else None
        if family == "alpha":
            if c.islower() and c.isalpha() and len(c) <= 2:
                return (ord(c[0]) - 96) if len(c) == 1 else (26 + ord(c[0]) - 96) if c[0] == c[1] else None
            return None
        if family == "upper":
            if c.isupper() and c.isalpha() and len(c) <= 2:
                return (ord(c[0]) - 64) if len(c) == 1 else (26 + ord(c[0]) - 64) if c[0] == c[1] else None
            return None
        if family == "roman":
            return roman_to_int(c) if c.islower() else None
        if family == "upper_roman":
            return roman_to_int(c) if c.isupper() else None
        return None

    @staticmethod
    def clause_value_bijective(c: str, family: str) -> int | None:
        """The bijective base-26 reading of a two-letter marker: (aa)=27, (ab)=28, (dk)=115.

        Turn 12 B.2.2, `gram.clause_alpha_bijective` (docs/turn12_decisions/a2_clause_layer.md
        R2 and s8 B.2.2).  `clause_value` reads a two-letter marker only when the two letters
        are the same -- the American doubled-letter convention -- so a list drafted
        `(z), (aa), (ab), ..., (dk)` has no reading in any family past (aa) and the sequencer
        cannot place it at any cost.  This is a SECOND reading in the SAME family, never a
        replacement: a document that doubles its letters keeps reading (bb) as 28.

        Scope (s8 B.2.2, from the banks): N1b -- a marker that already reads, i.e. a repeat --
        unconditionally, in either case; N1a -- a marker that reads nowhere -- only where
        `lower_double`, the two letters lower case.  That clause is what keeps the `(NY)` /
        `(CA)` state codes of law-firm footers and subsidiary schedules unreadable.  Every
        bijective value is >= 27 and `sequence()` may open or restart only at 1 or 2, so the
        reading can never open a list -- it can only continue one that is already running.
        """
        if family not in ("alpha", "upper") or len(c) != 2 or not c.isalpha():
            return None
        if family == "alpha" and not c.islower():
            return None
        if family == "upper" and not (c.isupper() and c[0] == c[1]):
            return None  # `lower_double`: an upper-case pair reads only where it already did
        i0, i1 = (ord(c[0]) | 32) - 96, (ord(c[1]) | 32) - 96
        return 26 * i0 + i1 if 1 <= i0 <= 26 and 1 <= i1 <= 26 else None
