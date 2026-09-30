"""Form 10-K agenda: Parts I-IV and Items 1..16 with letter suffixes."""

from __future__ import annotations

import regex

from .base import LevelSpec

_ITEM_ROMAN = {r: i + 1 for i, r in enumerate(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI"])}
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "1": 1, "2": 2, "3": 3, "4": 4, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4}
# gram.item.word (Turn 4): pre-2000 filers spelled the item number out --
# "ITEM ONE - BUSINESS" (0000002601-95-000017, 0000820957-95-000022).
_ITEM_WORD = {w: i + 1 for i, w in enumerate(
    ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
     "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN"])}
# longest-first so ONE cannot shadow nothing and SIX cannot shadow SIXTEEN
_ITEM_WORD_ALT = "|".join(sorted(_ITEM_WORD, key=len, reverse=True))

# Label must start the block.  Titles are captured loosely; validation happens in candidates.py.
PART_RE = regex.compile(
    r"^[\s\"'\(\[\*•\-]*P\s?A\s?R\s?T\s+(?P<n>I{1,3}|IV|[1-4]|ONE|TWO|THREE|FOUR)\b(?!\d)"
    r"(?P<sep>\s*[\.:,\-–—]+\s*|\s+|$)(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)
ITEM_RE = regex.compile(
    r"^[\s\"'\(\[\*•\-]*I\s?T\s?E\s?M(?P<plural>S)?(?:\s*No\.?)?\s*"
    rf"(?P<num>\d{{1,2}}|(?=[IVX]+\b)(?:XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)|(?<=\s)(?:{_ITEM_WORD_ALT})(?![A-Za-z]))"
    r"\s*(?:\.\s*(?=[A-Ca-c](?![\w])))?(?P<suf>[A-Ca-c])?(?![\d\w])(?:\s*\((?P<psuf>[A-Za-z])\))?"
    r"(?P<sep>\s*[\.:,;\-–—\)]+\s*|\s+|$)(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)

# Turn 12 B.4 (A6, docs/turn12_decisions/a4_label_family.md section 6.2): Regulation AB's
# four-digit numbered items (ABS 10-Ks, `classify.abs_two_tier`), 83,804 heading-shaped
# occurrences with no cross-reference hint in 11,523 documents (`turn12-a4-regab-full.
# {txt,parquet}`), never matched at all: `ITEM_RE`'s `num` group caps at `\d{1,2}`, and a
# raw scan for the shadow risk (an ordinary two-digit "Item 11" glued to a following page
# number) found zero genuine collisions in the whole corpus (section 2.3). A SEPARATE
# pattern, not a widening of the `\d{1,2}` alternation, bounded to the numeric range Reg
# AB's items actually occupy (1100-1123) so an accidental collision stays structurally
# impossible regardless of what the corpus does next -- `ITEM_RE`'s own num group already
# cannot reach four digits (`\d{1,2}` plus the `(?![\d\w])` lookahead reject a third digit
# outright), so trying `_REGAB_RE` on a line is never in tension with the ordinary item
# alternative; canonicalize() below tells the two matches apart by which pattern produced
# them (`m.re is _REGAB_RE`), not by inspecting groups the other pattern doesn't have.
_REGAB_RE = regex.compile(
    r"^[\s\"'\(\[\*•\-]*I\s?T\s?E\s?M(?:\s*No\.?)?\s*"
    r"(?P<num>11(?:0[0-9]|1[0-9]|2[0-3]))"
    # Turn 13 B.1 (R-B4-4, docs/turn13_decisions/a6a_regab.md 3.1): a second
    # parenthesised digit after the letter -- "Item 1114(b)(2) of Regulation AB" --
    # 10,225 census rows in 9,115 documents (turn12-b4-10k-losses.txt (8e)) that the
    # Turn 12 pattern refused outright. `psuf` itself is unchanged.
    r"(?:\((?P<psuf>[a-z])\)(?:\(\d+\))?)?(?![\d\w])"
    r"(?P<sep>\s*[\.:,;\-–—\)]+\s*|\s+|$)(?P<title>.*)$",
    regex.IGNORECASE | regex.DOTALL,
)

# Hand-read, 30 windows (seed 12), 30/30 real (section 2.1) -- the seven Reg-AB items
# that appear on the overwhelming majority of ABS 10-Ks (mean 7.27 Reg-AB labels per
# document, median 7.0, matching this table's own size). Titles as printed on the form;
# canonicalize() accepts only these seven numbers, the same "canonical_only" discipline
# `ITEM_TITLES` above already applies to the ordinary 1-16 item space -- the other six
# numbers the raw census also found live (1100, 1101, 1105, 1108, 1110, 1121) were not
# part of the hand-read sample and are left for a later turn rather than shipped
# unverified.
REGAB_TITLES = {
    "1112": "Significant Obligor(s) of Pool Assets",
    "1114": "Credit Enhancement and Other Support, Except for Certain Derivatives Instruments",
    "1115": "Certain Derivatives Instruments",
    "1117": "Legal Proceedings",
    "1119": "Affiliations and Certain Relationships and Related Transactions",
    "1122": "Compliance with Applicable Servicing Criteria",
    "1123": "Servicer Compliance Statement",
}

# Item -> part (Item 14 is Part IV pre-2003 (exhibits) and Part III after; decided per doc)
_ITEM_PART = {**{k: 1 for k in ("1", "1A", "1B", "1C", "2", "3", "4")},
              **{k: 2 for k in ("5", "6", "7", "7A", "8", "9", "9A", "9B", "9C")},
              **{k: 3 for k in ("10", "11", "12", "13", "14")},
              **{k: 4 for k in ("15", "16")}}

ITEM_TITLES = {
    "1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments", "1C": "Cybersecurity",
    "2": "Properties", "3": "Legal Proceedings", "4": "Mine Safety Disclosures / Submission of Matters to a Vote",
    "5": "Market for Registrant's Common Equity", "6": "Selected Financial Data / [Reserved]",
    "7": "Management's Discussion and Analysis", "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data", "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures", "9B": "Other Information", "9C": "Disclosure Regarding Foreign Jurisdictions",
    "10": "Directors and Executive Officers", "11": "Executive Compensation", "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions", "14": "Principal Accountant Fees / Exhibits (pre-2003)",
    "15": "Exhibits and Financial Statement Schedules", "16": "Form 10-K Summary",
}


# The statutory title of each item, anchored: used by title_matches() to tell a run-in
# heading from a sentence that merely mentions the item.  Alternatives are the wordings
# the form has actually used over 1993-2026 (Item 4 and Item 6 were repurposed; Item 14
# was Exhibits before 2003).  `_AP` is any apostrophe, straight or typographic.
_AP = r"['‘’ʼ`]?s?"
# Turn 13 B.1 (R-B4-1): "Item 1117 of Regulation AB, Legal Proceedings." -- the corpus
# prints the lead-in 44,444 times (turn12_decisions/a4_label_family.md 2.1).
_REGAB_LEAD = r"(?:of\s+regulation\s+ab\b[,.:;]?\s*(?:[\-–—]\s*)?)?"
_ITEM_TITLE_RE = {k: regex.compile(v, regex.IGNORECASE) for k, v in {
    "1": r"(?:the\s+)?business\b",
    "1A": r"risk\s+factors\b",
    "1B": r"unresolved\s+(?:staff|sec\s+staff)\s+comments\b",
    "1C": r"cyber\s?security\b",
    "2": r"(?:description\s+of\s+)?propert(?:y|ies)\b",
    "3": r"legal\s+proceedings\b",
    "4": r"(?:submission\s+of\s+matters|mine\s+safety|\(?\s*(?:removed\s+and\s+)?reserved\s*\)?|\[\s*reserved\s*\]|none\b)",
    "5": r"market\s+for\b",
    "6": r"(?:selected\s+(?:consolidated\s+)?financial\s+data|\(?\s*reserved\s*\)?|\[\s*reserved\s*\])",
    "7": r"management" + _AP + r"\s+discussion\s+and\s+analysis\b",
    "7A": r"quantitative\s+and\s+qualitative\s+disclosures?\b",
    "8": r"(?:consolidated\s+|index\s+to\s+)?financial\s+statements\b",
    "9": r"changes?\s+in\s+and\s+disagreements?\s+with\s+accountants\b",
    "9A": r"controls\s+and\s+procedures\b",
    "9B": r"other\s+information\b",
    "9C": r"disclosure\s+regarding\s+foreign\s+jurisdictions\b",
    "10": r"directors\b",
    "11": r"executive\s+compensation\b",
    "12": r"security\s+ownership\b",
    "13": r"certain\s+relationships\s+and\s+related\b",
    "14": r"(?:principal\s+account(?:ant|ing)?\s*(?:s\b)?|exhibits\b|controls\s+and\s+procedures\b)",
    # Turn 13 B.0 (docs/turn12_decisions/b4_labels_build.md section 5.2, 5.8): the
    # Reg-AB rescue (`out_of_order_items`) needs a title match to keep a displaced
    # body Item 15/16 heading; the singular "Exhibit and Financial Statement
    # Schedules" (983 of the 1,058 lost ITEM 15 rows) never matched the plural-only
    # `exhibits\b`, and "Form 10–K Summary" with an en dash (125 of the 221 lost
    # ITEM 16 rows) never matched the hyphen-only `10-?k`.
    "15": r"(?:exhibits?\b|financial\s+statement\s+schedules\b)",
    "16": r"form\s+10[-–—]?k\s+summary\b",
    # Turn 13 B.1 (R-B4-1, a6a_regab.md 3.1): the seven Reg-AB items, each an optional
    # "of Regulation AB" lead-in and then the statutory title's anchor words. Keyed on
    # item_key(canon) like every entry above, so title_matches()/title_match_end() need
    # no change. An exhibit-index row ("Item 1122 Report on Assessment of Compliance
    # ...") opens with neither and gets no title evidence (R-B4-2).
    **{k: _REGAB_LEAD + r"\(?\s*" + v for k, v in {
        "1112": r"(?:financial\s+information\s+(?:of|regarding)\s+)?significant\s+obligors?\b",
        "1114": r"(?:(?:financial\s+information\s+(?:of|regarding)\s+)?significant\s+enhancement\b|credit\s+enhancements?\b)",
        "1115": r"certain\s+derivatives?\b",
        "1117": r"legal\s+proceedings\b",
        "1119": r"affiliations?\s+and\s+certain\b",
        "1122": r"compliance\s+with\s+applicable\b",
        "1123": r"servic(?:er|ing)\s+compliance\b",
    }.items()},
}.items()}


# Title evidence that a parenthesized letter is an item suffix ("Item 1(A). Risk Factors")
_PSUF_TITLE_RE = {
    "1A": regex.compile(r"risk\s+factors", regex.IGNORECASE),
    "1B": regex.compile(r"unresolved\s+staff", regex.IGNORECASE),
    "1C": regex.compile(r"cybersecurity", regex.IGNORECASE),
    "7A": regex.compile(r"quantitative|market\s+risk", regex.IGNORECASE),
    "9A": regex.compile(r"controls\s+and\s+procedures", regex.IGNORECASE),
    "9B": regex.compile(r"other\s+information", regex.IGNORECASE),
    "9C": regex.compile(r"foreign\s+jurisdictions?", regex.IGNORECASE),
}


class Form10KGrammar:
    name = "form10k"
    levels = (
        LevelSpec(kind="part", depth=1, pattern=PART_RE, canonical_only=True),
        LevelSpec(kind="item", depth=2, pattern=ITEM_RE, canonical_only=True),
        # Turn 12 B.4 (A6): tried after the ordinary item pattern on every block, never
        # instead of it -- `find_candidates` only reaches this level when `ITEM_RE`
        # itself failed to match the line at all (its `num` group cannot reach a third
        # digit), so there is no ordering hazard between the two.
        LevelSpec(kind="item", depth=2, pattern=_REGAB_RE, canonical_only=True),
    )

    def canonicalize(self, kind: str, m: regex.Match) -> str | None:
        if kind == "part":
            n = _ROMAN.get(m.group("n").upper())
            return f"PART {['I', 'II', 'III', 'IV'][n - 1]}" if n else None
        if kind == "item" and m.re is _REGAB_RE:
            num = m.group("num")
            return f"ITEM {num}" if num in REGAB_TITLES else None
        if kind == "item":
            raw_num = m.group("num").upper()
            num = int(raw_num) if raw_num.isdigit() else (_ITEM_ROMAN.get(raw_num) or _ITEM_WORD.get(raw_num, 0))
            suf = (m.group("suf") or "").upper()
            if not suf:
                # "Item 1(A). Risk Factors" means Item 1A, but "Item 1(a) General ..." is a
                # subsection of Item 1 — promote the parenthesized letter only when the title
                # corroborates the suffixed item
                psuf = (m.group("psuf") or "").upper()
                probe = _PSUF_TITLE_RE.get(f"{num}{psuf}")
                if probe and probe.search(m.group("title") or ""):
                    suf = psuf
            if num < 1 or num > 16:
                return None
            key = f"{num}{suf}"
            if key not in ITEM_TITLES:
                return None
            return f"ITEM {key}"
        return None

    @staticmethod
    def title_matches(kind: str, canon: str, title: str) -> bool:
        """gram.item.title_match (Turn 4): does `title` OPEN with this item's own title?

        A run-in heading -- "ITEM 8. Financial Statements and Supplementary Data. The
        information set forth on pages 13-14 to 13-41 of Exhibit 13 hereto is
        incorporated herein by reference." -- is a real heading whose section body
        starts on the same line, and the prose / long-line / cross-reference penalties
        in candidates.py all read it as a sentence containing an item reference
        (E3 rej_low_score, 233 judged positives).  The item's own statutory title,
        anchored immediately after the label, settles the question: a genuine
        cross-reference ("Item 8 of Form 10-K.", "Item 6 is incorporated by reference
        to page 20") never opens with it.
        """
        return Form10KGrammar.title_match_end(kind, canon, title) is not None

    @staticmethod
    def title_match_end(kind: str, canon: str, title: str) -> int | None:
        """Where the statutory title `title_matches` found ENDS inside `title`, or None.

        Same test as `title_matches`, reported as an offset so a caller can read what
        follows the title.  candidates.py's `rej.xref_pointer` needs it: the pointer
        sentences that defeat the waiver ("Item 8., Financial Statements and
        Supplementary Data is set forth in the registrant's 1996 Annual Report ...")
        are told from a real run-in heading by what comes after the title, not by the
        title itself.
        """
        if kind != "item":
            return None
        pat = _ITEM_TITLE_RE.get(Form10KGrammar.item_key(canon))
        if pat is None:
            return None
        lead = len(title) - len(title.lstrip(" .:-–—\t"))
        m = pat.match(title[lead:])
        return lead + m.end() if m else None

    @staticmethod
    def label_rules(kind: str, m: regex.Match) -> list[str]:
        """Extra rule ids describing *how* a label was spelled (Turn 4 recall work).

        Optional grammar hook: candidates.py calls it when present so an exotic
        spelling is traceable in the node's rule_ids without changing label_canon.
        """
        if kind != "item":
            return []
        if m.re is _REGAB_RE:
            return ["gram.item.regab"]
        raw = (m.group("num") or "").upper()
        if raw in _ITEM_WORD:
            return ["gram.item.word"]
        if raw in _ITEM_ROMAN and not raw.isdigit():
            return ["gram.item.roman"]
        return []

    @staticmethod
    def is_regab_item(canon: str) -> bool:
        """Turn 13 B.1 (R-B4-3, a6a_regab.md 3.2): is this item a Regulation AB item?

        tree.build_tree chains these as their own increasing run under the synthetic
        PART V, apart from the ordinary items' run, and reunites the two before
        strong_duplicate_pass. A single chain cannot hold both: Reg-AB's order keys
        (11120-11230) sit above ITEM 16's (160), so whichever family came second in
        the document lost as `nonmonotone` (Turn 12 B.4's failed 10-K gate). Only this
        grammar has the hook; every other grammar keeps the one-chain path.
        """
        return canon.startswith("ITEM ") and canon[5:] in REGAB_TITLES

    @staticmethod
    def item_key(canon: str) -> str:
        return canon.split(" ", 1)[1]

    def order_key(self, kind: str, canon: str) -> int:
        if kind == "part":
            # Turn 12 B.4 (A6): "PART V" is a synthetic slot for Regulation AB's
            # four-digit items (part_of_item below) -- never matched from raw text
            # (PART_RE's own `n` group has no "V"/"FIVE" alternative), so it only ever
            # reaches here via the synthesized-Part path in tree.build_tree.
            return ["PART I", "PART II", "PART III", "PART IV", "PART V"].index(canon) + 1
        key = self.item_key(canon)
        num = int("".join(ch for ch in key if ch.isdigit()))
        suf = "".join(ch for ch in key if ch.isalpha())
        return num * 10 + ({"": 0, "A": 1, "B": 2, "C": 3}[suf])

    def parent_kind(self, kind: str) -> str | None:
        return {"part": None, "item": "part"}[kind]

    def part_of_item(self, canon: str, *, has_item_15: bool) -> str:
        key = self.item_key(canon)
        if key == "14" and not has_item_15:
            return "PART IV"
        if key in REGAB_TITLES:
            # Turn 12 B.4 (A6): Regulation AB items have no statutory Part of their own
            # -- they are appended after a mixed ABS filing's ordinary items (section
            # 2.1: 11,520 of 11,521 documents have BOTH), so they get a synthetic Part V
            # rather than colliding with Parts I-IV's ordinal scheme.
            return "PART V"
        return f"PART {['I', 'II', 'III', 'IV'][_ITEM_PART[key] - 1]}"

    @staticmethod
    def expected_items(year: int) -> tuple[str, ...]:
        base = ["1", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "10", "11", "12", "13", "14"]
        if year >= 1998:
            pass  # 7A introduced 1997; keep as expected from 1998
        else:
            base.remove("7A")
        if year >= 2004:
            base.append("15")
        if year >= 2006:
            base += ["1A", "1B"]
        if year >= 2008:
            base.append("9A")
        if year >= 2010:
            base.append("9B")
        if year >= 2024:
            base += ["1C", "9C"]
        return tuple(base)
