"""The raw-text input adapter (`load_text_submission`, CLI `--kind text`).

Fixture: one small exhibit copied verbatim out of a private loan-contracts corpus of
raw exhibit text (`--kind text`; not published; file `000078/785968/` under the corpus
root), which is plain text with no SGML envelope.
"""

from pathlib import Path

from edgar_itemize.pipeline import parse_document, result_rows
from edgar_itemize.sgml import load_text_submission

FIXTURE = Path(__file__).parent / "data" / "0001161697-11-000584_3.txt"


def _parse():
    sub = load_text_submission(FIXTURE, "0001161697-11-000584", 3)
    return sub, parse_document(sub, sub.documents[0], "785968")


def test_text_submission_wraps_whole_file():
    sub = load_text_submission(FIXTURE, "0001161697-11-000584", 3)
    (doc,) = sub.documents
    assert sub.accession == "0001161697-11-000584"
    assert doc.sequence == 3 and doc.type == "EX-10" and not doc.is_html
    assert (doc.text_start, doc.text_end) == (0, len(sub.text))
    assert sub.text == FIXTURE.read_bytes().decode("latin-1")


def test_contract_grammar_and_section_nodes():
    _sub, r = _parse()
    assert r.grammar == "contract" and r.profile.era == "text"
    sections = [n for n in r.nodes if n.level_kind == "section"]
    articles = [n for n in r.nodes if n.level_kind == "article"]
    assert len(sections) >= 5 and len(articles) >= 1
    assert any(n.label_canon and n.label_canon.startswith("SECTION") for n in sections)


def test_raw_offsets_are_file_offsets():
    """The point of the adapter: a node's raw offset indexes the file directly, so
    an absolute 1-indexed line number in the file is an absolute line in the parse."""
    sub, r = _parse()
    lines = sub.text.split("\n")
    starts, off = [], 0
    for ln in lines:
        starts.append(off)
        off += len(ln) + 1
    n = next(x for x in r.nodes if x.level_kind == "section" and x.label_canon)
    assert 0 <= n.head_raw_start < len(sub.text)
    i = max(k for k, s in enumerate(starts) if s <= n.head_raw_start)
    number = n.label_canon.split()[-1].lstrip("0") or "0"
    assert number.split(".")[0] in lines[i]


def test_result_rows_shape():
    _sub, r = _parse()
    nodes, doc, rejected = result_rows(r, 2011, keep_text=False)
    assert doc["accession_number"] == "0001161697-11-000584" and doc["sequence"] == 3
    assert doc["grammar"] == "contract" and doc["filed_year"] == 2011 and doc["error"] is None
    assert doc["normalized_text"] is None
    assert len(nodes) == len(r.nodes) and isinstance(rejected, list)
