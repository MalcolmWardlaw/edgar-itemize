"""The cacheable prefix of a parse: load, select, classify, normalise.

Everything up to and including normalisation lives here, split out of ``pipeline`` and
``cli`` so that (a) the normalisation cache (``normcache``) can digest exactly the source
this prefix executes and nothing downstream of it, and (b) a cache hit can hand
``pipeline.parse_prepared`` the same inputs a fresh parse would, without the raw file.

``Prepared`` holds every value the rest of ``parse_document`` reads from the Submission and
DocumentBlock after normalisation: the accession, the header's CONFORMED SUBMISSION TYPE
(grammar routing, gram.form_from_header), the <TYPE> of every document in the submission
(``has_ex13`` for the IBR flags), the chosen DocumentBlock itself (type, sequence,
text_start/text_end, carried into ParseResult and result_rows), the Profile, the blocks,
the normalised text, the input hash and size, and the header's first FILER CIK (D9). The
offset map ``normalize`` also returns is discarded by ``parse_document`` and is not carried.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import control
from .blocks import Block
from .classify import Profile, classify
from .normalize_text import text_to_blocks
from .select import select_primary
from .sgml import (DocumentBlock, Submission, blank_ix_header, conformed_name, conformed_type, document_text,
                   load_submission, load_text_submission)


@dataclass(slots=True)
class Prepared:
    accession: str
    doc: DocumentBlock
    header_type: str  # conformed_type(sub.header)
    doc_types: tuple  # (doc.type for doc in sub.documents)
    profile: Profile
    blocks: list[Block]
    normalized_text: str
    input_sha256: str | None = None  # Submission.input_sha256; pickled with the cache entry
    input_bytes: int | None = None
    # Submission.header_cik: the first FILER's CIK from the SGML header, the value the
    # output `cik` column carries (D9); None on headerless input (--kind text).
    header_cik: str | None = None


def normalize(body: str, raw_base: int, profile: Profile):
    if profile.era == "text":
        return text_to_blocks(body, raw_base)
    from .normalize_html import html_to_blocks

    return html_to_blocks(body, raw_base, profile)


def prepare_document(sub: Submission, doc: DocumentBlock, cik: str | int) -> Prepared:
    body = document_text(sub.text, doc)
    profile = classify(sub.accession, cik, doc, body, company_name=conformed_name(sub.header))
    if profile.era == "ixbrl":
        body = blank_ix_header(body)
    blocks, norm, _omap = normalize(body, doc.text_start, profile)
    return Prepared(sub.accession, doc, conformed_type(sub.header), tuple(d.type for d in sub.documents),
                    profile, blocks, norm, sub.input_sha256, sub.input_bytes, sub.header_cik)


def source_path(row: dict, kind: str):
    """The file a manifest row reads (what the cache's staleness check stats)."""
    return row["archive_path"] if kind == "text" else control.rewrite_path(row["archive_path"])


def load_row(row: dict, kind: str) -> tuple[Submission, DocumentBlock | None]:
    """Load a manifest row's submission and pick its document, exactly as ``cli._parse_one``
    always has (the per-kind routing lives here so the cache digests it)."""
    if kind == "text":
        # Raw exhibit text with no SGML envelope (the private contracts-text corpus): the
        # file IS the document, so raw offsets are file offsets. EX-10 grammar via
        # the synthetic <TYPE>, exactly as the ex10 kind routes a real one.
        sub = load_text_submission(row["archive_path"], row["accession_number"], row.get("sequence"))
        return sub, sub.documents[0]
    sub = load_submission(control.rewrite_path(row["archive_path"]))
    if kind in ("ex10", "ex13") and row.get("sequence") is not None:
        return sub, next(d for d in sub.documents if d.sequence == row["sequence"])
    return sub, select_primary(sub.documents, row.get("submission_type", "10-K"))
