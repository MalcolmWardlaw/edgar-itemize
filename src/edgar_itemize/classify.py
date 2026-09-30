"""Era and publisher classification for one document. Deterministic, signal-based."""

from __future__ import annotations

from dataclasses import dataclass, field

import regex

from .sgml import DocumentBlock, is_inline_xbrl

# Filer-agent CIK prefixes (first 10 digits of the accession number) -> publisher family.
AGENT_FAMILIES: dict[str, str] = {
    "0000912057": "donnelley", "0001047469": "donnelley", "0001193125": "donnelley", "0000950170": "donnelley",
    "0000893220": "bowne", "0000950123": "bowne", "0000950124": "bowne", "0000950131": "bowne", "0000950134": "bowne",
    "0000950135": "bowne", "0000950137": "bowne", "0000950144": "bowne", "0000950152": "bowne", "0000950109": "bowne",
    "0000950116": "bowne", "0000950129": "bowne", "0000950130": "bowne", "0000950133": "bowne", "0000950136": "bowne",
    "0000950148": "bowne", "0000950149": "bowne", "0000950150": "bowne", "0000950153": "bowne", "0000950168": "bowne",
    "0000950172": "bowne",
    "0001104659": "merrill", "0001047469": "donnelley", "0001056404": "merrill",
    "0001144204": "vintage", "0001213900": "edgar_agents", "0001493152": "m2_compliance", "0001437749": "rdg",
    "0001628280": "workiva", "0001564590": "activedisclosure", "0001558370": "toppan_bridge", "0001477932": "novaworks",
    "0001140361": "broadridge", "0001171843": "globalone", "0001062993": "newsfile", "0001185185": "issuer_direct",
    "0001019056": "edgar_online", "0000898430": "bowne", "0000927016": "bowne", "0000936392": "bowne",
}

_SIGNALS = {
    "workiva_comment": regex.compile(r"Created with Wdesk|Workiva", regex.IGNORECASE),
    "activedisclosure_comment": regex.compile(r"ActiveDisclosure", regex.IGNORECASE),
    "toppan_bridge_comment": regex.compile(r"Toppan Merrill Bridge", regex.IGNORECASE),
    "compsci_comment": regex.compile(r"CompSci Transform", regex.IGNORECASE),
    "bowne_link2": regex.compile(r"<!--\s*link2\s", regex.IGNORECASE),
    "donnelley_toc_anchor": regex.compile(r'<A NAME="toc_', regex.IGNORECASE),
    "page_break_style": regex.compile(r"page-break-(?:before|after)", regex.IGNORECASE),
    "hr_tag": regex.compile(r"<hr\b", regex.IGNORECASE),
    "font_tag": regex.compile(r"<font\b", regex.IGNORECASE),
    "div_tag": regex.compile(r"<div\b", regex.IGNORECASE),
    "td_tag": regex.compile(r"<td\b", regex.IGNORECASE),
    "page_mark": regex.compile(r"<PAGE>", regex.IGNORECASE),
    "hidden_white_text": regex.compile(r"font-size:\s*1pt;\s*color:\s*white", regex.IGNORECASE),
    "img_tag": regex.compile(r"<img\b", regex.IGNORECASE),
    "pre_tag": regex.compile(r"<pre\b", regex.IGNORECASE),
    "cross_reference_index": regex.compile(r"CROSS[\s-]*REFERENCE\s+INDEX|FORM 10-K CROSS[\s-]*REFERENCE", regex.IGNORECASE),
    "reg_ab": regex.compile(r"Regulation\s+AB|Item\s+11\d\d\b|General Instruction J|as the Depositor|Issuing Entity|Servicing Criteria|Asset[- ]Backed", regex.IGNORECASE),
}

# --- Two-tier ABS / Reg-AB structured-finance signal (overnight case A1/D1) ---
#
# The legacy "reg_ab" signal above leaks both ways: bare "Asset[- ]Backed" prose in
# operating 10-Ks reaches the >=3 threshold (44.8% false-positive rate), while
# pre-2006 trusts saying "as depositor" / "Noteholders" go unflagged.  The
# replacement is two tiers, either of which flags the filing (see metrics._is_abs
# / abs_two_tier):
#   1. specific tier: >=2 hits summed over the specific-vocabulary alternates,
#      counted over roughly the first 50 KB of the body (trust vocabulary is
#      front-loaded; operating-company ABS prose lives in the FS notes).
#   2. name tier: SGML COMPANY CONFORMED NAME matches the structured-finance
#      name pattern (catches pre-2006 skeleton trusts with no distinctive text).
# Bare "Asset[- ]Backed" is counted separately (reg_ab_generic) and is never
# sufficient on its own.

_REG_AB_WINDOW = 50_000  # bytes of body scanned by the two-tier signal

# Specific-vocabulary alternates; ids follow A1's per-alternate naming.
_REG_AB_SPECIFIC: dict[str, "regex.Pattern[str]"] = {
    "rab": regex.compile(r"Regulation\s+AB", regex.IGNORECASE),
    "i11": regex.compile(r"Item\s+11\d\d\b", regex.IGNORECASE),
    "gij": regex.compile(r"General\s+Instruction\s+J", regex.IGNORECASE),
    "dep": regex.compile(r"as\s+(?:the\s+)?depositor", regex.IGNORECASE),  # relaxed: "as depositor" too
    "ie": regex.compile(r"Issuing\s+Entity", regex.IGNORECASE),
    "sc": regex.compile(r"Servicing\s+Criteria", regex.IGNORECASE),
    "no": regex.compile(r"Noteholders?", regex.IGNORECASE),
    "ch": regex.compile(r"Certificateholders?", regex.IGNORECASE),
    "ps": regex.compile(r"Pooling\s+and\s+Servicing", regex.IGNORECASE),
}
_REG_AB_GENERIC = regex.compile(r"Asset[- ]Backed", regex.IGNORECASE)

# Structured-finance company-name pattern (A1's name tier).  Deliberately NOT
# bare "TRUST" (would sweep in REITs: Starwood Property Trust etc.); the series
# suffix clause ("2005-VT1", "2017-1") catches exotic trust names.
_SF_NAME_RE = regex.compile(
    r"RECEIVABLES|SECURITIZ|ASSET[ -]?BACKED|\bABS\b|PASS[ -]?THROUGH"
    r"|(?:OWNER|MASTER|GRANTOR|AUTO|LOAN|MORTGAGE|FUNDING)S?\s+TRUST"
    r"|CERTIFICATES|TRUST\s+(?:19|20)\d\d|(?:19|20)\d\d-[A-Z0-9]+\b",
    regex.IGNORECASE,
)


def abs_two_tier(signals: dict) -> bool:
    """Two-tier ABS decision over a stored signals dict.

    New-format signals (reg_ab_specific / reg_ab_name present): specific tier
    >=2 or name tier.  Old-format signals fall back to the legacy reg_ab>=3
    threshold so metrics over pre-fix parquet keep working.
    """
    if "reg_ab_specific" in signals or "reg_ab_name" in signals:
        return signals.get("reg_ab_specific", 0) >= 2 or bool(signals.get("reg_ab_name", 0))
    return signals.get("reg_ab", 0) >= 3


@dataclass(frozen=True, slots=True)
class Profile:
    era: str  # text | html_early | html_publisher | ixbrl
    publisher: str
    agent_cik: str
    signals: dict[str, int] = field(default_factory=dict)


def classify(accession: str, filer_cik: str | int, doc: DocumentBlock, body: str, *, company_name: str = "") -> Profile:
    agent = accession[:10]
    sig = {k: len(p.findall(body)) for k, p in _SIGNALS.items()}
    head = body[:_REG_AB_WINDOW]
    sig["reg_ab_specific"] = sum(len(p.findall(head)) for p in _REG_AB_SPECIFIC.values())
    sig["reg_ab_generic"] = len(_REG_AB_GENERIC.findall(head))
    sig["reg_ab_name"] = int(bool(company_name and _SF_NAME_RE.search(company_name)))
    self_filed = int(agent) == int(filer_cik)
    fam = AGENT_FAMILIES.get(agent, "self" if self_filed else "other")
    if sig["workiva_comment"]:
        fam = "workiva"
    elif sig["activedisclosure_comment"]:
        fam = "activedisclosure"
    elif sig["toppan_bridge_comment"]:
        fam = "toppan_bridge"
    elif sig["compsci_comment"]:
        fam = "compsci"
    elif sig["bowne_link2"] and fam in ("self", "other"):
        fam = "bowne"
    if not doc.is_html:
        era = "text"
    elif sig["hidden_white_text"] >= 3 and sig["img_tag"] >= 3:
        era = "image_text"  # scanned pages with hidden OCR text; no recoverable layout
    elif is_inline_xbrl(body):
        era = "ixbrl"
    elif fam in ("self", "other"):
        era = "html_early" if sig["font_tag"] > sig["div_tag"] else "html_generic"
    else:
        era = "html_publisher"
    return Profile(era=era, publisher=fam, agent_cik=agent, signals=sig)


# --- Incorporation-by-reference / internal cross-reference item flags (case A2) ---
#
# Pre-2009 filers satisfy Items 7/8 with a one-paragraph pointer instead of
# content.  The tiny span is a CORRECT parse; these flags say where the content
# actually lives:
#   item_incorporated_by_reference — IBR verb + EXTERNAL target (Annual Report
#     to Shareholders / Exhibit 13): content is NOT in this document.
#   ibr_target — "ex13" when Exhibit 13 is named, else "annual_report".
#   item_cross_reference — internal pointer/IBR (page F-1, Item 14/15, "this
#     Annual Report on Form 10-K"): content is elsewhere in the SAME document.
# The discriminator is target context; internal-context phrases are neutralized
# before testing for an external annual-report mention (A2's v3 classifier).

_IBR_SPAN_CAP = 4096  # tag-stripped span length gate: only tiny items are candidates

_IBR_RE = regex.compile(r"incorporated[\s\S]{0,80}?by\s+reference|incorporated\s+herein|by\s+reference\s+to", regex.IGNORECASE)
_IBR_INTERNAL_CTX_RE = regex.compile(r"(?:this|of\s+this|in\s+this)\s+annual\s+report|annual\s+report\s+(?:on|in)\s+form\s+10-?k", regex.IGNORECASE)
_IBR_EX13_RE = regex.compile(r"exhibit\s+(?:no\.?\s*)?13\b", regex.IGNORECASE)
_IBR_ANNUAL_REPORT_RE = regex.compile(r"annual\s+report", regex.IGNORECASE)
_XREF_INTERNAL_RE = regex.compile(
    r"\bF-\d|index\s+to\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements"
    r"|item\s+1[45]\b|(?:this|of\s+this|in\s+this)\s+(?:annual\s+)?report\s+on\s+form\s+10-?k"
    r"|follow(?:s|ing)\s+the\s+signature|elsewhere\s+(?:in|herein)",
    regex.IGNORECASE,
)


def ibr_flags(span_text: str) -> dict | None:
    """A2 item-level flags for one item node's tag-stripped span text.

    Returns None when the span is too large to be a pointer stub (>= ~4 KB);
    otherwise a dict with item_incorporated_by_reference (bool), ibr_target
    ("ex13" | "annual_report" | None) and item_cross_reference (bool).
    """
    if len(span_text) >= _IBR_SPAN_CAP:
        return None
    has_ibr = bool(_IBR_RE.search(span_text))
    ex13 = bool(_IBR_EX13_RE.search(span_text))
    neutral = _IBR_INTERNAL_CTX_RE.sub(" ", span_text)
    external = ex13 or bool(_IBR_ANNUAL_REPORT_RE.search(neutral))
    flag = has_ibr and external
    target = ("ex13" if ex13 else "annual_report") if flag else None
    xref = (not flag) and (has_ibr or bool(_XREF_INTERNAL_RE.search(span_text)))
    return dict(item_incorporated_by_reference=flag, ibr_target=target, item_cross_reference=xref)
