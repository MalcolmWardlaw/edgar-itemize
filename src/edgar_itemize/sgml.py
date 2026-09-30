"""Read an EDGAR full-submission .txt file and split it into <DOCUMENT> blocks.

Files are decoded as latin-1 so that every character corresponds to exactly one
byte: string offsets are byte offsets into the file on disk.

Handles the 1994-95 <IMS-DOCUMENT>/<IMS-HEADER> wrapper, the PEM envelope
(-----BEGIN PRIVACY-ENHANCED MESSAGE-----) present through the mid-2000s, the
optional <FILENAME> tag, and inline-XBRL <ix:header> blocks (blanked in place so
offsets are preserved).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_HEADER_RE = re.compile(
    r"<(?P<tag>SEC-HEADER|IMS-HEADER)>(?P<body>.*?)</(?P=tag)>", re.DOTALL | re.IGNORECASE
)
_DOCUMENT_RE = re.compile(r"<DOCUMENT>(?P<body>.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_TYPE_RE = re.compile(r"<TYPE>\s*([^\r\n<]*)", re.IGNORECASE)
_SEQUENCE_RE = re.compile(r"<SEQUENCE>\s*([^\r\n<]*)", re.IGNORECASE)
_FILENAME_RE = re.compile(r"<FILENAME>\s*([^\r\n<]*)", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"<DESCRIPTION>\s*([^\r\n<]*)", re.IGNORECASE)
_TEXT_RE = re.compile(r"<TEXT>(?P<inner>.*?)</TEXT>", re.DOTALL | re.IGNORECASE)

_HTML_SNIFF_RE = re.compile(
    r"<!DOCTYPE\s+HTML|<\s*html\b|content-type[^>]*text/html|<\s*body\b|<\s*div\b|<\s*p\b|<\s*font\b",
    re.IGNORECASE,
)
_IX_HEADER_RE = re.compile(r"<ix:header>.*?</ix:header>", re.DOTALL | re.IGNORECASE)
_NONHTML_WRAPPER_RE = re.compile(r"^\s*<(XML|PDF)>", re.IGNORECASE)
_IX_RE = re.compile(r"<ix:|xmlns:ix=", re.IGNORECASE)


@dataclass(slots=True)
class DocumentBlock:
    """One <DOCUMENT>...</DOCUMENT> block, addressed by byte offsets in the file."""

    sequence: int | None
    type: str | None
    filename: str | None
    description: str | None
    is_html: bool
    doc_start: int  # offset of '<DOCUMENT>'
    doc_end: int  # offset just past '</DOCUMENT>'
    text_start: int  # offset of first char inside <TEXT>, -1 if none
    text_end: int  # offset just past last char inside <TEXT>, -1 if none

    @property
    def has_text(self) -> bool:
        return self.text_start >= 0

    @property
    def text_length(self) -> int:
        return self.text_end - self.text_start if self.has_text else 0


def read_submission(path: Path | str) -> str:
    """Read a full submission as a latin-1 string (1 char == 1 byte)."""
    return read_submission_hashed(path)[0]


def read_submission_hashed(path: Path | str) -> tuple[str, str, int]:
    """(text, sha256 hex of the raw bytes, byte count). The hash is the unit of
    reproducibility (docs/OUTPUT_CONTRACT.md): the same bytes in give the same rows out."""
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("latin-1"), hashlib.sha256(raw).hexdigest(), len(raw)


def header_text(text: str) -> str:
    m = _HEADER_RE.search(text)
    return m.group("body") if m else ""


_CONFORMED_NAME_RE = re.compile(r"COMPANY\s+CONFORMED\s+NAME:\s*([^\r\n<]+)", re.IGNORECASE)


def conformed_name(header: str) -> str:
    """First COMPANY CONFORMED NAME in the SEC/IMS header (the filer), '' if absent."""
    m = _CONFORMED_NAME_RE.search(header)
    return m.group(1).strip() if m else ""


_CENTRAL_INDEX_KEY_RE = re.compile(r"CENTRAL\s+INDEX\s+KEY:\s*(\d+)", re.IGNORECASE)


def header_cik(header: str) -> str | None:
    """First CENTRAL INDEX KEY in the SEC/IMS header, i.e. the first FILER's CIK, as a
    string of digits with the leading zeros stripped (the manifests' `cik` convention:
    ``0000353944`` -> ``353944``). None when the header carries none (headerless input
    such as `load_text_submission`).

    Release decision D9 (`docs/RELEASE_PLAN.md` section 9): the `cik` stamped on output
    rows comes from the file's own header so that it is a function of the input bytes; a
    co-registrant filing listed under several CIK directories gets the same `cik` from
    every copy."""
    m = _CENTRAL_INDEX_KEY_RE.search(header)
    if m is None:
        return None
    return m.group(1).lstrip("0") or "0"


_CONFORMED_TYPE_RE = re.compile(r"CONFORMED\s+SUBMISSION\s+TYPE:\s*([^\r\n<]+)", re.IGNORECASE)


def conformed_type(header: str) -> str:
    """CONFORMED SUBMISSION TYPE from the SEC/IMS header (uppercased), '' if absent.

    EDGAR's own record of what form this submission is, independent of the per-document
    <TYPE> tag, which filers sometimes get wrong (gram.form_from_header)."""
    m = _CONFORMED_TYPE_RE.search(header)
    return m.group(1).strip().upper() if m else ""


def sniff_is_html(inner: str) -> bool:
    head = inner[:4096]
    if _NONHTML_WRAPPER_RE.search(head):
        return False
    return bool(_HTML_SNIFF_RE.search(head)) or bool(_IX_RE.search(head))


def is_inline_xbrl(inner: str) -> bool:
    return bool(_IX_RE.search(inner[:8192]))


def split_documents(text: str) -> list[DocumentBlock]:
    docs: list[DocumentBlock] = []
    for m in _DOCUMENT_RE.finditer(text):
        body = m.group("body")
        body_start = m.start("body")
        type_m = _TYPE_RE.search(body)
        seq_m = _SEQUENCE_RE.search(body)
        fn_m = _FILENAME_RE.search(body)
        desc_m = _DESCRIPTION_RE.search(body)
        text_m = _TEXT_RE.search(body)
        if text_m is not None:
            text_start = body_start + text_m.start("inner")
            text_end = body_start + text_m.end("inner")
            is_html = sniff_is_html(text_m.group("inner"))
        else:
            text_start = text_end = -1
            is_html = False
        seq: int | None = None
        if seq_m:
            s = seq_m.group(1).strip()
            if s.isdigit():
                seq = int(s)
        docs.append(
            DocumentBlock(
                sequence=seq,
                type=(type_m.group(1).strip() if type_m else None) or None,
                filename=(fn_m.group(1).strip() if fn_m else None) or None,
                description=(desc_m.group(1).strip() if desc_m else None) or None,
                is_html=is_html,
                doc_start=m.start(),
                doc_end=m.end(),
                text_start=text_start,
                text_end=text_end,
            )
        )
    return docs


def document_text(text: str, doc: DocumentBlock) -> str:
    """Slice the <TEXT> payload of `doc` out of the full submission string."""
    if not doc.has_text:
        return ""
    return text[doc.text_start : doc.text_end]


def blank_ix_header(html: str) -> str:
    """Replace every <ix:header>...</ix:header> span with spaces of equal length.

    Length-preserving so that offsets into the returned string remain valid
    offsets into the original.
    """

    def _blank(m: re.Match[str]) -> str:
        return " " * (m.end() - m.start())

    return _IX_HEADER_RE.sub(_blank, html)


@dataclass(slots=True)
class Submission:
    path: Path
    text: str
    header: str
    documents: list[DocumentBlock]
    input_sha256: str | None = None  # sha256 of the raw file bytes (read_submission_hashed)
    input_bytes: int | None = None

    @property
    def accession(self) -> str:
        return self.path.stem

    @property
    def company_name(self) -> str:
        return conformed_name(self.header)

    @property
    def header_cik(self) -> str | None:
        """The first FILER's CIK from the header (`header_cik`); None on headerless input."""
        return header_cik(self.header)


def load_submission(path: Path | str) -> Submission:
    p = Path(path)
    text, sha, nbytes = read_submission_hashed(p)
    return Submission(path=p, text=text, header=header_text(text), documents=split_documents(text),
                      input_sha256=sha, input_bytes=nbytes)


def load_text_submission(path: Path | str, accession: str | None = None, sequence: int | None = None,
                         doc_type: str = "EX-10") -> Submission:
    """Wrap a bare exhibit text file (no SGML envelope) as a one-document Submission.

    Input adapter only -- no parser rule is involved.  The file's whole content is
    the document's <TEXT> payload, so raw offsets returned by the parser are offsets
    into the file itself and an absolute 1-indexed line number in the file is an
    absolute line number in the parse.  ``doc_type`` drives grammar routing exactly
    as a real <TYPE> tag would (``EX-10`` -> ContractGrammar), and ``is_html`` is
    False, which is what makes ``classify`` pick the ``text`` era.
    """
    p = Path(path)
    text, sha, nbytes = read_submission_hashed(p)
    doc = DocumentBlock(sequence=sequence, type=doc_type, filename=p.name, description=None,
                        is_html=False, doc_start=0, doc_end=len(text), text_start=0, text_end=len(text))
    return Submission(path=p.with_name(f"{accession or p.stem}.txt"), text=text, header="", documents=[doc],
                      input_sha256=sha, input_bytes=nbytes)
