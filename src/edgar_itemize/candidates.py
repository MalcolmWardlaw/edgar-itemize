"""Heading candidates: blocks whose text starts with a grammar label and look like headings.

Every decision is tagged with rule ids so downstream tables can explain
exactly why a node exists or was dropped.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

import regex

from .blocks import Block
from .grammar.base import Grammar

_DOT_LEADER_RE = regex.compile(r"(?:\.\s?){4,}|(?:_\s?){4,}")
_TRAILING_PAGE_RE = regex.compile(r"(?:\s|\.)(?:[A-Z]-)?\d{1,3}\s*$")
_TITLE_XREF_RE = regex.compile(
    r"\b(?:of this (?:annual |quarterly )?report|of (?:this )?form 10-?[kq]|herein\b|hereof\b|regulation s-[kx]|"
    r"incorporated (?:herein )?by reference|see item|refer to item|is (?:not )?applicable\b|"
    r"above\b|below\b|as (?:discussed|described|defined))",
    regex.IGNORECASE,
)
_SENTENCE_RE = regex.compile(r"[a-z]{3,}\s+[a-z]{3,}\s+[a-z]{3,}.*[.;:]\s+[A-Z]")
# rej.xref_pointer (Turn 8 B.4): the item's own statutory title used as the SUBJECT of a
# pointer sentence -- "Item 8., Financial Statements and Supplementary Data is set forth in
# the registrant's 1996 Annual Report to Shareholders and is incorporated by reference"
# (0000709337-97-000004), "Item 7, Management's Discussion and Analysis of Financial
# Condition and Results of Operations, is incorporated by reference" (0000950152-02-005312).
# Anchored at the end of the statutory title and barred from crossing a sentence end, so a
# real run-in heading -- which CLOSES its title before the body starts ("... Supplementary
# Data.  The information set forth on pages 13-14 to 13-41 of Exhibit 13 hereto is
# incorporated herein by reference.") -- cannot match.  The tail before the verb is bounded
# because the statutory-title regexes are prefixes of the full printed title.
_IBR_POINTER_RE = regex.compile(
    r"^(?P<gap>[^.;:!?]{0,80}?)\b(?:is|are|was|were|has\s+been|have\s+been|shall\s+be|will\s+be|may\s+be|can\s+be)\s+"
    r"(?:(?:hereby|herein|hereto|therein|thereto)\s+)?"
    r"(?:incorporated|set\s+forth|included|contained|presented|filed|found|located|reported)\b"
    r"[\s,]*(?:in|on|to|under|from|by|with|herein|hereto|therein|elsewhere|as)\b",
    regex.IGNORECASE,
)
# What must NOT stand between the statutory title and the pointer verb.  A pointer
# sentence's subject IS the title, so only the rest of the printed title may intervene
# ("and Supplementary Data", "of Financial Condition and Results of Operations,").  These
# filers print headings with no terminal punctuation, so the sentence-end test above does
# not see the break by itself; three more things mean "the heading ended here":
#   a determiner — a NEW subject has started, and what stands above it is a real heading
#     with its body running on ("Item 5. MARKET FOR REGISTRANT'S COMMON EQUITY ...
#     Information responsive to this Item is set forth in ...", 0000007084-04-000364;
#     "... Supplementary Data The information required by this item is contained in Part
#     IV", 0000950135-07-004321);
#   a line break before a capitalised word — a new sentence on a new line;
#   a rule of dashes or underscores — the text era's heading underline ("Item 11.
#     Executive Compensation\n- ------- ------\nInformation concerning executive
#     compensation is included under the caption ...", 0000352947-98-000002).
_IBR_GAP_BREAK_RE = regex.compile(
    r"\b(?:the|a|an|this|these|those|such|its|our|their|his|her|which|that|all)\b"
    r"|\n\s*[A-Z]|[-_=–—]{3,}",
    regex.IGNORECASE,
)
_IBR_POINTER_WINDOW = 400
# "PART I, Item 1B. Unresolved Staff Comments." (E3 0000771726-05-000116): the comma
# after the part numeral used to kill the item candidate, so the whole block became a
# Part heading with no item under it.  The comma joins the separator class.
_PART_THEN_ITEM_RE = regex.compile(r"^[\s\"'\(\[\*•\-]*PART\s+(?:I{1,3}|IV|[1-4])\b[\s\.:,\-–—]*(?P<rest>ITEMS?\s*\d)", regex.IGNORECASE)
_RANGE_RE = regex.compile(r"\s*(?P<rng>(?:(?:,|and|&|-|–|—|through|to)\s*\d{1,2}[A-C]?\s*)+)", regex.IGNORECASE)
# a second ITEM label on the same heading line, introduced by a connector
_MULTI_REPEAT_RE = regex.compile(r"(?:\band\b|&|;|,|\bthrough\b|\bto\b)\s*ITEMS?\s*(?P<n>\d{1,2}\s*[A-Ca-c]?)(?![\d\w])", regex.IGNORECASE)
_ITEM_NUM_RE = regex.compile(r"(?:[1-9]|1[0-6])[A-C]?")
_MULTI_REPEAT_MAX = 3


_CONTINUED_FWD_RE = regex.compile(r"continued\s+on\s+(?:the\s+)?(?:next|following)\s+page", regex.IGNORECASE)

# --- rej.formula_denominator (Turn 9 B.1b) ---------------------------------------
#
# Credit agreements define the Eurodollar rate as a fraction, and the HTML/text
# normalizers drop the rule that draws the fraction bar, so the denominator arrives as a
# line of its own:
#
#     Eurodollar Base Rate
#     1.00 - Eurocurrency Reserve Requirements
#
# `ContractGrammar.SECTION_RE` reads that line as `SECTION 1.00` with the title
# "Eurocurrency Reserve Requirements".  It has always been a candidate and the monotone
# chain has always thrown it away; Turn 9 B.1's section restart then promoted it, because
# its order key (10000) sits below the real head's (`SECTION 1.01` = 10100) and the
# instrument's own numbering ascends behind it — 468 of 6,708 restart heads in
# runs/full_ex10_v16, displacing 243 of the 369 real headings that gate lost
# (docs/turn9_decisions/b1_section_restart.md section 3a).
#
# The guard is the line's own shape and nothing else: an ordinal whose subsection is zero,
# one or more dashes (the minus sign) or the word "minus", up to three qualifier words
# (Eurocurrency / Eurodollar / Euro-Rate / LIBOR / C/D / Canadian ...), the word Reserve,
# and then nothing but Requirements / Percentage / Rate.  Measured over every
# `SECTION n.0` / `n.00` candidate in the 856 documents that hold such a node
# (`runs/judge/turn9-b1-dot00-candidates.jsonl`, `scripts/turn9/b1_formula_census.py
# --scan`): 636 candidates match, in 590 documents, and every one of them is a fraction
# denominator — the genuine `SECTION n.00` headings of that population ("1.00
# AFFIRMATIVE COVENANTS", the outline numbers "1.0 / PROJECT MANAGEMENT" of a statement
# of work, and the ratio cells "2.00:1.00") carry no minus sign and match nothing here.
# The block-length bound keeps a heading whose body runs on below it in the same block.
_FORMULA_DENOM_RE = regex.compile(
    r"^[\s(\[]*\d{1,2}\.0{1,2}"
    r"(?:(?:\s*[-‐‑‒–—―−]\s*)+|\s+minus\s+(?:the\s+)?)"
    r"(?:[A-Za-z][A-Za-z/&.'-]*\s+){0,3}"
    r"Re\s?serve(?:\s+(?:Requirements?|Percentages?|Rate|Ratio))*"
    r"[\s.)\]:;,]*$",
    regex.IGNORECASE,
)
_FORMULA_DENOM_PENALTY = 0.6  # every measured match scores 0.20-0.75, i.e. below `min_score`
CONTINUED_RE = regex.compile(r"\((?:continued|cont'?d\.?|con'?t\.?|concl\.?|concluded)\)|\bcontinued\b|\bcont'd\b|\bcon't\b|[-\u2013\u2014]\s*continued\b", regex.IGNORECASE)


@dataclass(slots=True)
class Candidate:
    block_idx: int
    kind: str
    label_raw: str
    label_canon: str
    title: str
    score: float
    rule_ids: list[str] = field(default_factory=list)
    order_key: int = 0
    toc_hint: bool = False
    head_raw_start: int = 0
    head_raw_end: int = 0
    multi: bool = False
    norm_start: int = -1  # start of the heading in normalized text (block start unless a later line)


def _clean_title(t: str) -> str:
    t = _DOT_LEADER_RE.split(t, 1)[0]
    return regex.sub(r"\s+", " ", t).strip(" .:-–—|")


def _title_from_lines(first_title: str, extra_lines: tuple[str, ...]) -> tuple[str, int]:
    """Join a heading title that wraps onto following short/caps/title-case lines."""
    lines = [first_title]
    used = 0
    for extra in extra_lines[:2]:
        words = extra.split()
        capsish = sum(c.isupper() for c in extra) >= 0.6 * max(1, sum(c.isalpha() for c in extra))
        titleish = len(words) <= 12 and extra[:1].isupper() and not regex.search(r"[.;:]\s+[a-z]", extra)
        if len(extra) <= 90 and (capsish or titleish or not extra[:1].isalpha()):
            lines.append(extra)
            used += 1
            if not capsish and extra.rstrip().endswith("."):
                break
        else:
            break
    return _clean_title(" ".join(lines)), used


def _score_block_style(b: Block, first: str, rules: list[str], era: str, *, row: bool) -> float:
    score = 0.0
    if b.bold or (row and b.row_bold):
        score += 0.15; rules.append("sty.bold")
    if b.underline:
        score += 0.15; rules.append("sty.underline")
    if b.caps_ratio >= 0.85 and len(first) >= 6:
        score += 0.15; rules.append("sty.caps")
    if b.center:
        score += 0.05; rules.append("sty.center")
    if b.font_size_rel > 1.05:
        score += 0.1; rules.append("sty.font_size")
    if b.n_lines <= 3 and b.n_chars <= 220:
        score += 0.1; rules.append("pos.short_block")
    if b.blank_before:
        score += 0.05
    if era == "text" and b.indent > 12 and not b.center:
        score -= 0.05
    if b.in_table and era != "text" and not row:
        score -= 0.1; rules.append("pos.in_table")
    if b.preceding_comment and regex.search(r"item|part", b.preceding_comment, regex.IGNORECASE):
        score += 0.2; rules.append("pub.comment_marker")
    if b.anchor_ids:
        score += 0.05; rules.append("pos.anchor")
    return score


def _try_match(b: Block, lvl, grammar: Grammar, text: str, first: str, extra_lines: tuple[str, ...], head_start: int, head_end: int,  # noqa: C901
               era: str, *, row: bool, line_k: int, norm_start: int = -1) -> Candidate | None:
    m = lvl.pattern.match(text)
    if not m:
        return None
    canon = grammar.canonicalize(lvl.kind, m)
    if canon is None:
        return None
    rules = [f"lbl.{lvl.kind}"]
    label_rules = getattr(grammar, "label_rules", None)
    if label_rules is not None:
        rules += label_rules(lvl.kind, m)
    score = 0.5
    if "gram.item.suffix_refused" in rules:
        # Turn 13 B.0 (R-B4-5, docs/turn12_decisions/b4_labels_build.md sections 6, 9):
        # `text` is the block's whitespace-normalized text but `first` is the raw
        # physical line -- its own run of internal spaces is not collapsed. A
        # fixed-width typewriter sub-caption ("Item 6    (a)      None") makes
        # `m.start("title")`, an offset into normalized `text`, land mid-word once
        # sliced out of raw `first` ("a)      None" instead of "None"): the refused
        # "(a)" leaks back into the title used for scoring below and the real title
        # reads as prose starting with a lowercase letter. Re-matching the same
        # pattern directly against `first` gives an offset native to it, landing
        # right after the sub-caption the suffix was refused from.
        m_first = lvl.pattern.match(first)
        if m_first is not None:
            title_full = m_first.group("title") or ""
            first_title = title_full
        else:
            title_full = m.group("title") or ""
            first_title = first[m.start("title"):] if m.start("title") < len(first) else ""
    else:
        title_full = m.group("title") or ""
        first_title = first[m.start("title"):] if m.start("title") < len(first) else ""
    if getattr(lvl, "inline_ok", False):
        # heading text may run straight into body text: title = up to the first sentence end
        cut = regex.search(r"[.;:]\s|\.$", first_title[:160])
        first_title = first_title[: cut.end()] if cut else first_title[:120]
        extra_lines = ()
    title, used = _title_from_lines(first_title, extra_lines)
    if used and not row and line_k + used < len(b.line_raw_ends):
        head_end = b.line_raw_ends[line_k + used]
    covered: list[str] = []
    rng = _RANGE_RE.match(title_full) if "plural" in m.groupdict() else None
    if rng:
        # bare-number range after the label: "Items 1 and 2.", "ITEMS 10 THROUGH 13",
        # and (gram.item.multi_singular) the singular "Item 7 and 7A." form (D3)
        nums = regex.findall(r"\d{1,2}[A-C]?", rng.group("rng"))
        first_num = m.group("num")
        if regex.search(r"through|to|-|–|—", rng.group("rng"), regex.IGNORECASE) and nums and nums[-1].isdigit() and first_num.isdigit():
            covered = [str(k) for k in range(int(first_num) + 1, int(nums[-1]) + 1)]
        else:
            covered = [n.upper() for n in nums]
        if not m.group("plural"):
            rules.append("gram.item.multi_singular")
        title = _clean_title(title_full.split("\n")[0][rng.end():]) or title
    # gram.item.multi_repeat: the second label repeats ITEM, so _RANGE_RE never saw it --
    # "ITEM 1. BUSINESS AND ITEM 2. PROPERTIES" (0001028269-00-000114), "Item 1. Business
    # and Item 2. Properties" (0000702165-13-000042), "ITEM 1. BUSINESS; ITEM 2.
    # PROPERTIES" (0000950129-03-001049).  Only on the heading's own line, only when a
    # connector introduces the repeat, and at most _MULTI_REPEAT_MAX of them: a long
    # comma-separated run of ITEM labels is a cross-reference list, not a heading.
    if lvl.kind == "item":
        repeats = [regex.sub(r"\s+", "", mm.group("n")).upper() for mm in _MULTI_REPEAT_RE.finditer(first_title.split("\n")[0])]
        repeats = [k for k in repeats if _ITEM_NUM_RE.fullmatch(k)]
        if repeats and len(repeats) <= _MULTI_REPEAT_MAX:
            rules.append("gram.item.multi_repeat")
            covered += repeats
    if covered:
        seen: set[str] = set()
        covered = [k for k in covered if not (k in seen or seen.add(k))]
        rules.append("lbl.item.multi")
        rules += [f"multi.ITEM {k}" for k in covered]
    multi = bool(covered)
    toc_hint = False
    # Index evidence: dot leaders, or a trailing page number on a short row.  Two Turn 8
    # corrections to the first-physical-line-only scan, both found by hand-reading the
    # out-of-order census (docs/turn8_decisions/a5_out_of_order.md, "Known rule limits"):
    #
    #  toc.pageno_self_label — a bare numbered label with no title and no trailing
    #    punctuation ("ITEM 8", title on the next block, 0001437749-18-005827) matches
    #    _TRAILING_PAGE_RE against its OWN item number.  Digits inside the label are never
    #    a page number, so the row is tagged with what the hint actually matched.  Scope,
    #    as below: `toc.leader_or_pageno` and its -0.15 still stand, so no chain weight
    #    moves, and the tag is a description rather than a behaviour.  Two findings pin it
    #    there.  First, the tag cannot change an out-of-order decision even in principle:
    #    the trailing-page regex only reaches the label's own digits when nothing follows
    #    the label on the line, and a label with no title on its line never carries
    #    `gram.item.title_match`, which that rule requires -- A.5's hand-read case 16
    #    (0001437749-18-005827) is a miss for two independent reasons, and this is only
    #    one of them.  Second, dropping the penalty was measured on a 1-in-20 corpus
    #    sample: the 0.35 of chain weight it hands back to every bare "Item N" cell of a
    #    front-matter index flips whole documents from their body headings onto their
    #    table of contents (0000798941-24-000018, 14 item anchors moved from the body to
    #    a vetoed front TOC) as often as it rescues a real bare heading
    #    (0000931763-01-000739, 15 anchors moved off TOC rows onto the body).  38 anchor
    #    moves across the sample, both directions; that is a re-anchoring decision with
    #    its own gate, and this rule has no need of it.
    #  toc.leader_or_pageno_wrapped — a TOC row whose label and title fill the first line
    #    and whose leaders / page number wrap onto the second ("Item 12: Security Ownership
    #    of Certain Beneficial\nOwners and Management. . . . . 55", 0000062418-98-000003)
    #    carries no index evidence at all, and gram.item.title_match then waives the prose
    #    penalties for it.  Scope: this tag is index evidence for the `seq.out_of_order`
    #    pass and nothing else — it deliberately does NOT imply `toc.leader_or_pageno`, does
    #    not set `toc_hint` and does not move the score.  Folding it into the first-line
    #    rule was measured on a 1-in-20 corpus sample and un-does the Turn 3
    #    chain-completion veto (toc.veto_chain_completing, whose "strong member" test is
    #    `not _leader`) for text-era filings whose Part III is incorporated by reference:
    #    their front-matter listing stops being vetoed and the only copy of eight item
    #    labels disappears (0000798354-95-000007; 40 lost labels across the sample).  That
    #    is a re-anchoring decision with its own gate, not part of B.4.
    #    The wrapped scan is confined to a heading-sized block (the `pos.short_block`
    #    shape), to two lines, and to lines carrying words: in a long block the lines below
    #    the label are the body, and a wrapped line of pure furniture is the ruler under a
    #    text-era heading ("Item 2. Properties\n___________________", 0000098362-04-000001),
    #    not a dot leader.
    label_end = m.end("sep") if m.group("sep") else m.end()
    wrapped_head = " ".join(extra_lines[:2]) if (b.n_lines <= 3 and b.n_chars <= 220) else " ".join(extra_lines[:used])
    page_ok = lvl.kind == "item" and not multi
    leader = False
    if _DOT_LEADER_RE.search(first):
        leader = True
    elif page_ok and len(first) < 120:
        tp = _TRAILING_PAGE_RE.search(first)
        if tp is not None:
            leader = True
            if tp.start() < label_end:
                rules.append("toc.pageno_self_label")  # the label's own digits, not a page number
    if leader:
        toc_hint = True
        rules.append("toc.leader_or_pageno")
        score -= 0.15
    elif (wrapped_head and len(first) + len(wrapped_head) < 160
          and sum(c.isalpha() for c in wrapped_head) >= 3
          and (_DOT_LEADER_RE.search(wrapped_head) or (page_ok and _TRAILING_PAGE_RE.search(wrapped_head)))):
        rules.append("toc.leader_or_pageno_wrapped")
    if b.href_targets or (row and b.row_href):
        toc_hint = True
        rules.append("toc.href")
        score -= 0.15
    # gram.item.title_match (Turn 4, E3 rej_low_score): the item's own statutory title
    # opens the text right after the label.  That settles what the prose / long-line /
    # cross-reference penalties below are guessing at -- the line is a heading whose
    # section body runs on from it -- so those three penalties are not applied.
    # A cross-reference ("Item 8 of Form 10-K.") never opens with the statutory title.
    # An index row does open with it, so index evidence (dot leaders, a trailing page
    # number, an href) blocks the waiver: those rows must keep losing the chain.
    title_probe = getattr(grammar, "title_matches", None)
    title_match = bool(title_probe is not None and not toc_hint and not getattr(lvl, "inline_ok", False)
                       and title_probe(lvl.kind, canon, first_title))
    if title_match:
        rules.append("gram.item.title_match")
    # rej.xref_pointer: the cross-reference family's answer to the one shape the
    # title-match waiver was not built for.  `gram.item.title_match`'s contract is that a
    # cross-reference never OPENS with the statutory title; an incorporation-by-reference
    # pointer whose subject is the item title itself does exactly that, so the waiver hands
    # it the prose penalties' exemption (A.5 census, limit 3: the dominant residual false
    # accept in the judged sample).  The waiver itself is left alone — several other rules
    # read `gram.item.title_match` — and the cross-reference family is extended instead.
    # The scan runs over the head window rather than the first physical line because the
    # text era wraps the pointer verb onto line 2 ("Item 8., Financial Statements and
    # Supplementary Data\nis set forth in the registrant's 1996 Annual Report to\n
    # Shareholders...").
    #
    # Scope, like `toc.leader_or_pageno_wrapped`: the tag carries no score penalty and is
    # read only by tree.out_of_order_items.  Charging `rej.xref_phrase`'s -0.5 (or -0.35)
    # for it was measured on a 1-in-20 corpus sample: it sinks the sole copy of an item
    # whose whole section IS the pointer sentence ("Item 13, Certain Relationships and
    # Related Transactions, is hereby incorporated by reference to our definitive Proxy
    # Statement ...", 0000950159-01-000150 — the only Item 13 outside that filing's TOC),
    # and the label disappears: 11 item labels lost across the sample, one of them core.
    # Demoting a heading the monotone chain legitimately accepted is a separate decision
    # with its own gate; barring it from a NEW out-of-order placement needs no penalty.
    if title_match and not (b.bold or b.underline or b.caps_ratio >= 0.85):
        tstart = m.start("title")
        window = text[tstart : tstart + _IBR_POINTER_WINDOW]
        tend = getattr(grammar, "title_match_end", lambda *a: None)(lvl.kind, canon, window)
        hit = _IBR_POINTER_RE.match(window[tend:]) if tend is not None else None
        if hit is not None and not _IBR_GAP_BREAK_RE.search(hit.group("gap")):
            rules.append("rej.xref_pointer")
    # the Eurodollar-rate fraction's denominator, not a section (see `_FORMULA_DENOM_RE`)
    if lvl.kind == "section" and b.n_lines <= 2 and _FORMULA_DENOM_RE.match(first):
        rules.append("rej.formula_denominator")
        score -= _FORMULA_DENOM_PENALTY
    if not getattr(lvl, "inline_ok", False) and not title_match and _TITLE_XREF_RE.search(first) and not (b.bold or b.underline or b.caps_ratio >= 0.85):
        rules.append("rej.xref_phrase")
        score -= 0.5
    inline = getattr(lvl, "inline_ok", False)
    # the marker often sits on the wrapped second line of a running page header (C3 M1);
    # "(continued on the following page)" is a forward pointer on a real heading, not a repeat
    head_lines = " ".join((first,) + tuple(extra_lines[:2]))
    if CONTINUED_RE.search(head_lines) and not _CONTINUED_FWD_RE.search(head_lines):
        rules.append("rej.continued")  # running page header repeating an earlier heading
        score -= 0.45
    # a lowercase title is prose -- unless it is this item's own statutory title, which
    # some filers typeset entirely in lower case ("item 3. legal proceedings",
    # 0001493152-20-005159, 0001144204-12-017368)
    if title[:1].islower() and lvl.kind != "clause" and not title_match:
        rules.append("rej.lowercase_title")
        score -= 0.4
    if not inline:
        # the statutory-title waiver covers the verdicts that call the line prose --
        # rej.long_line, rej.prose, rej.xref_phrase -- but NOT the soft >110 length
        # prior below it. That prior is what keeps a long index row ("ITEM 12 -
        # Security Ownership of Certain Beneficial Owners and Management ... 24")
        # from out-scoring the body copy; waiving it too gave five such rows +0.1
        # each and tipped 0000741516-03-000007 onto its own table of contents.
        if len(first) > 160 and not title_match:
            rules.append("rej.long_line")
            score -= 0.35
        elif len(first) > 110:
            score -= 0.1
        # prose penalty only when the heading line itself runs into body text; a clean short
        # label line followed by unseparated paragraph lines is still a heading
        capsish_first = sum(c.isupper() for c in first) >= 0.85 * max(1, sum(c.isalpha() for c in first))
        heading_line_clean = (len(first) <= 90 or (capsish_first and len(first) <= 160)) and not regex.search(r"[.;:]\s+[a-z]", first_title)
        if not title_match and _SENTENCE_RE.search(text[:300]) and b.n_lines > 2 and not row and not heading_line_clean:
            rules.append("rej.prose")
            score -= 0.3
        elif b.n_lines > 3 and heading_line_clean and not row:
            rules.append("pos.head_line_only")
            score += 0.05
    score += _score_block_style(b, first, rules, era, row=row)
    if row:
        rules.append("pos.table_row")
    if line_k > 0:
        rules.append(f"pos.line{line_k}")
        score -= 0.05
    label_raw = regex.sub(r"\s+", " ", text[: m.end("sep") if m.group("sep") else m.end()].strip())
    return Candidate(
        block_idx=b.idx, kind=lvl.kind, label_raw=label_raw, label_canon=canon, title=title[:200], score=round(score, 3),
        rule_ids=rules, order_key=grammar.order_key(lvl.kind, canon), toc_hint=toc_hint,
        head_raw_start=head_start, head_raw_end=head_end, multi=multi, norm_start=(norm_start if norm_start >= 0 else b.norm_start),
    )


def find_candidates(blocks: list[Block], grammar: Grammar, *, era: str) -> list[Candidate]:
    out: list[Candidate] = []
    for b in blocks:
        if b.kind != "para" or not b.text:
            continue
        lines = b.lines or (b.text,)
        found_first = False
        for lvl in grammar.levels:
            # 1. block text (label must start the block)
            first = lines[0]
            end_first = b.line_raw_ends[0] if b.line_raw_ends else b.raw_end
            c = _try_match(b, lvl, grammar, b.text, first, lines[1:], b.raw_start, end_first, era, row=False, line_k=0)
            # 2. table row: label cell + title cell(s)
            if b.row_text and (c is None or len(c.title) < 3):
                rc = _try_match(b, lvl, grammar, b.row_text, b.row_text, (), b.raw_start, b.row_raw_end, era, row=True, line_k=0)
                if rc is not None:
                    c = rc
            if c is not None:
                out.append(c)
                found_first = True
                # "PART I Item 1. Business ..." on one line: also emit the Item that follows the Part label
                if c.kind == "part" and len(grammar.levels) > 1:
                    im = _PART_THEN_ITEM_RE.match(b.text)
                    if im:
                        off = im.start("rest")
                        rest = b.text[off:]
                        rest_first = rest.split("\n", 1)[0]
                        ic = _try_match(b, grammar.levels[1], grammar, rest, rest_first, lines[1:], b.raw_start + off, b.raw_start + off + len(rest_first), era,
                                        row=False, line_k=0, norm_start=b.norm_start + off)
                        if ic is not None:
                            ic.rule_ids.append("pos.inline_after_part")
                            # the +0.1 rewards part and item labels sharing a typeset
                            # heading line -- but a "documents incorporated by reference"
                            # index shares the line too ("Part II, Item 5 Annual Report*",
                            # 0000829499-96-000008). The item's own statutory title is
                            # what separates the two, so the bonus is conditional on it.
                            if "gram.item.title_match" in ic.rule_ids:
                                ic.score = round(ic.score + 0.1, 3)
                            out.append(ic)
                break
        # Turn 4 widened this scan for the 10-K/10-Q grammars only. EX-10 keeps the old
        # window (HTML/OCR eras, first 40 lines): the contract tree is Turn 6's and is
        # gated on its own EX-10 run_diff, so it must not move here.
        wide = grammar.name != "contract"
        if (found_first and era != "image_text") or len(lines) < 2 or (not wide and era == "text"):
            continue
        # 3. a label may start a line other than the block's first: <br>-separated HTML
        # lines, an OCR page blob of many pseudo-lines (A6), and -- since Turn 4 -- the
        # text era and blocks past the old 40-line cap. E3's 0000002024-02-000008 puts
        # "Item 2. Properties" on line 265 of a 268-line html_early block, past the old
        # 40-line cap; the era == "text" skip hid every text-era mid-block label.
        # The cap paid for the quadratic "\n".join(lines[k:]); the scan is now linear:
        # a single-line match of the level pattern is a necessary condition for the
        # multi-line one (a label never spans lines), so it prefilters, and the text
        # handed to _try_match is budgeted to the few lines its prose test can see.
        # normalized offsets must come from b.text, not b.lines: the text era collapses
        # runs of spaces inside a line and the HTML normalizer strips each line, so
        # summing b.lines lengths puts norm_start off by the difference.
        norm_lines = b.text.split("\n")
        norm_line_start = b.norm_start
        last = len(lines) if wide else min(len(lines), 600 if era == "image_text" else 40)
        for k in range(1, last):
            norm_line_start += (len(norm_lines[k - 1]) if k - 1 < len(norm_lines) else len(lines[k - 1])) + 1
            ln = lines[k]
            if not ln[:1].isalpha() and not ln[:1] in "([\"'":
                continue
            for lvl in grammar.levels:
                # the prefilter must see the next line too: an OCR page blob (A6) wraps
                # "SECTION" and "1." onto separate pseudo-lines, and the level pattern
                # matched that across the newline before the prefilter existed
                if not lvl.pattern.match(ln) and not (k + 1 < len(lines) and lvl.pattern.match(ln + "\n" + lines[k + 1])):
                    continue
                hs = b.line_raw_starts[k] if k < len(b.line_raw_starts) else b.raw_start
                he = b.line_raw_ends[k] if k < len(b.line_raw_ends) else hs + len(ln)
                c = _try_match(b, lvl, grammar, _budgeted(lines, k), ln, lines[k + 1:], hs, he, era,
                               row=False, line_k=k, norm_start=norm_line_start)
                if c is not None:
                    out.append(c)
                    break
    return _repeat_pass(out, blocks)


def _budgeted(lines: tuple[str, ...] | list[str], k: int, *, max_chars: int = 400, max_lines: int = 8) -> str:
    """lines[k:] joined, stopped at the first of max_chars / max_lines.

    _try_match reads the joined text only for the label match, the title's first line
    and the 300-character prose probe, so the tail beyond this budget cannot change a
    decision -- and joining the whole tail at every line makes the scan quadratic."""
    out: list[str] = []
    n = 0
    for ln in lines[k : k + max_lines]:
        out.append(ln)
        n += len(ln) + 1
        if n >= max_chars:
            break
    return "\n".join(out)


def _norm_title(t: str) -> str:
    return regex.sub(r"\s+", " ", t).strip(" .:-–—").lower()


def _at_page_start(blocks: list[Block], idx: int) -> bool:
    if blocks[idx].is_page_break:
        return True
    for j in range(idx - 1, max(-1, idx - 4), -1):
        b = blocks[j]
        if b.kind == "page" or b.is_page_break:
            return True
        if b.kind == "para" and b.text.strip() and not regex.fullmatch(r"[\s\d\-–—_.ivx]*", b.text, regex.IGNORECASE):
            return False
    return False


def _repeat_pass(cands: list[Candidate], blocks: list[Block]) -> list[Candidate]:
    """Running page headers, part 1 (C3 M1): the first continuation-marked copy of a label
    is relieved of most of the penalty, so that when every body copy is marked (the
    section's first page carries no heading candidate) it can still anchor the item.
    page_repeat_pass takes the relief back once TOC regions are known and an unmarked
    copy precedes it outside any region."""
    cont_seen: set[tuple[str, str]] = set()
    for c in cands:
        key = (c.kind, c.label_canon)
        if "rej.continued" in c.rule_ids:
            if key not in cont_seen:
                cont_seen.add(key)
                c.rule_ids.append("rej.continued_first")
                c.score = round(c.score + 0.3, 3)
    return cands


def page_repeat_pass(cands: list[Candidate], blocks: list[Block], toc_idx: set[int] = frozenset(), *, kinds=("item", "part")) -> None:
    """Running page headers, part 2 (C3 M1), applied after TOC detection: a later copy
    that repeats an earlier body copy's exact title at a page start is a page header even
    without a marker (rej.page_repeat), so equal-weight chains no longer tie toward the
    repeat. Only a body-looking copy can be the reference: TOC-region members, table
    cells, and blocks with dot leaders or page numbers on any line are index rows."""
    first_title: dict[tuple[str, str], tuple[str, bool]] = {}  # key -> (normalised title, styled?)
    body_seen: set[tuple[str, str]] = set()  # any non-continued copy outside a TOC region
    for i, c in enumerate(cands):
        if c.kind not in kinds:
            continue
        key = (c.kind, c.label_canon)
        if "rej.continued" in c.rule_ids:
            # an unmarked copy outside any TOC region precedes this continuation copy: that
            # copy is the heading (even a bare "Item 7" label block whose title sits in the
            # next block), so the first-copy relief is taken back in full
            if "rej.continued_first" in c.rule_ids and key in body_seen:
                c.rule_ids.append("rej.continued_after_copy")
                c.score = round(c.score - 0.3, 3)
            continue
        if i not in toc_idx:
            body_seen.add(key)
        if key not in first_title:
            b = blocks[c.block_idx]
            if i not in toc_idx and not c.toc_hint and not b.in_table and not _index_like_block(b):
                first_title[key] = (_norm_title(c.title), _styled(c))
            continue
        ref_title, ref_styled = first_title[key]
        # a styled copy after an unstyled one is the heading below a running header that
        # opens the section's own first page, not a repeat of it
        if c.title and _norm_title(c.title) == ref_title and _at_page_start(blocks, c.block_idx) and not (_styled(c) and not ref_styled):
            c.rule_ids.append("rej.page_repeat")
            c.score = round(c.score - 0.15, 3)


def _styled(c: Candidate) -> bool:
    return any(r in c.rule_ids for r in ("sty.bold", "sty.underline", "sty.caps", "sty.font_size"))


def _index_like_block(b: Block) -> bool:
    for ln in (b.lines or (b.text,)):
        if _DOT_LEADER_RE.search(ln) or _TRAILING_PAGE_RE.search(ln) or regex.fullmatch(r"\s*\d{1,3}\s*", ln):
            return True
    return False


# ---------------------------------------------------------------------------
# Cover-page caption guard (Turn 9 B.2; docs/turn9_decisions/a4_caption_guard.md)
# ---------------------------------------------------------------------------
# Three rejection-evidence tags for the item rows a filer prints under a cover-page
# caption that says, in so many words, that the item is NOT in this document:
#
#   rej.caption_omission  a Rule 12b-25 / "does not include the following" / "DOCUMENTS
#                         OMMITTED:" notice over an enumerated list of the items the
#                         filing leaves out (b4_out_of_order.md family 1)
#   rej.caption_ibr       a "DOCUMENTS INCORPORATED BY REFERENCE" table whose rows are
#                         item labels pointing at the proxy statement / annual report
#                         (family 2)
#   rej.caption_xref      a filer agent's item-to-page cross-reference table ("ITEM 8:
#                         FINANCIAL STATEMENTS ... | SEE NOTE (B) BELOW"), or an item
#                         label followed on its OWN line by a page/note pointer (family 3)
#
# Scope, exactly like `rej.xref_pointer` above: the tags carry NO score penalty and are
# read only by `tree.out_of_order_items` (`tree._OOO_BLOCKING`).  They bar a NEW
# out-of-order placement; they never demote a heading the monotone chain accepted, so no
# accepted node's anchor, parent or span can move because of them.
#
# Membership is list/table-scoped, never a fixed line window -- that is the whole point.
# The Instruction J filings ("PART I -- The following Items have been omitted in
# accordance with General Instruction J to Form 10-K: Item 1. Business ... Item 3. Legal
# Proceedings", then the one item that is NOT omitted) carry the SAME caption over the
# list, and the node `seq.out_of_order` exists to recover is the follower AFTER the list.
# A 6-line-above window catches 11 of 27 of the census's sampled followers, which is why
# A.4 rejected the window and specified the list-scoped test (a4_caption_guard.md sections
# 2 and 4).  Scoping to the list is necessary but, as the next paragraph shows, not
# sufficient.
#
# The list end is the census's validated stop rule: from the caption's own line, a line
# opening with "ITEM <n>" extends the list; a SINGLE blank line is transparent (the HTML
# normalizer puts one between every row of a dotted-leader table); two consecutive blank
# lines, a "PART <roman>" line, or any other non-item line ends it.
#
# The list end alone is NOT enough, and B.2's pre-gate probe is what found it (1,500
# sampled `seq.out_of_order` filings of runs/full_v19: 273 nodes removed, 259 of them
# ITEM 1B "Unresolved Staff Comments" — the very class the rule exists to recover).  The
# real Instruction J filing prints the item it does NOT omit INSIDE the run of item lines,
# not after it:
#
#     The following Items have been omitted in accordance with General Instruction J ...
#     Item 1. Business.        Item 1A. Risk Factors.        Item 2. Properties.
#     Item 3. Legal Proceedings.
#     Item 1B. Unresolved Staff Comments.      <- not omitted; its body is the "None." below
#     None.
#
# (0000929638-21-000484, 0001104659-18-020339 and 257 more.)  A.4 section 4's "the
# list-scoped test catches no follower by construction" is circular: the census *defined*
# a follower as the first row at or after the list end, so the row it measured was Item 4,
# never the Item 1B sitting in the run.  What separates the two is the enumeration's
# order: an omission notice, an IBR table and a cross-reference table all enumerate items
# in ascending statutory order, and an entry that breaks that order is the filer's own
# heading printed among them — which is also exactly why the monotone chain dropped it and
# `seq.out_of_order` is looking at it at all.  So a candidate whose `order_key` falls below
# the running maximum of the entries above it is not an entry of the list; it does not end
# the list either (the enumeration resumes after it: "... Item 3. | Item 1B. | Item 4.").
_CAPTION_OMISSION_RE = regex.compile(
    r"RULE\s+12B-25|DOES\s+NOT\s+INCLUDE\s+THE\s+FOLLOWING|DOCUMENTS?\s+OMM?ITTED|"
    r"ITEMS?\s+(?:HAVE\s+BEEN\s+)?OMITTED|(?<![A-Z])OMITTED\b",
    regex.IGNORECASE,
)
_CAPTION_IBR_RE = regex.compile(
    r"DOCUMENTS?\s+INCORPORATED\s+BY\s+REFERENCE|INCORPORATED\s+HEREIN\s+BY\s+REFERENCE",
    regex.IGNORECASE,
)
_CAPTION_XREF_RE = regex.compile(
    r"\bSEE\s+NOTE\b|\bCROSS[- ]REFERENCE\b|\bREFERENCE\s+TO\s+ITEM\b", regex.IGNORECASE
)
# (b) of family 3: the candidate's own line carries the label and then a page/note
# pointer.  No scoping is needed -- it reads only the candidate's own line, from the
# label forward -- and it is the sub-test that caught 0000852807-94-000004 ITEM 11
# ("ITEM 11. Executive Compensation  Page 5 under caption ...").
_XREF_POINTER_LINE_RE = regex.compile(
    r"\bSEE\s+NOTE\b|\bCROSS[- ]REFERENCE\b|\bPAGE\s+\d|\bP\.\s*\d+\b|\bNOTE\s*\(?[A-Z0-9]+\)?\b",
    regex.IGNORECASE,
)
# A list entry's line.  Two shapes on top of the census's, both of them entries the census
# could not see because the ONE line it read is not where the filer put the label:
#   "PART II, Item 7. Management's Discussion and Analysis of Plan of Operations" — the row
#     of a "DOCUMENTS OMMITTED:" notice carries its statutory Part (0001437749-12-002368,
#     b4 family 1).  The census's PART test fired first and ended the list at entry one, so
#     the notice reached nothing.  The item test is tried first here, so a Part-prefixed row
#     is an entry; a BARE "PART III" line still ends the list (it is a real Part heading, and
#     one of the census's 27 Instruction J list ends).
#   a bare enumeration ordinal on its own line — "Pursuant to Rule 12b-25(b), this Form 10-K
#     does not include the following: | 1. | Item 6. Selected Financial Data | 2. | Item 7.
#     ..." (0000950152-01-503037, -02-005312: b4's two canonical family-1 filings).  The
#     normalizer puts each table cell on its own line, so the "1." the census expected to
#     find in front of "Item 6" is a line of its own, and the list ended before entry one.
_CAPTION_ITEM_LINE_RE = regex.compile(
    r"^\s*(?:\(?\d{1,2}[.)]\s*)?(?:PARTS?\s+[IVX]{1,4}\s*[,.:;\-–—]\s*)?ITEM\s+\d{1,2}[A-Z]?\b",
    regex.IGNORECASE,
)
_CAPTION_ORDINAL_LINE_RE = regex.compile(r"^\s*\(?(?:\d{1,2}|[a-z])[.)]?\s*$", regex.IGNORECASE)
_CAPTION_MAX_LIST_LINES = 60
_CAPTION_TAIL = 3000  # how far past the last item candidate a caption may still open a list
# A caption is a line of caption length.  The census scanned only the first 8% of the
# document, before the first accepted Part, so every match it saw was cover-page furniture;
# with that cap dropped (a4_caption_guard.md section 3) the same words also turn up in the
# middle of body prose, and the line that follows such a sentence is a real heading, not a
# list entry.  Three of the 14 nodes B.2's pre-gate probe removed were exactly that, and
# all three sit on a normalized line of 380-1,000+ characters:
#   "... BB&T owns free-standing operations centers ... See Note 5 'Premises and
#    Equipment' ... for additional disclosures related to properties and other fixed
#    assets." followed by "ITEM 5. MARKET FOR REGISTRANT'S COMMON EQUITY ..."
#    (0000092230-15-000021, 0000092230-14-000014)
#   "We will file a definitive Proxy Statement ... omitted under General Instruction G(3)
#    to Form 10-K ..." followed by "ITEM 12." (0001495240-24-000004) -- a Part III
#    incorporation paragraph, and sinking the item whose whole section IS that pointer is
#    the thing `rej.xref_pointer` was deliberately scoped not to do (b4_out_of_order.md).
# Every real caption in the b4 catalogue and in the census's own examples is far shorter:
# "OMITTED IN ENTIRETY:" (20), "DOCUMENTS INCORPORATED BY REFERENCE" (35), "PORTIONS TO BE
# FILED BY AMENDMENT PURSUANT TO RULE 12B-25" (57), "Pursuant to Rule 12b-25(b), this Form
# 10-K does not include the following:" (74), "The following Items have been omitted in
# accordance with General Instruction J(1) to Form 10-K:" (94).
_CAPTION_LINE_MAX = 200
# ... and a caption is TYPESET as a caption.  The length test alone does not separate a
# caption from the tail of a text-era sentence, because the text era wraps at ~80
# characters: "... the Registrant's 1995 Annual\nReport to Shareholders, incorporated
# herein by reference.\n\nItem 9. Changes in and Disagreements with Accountants ..."
# (0000078778-94-000014, -95-000018, -96-000016) is a 57-character line that ends in the
# family-2 phrase and is immediately followed by a real body heading — three real headings
# the Turn 4 bank's consensus calls headings, lost before this test was added.  A caption
# line ends in a colon, is set in capitals, or is title-cased; a sentence tail is none of
# those.  Small words may stay lower case in a title ("Documents Incorporated by
# Reference"), which is what `_CAPTION_SMALL_WORDS` allows.
_CAPTION_SMALL_WORDS = frozenset(
    "a an and as at by for from in into of on or the to under with".split()
)
_CAPTION_WORD_RE = regex.compile(r"[A-Za-z][A-Za-z'’\-]*")


def _caption_shaped(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > _CAPTION_LINE_MAX:
        return False
    if s.endswith(":"):
        return True
    letters = [c for c in s if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) >= 0.85 * len(letters):
        return True
    words = _CAPTION_WORD_RE.findall(s)
    return bool(words) and all(w[0].isupper() or w.lower() in _CAPTION_SMALL_WORDS for w in words)


CAPTION_FAMILIES = (
    (_CAPTION_OMISSION_RE, "rej.caption_omission"),
    (_CAPTION_IBR_RE, "rej.caption_ibr"),
    (_CAPTION_XREF_RE, "rej.caption_xref"),
)
CAPTION_RULES = tuple(tag for _, tag in CAPTION_FAMILIES)


def _line_starts(norm: str) -> list[int]:
    starts = [0]
    pos = norm.find("\n")
    while pos >= 0:
        starts.append(pos + 1)
        pos = norm.find("\n", pos + 1)
    return starts


def _line_of(starts: list[int], off: int) -> int:
    return max(0, min(bisect_right(starts, off) - 1, len(starts) - 1))


def _line_end(norm: str, starts: list[int], li: int) -> int:
    return starts[li + 1] - 1 if li + 1 < len(starts) else len(norm)


def caption_list_end(norm: str, starts: list[int], cap_li: int) -> tuple[int, int]:
    """(normalized offset where the caption's list ends, number of ITEM lines in it).

    `scripts/turn9/caption_census.py`'s list-end logic, which is what the A.4 bank was
    measured with, plus the two entry shapes at `_CAPTION_ITEM_LINE_RE` / the bare
    enumeration ordinal.  Stop rules are unchanged: two blank lines in a row, a bare Part
    heading, or any other non-item line.
    """
    j = cap_li + 1
    n_item_lines = 0
    blanks = 0
    while j < len(starts) and j - cap_li <= _CAPTION_MAX_LIST_LINES:
        line = norm[starts[j] : _line_end(norm, starts, j)].strip()
        if line == "":
            blanks += 1
            j += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        if _CAPTION_ITEM_LINE_RE.match(line):
            n_item_lines += 1
            j += 1
            continue
        if _CAPTION_ORDINAL_LINE_RE.match(line):
            j += 1  # "1." / "(a)" in its own table cell: list furniture, like a blank line
            continue
        break
    return (starts[j] if j < len(starts) else len(norm)), n_item_lines


def caption_guard_pass(cands: list[Candidate], norm: str, *, kinds: tuple[str, ...] = ("item",)) -> None:
    """Tag item candidates that are members of a caption-opened list or table.

    Runs over the whole normalized text: there is no percentage-of-document cap.  The
    A.4 census used one (first 8% of raw bytes) purely to bound its own scan cost, and
    it excludes 5 of the 8 items in the two canonical family-1 filings, whose cover page
    runs past 8% of a document swollen by exhibits (a4_caption_guard.md section 1).
    """
    items = sorted((c for c in cands if c.kind in kinds), key=lambda c: c.norm_start)
    if not items:
        return
    starts = _line_starts(norm)
    item_offs = [c.norm_start for c in items]
    hi = min(len(norm), item_offs[-1] + _CAPTION_TAIL)
    for rx, tag in CAPTION_FAMILIES:
        seen_caption_lines: set[int] = set()
        for m in rx.finditer(norm, 0, hi):
            cap_li = _line_of(starts, m.start())
            if cap_li in seen_caption_lines:
                continue
            seen_caption_lines.add(cap_li)
            if not _caption_shaped(norm[starts[cap_li] : _line_end(norm, starts, cap_li)]):
                continue  # a paragraph or a sentence tail, not a caption
            list_end, n_item_lines = caption_list_end(norm, starts, cap_li)
            if n_item_lines == 0:
                continue  # a bare caption with no enumerated list under it reaches nothing
            run_max = -1
            for k in range(bisect_left(item_offs, m.start()), len(items)):
                c = items[k]
                if c.norm_start >= list_end:
                    break
                if c.order_key < run_max:
                    continue  # breaks the enumeration: the filer's own item, printed inside it
                run_max = max(run_max, c.order_key)
                if tag not in c.rule_ids:
                    c.rule_ids.append(tag)
    for c in items:
        if "rej.caption_xref" in c.rule_ids:
            continue
        li = _line_of(starts, c.norm_start)
        if _XREF_POINTER_LINE_RE.search(norm, c.norm_start, _line_end(norm, starts, li)):
            c.rule_ids.append("rej.caption_xref")


# ---------------------------------------------------------------------------
# Front-matter table detector (Turn 10 B.2; docs/turn10_decisions/a3_fmtable.md)
# ---------------------------------------------------------------------------
# `rej.fm_table`: the candidate is a ROW of a >= 2-row front-matter table -- a cover-page
# "documents incorporated by reference" table or a filer agent's item-to-page
# cross-reference table -- whose second column says where the item's content actually is.
#
# The caption guard above cannot reach these (b2_caption_guard.md section 7 leaves 15 of
# the 26 b4-catalogued `seq.out_of_order` false accepts standing) for three reasons, all
# structural: a column header sits between the caption and the first row and ends the
# caption's list walk; a bare `PART <roman>` line sits between the rows, and that is the
# one stop rule the caption guard may not relax (it is also one of A.4's 27 Instruction J
# list ends); and the pointer sits in the SECOND column, never on the label's own line,
# which is all `rej.caption_xref`'s own-line sub-test reads.  The mechanism that reaches
# all three is the second column itself, and it needs no caption: the census confirms the
# two guards are near-disjoint (of 80,470 caught rows only 240 carry a caption tag, and of
# the 78 accepted out-of-order nodes this tag removes, 0 do --
# runs/judge/turn10-fmtable-census.txt section 1).
#
# Scope, exactly like the caption guard and `rej.xref_pointer`: NO score weight, read only
# by `tree.out_of_order_items` (`tree._OOO_BLOCKING`).  It bars a NEW out-of-order
# placement and never demotes a node the monotone chain accepted -- 393 chain-accepted
# table rows keep their nodes (a3_fmtable.md section 4).  It runs BEFORE `detect_toc`,
# which consults none of its ids, so every TOC region is bit-identical.
#
#   row test   the candidate's printed row carries a second column beside label+title:
#                cell        html/ixbrl: the block is in a table and its ROW holds >= 2
#                            cells with text (`Block.row_text` beyond the candidate's own
#                            cell, or a sibling cell of the same row within 6 blocks)
#                gap         text era: the candidate's own line in `Block.lines` carries an
#                            internal run of >= 3 spaces opening at column >= 12 with >= 2
#                            non-space characters after it; the LAST such gap is the column
#                            boundary.  The gap is NOT in `normalized_text` -- the
#                            normalizer collapses runs of spaces inside a line, which is
#                            exactly why b4 read these rows as "a heading followed by
#                            prose" -- so the pass takes `blocks` as well, like
#                            `page_repeat_pass` and `_index_like_block`.
#                interleave  as `gap`, and a wrapped continuation line within 4 lines
#                            carries a gap at the same column (+-3): the two-column row
#                            the normalizer interleaves.
#              Two guards, both measured (a3_fmtable.md section 2, stoprule.txt A -> C):
#              `_fm_first_col_carries_label_and_title` -- the first column must hold the
#              label AND >= 3 characters of its title, because "ITEM 4." | "(REMOVED AND
#              RESERVED)", "Item" | "1B. Unresolved Staff Comments" and "*" | "Item 2.
#              Properties." are ordinary headings typeset in two cells (1.1168% ->
#              0.1304% short-body false catches); and `_fm_prose_cell` -- a second cell of
#              >= 150 characters that is prose naming no page, note, document or status is
#              the item's own BODY beside it (0001169232-07-001444), not a pointer column
#              (0.1304% -> 0.1223%).
#   run test   >= 2 rows, consecutive members within 12 blocks (one html table row emits
#              one block per cell) and 1,200 normalized characters, with only FURNITURE
#              between them: blank lines, rule lines, `<PAGE>`, SGML `<S>`/`<C>`/`<TABLE>`
#              tags, bare page numbers, bare enumeration ordinals, any block of the same
#              table, a bare `PART <roman>` line, and a column-header line naming Page /
#              Note / Document / Incorporated / Reference / Location / Caption / Annual
#              Report / Proxy Statement / Form 10-K / Exhibit.
#   stop rule  a prose block (a non-table `para` of >= 200 chars with three consecutive
#              lower-case words, `toc._PROSE_RE`'s own test), two blank lines outside a
#              table or any other non-furniture line, an item candidate that FAILS the row
#              test, or the gap limits.  No body-length threshold is used anywhere: a
#              front-matter table row and a one-line item have exactly the same body
#              length, and only the second column tells them apart (section 3).
#   evidence   a condemned run must say WHERE the item's content is: a column header or
#              caption above the run names the second column, or at least one member's
#              second column is itself a page / note / document pointer.  A run of pure
#              status cells is a body laid out in two columns -- 0001193125-12-140190
#              typesets Part I as "Item 1B. Unresolved Staff Comments." | "Not
#              Applicable." -- and is not condemned (0.1223% -> 0.1141%).
#
# `PART <roman>` may be transparent here where the caption guard could not make it so
# because every member must pass the row test first: after a real body Part heading the
# next item is a heading block rather than a two-column row, so the run ends on the MEMBER
# (stop rule 3), not on the Part line.  Measured, not argued: A.4's whole must-not-catch
# set -- the four family-1 sole-copy real headings and all 28 `instruction_j_follower`
# windows -- is 0 of 32 caught (runs/judge/turn10-fmtable-seeds.txt set 2).
_FM_MAX_GAP_BLOCKS = 12
_FM_MAX_GAP_CHARS = 1200
_FM_MIN_RUN = 2
_FM_MIN_GAP_SPACES = 3
_FM_MIN_GAP_COL = 12
_FM_COL_TOL = 3
_FM_MIN_TITLE_CHARS = 3
_FM_PROSE_CELL_MIN = 150

_FM_ITEM_LINE_RE = regex.compile(
    r"^\s*(?:\(?\d{1,2}[.)]\s*)?(?:PARTS?\s+[IVX]{1,4}\s*[,.:;\-–—]\s*)?ITEMS?\s+\d{1,2}[A-Z]?\b",
    regex.IGNORECASE,
)
_FM_PART_SHORT_RE = regex.compile(r"^\s*PARTS?\s+(?:[IVX]{1,4}|[1-4])\b[\s.:,\-–—]*(?:\(?SEE\b.*)?$", regex.IGNORECASE)
_FM_RULE_LINE_RE = regex.compile(r"^\s*[-_=–—\s.]{4,}\s*$")
_FM_PAGENO_LINE_RE = regex.compile(r"^\s*\(?(?:[A-Z]{1,3}-)?\d{1,4}\)?\s*$")
_FM_SGML_TAG_RE = regex.compile(r"^\s*<(?:S|C|PAGE|TABLE|/TABLE|CAPTION|/S|/C)>\s*$", regex.IGNORECASE)
_FM_HEADER_RE = regex.compile(
    r"\b(?:PAGES?|NOTES?|DOCUMENTS?|INCORPORATED|REFERENCES?|LOCATIONS?|CAPTIONS?|"
    r"ANNUAL\s+REPORT|PROXY\s+STATEMENT|FORM\s+10-?K|EXHIBITS?|WHERE\s+(?:FOUND|LOCATED))\b",
    regex.IGNORECASE,
)
_FM_PROSE_RE = regex.compile(r"[a-z]{3,}\s+[a-z]{3,}\s+[a-z]{3,}")
_FM_GAP_RE = regex.compile(r"\S {%d,}(?=\S)" % _FM_MIN_GAP_SPACES)
# What the second column holds, for the run-level evidence test.
_FM_K_PAGE = regex.compile(r"\bPAGES?\b|\bP\.?\s*\d|\bPP\.\s*\d|^\s*\(?(?:[A-Z]{1,3}-)?\d{1,4}\)?\s*$|\b(?:[A-Z]{1,3}-)?\d{1,3}\s*$")
_FM_K_NOTE = regex.compile(r"\bSEE\s+NOTE\b|\bNOTE\s*\(?[A-Z0-9]\)?|\bSEE\s+(?:BELOW|ABOVE|PAGE)\b|\bFOOTNOTE\b", regex.IGNORECASE)
_FM_K_DOC = regex.compile(r"\bANNUAL\s+REPORT\b|\bPROXY\s+STATEMENT\b|\bFORM\s+10-?K\b|\bEXHIBIT\b|\bINCORPORAT|\bREGISTRATION\s+STATEMENT\b|\bINFORMATION\s+STATEMENT\b|\bPART\s+[IVX]", regex.IGNORECASE)
_FM_K_STATUS = regex.compile(r"^\W*(?:OMITTED|NONE|NOT\s+APPLICABLE|N\s*/\s*A|INAPPLICABLE|RESERVED)\b", regex.IGNORECASE)
_FM_POINTER_KINDS = ("page", "note", "document")


def _fm_lines(b: Block) -> tuple[str, ...]:
    return b.lines or (b.text,)


def _fm_kind2(s: str) -> str:
    if _FM_K_STATUS.search(s):
        return "status"
    if _FM_K_NOTE.search(s):
        return "note"
    if _FM_K_DOC.search(s):
        return "document"
    if _FM_K_PAGE.search(s):
        return "page"
    return "other"


def _fm_first_col_carries_label_and_title(first: str) -> bool:
    """The row's FIRST column must hold the item label AND some of its title."""
    t = first.strip()
    m = _FM_ITEM_LINE_RE.match(t)
    if m is None:
        return False
    return len(t[m.end():].strip(" .:;)-–—")) >= _FM_MIN_TITLE_CHARS


def _fm_prose_cell(s: str) -> bool:
    """The second cell is the item's BODY beside it, not a pointer column."""
    return _fm_kind2(s) == "other" and len(s) >= _FM_PROSE_CELL_MIN and bool(_FM_PROSE_RE.search(s))


def _fm_gap_split(line: str) -> tuple[int, str] | None:
    """(column where the second column opens, its text) for a line with a column gap."""
    best = None
    for m in _FM_GAP_RE.finditer(line):
        col = m.end()
        if col < _FM_MIN_GAP_COL:
            continue
        rest = line[col:].strip()
        if len(rest) < 2:
            continue
        best = (col, rest)  # the LAST gap on the line is the column boundary
    return best


def _fm_html_row_second_cell(blocks: list[Block], b: Block) -> str | None:
    """The rest of this table row beyond the candidate's own cell."""
    if not b.in_table:
        return None
    if b.row_text:
        own = b.text.strip()
        rest = b.row_text.strip()
        if rest.startswith(own):
            rest = rest[len(own):]
        elif own in rest:
            rest = rest.split(own, 1)[1]
        rest = rest.strip(" \t|")
        return rest if len(rest) >= 2 else None
    for j in range(b.idx + 1, min(len(blocks), b.idx + 6)):
        x = blocks[j]
        if not x.in_table or x.table_id != b.table_id:
            break
        if x.row_text:  # a new row started
            break
        if x.cell_col != b.cell_col and x.text.strip():
            return x.text.strip()
    return None


def _fm_row_test(blocks: list[Block], c: Candidate) -> tuple[str, str] | None:
    """(row_kind, the second column's text) when the candidate is a two-column row."""
    b = blocks[c.block_idx]
    rest = _fm_html_row_second_cell(blocks, b)
    if rest:
        if not _fm_first_col_carries_label_and_title(b.text.strip().split("\n", 1)[0]):
            return None  # "ITEM 4." | "(REMOVED AND RESERVED)": a heading in two cells
        if _fm_prose_cell(rest):
            return None  # the item's own body in the next cell
        return ("cell", rest)
    lns = _fm_lines(b)
    own_i = next((i for i, ln in enumerate(lns) if _FM_ITEM_LINE_RE.match(ln)), 0)
    if own_i >= len(lns):
        return None
    g = _fm_gap_split(lns[own_i])
    if g is None:
        return None
    col, rest = g
    if not _fm_first_col_carries_label_and_title(lns[own_i][:col]):
        return None
    for ln in lns[own_i + 1: own_i + 5]:
        g2 = _fm_gap_split(ln)
        if g2 and abs(g2[0] - col) <= _FM_COL_TOL:
            joined = rest + " " + g2[1]
            return None if _fm_prose_cell(joined) else ("interleave", joined)
    return None if _fm_prose_cell(rest) else ("gap", rest)


def _fm_furniture_line(line: str) -> bool:
    s = line.strip()
    if s == "" or _FM_RULE_LINE_RE.match(s) or _FM_PAGENO_LINE_RE.match(s) or _FM_SGML_TAG_RE.match(s):
        return True
    if _CAPTION_ORDINAL_LINE_RE.match(s):
        return True  # "1." / "(a)" alone in its own cell
    if _FM_PART_SHORT_RE.match(s):
        return True  # a bare PART <roman> between two rows (see the note above)
    if len(s) <= 120 and _FM_HEADER_RE.search(s) and not _FM_PROSE_RE.search(s.lower()) and len(s.split()) <= 14:
        return True  # a column header naming the second column
    return False


def _fm_block_is_furniture(b: Block, row_block_ids: set[int]) -> bool:
    if b.idx in row_block_ids:
        return True
    if b.kind in ("page", "hr", "comment", "nav"):
        return True
    if b.in_table:
        return True  # sibling / continuation cells of the table itself
    return all(_fm_furniture_line(ln) for ln in _fm_lines(b))


def _fm_is_prose(b: Block) -> bool:
    return (not b.in_table) and b.kind == "para" and len(b.text) >= 200 and bool(_FM_PROSE_RE.search(b.text))


def _fm_header_above(blocks: list[Block], c: Candidate) -> bool:
    b = blocks[c.block_idx]
    for j in range(max(0, b.idx - 6), b.idx):
        for ln in _fm_lines(blocks[j]):
            s = ln.strip()
            if not s or len(s) > 120 or _FM_ITEM_LINE_RE.match(s):
                continue
            if _FM_HEADER_RE.search(s) and len(s.split()) <= 14:
                return True
    return False


def fm_table_pass(cands: list[Candidate], blocks: list[Block], *, kinds: tuple[str, ...] = ("item",)) -> None:
    """Tag item candidates that are rows of a front-matter table (`rej.fm_table`).

    Turn 10 B.2, docs/turn10_decisions/a3_fmtable.md.  Tag only: no score moves, and only
    `tree.out_of_order_items` (`tree._OOO_BLOCKING`) reads it.  `fm.row_cell` /
    `fm.row_gap` / `fm.row_interleave` record which mechanism found the second column and
    have no consumer.  Takes `blocks` because the text era's column gap survives only in
    `Block.lines`; `normalized_text` collapses it.
    """
    tested: dict[int, tuple[str, str]] = {}
    for i, c in enumerate(cands):
        if c.kind not in kinds:
            continue
        t = _fm_row_test(blocks, c)
        if t is not None:
            tested[i] = t
    if len(tested) < _FM_MIN_RUN:
        return
    row_block_ids = {cands[i].block_idx for i in tested}
    runs: list[list[int]] = []
    cur: list[int] = []
    for i in sorted(tested):
        if not cur:
            cur = [i]
            continue
        bp, bc = blocks[cands[cur[-1]].block_idx], blocks[cands[i].block_idx]
        broke = bc.idx - bp.idx > _FM_MAX_GAP_BLOCKS or bc.norm_start - bp.norm_end > _FM_MAX_GAP_CHARS
        if not broke:
            for j in range(bp.idx + 1, bc.idx):
                x = blocks[j]
                if _fm_is_prose(x) or not _fm_block_is_furniture(x, row_block_ids):
                    broke = True
                    break
        if broke:
            runs.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        runs.append(cur)
    for run in runs:
        if len(run) < _FM_MIN_RUN:
            continue
        if not (_fm_header_above(blocks, cands[run[0]])
                or any(_fm_kind2(tested[i][1]) in _FM_POINTER_KINDS for i in run)):
            continue  # a run of pure status cells is a body in two columns, not a table
        for i in run:
            c = cands[i]
            if "rej.fm_table" not in c.rule_ids:
                c.rule_ids.append("rej.fm_table")
                c.rule_ids.append(f"fm.row_{tested[i][0]}")


# ---------------------------------------------------------------------------
# Turn 11 B.1 / B.2: the contract table-of-contents boundary
# docs/turn11_decisions/a1_ctoc_boundary.md sections 4, 5 and 10.
#
# `toc._contract_regions` both under-reaches and over-reaches, and the two defects are
# mirror images, so the two rules are built together and gated on one diff.
#
#   rej.ctoc_row        (B.1) a contract section/article candidate that is a two-column
#                       INDEX ROW, sitting in a RUN of >= _CTOC_MIN_RUN such rows with only
#                       furniture between them, followed by no prose, whose OWN second
#                       column is a page pointer, WHERE NO REGION COVERS IT.  The 137 live
#                       false accepts of runs/full_ex10_v19 are that shape: in the HTML eras
#                       `Section 11.23` | `USA Patriot Act 101`, in the text era the same row
#                       typeset with a column gap.  `_contract_regions` proposes nothing over
#                       them because the run's dup share is 0.42 (median 0.00 over the whole
#                       census) against `_CTOC_MIN_DUP` 0.6 -- the body copies are not weakly
#                       duplicated, they are not candidates at all -- so no relaxation of the
#                       region thresholds reaches this class and only a rule that reads the
#                       ROW does (memo section 2).
#
#   ctoc.region_relief  (B.2) a member of a proposed region that is NOT an index row, IS
#                       followed by prose within one block, and carries no page or leader
#                       evidence anywhere on its row.  The 19 real body headings the parser
#                       condemns as `toc` are that shape (memo section 5).
#
# The two predicates are disjoint three times over and the disjointness is asserted in
# tests/test_turn11_ctoc.py: B.1 fires only outside every region and B.2 only inside one;
# B.2 requires the row test to FAIL and B.1 requires it to pass; B.1 requires no prose
# within one block and B.2 requires prose.
#
# Measured before anything was built (the memo's artifacts): the row test reads 134 of the
# 137 and 0 of the 19 (turn11-ctoc-setclass.txt); the member-level page clause is the whole
# difference between 0.780 and 1.000 precision on the `flagged_accepted` stratum and clears
# 0.90 out of sample at 1.000 on a disjoint 100-window bank (turn11-ctoc-b1v2-bank-report.txt);
# B.2 clears its 0.80 gate at 0.862 on 94 windows and `control_rejected` says it leaves no
# second class of wrongly-condemned rows behind (0 of 100).  `image_text` is out of scope for
# both: that profile emits one undifferentiated block, so "prose follows within one block" is
# vacuously true and there is no table structure to read (census section 3b).
#
# `scripts/turn11/a1_features.py` is the reference implementation this is ported from and
# must be kept in step with it -- the census and both banks were computed with it.
# ---------------------------------------------------------------------------
_CTOC_KINDS = ("section", "article")
_CTOC_MIN_RUN = 3        # memo section 4: costs nothing on the 137, drops 3,717 census rows vs 2
_CTOC_MIN_CELL = 1       # a contract index row's first page cell is literally "1"
_CTOC_PROSE_MIN = 80     # a body sentence beside an index row's 30-60 character title
_CTOC_PROSE_MIN_CAND = 150   # when the next block is itself a section/article row
_CTOC_MIN_TITLE_ALPHA = 3

# The contract analogue of `_FM_ITEM_LINE_RE`.  `_fm_row_test` cannot be called on contract
# labels at all: both of its arms run the label line through
# `_fm_first_col_carries_label_and_title`, whose `_FM_ITEM_LINE_RE` matches "ITEM n" only.
_CTOC_LABEL_LINE_RE = regex.compile(
    r"^\s*(?:\(?\d{1,2}[.)]\s*)?"
    r"(?:SECTIONS?|ARTICLES?|CLAUSES?)?\s*"
    r"(?:\d{1,3}(?:\.\d{1,3})*[A-Za-z]?|[IVXLCDM]{1,7}|[A-Z])\b",
    regex.IGNORECASE,
)

# Page / leader evidence on a line.  `TRAIL_NUM` / `LEADER_DOTS` / `TRAIL_ROMAN` /
# `TRAIL_FPAGE` / `_LABEL_LEAD` / `BARE_PAGE` are scripts/turn10/audit_bank.py verbatim (the
# feature every Turn 10 and Turn 11 bank was scored with).  `_CTOC_GLUED_PAGE` is the
# widening memo section 7d demands: a page number glued to a TWO-dot leader
# ("13.1 Rights, Duties and Immunities of the Administrative Agent..51") is missed by
# `LEADER_DOTS` (three dots) and by `TRAIL_NUM` (needs whitespace before the number).
_CTOC_TRAIL_NUM = regex.compile(r"(?:^|\s)-?\s*\d{1,4}\s*-?\s*$")
_CTOC_LEADER_DOTS = regex.compile(r"\.\s?\.\s?\.")
_CTOC_GLUED_PAGE = regex.compile(r"\.{2,}\s*\d{1,4}\s*$")
_CTOC_ROMAN = r"(?=[MDCLXVI])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_CTOC_TRAIL_ROMAN = regex.compile(rf"(?:^|\s{{2}})\s*[-(]?\s*(?i:{_CTOC_ROMAN})\s*[-)]?\s*$")
_CTOC_TRAIL_FPAGE = regex.compile(r"(?:^|\s)-?\s*(?i:[A-Z])\s?-\s?\d{1,4}\s*-?\s*$")
_CTOC_LABEL_LEAD = regex.compile(
    r"^\s*(?:(?:PART|ITEM|ARTICLE|SECTION|CLAUSE|CHAPTER|APPENDIX|ANNEX|EXHIBIT|SCHEDULE|TITLE)\s*)?"
    r"(?:No\.?\s*)?(?:\d+(?:\.\d+)*[A-Za-z]?(?:\([A-Za-z0-9]+\))?|[IVXLCDM]+|[A-Za-z])?\s*[.:)\-–—]*\s*",
    regex.IGNORECASE)
_CTOC_BARE_PAGE = regex.compile(
    r"^\s*[-(\[]?\s*(?:\d{1,4}|(?i:[ivxlcdm]{1,7})|(?i:[A-Z])\s?-\s?\d{1,4})\s*[-)\]]?\s*$")
_CTOC_NON_ALPHA_RE = regex.compile(r"[^A-Za-z]")


def _ctoc_evidence_kinds(line: str) -> list[str]:
    """Which table-of-contents page/leader patterns a line carries (audit_bank.evidence_kinds
    plus the two-dot-leader widening).  A trailing number counts only when TITLE TEXT
    precedes it, or every bare label line ("SECTION 7.05") would read as page evidence."""
    body = line.rstrip()
    out: list[str] = []
    if _CTOC_LEADER_DOTS.search(body):
        out.append("dot_leader")
    if _CTOC_GLUED_PAGE.search(body):
        out.append("glued_page")
    for name, rx in (("page_number", _CTOC_TRAIL_NUM), ("roman_page", _CTOC_TRAIL_ROMAN),
                     ("f_page", _CTOC_TRAIL_FPAGE)):
        m = rx.search(body)
        if m is None:
            continue
        prefix = _CTOC_LABEL_LEAD.sub("", body[:m.start()], count=1)
        if len(_CTOC_NON_ALPHA_RE.sub("", prefix)) >= _CTOC_MIN_TITLE_ALPHA:
            out.append(name)
    return out


def _ctoc_is_bare_page(line: str) -> bool:
    """A line that is nothing but a page number: the HTML-era row whose page is its own cell."""
    return bool(line.strip()) and bool(_CTOC_BARE_PAGE.match(line))


def _ctoc_first_col_carries_label_and_title(first: str) -> bool:
    """`_fm_first_col_carries_label_and_title` with the contract label set."""
    t = first.strip()
    m = _CTOC_LABEL_LINE_RE.match(t)
    if not m:
        return False
    return len(t[m.end():].strip(" .:;)-–—")) >= _FM_MIN_TITLE_CHARS


def _ctoc_html_row_second_cell(blocks: list[Block], b: Block) -> str | None:
    """`_fm_html_row_second_cell` with its `len(rest) >= 2` floor lowered to one character.

    The 10-K floor drops one-character second cells as noise.  A credit agreement's index row
    for the first section of the first article has a second column that is literally "1", so
    the 10-K floor rejects exactly the first row of every contract table of contents (5 of
    the 11 rows of the 137 the un-relaxed test missed).
    """
    if not b.in_table:
        return None
    if b.row_text:
        own = b.text.strip()
        rest = b.row_text.strip()
        if rest.startswith(own):
            rest = rest[len(own):]
        elif own in rest:
            rest = rest.split(own, 1)[1]
        rest = rest.strip(" \t|")
        return rest if len(rest) >= _CTOC_MIN_CELL else None
    for j in range(b.idx + 1, min(len(blocks), b.idx + 6)):
        x = blocks[j]
        if not x.in_table or x.table_id != b.table_id:
            break
        if x.row_text:
            break
        if x.cell_col != b.cell_col and x.text.strip():
            return x.text.strip()
    return None


def _ctoc_gap_split(line: str) -> tuple[int, str] | None:
    """`_fm_gap_split` with the same one-character floor, for the same reason."""
    best = None
    for m in _FM_GAP_RE.finditer(line):
        col = m.end()
        if col < _FM_MIN_GAP_COL:
            continue
        rest = line[col:].strip()
        if len(rest) < _CTOC_MIN_CELL:
            continue
        best = (col, rest)  # the LAST gap on the line is the column boundary
    return best


def _ctoc_own_line(b: Block, c: Candidate) -> int:
    """Index into `_fm_lines(b)` of the line the candidate's head sits on."""
    lns = _fm_lines(b)
    lrs = b.line_raw_starts
    if lrs:
        i = bisect_right(list(lrs), c.head_raw_start) - 1
        if 0 <= i < len(lns):
            return i
    for i, ln in enumerate(lns):
        if _CTOC_LABEL_LINE_RE.match(ln.strip()):
            return i
    return 0


def _ctoc_row_test(blocks: list[Block], c: Candidate) -> tuple[str, str] | None:
    """(row_kind, second column text) when a contract candidate is a two-column row.

    Four column-gap arms (`_ctoc_row_test_columns`) and then the leader arm
    (`_ctoc_leader_arm`, Turn 12 B.1 / C1), which is reached only when all four return
    `None`.  Split in two so the leader arm can state its own predicate; the call order is
    what `docs/turn12_decisions/a1_index_anchor.md` section 9.2 specifies and what
    `scripts/turn12/a1_probe_ctr.py::_install` measured (129/129 on the rows it condemns).
    """
    t = _ctoc_row_test_columns(blocks, c)
    if t is not None:
        return t
    return _ctoc_leader_arm(blocks, c)


def _ctoc_row_test_columns(blocks: list[Block], c: Candidate) -> tuple[str, str] | None:
    """(row_kind, second column text) when a contract candidate is a two-column row.

    `_fm_row_test`'s shape with three deliberate departures, each measured on the 137
    (memo section 4, clause 1):

    1. the cell arm does NOT require the first column to carry label AND title.  The 10-K
       guard exists because "ITEM 4." | "(REMOVED AND RESERVED)" is one heading typeset in
       two cells; the contract index is the opposite shape -- "Section 11.23" | "USA Patriot
       Act 101" -- and requiring a title in the first cell rejects every row of it (76 of
       the 137).  What separates that from a real body heading in a table is the run test,
       the prose stop rule and the member's own page cell, never the first column.
    2. a one-character second column counts (the page cell "1").
    3. `wrap`: the label line carries no gap but a continuation line within 4 lines does --
       the index row whose title wraps ("SECTION 2.03. Issuance of and Drawings and
       Reimbursement" / "Under Letters of Credit    14").  `_fm_row_test`'s `interleave` arm
       requires a gap on the label line first and so cannot see it.

    `_fm_prose_cell` guards every arm: a second cell of >= 150 characters that is prose
    naming no page, note, document or status is the section's own body beside it.
    """
    b = blocks[c.block_idx]
    rest = _ctoc_html_row_second_cell(blocks, b)
    if rest:
        if _fm_prose_cell(rest):
            return None  # the section's own body in the next cell, not an index row
        return ("cell", rest)
    lns = _fm_lines(b)
    if not lns:
        return None
    own_i = _ctoc_own_line(b, c)
    own = lns[own_i]
    if not _CTOC_LABEL_LINE_RE.match(own.strip()):
        return None
    g = _ctoc_gap_split(own)
    if g is None:
        for ln in lns[own_i + 1: own_i + 5]:
            if _CTOC_LABEL_LINE_RE.match(ln.strip()):
                break  # the next row, not this row's continuation
            g2 = _ctoc_gap_split(ln)
            if g2 and g2[0] >= _FM_MIN_GAP_COL:
                return None if _fm_prose_cell(g2[1]) else ("wrap", g2[1])
        return None
    col, rest = g
    if not _ctoc_first_col_carries_label_and_title(own[:col]):
        return None
    for ln in lns[own_i + 1: own_i + 5]:
        g2 = _ctoc_gap_split(ln)
        if g2 and abs(g2[0] - col) <= _FM_COL_TOL:
            joined = rest + " " + g2[1]
            return None if _fm_prose_cell(joined) else ("interleave", joined)
    return None if _fm_prose_cell(rest) else ("gap", rest)


# Turn 12 B.1 / C1: the leader arm (docs/turn12_decisions/a1_index_anchor.md sections 4b,
# 5 "C1" and 9.2).  The four column arms above all split on a WHITESPACE gap column, so
# `1.1  Certain Defined Terms.........  1` -- the text-era contract index row whose column
# separator is the leader itself -- reads as no row at all: 504 of 504 alarm candidates in
# `runs/judge/turn12-leadctr-vetoprobe.txt` come out `row_test=None`, which is why
# `toc.veto_chain_completing` drops their region and Turn 11's B.1 then never reaches them.
# The arm splits on the leader run instead and hands the second column to exactly the same
# guards: `ctoc_row_pass`'s run of >= `_CTOC_MIN_RUN` rows with only furniture between
# them, the member-level `_fm_kind2(second) == "page"` test, `_ctoc_prose_after`, the
# `in_region` skip and the `image_text` exclusion.  None of them is relaxed for it, and
# they are what make it safe on the known counterexample (`0000072020-03-000021` seq 3,
# `Section 8.3.....Bankruptcy Defaults. When any Event of Default...`): a leader is a
# typographic habit in that document's body headings, but its second column is prose, not
# a page cell.
#
# Measured before it was built (`runs/judge/turn12-a1-ctr.txt`): the arm reads 23,499 rows
# in the 684 alarm documents and B.1 then condemns 10,904 in 196 of them; on a 1,000
# document control sample holding no alarm node it reads 20,162 rows, B.1 condemns 1,897 --
# and the accepted tree does not move at all (section+article 119,156 -> 119,156).  Bank:
# 129 of 129 condemned rows are index rows [0.971, 1.000], and none of the 26 real heading
# windows of the `ctr_alarm` stratum is condemned (`turn12-a1-bank-report.txt` section 3).
_CTOC_LEADER_RE = regex.compile(r"(?:\.\s?){3,}|_{3,}")


def _ctoc_leader_split(line: str, min_col: int = 0) -> tuple[int, str] | None:
    """The LAST leader run on the line that leaves a non-empty second column.

    `min_col` is the column the leader run STARTS at, so it means what `_ctoc_gap_split`'s
    `_FM_MIN_GAP_COL` means: how much first column there has to be.  The floor is not the
    binding constraint -- 23,335 of the 23,499 rows the arm reads would also split at 12
    (`turn12-a1-ctr.txt` section 4b) -- and it is kept for symmetry with the gap arm.
    """
    best = None
    for m in _CTOC_LEADER_RE.finditer(line):
        col = m.start()
        if col < min_col:
            continue
        rest = line[m.end():].strip()
        if len(rest) < _CTOC_MIN_CELL:
            continue
        best = (col, rest)
    return best


def _ctoc_leader_arm(blocks: list[Block], c: Candidate) -> tuple[str, str] | None:
    """`("leader", rest)` / `("leader_wrap", rest)` -- the fifth arm of `_ctoc_row_test`.

    The same walk as the gap arm with `_ctoc_leader_split` in place of `_ctoc_gap_split`:
    the candidate's own line must look like a label line, the first column must carry label
    AND title (unlike the cell arm -- see `_ctoc_row_test_columns` clause 1 -- because a
    leader with nothing in front of it is not a contents row), and `_fm_prose_cell` vetoes
    a prose second column.  `leader_wrap` applies the split to a continuation line within
    four lines, stopping at the next label line, exactly as the `wrap` arm does.
    """
    b = blocks[c.block_idx]
    lns = _fm_lines(b)
    if not lns:
        return None
    own_i = _ctoc_own_line(b, c)
    own = lns[own_i]
    if not _CTOC_LABEL_LINE_RE.match(own.strip()):
        return None
    g = _ctoc_leader_split(own, _FM_MIN_GAP_COL)
    if g is None:
        for ln in lns[own_i + 1: own_i + 5]:
            if _CTOC_LABEL_LINE_RE.match(ln.strip()):
                break  # the next row, not this row's continuation
            g2 = _ctoc_leader_split(ln, _FM_MIN_GAP_COL)
            if g2:
                return None if _fm_prose_cell(g2[1]) else ("leader_wrap", g2[1])
        return None
    col, rest = g
    if not _ctoc_first_col_carries_label_and_title(own[:col]):
        return None
    return None if _fm_prose_cell(rest) else ("leader", rest)


def _ctoc_is_prose_text(t: str, min_chars: int) -> bool:
    """Body text, case-insensitively: `_FM_PROSE_RE` on the LOWER-CASED text, so an
    all-capitals governing-law paragraph counts (0001047469-99-036121 seq 4)."""
    return len(t) >= min_chars and bool(_FM_PROSE_RE.search(t.lower()))


def _ctoc_prose_after(blocks: list[Block], c: Candidate, cand_blocks: frozenset[int]) -> bool:
    """Prose follows the heading within one block: in the candidate's OWN block after the
    heading line, or in the next non-furniture block.

    Calibrated on the two acceptance sets, not argued (memo section 5): `toc._prose_between`'s
    plain test (`para`, > 200 characters, three consecutive lower-case words) reads only 15 of
    the 19 as followed by prose -- the four it misses are a 92-character lead-in sentence, a
    226-character own block, a 181-character lead-in and an all-capitals 566-character
    paragraph -- while reading 0 of the 137 that way.  The floor is raised to
    `_CTOC_PROSE_MIN_CAND` when the next block is itself a section/article row, which is what
    an index row's neighbour always is.  On this definition: 18 of 19 and 0 of 137.
    """
    b = blocks[c.block_idx]
    txt = b.text
    tail = txt.split("\n", 1)[1] if "\n" in txt else ""
    if _ctoc_is_prose_text(tail, _CTOC_PROSE_MIN):
        return True
    if not b.in_table and b.kind == "para" and _ctoc_is_prose_text(txt, _CTOC_PROSE_MIN + 60):
        return True  # a heading that runs straight into its own paragraph, on one line
    for j in range(b.idx + 1, min(len(blocks), b.idx + 12)):
        x = blocks[j]
        if x.kind in ("page", "hr", "comment", "nav") or not x.text.strip():
            continue
        floor = _CTOC_PROSE_MIN_CAND if x.idx in cand_blocks else _CTOC_PROSE_MIN
        return x.kind == "para" and not x.in_table and _ctoc_is_prose_text(x.text, floor)
    return False


def _ctoc_row_cells(blocks: list[Block], c: Candidate) -> list[str]:
    """The cells of the candidate's table row (the HTML era's page column is its own cell)."""
    b = blocks[c.block_idx]
    if b.row_text:
        return [s for s in regex.split(r"\s{2,}|\|", b.row_text) if s.strip()]
    out = []
    for j in range(b.idx + 1, min(len(blocks), b.idx + 6)):
        x = blocks[j]
        if not x.in_table or x.table_id != b.table_id or x.row_text:
            break
        if x.text.strip():
            out.append(x.text.strip())
    return out


def _ctoc_page_evidence(blocks: list[Block], c: Candidate, own_line: str, next_line: str) -> bool:
    """Page or leader evidence ANYWHERE on the candidate's row (B.2 clause 3).

    The candidate's own normalized line, the next non-blank line, any cell of its table row,
    and -- the widening memo section 7d demands, 13 of the 19 `flagged_rejected` misses --
    a SIBLING LINE of the candidate's own block.  The sibling arm is deliberately narrow:
    only a bare page line, a dot leader or a two-dot-glued page, or another index row of the
    same block carrying evidence.  A bare trailing number on a sibling PROSE line ("... in
    1999") is not evidence and must not be read as one.
    """
    if _ctoc_evidence_kinds(own_line):
        return True
    if _ctoc_is_bare_page(next_line):
        return True
    b = blocks[c.block_idx]
    if b.row_text and _ctoc_evidence_kinds(b.row_text):
        return True
    if any(_ctoc_is_bare_page(x) for x in _ctoc_row_cells(blocks, c)):
        return True
    lns = _fm_lines(b)
    if len(lns) > 1:
        own_i = _ctoc_own_line(b, c)
        for i, ln in enumerate(lns):
            if i == own_i:
                continue
            s = ln.strip()
            if not s:
                continue
            if _ctoc_is_bare_page(s):
                return True
            kinds = _ctoc_evidence_kinds(s)
            if "dot_leader" in kinds or "glued_page" in kinds:
                return True
            if kinds and _CTOC_LABEL_LINE_RE.match(s):
                return True  # a sibling index row of the same block
    return False


def _ctoc_line_index(norm: str) -> tuple[list[str], list[int]]:
    lines = norm.split("\n")
    starts: list[int] = []
    p = 0
    for ln in lines:
        starts.append(p)
        p += len(ln) + 1
    return lines, starts


def ctoc_row_pass(cands: list[Candidate], blocks: list[Block], regions, norm: str, *, era: str = "") -> None:
    """Tag the contract table-of-contents boundary: `rej.ctoc_row` and `ctoc.region_relief`.

    Turn 11 B.1 and B.2, docs/turn11_decisions/a1_ctoc_boundary.md section 10.  Tags only:
    no score moves, no effect on `detect_toc` (it runs after it and every region stays
    bit-identical), and the only reader is `tree_contract.build_contract_tree`, which
    consults both before chaining -- rejecting a `rej.ctoc_row` candidate with reason `toc`
    so the rejected table's `reason` column stays a closed vocabulary, and sparing a
    `ctoc.region_relief` member at the per-member rescue point.  `ctoc.row_cell` /
    `ctoc.row_gap` / `ctoc.row_interleave` / `ctoc.row_wrap` record which mechanism found the
    second column and have no consumer, exactly as `rej.fm_table` records `fm.row_*`.

    Both rules are computed here, in one walk, because they are complementary sides of the
    same predicate and must never both fire on one candidate.
    """
    if era == "image_text":
        return
    sec = [i for i, c in enumerate(cands) if c.kind in _CTOC_KINDS]
    if not sec:
        return
    in_region: set[int] = set()
    for r in regions:
        in_region.update(range(r.first_cand, r.last_cand + 1))
    cand_blocks = frozenset(cands[i].block_idx for i in sec)

    tested: dict[int, tuple[str, str]] = {}
    for i in sec:
        t = _ctoc_row_test(blocks, cands[i])
        if t is not None:
            tested[i] = t

    # --- B.1: runs of index rows where no region was proposed ------------------
    if len(tested) >= _CTOC_MIN_RUN:
        row_block_ids = {cands[i].block_idx for i in tested}
        runs: list[list[int]] = []
        cur: list[int] = []
        for i in sorted(tested):
            if not cur:
                cur = [i]
                continue
            bp, bc = blocks[cands[cur[-1]].block_idx], blocks[cands[i].block_idx]
            broke = bc.idx - bp.idx > _FM_MAX_GAP_BLOCKS or bc.norm_start - bp.norm_end > _FM_MAX_GAP_CHARS
            if not broke:
                for j in range(bp.idx + 1, bc.idx):
                    x = blocks[j]
                    if _fm_is_prose(x) or not _fm_block_is_furniture(x, row_block_ids):
                        broke = True
                        break
            if broke:
                runs.append(cur)
                cur = [i]
            else:
                cur.append(i)
        if cur:
            runs.append(cur)
        for run in runs:
            if len(run) < _CTOC_MIN_RUN:
                continue
            for i in run:
                if i in in_region:
                    continue  # B.1 and the C.2 condemnation never disagree about one row
                if _fm_kind2(tested[i][1]) != "page":
                    continue  # the member-level evidence test: 0.780 -> 1.000 (memo 7e)
                c = cands[i]
                if _ctoc_prose_after(blocks, c, cand_blocks):
                    continue  # a row followed by its own body is not an index row
                if "rej.ctoc_row" not in c.rule_ids:
                    c.rule_ids.append("rej.ctoc_row")
                    c.rule_ids.append(f"ctoc.row_{tested[i][0]}")

    # --- B.2: the relief for leaderless index-row members ----------------------
    if not in_region:
        return
    lines, starts = _ctoc_line_index(norm)
    for i in sec:
        if i not in in_region or i in tested:
            continue  # clause 4: an index row is never relieved
        c = cands[i]
        if not _ctoc_prose_after(blocks, c, cand_blocks):
            continue
        off = c.norm_start if c.norm_start >= 0 else blocks[c.block_idx].norm_start
        k = max(0, bisect_right(starts, off) - 1)
        own_line = lines[k] if k < len(lines) else ""
        next_line = next((l for l in lines[k + 1:k + 6] if l.strip()), "")
        if _ctoc_page_evidence(blocks, c, own_line, next_line):
            continue
        if "ctoc.region_relief" not in c.rule_ids:
            c.rule_ids.append("ctoc.region_relief")


# ---------------------------------------------------------------------------
# Turn 12 B.1 / K1: rej.vetoed_index_row, the index row the monotone chain anchors on
# docs/turn12_decisions/a1_index_anchor.md sections 1, 5 "K1", 7b, 7c and 9.1.
#
# `toc.veto_chain_completing` (toc.py) drops a proposed region whose members are the
# document's only strong copies of their labels and tags every member `toc.vetoed_region`.
# Turn 8's `tree.vetoed_index_run` recognises a long ordered run of those tags as an index
# -- but it is read in exactly ONE place, `tree.out_of_order_items`, the SECOND placement
# pass.  The monotone chain never sees it, so it anchors items on the rows of an index the
# parser has already decided is an index, and the real body heading then loses as
# `nonmonotone`: 6,221 accepted 10-K item nodes in 547 documents and 8,537 10-Q nodes in
# 4,729 (`turn12-lead10k-veto.txt`, `turn12-lead10q-tocaccept.txt`).
#
# This pass moves that same test to CANDIDATE time, where the chain can read it.  Tags
# only -- no score moves, `detect_toc` untouched, so every TOC region stays bit-identical
# and the contract and EX-13 paths cannot move.  The single reader is `tree.build_tree`,
# which rejects a tagged candidate with reason `toc` before the `toc_idx` branch, exactly
# as `tree_contract.build_contract_tree` reads Turn 11's `rej.ctoc_row`.
#
# THE LATER-COPY CLAUSE IS THE WHOLE RULE.  Condemning every member of a qualifying run is
# 80.3% index rows on a 200-window bank and deletes 3,978 of the 6,221 10-K items, costing
# 332 of 752 documents their core; the variant that fires only where the document holds
# ANOTHER copy of the label outside the run is 74.6%, and 5.6% on its no-later-copy subset
# (17 of those 18 windows are real headings).  Restricted to rows with a LATER same-label
# candidate the tree rejected it is 53/53 = 100% [0.932, 1.000] in sample and 71/75 =
# 94.7% and 75/75 = 100% on the out-of-sample re-bank (`turn12-a1v2-bank-report.txt`).
# What the `later=False` half holds instead is a real class: 26 incorporation-by-reference
# item runs, 11 General Instruction J(1) omission lists and 10 ordinary body headings out
# of 47 windows -- exactly the stub run `veto_chain_completing` was built to condemn, which
# `vetoed_index_run` then re-reads as an index (`turn12-a1-bank-headings-rulings.txt`).
# Where no later copy exists there is no better target to re-anchor on, and condemning the
# row converts a wrong offset into a missing item.
#
# The later candidate must itself carry `gram.item.title_match` -- the test
# `tree.out_of_order_items` already demands before it will place an item out of order.
# All four re-bank errors are LABEL COLLISIONS whose later candidate lacks it: an
# exhibit-index group row ("Item 3 / Articles of Incorporation and Bylaws") in
# `0001354488-13-002619`, and an abbreviated-report filer renumbering its Part III/IV items
# from 1 in `0001492091-14-000006`, so the clause was satisfied by a row belonging to a
# DIFFERENT item (`turn12-a1v2-latercheck.txt`: requiring the title match removes all four
# and keeps 46 of 75 banked rows at 46/46).
#
# Disjoint from Turn 11's two contract rules: `rej.ctoc_row` is written only for the
# contract grammar, whose tree `build_contract_tree` builds; `ctoc.region_relief` applies
# only to members of a LIVE region, and a condemned row here is a member of a VETOED one,
# which is not in `toc_idx` at all.  Both are asserted in tests/test_turn12_vetoed_index.py.
# ---------------------------------------------------------------------------
_VIDX_UNRESOLVED_PREFIX = "ITEM ?."


def _vidx_run_qualifies(cands: list[Candidate], lo: int, hi: int, min_items: int) -> bool:
    """Does the run `[lo, hi]` of consecutive `toc.vetoed_region` candidates read as an index?

    `tree.vetoed_index_run`'s test -- >= `min_items` item candidates whose order keys
    strictly increase -- plus the Form 10-Q `stretch` amendment.  On the 10-Q the keys are
    read BEFORE `gram.part_from_context` resolves `ITEM ?.k` (that pass runs on `live`,
    after the live/rejected split), so a two-Part contents page `?.1 ?.2 ?.3 ?.4 ?.1 ?.2`
    descends at the Part boundary and the shipped test refuses the run: 416 of 498 sampled
    stuck rows sit in runs of >= 4 item keys that do not increase, 394 of them because the
    labels are still unresolved (`turn12-a1-10q-runs.txt`).  A run holding an unresolved
    label therefore also qualifies when its key sequence is a concatenation of maximal
    strictly-ascending stretches EACH holding >= 2 items -- descents are allowed at a Part
    boundary, a scrambled run with isolated singletons is not.  Reach on the 8,537 tightest
    10-Q accepts: 3,106 under the shipped test, 7,119 under this one, and the shipped set is
    a subset (`turn12-a1v2-pop.txt`).  A run with no unresolved label keeps the shipped
    strictly-increasing test, so the 10-K is untouched by the amendment.

    Reference implementation: `scripts/turn12/a1_common.run_qualifies_amended(mode="stretch")`.
    """
    items = [j for j in range(lo, hi + 1) if cands[j].kind == "item"]
    if len(items) < min_items:
        return False
    keys = [cands[j].order_key for j in items]
    if all(a < b for a, b in zip(keys, keys[1:])):
        return True
    if not any((cands[j].label_canon or "").startswith(_VIDX_UNRESOLVED_PREFIX) for j in items):
        return False
    stretch = 1
    for a, b in zip(keys, keys[1:]):
        if a < b:
            stretch += 1
            continue
        if stretch < 2:
            return False
        stretch = 1
    return stretch >= 2


def vetoed_index_row_pass(cands: list[Candidate], *, min_items: int) -> None:
    """Tag `rej.vetoed_index_row` on the rows of a long ordered vetoed index that the
    document re-states later, so `tree.build_tree` can refuse to anchor an item on them.

    Two clauses, both measured in `docs/turn12_decisions/a1_index_anchor.md`:

      1. the candidate is a member of a maximal run of consecutive `toc.vetoed_region`
         candidates that `_vidx_run_qualifies` calls an index;
      2. the document holds a LATER candidate of the same `(kind, label_canon)` carrying
         `gram.item.title_match` -- a second copy of this very item, with its statutory
         title, for the chain to move to.

    `toc.vetoed_index` goes on beside `rej.vetoed_index_row` so this pass and
    `tree.out_of_order_items` speak one vocabulary (it has no reader in either).
    """
    n = len(cands)
    if n < min_items:
        return
    later_tm: dict[tuple[str, str], int] = {}
    for c in cands:
        if "gram.item.title_match" not in c.rule_ids:
            continue
        k = (c.kind, c.label_canon)
        if c.head_raw_start > later_tm.get(k, -1):
            later_tm[k] = c.head_raw_start
    if not later_tm:
        return
    i = 0
    while i < n:
        if "toc.vetoed_region" not in cands[i].rule_ids:
            i += 1
            continue
        lo = hi = i
        while hi + 1 < n and "toc.vetoed_region" in cands[hi + 1].rule_ids:
            hi += 1
        if _vidx_run_qualifies(cands, lo, hi, min_items):
            for j in range(lo, hi + 1):
                c = cands[j]
                if later_tm.get((c.kind, c.label_canon), -1) <= c.head_raw_start:
                    continue
                if "rej.vetoed_index_row" not in c.rule_ids:
                    c.rule_ids.append("rej.vetoed_index_row")
                    c.rule_ids.append("toc.vetoed_index")
        i = hi + 1


# ---------------------------------------------------------------------------------------
# Turn 13 B.4: rej.section_xref, the contract analogue of `rej.xref_pointer`
# (docs/turn13_decisions/a3_lexnlp.md section 9, docs/turn13_decisions/b4_section_xref.md).
# A definitional cross-reference whose `Section n.n` token lands at the head of a line --
# "... shall have the meaning assigned to that term in / Section 5.04." -- reads as a
# section label to the grammar and can win the chain slot of the real body heading of the
# same label.  The test is A.3's `xref3` proxy, ported verbatim from
# scripts/turn13/a3_proxy.py so the rule fires on exactly the line set the out-of-sample
# bank measured (runs/judge/turn13-b4-windows.jsonl, 100 of 100 rated cross_reference,
# runs/judge/turn13-b4-consensus.jsonl):
#   1. the previous non-blank normalised line ends in a comma or an all-lower-case word
#      (the sentence runs on into the label line);
#   2. that line is not a `; or` / `; and` list close, and is not a short (< 40 character)
#      line opening in upper case (a title line: `Costs and expenses`);
#   3. the label line is written with the word `Section` and nothing after the number reads
#      as a title: the rest is empty, punctuation, a bracket or parenthesis, or lower case.
# Tag only; `tree_contract.build_contract_tree` reads it BEFORE chaining (like
# `rej.ctoc_row`), so the reference can neither open a numbering restart nor hold a slot.
_SXREF_SEC_RE = regex.compile(r"^\s*section\s+\d+(?:\.\d+)*\.?", regex.IGNORECASE)
_SXREF_LIST_CLOSE_RE = regex.compile(r";\s*(or|and)$")
_SXREF_BARE_CONJ = frozenset({"or", "and", "and/or"})


def _sxref_prev_nonblank_at(norm: str, ls: int) -> tuple[str, int]:
    """The last non-blank line before the line starting at `ls`, and its start offset."""
    s = ls - 1
    while s > 0:
        p = norm.rfind("\n", 0, s)
        ln = norm[p + 1:s].rstrip()
        if ln.strip():
            return ln, p + 1
        s = p
    return "", 0


def _sxref_prev_nonblank(norm: str, ls: int) -> str:
    return _sxref_prev_nonblank_at(norm, ls)[0]


def _sxref_prev_runs_on(norm: str, ls: int) -> bool:
    """Conditions 1 and 2 on the line starting at `ls` (a3_proxy.py `xref`)."""
    ln, ps = _sxref_prev_nonblank_at(norm, ls)
    if not ln:
        return False
    if not ln.endswith(","):
        w = ln.split()[-1]
        if not (w.isalpha() and w.islower()):
            return False
    ln = ln.strip()
    if _SXREF_LIST_CLOSE_RE.search(ln):
        return False
    if ln.lower() in _SXREF_BARE_CONJ and _sxref_prev_nonblank(norm, ps).rstrip().endswith(";"):
        # Turn 13 B.4 R2: the list close split from its `;` (`...insurance;` / `or` /
        # `Section 6.1.11. (i) the Borrower ...`, 0000948945-00-000010 seq 4) -- an Event of
        # Default item heading, not a reference (runs/judge/turn13-b4-collateral.txt).
        # R2b: only when the line before the bare conjunction ends in `;`.  HTML bold runs put
        # a prose `or` on a line of its own (`pursuant to` / `Section 2.5, Section 2.6(e)` /
        # `or` / `Section 9.2` / `.`, 0000949377-06-000535 seq 2); 8 of R2's 10 lifts on the
        # B.4+B.4b gate were such references (docs/turn13_decisions/b4_section_xref.md 8.5).
        return False
    if len(ln) < 40 and ln[:1].isupper():
        return False
    return True


def _sxref_no_title(line: str) -> bool:
    """Condition 3 (a3_proxy.py `notitle`)."""
    m = _SXREF_SEC_RE.match(line)
    if not m:
        return False
    rem = line[m.end():].strip()
    if not rem:
        return True
    if rem[0] in "([;,)":
        return True
    rem2 = rem.lstrip(".;:,").strip()
    return not rem2 or rem2[0] in "([" or rem2[0].islower()


def section_xref_test(norm: str, norm_pos: int) -> bool:
    """xref3 at the normalised line holding `norm_pos`."""
    ls = norm.rfind("\n", 0, norm_pos) + 1
    le = norm.find("\n", ls)
    line = norm[ls: le if le >= 0 else len(norm)]
    return _sxref_prev_runs_on(norm, ls) and _sxref_no_title(line)


def section_xref_pass(cands: list[Candidate], blocks: list[Block], norm: str) -> None:
    """Tag `rej.section_xref` on contract section candidates that pass `section_xref_test`."""
    for c in cands:
        if c.kind != "section":
            continue
        pos = c.norm_start if c.norm_start >= 0 else blocks[c.block_idx].norm_start
        if section_xref_test(norm, pos) and "rej.section_xref" not in c.rule_ids:
            c.rule_ids.append("rej.section_xref")
