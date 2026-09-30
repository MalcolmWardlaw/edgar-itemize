"""Choose which <DOCUMENT> blocks in a submission to parse."""

from __future__ import annotations

import re

from .sgml import DocumentBlock

_BINARY_EXT = re.compile(r"\.(pdf|gif|jpg|jpeg|png|xls|xlsx|zip|xml|xsd|json)$", re.IGNORECASE)
_EX10_TYPE_RE = re.compile(r"^EX-10(?![0-9])", re.IGNORECASE)
_EX4_TYPE_RE = re.compile(r"^EX-4(?![0-9])", re.IGNORECASE)

_CREDIT_DESC_RE = re.compile(
    r"\b(credit|loan|term loan|revolving|financing|facilit(?:y|ies))\b.*\bagreement\b",
    re.IGNORECASE,
)
_CREDIT_FN_RE = re.compile(r"credit|loan|facilit", re.IGNORECASE)
regex_fn_excl = re.compile(r"amend|waiver|joinder|consent|supplement|guarant|pledge|mortgage", re.IGNORECASE)
_CREDIT_EXCLUDE_RE = re.compile(
    r"\b(amendment|amended and restated amendment|joinder|waiver|consent|guarant\w*|pledge|"
    r"security agreement|mortgage|supplement|letter|schedule|exhibit list|commitment)\b",
    re.IGNORECASE,
)


def _base_form(submission_type: str) -> str:
    return re.sub(r"/A$", "", submission_type.strip().upper())


def select_primary(docs: list[DocumentBlock], submission_type: str) -> DocumentBlock | None:
    """Pick the main body document for a filing.

    Order: exact TYPE match on submission type; TYPE match on the base form
    (10-K/A -> 10-K); first document whose TYPE starts with the base form's
    leading token (10-K405, 10-KT); first non-binary text-bearing document.
    """
    st = submission_type.strip().upper()
    base = _base_form(st)
    candidates = [d for d in docs if d.has_text and not (d.filename and _BINARY_EXT.search(d.filename))]
    for want in (st, base):
        for d in candidates:
            if (d.type or "").upper() == want:
                return d
    stem = base.split("-")[0] + "-" + base.split("-")[1][:1] if "-" in base else base
    for d in candidates:
        if (d.type or "").upper().startswith(stem):
            return d
    return candidates[0] if candidates else None


def is_credit_agreement(doc: DocumentBlock, *, include_ex4: bool = False, min_bytes: int = 60_000) -> bool:
    t = (doc.type or "").upper()
    if not (_EX10_TYPE_RE.match(t) or (include_ex4 and _EX4_TYPE_RE.match(t))):
        return False
    if not doc.has_text or doc.text_length < min_bytes:
        return False
    if doc.filename and _BINARY_EXT.search(doc.filename):
        return False
    desc = doc.description or ""
    fn = doc.filename or ""
    hit = bool(_CREDIT_DESC_RE.search(desc)) or bool(_CREDIT_FN_RE.search(fn))
    if not hit:
        return False
    if regex_fn_excl.search(fn):
        return False
    if _CREDIT_EXCLUDE_RE.search(desc):
        # "AMENDED AND RESTATED CREDIT AGREEMENT" is a full agreement; keep it.
        if not re.search(r"amended\s+and\s+restated", desc, re.IGNORECASE) or re.search(
            r"\bamendment\b", desc, re.IGNORECASE
        ):
            return False
    return True


def select_credit_agreements(docs: list[DocumentBlock], **kw) -> list[DocumentBlock]:
    return [d for d in docs if is_credit_agreement(d, **kw)]
