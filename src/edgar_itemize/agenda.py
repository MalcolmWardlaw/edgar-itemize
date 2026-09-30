"""Ordinal agenda paths over a parsed tree.

Every node gets a fixed-length path of ordinals: path[0] is the meta segment
(0 front matter, 1 table of contents, 2 main body, 3 back matter), path[1:]
are 1-based ordinals of appearance among siblings, zero-padded to PATH_LEN.
Text under a node that has no further enumeration is addressed by the node's
path with trailing zeros, so [2,3,2,0,0,0,0,0] is "everything under the second
child of the third top-level heading of the main body".

Labels are deliberately not encoded in the digits: Item numbering has gaps
(1A, reserved 6, Item 14 moving Parts) and clause schemes vary, so digits are
pure ordinals and the canonical label lives in its own column.

Compound exhibits (several agreements glued into one EX-10 document) carry a
second, orthogonal ordinal: `segment`.  0 is the primary/only agreement, 1, 2,
... each subsequent glued instrument.  Segment identity is deliberately kept
out of `meta` for the same reason labels are kept out of the digits: "which
region of an agreement" and "which of several glued agreements" are two facts,
so each agreement gets its own front/toc/main/back computation and its own
ordinals within `path[1:]` (Turn 7 decision (d), docs/turn7_decisions/d_compound.md).

A `segment` is claimed only where there is evidence that a *different instrument*
begins (`seg.instrument_boundary`), which is a narrower fact than "the article
numbering restarts here" (`chain.restart`).  Turn 8 B.1 fused the two and B.2
measured the cost; `chain_restarts` below decides both separately.
"""

from __future__ import annotations

from bisect import bisect_right

import regex

from .blocks import Block
from .tree import Node

PATH_LEN = 8
META_FRONT, META_TOC, META_MAIN, META_BACK = 0, 1, 2, 3
META_NAMES = {0: "front", 1: "toc", 2: "main", 3: "back"}
# `segment` is int8 and every consumer treats 0 as "the only agreement"; a document
# with more restarts than this keeps the extra ones in the last segment.
MAX_SEGMENTS = 32

_BACK_MATTER_RE = regex.compile(
    r"^\s*(?:SIGNATURES?|SIGNATURE PAGE|POWER OF ATTORNEY|EXHIBIT INDEX|INDEX TO EXHIBITS|LIST OF EXHIBITS|"
    r"SCHEDULE\s+[\dA-Z]|EXHIBIT\s+[A-Z\d]|ANNEX\s+[A-Z\d]|IN WITNESS WHEREOF)\b",
    regex.IGNORECASE,
)


_IWW_RE = regex.compile(r"^\s*IN WITNESS WHEREOF\b", regex.IGNORECASE)

# --- Turn 7 (B3): the SIGNATURES boundary in the financial-appendix population ---
#
# `_BACK_MATTER_RE` alone matches any line that opens with one of its words, which is
# how a company name ("Signature Assisted Living of Texas, LLC") and an exhibit-table
# row ("Power of Attorney (included on Signature Page)") became back_start in v10
# (docs/turn7_decisions/a_appendix.md section 5D). The two weakest alternatives —
# SIGNATURE(S) and POWER OF ATTORNEY — therefore have to look like a heading:
# the first line is the bare word, or the block itself carries the signature block's
# attestation sentence / an /s/ execution line (agenda.back_head_strict).
_WEAK_BACK_RE = regex.compile(r"^\s*(?:SIGNATURES?|SIGNATURE PAGES?|POWER OF ATTORNEY)\b", regex.IGNORECASE)
_BARE_SIG_RE = regex.compile(r"^\s*(?:SIGNATURES?|SIGNATURE PAGES?|POWER OF ATTORNEY)\s*[.:;*]?\s*\d{0,4}\s*$", regex.IGNORECASE)
# The Turn 7 rule reaches only the two boundaries the decision names — the signature
# page and the exhibit index. A bare "Power of Attorney" line is just as often the
# description cell of exhibit 24 in the middle of an exhibit index, so it keeps the v10
# short-tail treatment and cannot open back matter with the appendix still to come.
_BARE_SIGONLY_RE = regex.compile(r"^\s*(?:SIGNATURES?|SIGNATURE PAGES?)\s*[.:;*]?\s*\d{0,4}\s*$", regex.IGNORECASE)
_BARE_EXIDX_RE = regex.compile(
    r"^\s*(?:EXHIBITS?\s+INDEX|EXHIBIT\s+INDEX|INDEX\s+TO\s+EXHIBITS?|INDEX\s+OF\s+EXHIBITS?|LIST\s+OF\s+EXHIBITS?)"
    r"\s*[.:;*]?\s*\d{0,4}\s*$",
    regex.IGNORECASE,
)
# --- Turn 12 B.5 (docs/turn12_decisions/a5_ex13.md, "Refined design"): the ex13-scoped
# boundary guard. `_BARE_SIG_RE`/`_BARE_SIGONLY_RE` match "SIGNATURE" or "SIGNATURES"
# indifferently (the trailing S is optional); this pattern isolates the bare SINGULAR
# form specifically, because on the EX-13 (annual report) path that lone word is just as
# often a proxy/dividend-reinvestment/address-change card's blank form-field label as it
# is the filing's own signature-page heading, with no attestation sentence or /s/ nearby
# (4 of 46 `back_short_tail` false rulings on the 59-document bank). The PLURAL forms
# ("SIGNATURES", "SIGNATURE PAGES", "POWER OF ATTORNEY") are never wrong in that bank and
# keep the unscoped, bare-match-alone behaviour.
_BARE_SIG_SINGULAR_RE = regex.compile(r"^\s*SIGNATURE\s*[.:;*]?\s*\d{0,4}\s*$", regex.IGNORECASE)
# the Exchange Act signature attestation: a sentence, like IN WITNESS WHEREOF, not a heading.
# Some filers print no SIGNATURES heading at all and open the signature block with it.
_ATTEST_RE = regex.compile(
    r"(?:PURSUANT TO THE REQUIREMENTS OF|IN ACCORDANCE WITH)\s+"
    r"(?:SECTION\s*1[35]|THE SECURITIES(?:\s+EXCHANGE)?\s+ACT|THE EXCHANGE ACT)[^.]{0,400}?"
    r"(?:DULY CAUSED|HAS CAUSED|CAUSED THIS (?:ANNUAL )?REPORT|(?:HAS |HAVE )?BEEN SIGNED|SIGNED ON (?:ITS|OUR) BEHALF)",
    regex.IGNORECASE | regex.DOTALL,
)
_SLASH_S_RE = regex.compile(r"/s/\s*\S", regex.IGNORECASE)
# --- compound-exhibit segmentation (Turn 7 (d)) --------------------------------

# A bare cover line introducing an attached instrument ("EXHIBIT F", "ANNEX B").
_COVER_RE = regex.compile(r"^\s*(?:EXHIBIT|ANNEX|SCHEDULE|APPENDIX)\s+[\dA-Z][\dA-Z.\-]*\s*$", regex.IGNORECASE)
# Parties / recitals: the preamble every free-standing instrument opens with.
_RECITAL_RE = regex.compile(
    r"\bWHEREAS\b|\bNOW,?\s*THEREFORE\b|\bW\s*I\s*T\s*N\s*E\s*S\s*S\s*E\s*T\s*H\b|"
    r"\bagree[sd]?[^.\n]{0,80}\bas follows\b|\bas follows\s*:|\bin consideration of\b",
    regex.IGNORECASE,
)
# Words that make a centred all-caps line an instrument title rather than a caption.
_INSTRUMENT_WORDS = (
    "AGREEMENT", "GUARANTY", "GUARANTEE", "MORTGAGE", "INDENTURE", "NOTE", "PLEDGE", "SECURITY",
    "DEED OF TRUST", "LEASE", "WARRANT", "SUBSCRIPTION", "PROMISSORY", "ASSIGNMENT", "SUPPLEMENT",
    "AMENDMENT", "CONTRACT", "DEBENTURE", "CERTIFICATE", "INSTRUMENT", "FACILITY",
)
# Candidate rule ids that mark the "heading" as a cross-reference, an index row or
# prose: "Article 1 of the Uniform Commercial Code" must never open a segment.
_XREF_RULES = frozenset({"rej.lowercase_title", "rej.prose", "rej.long_line", "rej.xref_phrase",
                         "rej.continued", "toc.leader_or_pageno", "toc.href"})
# How far back from a restart the fresh-instrument / evidence scan reaches when the
# previous structural heading is further away than that (bounds the work on 2 MB documents).
_FRESH_SCAN_BYTES = 60_000

# --- Turn 8 (B.2b): the three named instrument-boundary evidence terms -----------
#
# Ported from the B.2 branch (9b74d19, docs/turn8_decisions/b2_compound.md) and used
# here for a narrower question than B.2 asked.  B.1 fused two facts: "where does the
# article numbering restart" (what the chains need) and "where does a new instrument
# begin" (what the `segment` column claims).  B.2 measured the second one and found the
# Turn 7 test — a fresh instrument title, a recital or an exhibit cover ANYWHERE in a
# window up to 60 KB long — carries no information: on the 239-window binary acceptance
# set each of those terms sits in front of ~85% of the consensus positives and ~85% of
# the consensus continuations alike (precision 0.295 at recall 0.756).  What separates
# is ADJACENCY: the previous instrument's own closing immediately before the new
# instrument's opening (precision 0.857 at recall 0.293).
#
# So the two facts are now decided separately.  The numbering restart keeps B.1's test
# and drives the chains (`chain.restart`); a restart is an instrument boundary, and
# therefore a `segment`, only when the evidence below also holds
# (`seg.instrument_boundary`).  A restart without it chains separately and stays in the
# enclosing segment (`chain.restart_unsegmented`): a false boundary puts a wrong value
# in a column whose whole purpose is to say which of several glued agreements a node
# belongs to, while a false chain restart only ever costs the labels the document-wide
# monotone chain was already losing.

# seg.prior_closed: the previous instrument closes.  IN WITNESS WHEREOF (`_IWW_RE`), an
# execution clause, or a signature block.  The B.2 memo also names "an explicit page/tab
# break"; admitting a bare EXHIBIT/ANNEX cover line as a third closing form was measured
# on the bank and costs more than it buys (precision 0.867 -> 0.640 for +3 recall
# points, runs/judge/turn8-b2-bank.txt), so a cover line is evidence of a new
# instrument's NAME below, not of the previous one's end.
_SIG_BY_RE = regex.compile(r"^\s*By\s*:?\s*(?:_{3,}|/s/|\s*$)", regex.IGNORECASE | regex.MULTILINE)
_SIG_NAMETITLE_RE = regex.compile(r"\bName\s*:.{0,80}?\bTitle\s*:", regex.IGNORECASE | regex.DOTALL)
_EXEC_CLAUSE_RE = regex.compile(
    r"\b(?:has|have)\s+(?:hereunto\s+)?(?:duly\s+)?caused\b[^.]{0,200}?\b(?:executed|signed)\b|"
    r"\bhave\s+executed\s+th(?:is|e)\b|\bexecuted\s+(?:this|these)\s+\w+[^.]{0,80}?\bas of the\b|"
    r"\bduly\s+executed\s+and\s+delivered\b",
    regex.IGNORECASE,
)
# How much body prose may sit between that closing and the restart.  0 characters is
# what a bound-in second instrument looks like (closing, page break, cover, title,
# preamble, ARTICLE 1); 1,000 admits a short tail paragraph.  Measured in the same
# "body prose" the `seg.prior_body` test counts (`toc._PROSE_RE`, a paragraph over 200
# characters), not in raw bytes, because a raw-byte budget means a different amount of
# text in a 1996 text-era filing than in a 2016 publisher one.  The bank is flat either
# side of this value (precision 0.80 at 800, 0.81 at 1000, 0.80 at 1200) and falls off
# a cliff at 1500, so the threshold sits in the middle of the flat region.
_PRIOR_CLOSED_PROSE = 1000

# seg.new_title: the new instrument names itself, with a name the document has not used
# before.  Either a titled line (`_instrument_title`) or a "THIS ... AGREEMENT ... is
# made" preamble.  "New" is the discriminating half: the false positives repeat the same
# document's own title as a running header.
_THIS_AGT_RE = regex.compile(
    r"\bTHIS\s+(?P<t>[A-Z][A-Za-z'&\-.]*(?:\s+[A-Z][A-Za-z'&\-.]*){0,8}?\s*"
    r"(?:AGREEMENT|GUARANTY|GUARANTEE|MORTGAGE|INDENTURE|NOTE|PLEDGE|LEASE|WARRANT|DEED|"
    r"CONTRACT|DEBENTURE|SUPPLEMENT|AMENDMENT|ASSIGNMENT|CERTIFICATE|INSTRUMENT))\b"
    r"[^.]{0,300}?\b(?:is|are|shall be)\s+(?:hereby\s+)?(?:made|entered\s+into|executed|dated)\b",
    regex.IGNORECASE,
)

# seg.new_parties: a party role the document has not used before.  A second instrument
# introduces a Pledgor, a Mortgagor, a Trustee; a continuation keeps the same
# Borrower / Lenders / Administrative Agent it already had.
_PARTY_ROLES = (
    "Pledgor", "Pledgee", "Debtor", "Secured Party", "Grantor", "Grantee", "Mortgagor",
    "Mortgagee", "Trustor", "Trustee", "Beneficiary", "Guarantor", "Borrower", "Lender",
    "Administrative Agent", "Collateral Agent", "Landlord", "Tenant", "Lessor", "Lessee",
    "Sublessor", "Sublessee", "Buyer", "Seller", "Purchaser", "Vendor", "Employer",
    "Employee", "Executive", "Licensor", "Licensee", "Assignor", "Assignee", "Issuer",
    "Holder", "Investor", "Subscriber", "Depositor", "Servicer", "Custodian", "Obligor",
    "Maker", "Payee", "Party A", "Party B", "Contractor", "Owner", "Franchisor",
    "Franchisee", "Optionor", "Optionee", "Transferor", "Transferee", "Consultant",
    "Supplier", "Distributor", "Warrantholder", "Noteholder", "Indemnitor", "Indemnitee",
    "Depositary", "Agent", "Company", "Parent",
)
_ROLE_RE = regex.compile(r"\b(?:" + "|".join(_PARTY_ROLES) + r")\b")

# Rule ids this module writes onto a restart candidate, in the order they are appended.
CHAIN_RESTART = "chain.restart"                    # the numbering restarts here: new chains
INSTRUMENT_BOUNDARY = "seg.instrument_boundary"    # ... and a new instrument begins: new segment
UNSEGMENTED_RESTART = "chain.restart_unsegmented"  # ... but no instrument evidence: same segment
SECTION_RESTART = "chain.section_restart"          # the SECTION numbering restarts, no ARTICLE says so

# --- Turn 9 (B.1): the section-level restart -------------------------------------
#
# `chain.restart` above decides chain starts on ARTICLE candidates only, so an
# instrument that numbers its sections and carries no ARTICLE heading at all can never
# open a chain: its whole body sits in the previous instrument's chain stretch, the
# monotone chain has to choose between two ascending runs, and every member of the
# loser is written out `nonmonotone`.  A.3 measured that gap
# (docs/turn9_decisions/a3_chain_flips.md): 254 of the 274 labels Turn 8 B.1 lost sit
# where no ARTICLE 1 candidate exists, and the same shape holds 97,430 rejected section
# rows across 5,537 of 17,371 EX-10 documents.
#
# A section restart is a numbering fact only.  It never carries `seg.instrument_boundary`
# — `_evidence` reads an article candidate's neighbourhood and there is no article here —
# so it always carries `chain.restart_unsegmented` and can never open a `segment`.
_SECTION_RESTART_TOC_RULES = frozenset({"toc.leader_or_pageno", "toc.member_rescued",
                                        "toc.vetoed_region", "toc.section_run"})
_TABLE_RULES = frozenset({"pos.in_table", "pos.table_row"})
SECTION_RESTART_MIN_RUN = 3   # A.3 section 6: min_run 3 dominates 5 and 8 on every axis
SECTION_RESTART_MIN_SCORE = 0.5  # `chain.restart`'s own bar, test (iv)


def _instrument_title(line: str) -> bool:
    """An all-caps standalone line naming an instrument ("SECURITY AGREEMENT")."""
    s = line.strip()
    if not (4 <= len(s) <= 140):
        return False
    up = s.upper()
    if not any(w in up for w in _INSTRUMENT_WORDS):
        return False
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) >= 0.8 * len(letters)


def _fresh_instrument(blocks: list[Block], lo: int, hi: int) -> bool:
    """True when the blocks in (lo, hi) open a new instrument: a fresh instrument
    title, parties/recitals, or a bare exhibit cover line."""
    for b in blocks:
        if b.raw_start <= lo or b.raw_start >= hi or b.kind != "para":
            continue
        lines = b.lines or (b.text,)
        if _COVER_RE.match(lines[0].strip()):
            return True
        if _RECITAL_RE.search(b.text):
            return True
        if any(_instrument_title(x) for x in lines):
            return True
    return False


def _norm_name(s: str) -> str:
    """An instrument name reduced to its letters, so a running header and its own
    title page count as the same name however they are punctuated."""
    return regex.sub(r"\s+", " ", regex.sub(r"[^A-Za-z ]+", " ", s.upper())).strip()


def _closing(b: Block) -> bool:
    """seg.prior_closed's evidence in one block: the execution clause, the attestation
    sentence, or a signature block."""
    return bool(_IWW_RE.match(b.text) or _EXEC_CLAUSE_RE.search(b.text)
                or _SLASH_S_RE.search(b.text) or _SIG_BY_RE.search(b.text)
                or _SIG_NAMETITLE_RE.search(b.text))


def _names_roles(b: Block) -> tuple[list[str], list[str]]:
    """The instrument names a block declares and the party roles it uses."""
    names = [_norm_name(x) for x in (b.lines or (b.text,)) if _instrument_title(x)]
    m = _THIS_AGT_RE.search(b.text)
    if m:
        names.append(_norm_name(m.group("t")))
    return [n for n in names if n], sorted(set(_ROLE_RE.findall(b.text)))


def _evidence(blocks: list[Block], lo: int, hi: int, seen_names: set[str], seen_roles: set[str],
              ) -> tuple[bool, bool, bool]:
    """(seg.prior_closed, seg.new_title, seg.new_parties) over the blocks in (lo, hi).

    One forward pass.  `prior_closed` tracks the body prose accumulated since the last
    closing block, so it is true only when the closing is what immediately precedes the
    new instrument's opening rather than something the window happens to contain.
    """
    from .toc import _PROSE_RE

    closed = False
    gap = 0
    new_title = new_parties = False
    for b in blocks:
        if b.raw_start <= lo or b.raw_start >= hi or b.kind != "para" or not b.text:
            continue
        if _closing(b):
            closed, gap = True, 0
        elif len(b.text) > 200 and _PROSE_RE.search(b.text):
            gap += len(b.text)
        names, roles = _names_roles(b)
        new_title = new_title or any(n not in seen_names for n in names)
        new_parties = new_parties or any(r not in seen_roles for r in roles)
    return closed and gap <= _PRIOR_CLOSED_PROSE, new_title, new_parties


def _body_offsets(blocks: list[Block], cands, elig_struct: list[int]) -> list[int]:
    """`seg.prior_body`: the offsets at which an agreement BODY is established.

    Two consecutive eligible structural candidates with a body paragraph between them
    (`toc._prose_between`, the same predicate the contract-TOC detector uses to tell an
    index row from a heading) say there is real body text between them.  A table of
    contents has no prose between its rows, so a document whose own index is not
    condemned cannot pass its own first article off as "a second instrument": everything
    before that article is index rows and a preamble, not an agreement to close.

    Returns the head offset of the second member of every such pair, ascending.
    """
    from .toc import _prose_between

    return [cands[b].head_raw_start for a, b in zip(elig_struct, elig_struct[1:])
            if _prose_between(blocks, blocks[cands[a].block_idx], blocks[cands[b].block_idx])]


def _article_restarts(blocks: list[Block], cands, toc_idx: set[int], *, min_score: float,
                      max_restarts: int) -> list[int]:
    """Candidate indices at which the ARTICLE numbering restarts (rule `chain.restart`),
    each also marked `seg.instrument_boundary` or `chain.restart_unsegmented`.

    A compound exhibit binds several instruments into one EX-10 document; the second
    and later ones restart the article numbering at 1.  The decision is taken here, on
    the *candidate* list, and not on the finished tree, because the article and section
    chains run per restart (`tree_contract.build_contract_tree`, `seq.per_segment`)
    and therefore cannot be what defines one: under one document-wide monotone chain only
    one accepted article per document can carry order key 1, so the second instrument's
    ARTICLE 1 either never became a node or displaced the first instrument's chain
    (docs/TURN7_REPORT.md section 6, the 664 chain casualties of the C2 gate).

    **Two facts, two answers.**  Turn 8 B.1 fused "the numbering restarts here" with "a
    new instrument begins here" and gave both to the same test; B.2 then measured the
    second one against a three-rater acceptance set and found that test reaches
    precision 0.295.  They are separated here:

    * every candidate passing (i)-(vii) below is a **numbering restart** and gets its own
      article and section chain — this is B.1's test and B.1's behaviour, unchanged;
    * a restart is an **instrument boundary**, and therefore a `segment`, only when it
      also carries B.2's adjacency evidence (`_evidence`): `seg.prior_closed` AND
      (`seg.new_title` OR `seg.new_parties`), precision 0.857 on the same bank.  Only
      these reach `compound_restarts`, and so only these restart the top-level ordinals,
      split `path_str`, and get their own back-matter scan.
    * a restart without that evidence carries `chain.restart_unsegmented`: it chains
      separately but stays in the enclosing segment, so its top-level ordinals continue
      that segment's count and it shares that segment's back-matter scan.

    An article candidate is a numbering restart iff it is

      (i)   preceded, *inside the stretch the previous restart opened*, by that
            agreement's body
            (`seg.prior_body`, above): there is nothing to close otherwise.  This
            replaces Turn 7's "not the document's first article node" test, which
            counted a `gram.synth_article` phantom as an earlier article and so could
            not be asked of a candidate; asking for body text instead both covers the
            first instrument that numbers its sections without any ARTICLE heading and
            refuses the far larger class the article test let through — a single
            agreement whose own table of contents was not condemned, where the index
            rows precede the document's own first article and the preamble in between
            reads as a fresh instrument.  Scoping it to the closing segment rather than
            to the whole document refuses the same thing one level down: an index bound
            between two agreements is not a third agreement,
      (ii)  a value-1 restart (`ARTICLE 1` / `ARTICLE I`),
      (iii) a real heading rather than a synthesized parent (`gram.synth_article` is a
            tree artifact and can never reach a candidate; the guard is kept explicit),
      (iv)  scored at least 0.5,
      (v)   free of the cross-reference / index-row / prose rule ids (`_XREF_RULES`):
            "Article 1 of the Uniform Commercial Code" must never open a segment,
      (vi)  outside every condemned table-of-contents region — TOC condemnation
            (`toc.dup_later_contract`) stays document-wide, because a second
            instrument's index can sit at the front of the file, and
      (vii) preceded, between the last structural heading before it and the restart, by
            fresh-instrument evidence: an instrument title, parties/recitals, or a bare
            exhibit cover line (`_fresh_instrument`).

    The execution-clause precursor alone is not enough: on the 208-window Turn 7 bank
    it sits in front of 85 continuations as well as 31 of the 39 restarts
    (docs/turn7_decisions/d_compound.md addendum).

    and it is additionally an instrument boundary iff

      (viii) `seg.prior_closed` AND (`seg.new_title` OR `seg.new_parties`): the previous
            instrument's own closing sits at most `_PRIOR_CLOSED_PROSE` characters of
            body prose before the restart, and the text in between names an instrument
            or a party role the document has not used before (B.2, `_evidence`).

    Eligible candidates are those outside the condemned regions scoring at least the
    tree's own `min_score` — the population the tree can build articles from — so every
    test here asks its question of the same population the tree chains.
    """
    elig = [i for i, c in enumerate(cands)
            if c.kind == "article" and i not in toc_idx and c.score >= min_score]
    if not elig:
        return []
    struct = sorted((i for i, c in enumerate(cands)
                     if c.kind in ("article", "section") and i not in toc_idx and c.score >= min_score),
                    key=lambda i: (cands[i].head_raw_start, i))
    if len(struct) < 2:
        return []
    body = _body_offsets(blocks, cands, struct)
    if not body:
        return []
    heads = [cands[i].head_raw_start for i in struct]
    # the fresh-instrument and evidence scans read one bounded window per restart, not
    # the whole block stream: a mega-exhibit carries 10^5 blocks and dozens of ARTICLE 1s
    bstarts = [b.raw_start for b in blocks]
    ordered = all(bstarts[i] <= bstarts[i + 1] for i in range(len(bstarts) - 1))
    # `seg.new_title` / `seg.new_parties` ask what the document has used BEFORE the
    # window.  Restarts are visited in ascending offset order and each window's `lo` is
    # monotone in the restart's offset, so one forward pointer over the block stream
    # answers that for every restart in a single pass rather than one pass each.
    border = list(range(len(blocks))) if ordered else sorted(range(len(blocks)), key=lambda j: bstarts[j])
    seen_names: set[str] = set()
    seen_roles: set[str] = set()
    scan = 0  # index into `border`: blocks folded into seen_* so far

    def fold_to(lo: int) -> None:
        nonlocal scan
        while scan < len(border) and bstarts[border[scan]] <= lo:
            b = blocks[border[scan]]
            if b.kind == "para" and b.text:
                n, r = _names_roles(b)
                seen_names.update(n)
                seen_roles.update(r)
            scan += 1

    out: list[int] = []
    seg_lo = 0  # start of the stretch a restart would close: the last restart accepted
    for i in sorted(elig, key=lambda i: (cands[i].head_raw_start, i)):
        c = cands[i]
        # seg.prior_body, scoped to the segment being closed.  Strictly inside it: the
        # offset recorded is the one at which the body is established, i.e. the SECOND
        # member of the prose-separated pair, so a restart that opens a segment must not
        # be allowed to count itself as that segment's body.
        if not any(seg_lo < o < c.head_raw_start for o in body):
            continue
        if c.order_key != 1:
            continue  # only a restart to the top of the numbering is a new instrument
        if "gram.synth_article" in c.rule_ids:
            continue  # phantom parent synthesized from a stray section numeral (F1)
        if c.score < 0.5:
            continue
        if _XREF_RULES.intersection(c.rule_ids):
            continue  # statutory citation, index row or mid-sentence numeral
        k = bisect_right(heads, c.head_raw_start - 1) - 1
        if k < 0:
            continue
        lo = max(heads[k], c.head_raw_start - _FRESH_SCAN_BYTES)
        window = blocks[bisect_right(bstarts, lo):bisect_right(bstarts, c.head_raw_start)] if ordered else blocks
        if not _fresh_instrument(window, lo, c.head_raw_start):
            continue
        # (i)-(vii) hold: the numbering restarts here, so the chains restart here.
        # (viii) then asks the separate question of whether a new INSTRUMENT begins.
        fold_to(lo)
        prior_closed, new_title, new_parties = _evidence(window, lo, c.head_raw_start,
                                                        seen_names, seen_roles)
        boundary = prior_closed and (new_title or new_parties)
        for rid in (CHAIN_RESTART,
                    "seg.prior_closed" if prior_closed else None,
                    "seg.new_title" if new_title else None,
                    "seg.new_parties" if new_parties else None,
                    INSTRUMENT_BOUNDARY if boundary else UNSEGMENTED_RESTART):
            if rid and rid not in c.rule_ids:
                c.rule_ids.append(rid)
        out.append(i)
        seg_lo = c.head_raw_start
        if len(out) >= max_restarts - 1:
            break
    return out


def _live_structural(cands, toc_idx: set[int], min_score: float) -> list[int]:
    """`build_contract_tree`'s own `live` list, recomputed here.

    The chain decision is taken before the tree marks anything, so the population a
    section restart is asked about has to be reconstructed: everything outside the
    condemned regions scoring at least `min_score`, plus the per-member rescue
    (`toc.member_rescued`) — a condemned row that is the document's only live copy of
    its label.  Mirrors `tree_contract.build_contract_tree` lines 77-89; the rescue
    rule id itself is written there, so it is not yet on a candidate at this point.
    """
    outside = {(c.kind, c.label_canon) for i, c in enumerate(cands)
               if i not in toc_idx and c.score >= min_score}
    live = []
    for i, c in enumerate(cands):
        if i in toc_idx:
            if (c.score >= 0.5 and "toc.leader_or_pageno" not in c.rule_ids
                    and (c.kind, c.label_canon) not in outside):
                live.append(i)
        elif c.score >= min_score:
            live.append(i)
    return live


def _section_restart_clean(c) -> bool:
    """Tests (c)-(e) of `chain.section_restart` on the opening candidate.

    (c) scored at least `SECTION_RESTART_MIN_SCORE`;
    (d) free of the cross-reference / index-row rule ids — an index run must never open
        a chain, which would re-accept the whole table of contents as a second
        instrument's body;
    (e) not a bare table cell: a `pos.in_table` / `pos.table_row` numeral with no style
        evidence and no title is a commitment schedule's amount or a covenant ratio, not
        a heading (A.3 section 3 found four of them among forty hand-read labels).
    """
    if c.score < SECTION_RESTART_MIN_SCORE:
        return False
    if _XREF_RULES.intersection(c.rule_ids) or _SECTION_RESTART_TOC_RULES.intersection(c.rule_ids):
        return False
    if (_TABLE_RULES.intersection(c.rule_ids)
            and not any(r.startswith("sty.") for r in c.rule_ids)
            and not (c.title or "").strip()):
        return False
    return True


def _section_restarts(cands, live_sec: list[int], toc_idx: set[int], *, min_run: int,
                      budget: int) -> list[int]:
    """Candidate indices inside ONE article-chain stretch at which the section numbering
    restarts (rule `chain.section_restart`).

    Walks the stretch's live section candidates in document order keeping `hi`, the
    largest section order key seen so far.  Candidate `c` opens a new chain when

      (a) `c.order_key < hi` — the numbering really does go back to the top;
      (b) `c` heads a run of at least `min_run` live section candidates that ascend
          strictly and all stay below `hi` — one stray label out of order is noise, a
          run of them is an instrument;
      (c)-(e) `_section_restart_clean`, and `c` is outside every condemned TOC region;
      (f) fewer than `budget` chain starts are left.

    After a restart `hi` becomes the opening key, so the new instrument's own numbering
    is what the next restart is measured against.
    """
    out: list[int] = []
    hi = -1
    for p, i in enumerate(live_sec):
        c = cands[i]
        ok = c.order_key
        if hi >= 0 and ok < hi and len(out) < budget:
            run, last = 1, ok
            for j in live_sec[p + 1:]:
                k = cands[j].order_key
                if last < k < hi:
                    run, last = run + 1, k
                    if run >= min_run:
                        break
                else:
                    break
            if run >= min_run and i not in toc_idx and _section_restart_clean(c):
                out.append(i)
                hi = ok
                continue
        hi = max(hi, ok)
    return out


def chain_restarts(blocks: list[Block], cands, toc_idx=(), *, min_score: float = 0.35,
                   max_restarts: int = MAX_SEGMENTS) -> list[int]:
    """Candidate indices at which the numbering restarts and a new pair of chains begins,
    in document order: `chain.restart` on an article, `chain.section_restart` on a section.

    Two rules, one list.  `_article_restarts` answers "a new instrument begins here" from
    an `ARTICLE 1` candidate and its neighbourhood, and only it can also claim a `segment`
    (`seg.instrument_boundary`).  `_section_restarts` answers the narrower, purely
    numerical question the article rule cannot even be asked when the second instrument
    has no ARTICLE heading: the section numbering goes back below its running maximum and
    a run of ascending sections follows.  A section restart therefore always carries
    `chain.restart_unsegmented` and never `seg.instrument_boundary`, so `compound_restarts`
    — which reads instrument boundaries off article nodes — is untouched by it and the
    segment partition is unchanged.

    Both kinds split BOTH chains at their offset, so `tree_contract`'s `(chain, order key)`
    addressing holds: a section still finds an article of its own chain, and two `ARTICLE 1`
    or `SECTION 1.01` nodes in one segment cannot collide.
    """
    toc_idx = set(toc_idx)
    arts = _article_restarts(blocks, cands, toc_idx, min_score=min_score, max_restarts=max_restarts)
    live_sec = [i for i in _live_structural(cands, toc_idx, min_score) if cands[i].kind == "section"]
    if not live_sec:
        return arts
    # one section walk per article-chain stretch: the running maximum resets where the
    # article numbering already restarted, exactly as the chains themselves do.
    starts = [cands[i].head_raw_start for i in arts]
    stretches: dict[int, list[int]] = {}
    for i in live_sec:
        stretches.setdefault(bisect_right(starts, cands[i].head_raw_start), []).append(i)
    secs: list[int] = []
    for s in sorted(stretches):
        budget = max_restarts - 1 - len(arts) - len(secs)
        if budget <= 0:
            break
        secs += _section_restarts(cands, stretches[s], toc_idx,
                                  min_run=SECTION_RESTART_MIN_RUN, budget=budget)
    for i in secs:
        for rid in (SECTION_RESTART, UNSEGMENTED_RESTART):
            if rid not in cands[i].rule_ids:
                cands[i].rule_ids.append(rid)
    return sorted(arts + secs, key=lambda i: (cands[i].head_raw_start, i))


def compound_restarts(nodes: list[Node], blocks: list[Block] | None = None) -> list[Node]:
    """Article nodes that begin a new glued agreement (rule `agenda.compound_restart`).

    One source of truth: the decision is `chain_restarts`', taken on the candidate list
    before the chains run, and carried onto the node by `tree_contract.build_contract_tree`
    as `seg.instrument_boundary`.  This function only reads it back for `assign_paths`.

    Not every `chain.restart` is here: a numbering restart with no instrument evidence
    carries `chain.restart_unsegmented`, gets its own chains, and stays inside the
    enclosing segment.  So the chain partition refines the segment partition rather than
    equalling it — the chains never straddle a segment boundary, which is what
    `tree_contract`'s per-chain article keying needs.  `blocks` is accepted and ignored
    (callers hold it).
    """
    out = sorted((n for n in nodes
                  if n.level_kind == "article" and INSTRUMENT_BOUNDARY in n.rule_ids),
                 key=lambda n: (n.head_raw_start, n.node_id))
    for n in out:
        if "agenda.compound_restart" not in n.rule_ids:
            n.rule_ids.append("agenda.compound_restart")
    return out


# What the material after a signature page has to look like for it to be a boundary
# rather than a page inside the body: the financial appendix or the exhibit index.
# The caption family is `judge_batch._FIN_CAPTION_RE`, the one the 269 fin_caption
# windows were drawn with, plus the statement/notes captions that carry no
# "CONSOLIDATED" prefix.
_FIN_CAPTION_RE = regex.compile(
    r"^\s*(?:REPORT OF INDEPENDENT|INDEPENDENT AUDITORS?'?S? REPORT|CONSOLIDATED (?:BALANCE SHEETS?|STATEMENTS? OF)|"
    r"INDEX TO (?:CONSOLIDATED )?FINANCIAL STATEMENTS|MANAGEMENT'S DISCUSSION AND ANALYSIS|LETTER TO (?:SHARE|STOCK)HOLDERS|"
    r"TO OUR (?:SHARE|STOCK)HOLDERS|SELECTED (?:CONSOLIDATED )?FINANCIAL DATA|FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA|"
    r"NOTES TO (?:THE )?(?:CONSOLIDATED )?FINANCIAL STATEMENTS|BALANCE SHEETS?\b|"
    r"STATEMENTS? OF (?:OPERATIONS|INCOME|CASH FLOWS?|STOCKHOLDERS|SHAREHOLDERS|CHANGES IN|COMPREHENSIVE))",
    regex.IGNORECASE | regex.MULTILINE,
)
_FPAGE_RE = regex.compile(r"(?:^|\s)F-\s?\d{1,3}(?:\s|$)")
# exhibit-index rows say where the exhibit came from; financial tables never do
_EX_ROW_RE = regex.compile(
    r"(?:INCORPORATED (?:HEREIN )?BY REFERENCE|FILED HEREWITH|FURNISHED HEREWITH|"
    r"CERTIFICATION (?:OF|PURSUANT|REQUIRED)|CONSENT OF INDEPENDENT|RULE 13a-14|"
    r"SECTION (?:302|906) OF THE SARBANES|XBRL (?:INSTANCE|TAXONOMY)|101\.(?:INS|SCH|CAL))",
    regex.IGNORECASE,
)
_APPENDIX_SCAN_BLOCKS = 6000
# boundaries v10 could not draw; nodes that are back matter only because of one of
# these carry it as a rule id
_NEW_BACK_RULES = ("agenda.back_after_sigs", "agenda.back_attestation", "agenda.back_iww_before_tail")


def _sig_block(b: Block, bare=_BARE_SIG_RE, *, ex13: bool = False) -> bool:
    """Does this block open a signature page / power of attorney, as opposed to merely
    starting with one of those words?  Either the first line is the bare heading, or the
    block carries the signature block's own evidence (the attestation sentence, an /s/).

    `ex13`: Turn 12 B.5 narrowing, scoped to the EX-13 path only (see
    `_BARE_SIG_SINGULAR_RE` above) -- a bare match against the SINGULAR "Signature" no
    longer suffices on its own; it still needs the attestation sentence or an /s/ line in
    the same block. A bare PLURAL match ("SIGNATURES", "SIGNATURE PAGES", "POWER OF
    ATTORNEY") is unaffected."""
    if not _WEAK_BACK_RE.match(b.text):
        return False
    first = b.lines[0] if b.lines else b.text
    evidence = bool(_ATTEST_RE.search(b.text) or _SLASH_S_RE.search(b.text))
    if evidence:
        return True
    if ex13 and _BARE_SIG_SINGULAR_RE.match(first):
        return False
    return bool(bare.match(first))


def _strict_back_head(b: Block, *, ex13: bool = False) -> bool:
    """agenda.back_head_strict: is this block a back-matter *heading*, not a line that
    merely opens with one of the words?  Only the SIGNATURE / POWER OF ATTORNEY
    alternatives are tested; SCHEDULE/EXHIBIT/ANNEX keep their v10 behaviour."""
    return not _WEAK_BACK_RE.match(b.text) or _sig_block(b, ex13=ex13)


def _appendix_follows(blocks: list[Block], idx: int, doc_end: int) -> bool:
    """Is the material after blocks[idx] the financial appendix or the exhibit index
    (rather than continuing items)?  Evidence: one financial-statement caption, or
    three F-page numbers, or three exhibit-index rows."""
    fin = fpage = exrow = 0
    for b in blocks[idx + 1 : idx + 1 + _APPENDIX_SCAN_BLOCKS]:
        t = b.text
        if not t:
            continue
        if _FIN_CAPTION_RE.search(t):
            return True
        fpage += len(_FPAGE_RE.findall(t))
        if _EX_ROW_RE.search(t):
            exrow += 1
        if fpage >= 3 or exrow >= 3:
            return True
    return False


def _real_article_after(structural: list[Node], off: int) -> bool:
    """A genuine article-level node begins after `off` — as opposed to a
    `gram.synth_article` phantom, the parent `tree_contract` invents for an unmatched
    section numeral (order key `a_num`) so it has something to nest under; that node
    was never a heading candidate and must not count as a continuation (the guard
    `segment_starts` already applies when it asks the same question of a candidate)."""
    return any(n.level_kind == "article" and "gram.synth_article" not in n.rule_ids and n.head_raw_start > off
               for n in structural)


def _back_matter_boundary(blocks: list[Block], nodes: list[Node], doc_end: int, *,
                           doc_start: int | None = None, ex13: bool = False) -> tuple[int, int, str | None]:
    """(raw offset, normalized offset, rule id) where back matter begins: the first
    back-matter heading after the last structural node's heading, or doc_end if none.

    `doc_start`/`ex13` (Turn 12 B.5, docs/turn12_decisions/a5_ex13.md section 1): on the
    EX-13 path `structural` is empty for 13,081 of 13,140 documents -- pipeline.py's
    `synth_root` anchor exists only in memory, for `find_headings`' parent-linking, and is
    filtered out of `nodes` before this function ever runs (`headings.merge_and_renumber`).
    Rather than returning `doc_end` immediately (no boundary can ever be found), reproduce
    that same anchor here -- `head_raw_start = doc_start` -- so the scan below has
    something to anchor on, scoped to the `ex13` caller only (`doc_start` is otherwise
    unused and this branch is a no-op unless both are supplied)."""
    structural = [n for n in nodes if n.level_kind in ("part", "item", "article", "section")]
    if not structural:
        if not (ex13 and doc_start is not None):
            return doc_end, -1, None
        last_head = doc_start
        doc_len = max(1, doc_end - doc_start)
    else:
        last_head = max(n.head_raw_start for n in structural)
        doc_len = max(1, doc_end - min(n.raw_start for n in structural))
    # agenda.back_iww_before_tail (Turn 8 B3): checked before the scan below, not after —
    # the scan is anchored on `last_head` and so never reaches an execution clause that
    # sits *before* it (the segment's last structural head is an attached form, schedule,
    # or a clause-level heading after the signature page rather than a continuation of the
    # instrument's own numbering; Turn 6's 53 stranded-IWW windows with no article heading
    # after the clause, docs/TURN8_PLAN.md Phase B.3). Any such clause is, by construction,
    # earlier than anything the scan below can find (which only ever looks past `last_head`),
    # so it wins outright rather than needing to be compared against that scan's result —
    # including against a second, later IN WITNESS WHEREOF the scan would otherwise catch
    # on its own (an attached form's or schedule's own execution page, itself back matter).
    # Fire on the earliest clause whose only continuation is non-article structure: a real
    # (non-restart) article after it is still the same instrument's body, and a value-1
    # restart opens a new segment (`seg.candidate_restart`) — both stay main body here,
    # left to the compound-restart detector, not this rule.
    for b in blocks:
        if b.raw_start > last_head or b.kind != "para" or not _IWW_RE.match(b.text):
            continue
        if _real_article_after(structural, b.raw_start):
            continue
        return b.raw_start, b.norm_start, "agenda.back_iww_before_tail"
    for i, b in enumerate(blocks):
        if b.raw_start <= last_head or b.kind != "para":
            continue
        marked = bool(_BACK_MATTER_RE.match(b.text))
        attest = bool(_ATTEST_RE.match(b.text))
        if not marked and not attest:
            continue
        # the execution clause is a sentence, not a heading: no length gate, and everything
        # after it is back matter however much follows (B3)
        if _IWW_RE.match(b.text):
            return b.raw_start, b.norm_start, "agenda.back_iww"
        if marked and not _strict_back_head(b, ex13=ex13):
            continue  # agenda.back_head_strict: a name or a table row, not a heading
        first = b.lines[0] if b.lines else b.text
        short_tail = (doc_end - b.raw_start) <= 0.3 * doc_len
        # a back-matter heading starts the back matter when little of the document follows it.
        # Text-era blocks glue the heading to the table that follows: test the first line (B3).
        if marked and len(first) <= 60 and short_tail:
            return b.raw_start, b.norm_start, "agenda.back_short_tail"
        # the attestation sentence is the signature block itself, heading or no heading
        if attest and not marked and short_tail:
            return b.raw_start, b.norm_start, "agenda.back_attestation"
        # Turn 7 decision (a): when more than 30% follows, the tail is the financial
        # appendix or the exhibit index, not continuing items — the appendix belongs in
        # back matter, so a signature page / exhibit index there is still the boundary.
        # Turn 12 B.5 (a5_ex13.md, "Refined design" point 1): on the ex13 path this test
        # keeps only the exhibit-index shape -- the signature-shape arm (`_sig_block`
        # against the bare-signature-only pattern, or a bare attestation sentence alone)
        # is what mistook an ARS's front-loaded, signed chairman/CEO letter for the
        # filing's own end-of-document signature in 6 of 8 `back_after_sigs` fires on the
        # 59-document bank, so it is dropped for `ex13` only.
        if ex13:
            strong = bool(_BARE_EXIDX_RE.match(first))
        else:
            strong = bool(_sig_block(b, _BARE_SIGONLY_RE) or _BARE_EXIDX_RE.match(first) or attest)
        if strong and _appendix_follows(blocks, i, doc_end):
            return b.raw_start, b.norm_start, "agenda.back_after_sigs" if marked else "agenda.back_attestation"
    return doc_end, -1, None


def regab_back_matter(blocks: list[Block], cands: list, nodes: list[Node], grammar, *,
                      doc_start: int, doc_end: int) -> list[int]:
    """rej.regab_back_matter (Turn 13 B.1b, docs/turn13_decisions/b1_regab_build.md 9):
    the candidate indexes of the Reg-AB items to veto before the tree is rebuilt.

    The boundary is `_back_matter_boundary` over the tree WITHOUT its Reg-AB items and
    PART V, so a Reg-AB node cannot push the boundary it is judged against. A Reg-AB
    candidate with no `gram.item.title_match` whose head lies at or after that boundary is
    an exhibit's own heading ("ITEM 1123 ANNUAL STATEMENT OF COMPLIANCE" in an EX-35) or an
    exhibit-index row ("Item 1122 Report on Assessment of Compliance ..."), never the
    filing's Reg-AB block: the B.1 gate found 67 such nodes in 51 documents and no real
    one (turn13-b1-backstart-read.txt). A title-matched block printed after the exhibit
    index (11 documents there) keeps its place. Returns [] -- and the caller does not
    rebuild -- unless such a candidate was actually accepted, so every other document's
    tree is untouched.
    """
    is_regab = getattr(grammar, "is_regab_item", None)
    if is_regab is None:
        return []

    def rg(x) -> bool:
        return x.label_canon is not None and is_regab(x.label_canon)

    untitled = [n for n in nodes if n.level_kind == "item" and rg(n) and "gram.item.title_match" not in n.rule_ids]
    if not untitled:
        return []
    ordinary = [n for n in nodes if not (n.level_kind == "item" and rg(n)) and not (n.level_kind == "part" and n.label_canon == "PART V")]
    bs = _back_matter_boundary(blocks, ordinary, doc_end, doc_start=doc_start)[0]
    if not any(n.head_raw_start >= bs for n in untitled):
        return []
    return [i for i, c in enumerate(cands)
            if c.kind == "item" and rg(c) and "gram.item.title_match" not in c.rule_ids and c.head_raw_start >= bs]


def _back_matter_start(blocks: list[Block], nodes: list[Node], doc_end: int, *, doc_start: int | None = None, ex13: bool = False) -> int:
    return _back_matter_boundary(blocks, nodes, doc_end, doc_start=doc_start, ex13=ex13)[0]


def assign_paths(nodes: list[Node], blocks: list[Block], *, doc_start: int, doc_end: int, path_len: int = PATH_LEN,
                  ex13: bool = False) -> dict[int, list[int]]:
    """Return {node_id: path}. Also sets meta boundaries as three synthetic
    entries under keys -1 (front), -2 (toc span) and -3 (back) for the caller,
    and the compound-exhibit segmentation under "_segments"."""
    by_id = {n.node_id: n for n in nodes}
    root = by_id[0]
    toc_nodes = [n for n in nodes if n.level_kind == "toc"]
    body_nodes = [n for n in nodes if n.node_id != 0 and n.level_kind != "toc"]

    # Each glued agreement gets its own front/toc/main/back computation: a compound
    # exhibit's second instrument has its own signature page, and the document-wide
    # scan only ever sees the last one (Turn 6's "IWW stays in the main body" class).
    seg_starts = [doc_start] + [a.head_raw_start for a in compound_restarts(nodes, blocks)][: MAX_SEGMENTS - 1]
    seg_back: list[int] = []
    seg_norm: list[int] = []
    seg_rule: list[str | None] = []
    for si, lo in enumerate(seg_starts):
        hi = seg_starts[si + 1] if si + 1 < len(seg_starts) else doc_end
        if len(seg_starts) == 1:
            bs, bn, br = _back_matter_boundary(blocks, nodes, doc_end, doc_start=doc_start, ex13=ex13)
        else:
            sn = [n for n in nodes if n.node_id != 0 and lo <= n.raw_start < hi]
            sb = [b for b in blocks if lo <= b.raw_start < hi]
            bs, bn, br = _back_matter_boundary(sb, sn, hi, doc_start=lo, ex13=ex13)
        seg_back.append(bs); seg_norm.append(bn); seg_rule.append(br)

    def seg_of(off: int) -> int:
        return max(0, bisect_right(seg_starts, off) - 1)

    def meta_of(off: int) -> int:
        return META_BACK if off >= seg_back[seg_of(off)] else META_MAIN

    back_start, back_norm_start, back_rule = seg_back[-1], seg_norm[-1], seg_rule[-1]
    first_main = min((n.raw_start for n in body_nodes), default=doc_end)
    if toc_nodes:
        first_main = min(first_main, min(n.raw_start for n in toc_nodes))
    paths: dict[int, list[int]] = {0: [META_MAIN] + [0] * (path_len - 1)}

    # ordinals among siblings, in document order, per meta segment
    children: dict[int, list[Node]] = {}
    for n in body_nodes:
        children.setdefault(n.parent_id, []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: (n.raw_start, n.node_id))

    def walk(parent_id: int, prefix: list[int]) -> None:
        # prefix is unpadded: [meta, o1, o2, ...]
        # top-level ordinals restart at 1 in every segment, so each glued agreement is
        # addressed as if it were its own document.  A numbering restart that is NOT an
        # instrument boundary (`chain.restart_unsegmented`) gets its own chains but no
        # segment, so its articles continue the enclosing segment's count: two ARTICLE 1
        # nodes in one instrument are the first and the nth top-level heading of that
        # instrument, not the first of two documents.
        counts: dict[int, int] = {}
        for i, n in enumerate(children.get(parent_id, []), start=1):
            if parent_id == 0:
                s = seg_of(n.raw_start)
                counts[s] = i = counts.get(s, 0) + 1
            meta = meta_of(n.raw_start)
            rule = seg_rule[seg_of(n.raw_start)]
            if meta == META_BACK and rule in _NEW_BACK_RULES and rule not in n.rule_ids:
                n.rule_ids.append(rule)  # this node is back matter only under the Turn 7 rule
            if len(prefix) < path_len:
                p = [meta] + prefix[1:] + [i]
            else:  # beyond the global depth limit: inherit the parent's path
                p = [meta] + prefix[1:]
            paths[n.node_id] = (p + [0] * path_len)[:path_len]
            walk(n.node_id, p)

    walk(0, [META_MAIN])
    for i, t in enumerate(toc_nodes, start=1):
        paths[t.node_id] = ([META_TOC, i] + [0] * path_len)[:path_len]
    paths[-1] = [META_FRONT] + [0] * (path_len - 1)
    paths[-3] = [META_BACK] + [0] * (path_len - 1)
    paths.setdefault("_bounds", None)  # type: ignore[arg-type]
    paths["_bounds"] = {"front_end": first_main, "back_start": back_start,  # type: ignore[index]
                        "back_norm_start": back_norm_start, "back_rule": back_rule}
    of_node = {n.node_id: (0 if n.node_id == 0 else seg_of(n.raw_start)) for n in nodes}
    for n in nodes:
        # a node that only reaches back matter through its own segment's scan carries
        # the reason, so run_diff can enumerate the class Turn 6 left in the main body
        if len(seg_starts) > 1 and of_node[n.node_id] < len(seg_starts) - 1 and paths.get(n.node_id, [META_MAIN])[0] == META_BACK:
            if "agenda.segment_back_matter" not in n.rule_ids:
                n.rule_ids.append("agenda.segment_back_matter")
    paths["_segments"] = {"starts": seg_starts, "back": seg_back, "of_node": of_node}  # type: ignore[index]
    return paths


def path_str(p: list[int]) -> str:
    return ".".join(str(x) for x in p)


def level_profile(nodes: list[Node], paths: dict) -> list[dict]:
    """Per path position (1..), the dominant level_kind and node count: the document's
    'level profile', used to align depths across documents."""
    from collections import Counter

    counts: dict[int, Counter] = {}
    for n in nodes:
        p = paths.get(n.node_id)
        if not p or n.node_id == 0 or p[0] != META_MAIN:
            continue
        depth = max(i for i, x in enumerate(p) if x) if any(p[1:]) else 0
        counts.setdefault(depth, Counter())[n.level_kind] += 1
    out = []
    for d in sorted(counts):
        c = counts[d]
        out.append(dict(position=d, dominant=c.most_common(1)[0][0], kinds=dict(c), n=sum(c.values())))
    return out
