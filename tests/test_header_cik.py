"""D9 (docs/RELEASE_PLAN.md section 9): the `cik` on output rows is the SGML header's first
FILER CENTRAL INDEX KEY, so it is a function of the input bytes; the manifest's CIK is kept
as `documents.manifest_cik`. Headerless input (`--kind text`) keeps the manifest value."""

from pathlib import Path

from edgar_itemize.pipeline import parse_document, result_rows
from edgar_itemize.prepare import prepare_document
from edgar_itemize.schema import DOC_SCHEMA, NODE_SCHEMA, REJECTED_SCHEMA
from edgar_itemize.sgml import Submission, header_cik, load_text_submission, split_documents, header_text

FIXTURE = Path(__file__).parent / "data" / "0001161697-11-000584_3.txt"

SEC_HEADER = (
    "0000353944-94-000005.hdr.sgml : 19950612\n"
    "ACCESSION NUMBER:\t\t0000353944-94-000005\n"
    "CONFORMED SUBMISSION TYPE:\t10-K\n"
    "PUBLIC DOCUMENT COUNT:\t\t9\n\n"
    "FILER:\n\n\tCOMPANY DATA:\t\n"
    "\t\tCOMPANY CONFORMED NAME:\t\t\tINTERNATIONAL GAME TECHNOLOGY\n"
    "\t\tCENTRAL INDEX KEY:\t\t\t0000353944\n"
    "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\t3990\n\n"
    "\tFILING VALUES:\n\t\tFORM TYPE:\t\t10-K\n\t\tSEC FILE NUMBER:\t001-10684\n"
)

MULTI_FILER_HEADER = (
    "ACCESSION NUMBER:\t\t0001193125-20-000001\nCONFORMED SUBMISSION TYPE:\t10-K\n\n"
    "FILER:\n\n\tCOMPANY DATA:\t\n\t\tCOMPANY CONFORMED NAME:\t\t\tPARENT HOLDINGS INC\n"
    "\t\tCENTRAL INDEX KEY:\t\t\t0001080099\n\n"
    "FILER:\n\n\tCOMPANY DATA:\t\n\t\tCOMPANY CONFORMED NAME:\t\t\tPARENT OPERATING LLC\n"
    "\t\tCENTRAL INDEX KEY:\t\t\t0000005187\n\n"
    "FILER:\n\n\tCOMPANY DATA:\t\n\t\tCOMPANY CONFORMED NAME:\t\t\tPARENT FINANCE CORP\n"
    "\t\tCENTRAL INDEX KEY:\t\t\t0001837671\n"
)


def test_header_cik_normal_header():
    assert header_cik(SEC_HEADER) == "353944"


def test_header_cik_multi_filer_first_wins():
    assert header_cik(MULTI_FILER_HEADER) == "1080099"


def test_header_cik_strips_zero_padding_like_the_manifest():
    assert header_cik("CENTRAL INDEX KEY:\t\t\t0000005187\n") == "5187"
    assert header_cik("CENTRAL INDEX KEY: 0001837671\n") == "1837671"
    assert header_cik("CENTRAL INDEX KEY: 353944\n") == "353944"  # already unpadded
    assert header_cik("CENTRAL INDEX KEY: 0000000000\n") == "0"


def test_header_cik_ims_header_variant():
    sub = (
        "<IMS-DOCUMENT>0000000001-95-000001.txt : 19950101\n"
        "<IMS-HEADER>0000000001-95-000001.hdr.sgml : 19950101\n"
        "ACCESSION NUMBER:\t\t0000000001-95-000001\nCONFORMED SUBMISSION TYPE:\t10-K\n"
        "FILER:\n\tCOMPANY DATA:\n\t\tCOMPANY CONFORMED NAME:\t\tOLD CO\n\t\tCENTRAL INDEX KEY:\t\t0000000042\n"
        "</IMS-HEADER>\n<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<TEXT>\nx\n</TEXT>\n</DOCUMENT>\n</IMS-DOCUMENT>\n"
    )
    hdr = header_text(sub)
    assert "IMS-HEADER" not in hdr and header_cik(hdr) == "42"


def test_header_cik_missing_is_none():
    assert header_cik("") is None
    assert header_cik("ACCESSION NUMBER:\t\t0000000001-95-000001\nCONFORMED SUBMISSION TYPE:\t10-K\n") is None
    assert header_cik("CENTRAL INDEX KEY:\t\t\n") is None  # label with no digits


def test_submission_property_and_text_submission_none():
    sub = Submission(path=Path("x.txt"), text="", header=SEC_HEADER, documents=[])
    assert sub.header_cik == "353944"
    text_sub = load_text_submission(FIXTURE, "0001161697-11-000584", 3)
    assert text_sub.header == "" and text_sub.header_cik is None


# --- the pipeline stamping ---------------------------------------------------------------

def _synthetic_submission(header: str, accession: str) -> Submission:
    body = (
        "<PAGE>\nPART I\n\nITEM 1. BUSINESS\nWe make things. " + ("Text. " * 40) + "\n\n"
        "ITEM 1A. RISK FACTORS\nRisks. " + ("Text. " * 40) + "\n\n"
        "ITEM 2. PROPERTIES\nBuildings. " + ("Text. " * 40) + "\n\n"
        "PART II\n\nITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\nMD&A. " + ("Text. " * 40) + "\n\n"
        "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\nNumbers. " + ("Text. " * 40) + "\n"
    )
    text = (
        f"<SEC-DOCUMENT>{accession}.txt : 20200301\n<SEC-HEADER>{accession}.hdr.sgml : 20200301\n"
        f"{header}</SEC-HEADER>\n<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<FILENAME>form10k.txt\n"
        f"<TEXT>\n{body}</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>\n"
    )
    return Submission(path=Path(f"{accession}.txt"), text=text, header=header_text(text),
                      documents=split_documents(text), input_sha256="0" * 64, input_bytes=len(text))


def test_pipeline_stamps_header_cik_and_keeps_manifest_cik():
    """The manifest lists the file under the co-registrant directory 5187 (the second FILER);
    the stamped cik is the first FILER's, 1080099, and manifest_cik records 5187."""
    sub = _synthetic_submission(MULTI_FILER_HEADER, "0001193125-20-000001")
    doc = sub.documents[0]
    manifest_cik = "5187"
    prep = prepare_document(sub, doc, manifest_cik)
    assert prep.header_cik == "1080099"
    r = parse_document(sub, doc, manifest_cik)
    assert r.cik == "1080099" and r.manifest_cik == "5187"
    assert r.profile.agent_cik == "0001193125"  # untouched: still the accession prefix
    nodes, d, rej = result_rows(r, 2020, keep_text=False)
    assert d["cik"] == "1080099" and d["manifest_cik"] == "5187" and d["agent_cik"] == "0001193125"
    assert nodes and all(n["cik"] == "1080099" for n in nodes)
    assert all(n["agent_cik"] == "0001193125" for n in nodes)
    assert not any("cik" in x for x in rej)  # rejected rows never carried a cik
    assert set(d) == set(DOC_SCHEMA.names)
    assert set(nodes[0]) == set(NODE_SCHEMA.names)
    assert rej == [] or set(rej[0]) == set(REJECTED_SCHEMA.names)


def test_pipeline_manifest_cik_equals_cik_when_they_agree():
    sub = _synthetic_submission(SEC_HEADER, "0000353944-94-000005")
    r = parse_document(sub, sub.documents[0], 353944)  # manifests may pass an int
    _nodes, d, _rej = result_rows(r, 1993, keep_text=False)
    assert d["cik"] == d["manifest_cik"] == "353944"


def test_pipeline_falls_back_to_manifest_cik_without_a_header():
    sub = _synthetic_submission("ACCESSION NUMBER:\t\t0000353944-94-000005\nCONFORMED SUBMISSION TYPE:\t10-K\n",
                                "0000353944-94-000005")
    r = parse_document(sub, sub.documents[0], "353944")
    _nodes, d, _rej = result_rows(r, 1993, keep_text=False)
    assert d["cik"] == "353944" and d["manifest_cik"] == "353944"


def test_text_kind_keeps_manifest_cik():
    """The --kind text input: no SGML envelope, so cik is the manifest's on every row."""
    sub = load_text_submission(FIXTURE, "0001161697-11-000584", 3)
    r = parse_document(sub, sub.documents[0], "785968")
    nodes, d, _rej = result_rows(r, 2011, keep_text=False)
    assert r.cik == "785968" and d["cik"] == "785968" and d["manifest_cik"] == "785968"
    assert nodes and all(n["cik"] == "785968" for n in nodes)


def test_manifest_cik_is_the_last_documents_column():
    assert DOC_SCHEMA.names[-3:] == ["input_sha256", "input_bytes", "manifest_cik"]
    assert "manifest_cik" not in NODE_SCHEMA.names and "manifest_cik" not in REJECTED_SCHEMA.names


def test_error_rows_carry_manifest_cik_twice(tmp_path):
    """An error row (file missing) has cik = manifest_cik = the manifest's value."""
    from edgar_itemize.cli import _parse_one_cached

    row = dict(accession_number="0000000001-20-000001", cik="5187", year=2020, submission_type="10-K",
               archive_path=str(tmp_path / "missing" / "0000000001-20-000001.txt"))
    nodes, d, rej, status = _parse_one_cached(row, "10k", False, None)
    assert nodes == [] and rej == [] and status == "off"
    assert d["cik"] == "5187" and d["manifest_cik"] == "5187" and d["error"]
