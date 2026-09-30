from edgar_itemize.sgml import blank_ix_header, split_documents

SUB = (
    "-----BEGIN PRIVACY-ENHANCED MESSAGE-----\nProc-Type: 2001,MIC-CLEAR\n\n"
    "<IMS-DOCUMENT>0000000001-95-000001.txt : 19950101\n<IMS-HEADER>0000000001-95-000001.hdr.sgml : 19950101\n"
    "ACCESSION NUMBER:\t\t0000000001-95-000001\nCONFORMED SUBMISSION TYPE:\t10-K\n</IMS-HEADER>\n"
    "<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<DESCRIPTION>ANNUAL REPORT\n<TEXT>\n\n<PAGE>\nPART I\n\nITEM 1. BUSINESS\nText.\n</TEXT>\n</DOCUMENT>\n"
    "<DOCUMENT>\n<TYPE>EX-10.1\n<SEQUENCE>2\n<FILENAME>ex10.htm\n<DESCRIPTION>CREDIT AGREEMENT\n<TEXT>\n<html><body><p>ARTICLE I</p></body></html>\n</TEXT>\n</DOCUMENT>\n"
    "</IMS-DOCUMENT>\n"
)


def test_split_documents_offsets_and_flags():
    docs = split_documents(SUB)
    assert [d.type for d in docs] == ["10-K", "EX-10.1"]
    assert docs[0].sequence == 1 and docs[0].filename is None and not docs[0].is_html
    assert docs[1].filename == "ex10.htm" and docs[1].is_html
    for d in docs:
        assert SUB[d.doc_start : d.doc_start + 10] == "<DOCUMENT>"
        assert SUB[d.doc_end - 11 : d.doc_end] == "</DOCUMENT>"
        assert SUB[d.text_start - 6 : d.text_start] == "<TEXT>"
        assert SUB[d.text_end : d.text_end + 7] == "</TEXT>"


def test_blank_ix_header_preserves_length():
    html = "<html><div style='display:none'><ix:header><ix:hidden>x</ix:hidden></ix:header></div><p>Item 1.</p></html>"
    out = blank_ix_header(html)
    assert len(out) == len(html)
    assert "ix:hidden" not in out and "<p>Item 1.</p>" in out
    assert out.index("<p>Item 1.</p>") == html.index("<p>Item 1.</p>")
