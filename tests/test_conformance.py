"""conformance.py: the canonical output hash, the deterministic draw, and `verify`.

Everything here runs offline on the tests/data fixture and synthetic tables; no data root
is needed (the verify tests build their own under tmp_path).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from edgar_itemize import __version__, control
from edgar_itemize.conformance import (EMPTY_DOCUMENT_HASH, EXPECTED_SCHEMA, MANIFEST_SCHEMA, build_expected, canonical_bytes,
                                       document_hash, draw_key, fixture_rows, largest_remainder, manifest_hashes, select_rows,
                                       verify_set)
from edgar_itemize.pipeline import parse_document, result_rows
from edgar_itemize.sgml import load_text_submission
from edgar_itemize.writer import write_run

FIXTURE = Path(__file__).parent / "data" / "0001161697-11-000584_3.txt"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def fixture_rows_parsed():
    sub = load_text_submission(FIXTURE, "0001161697-11-000584", 3)
    r = parse_document(sub, sub.documents[0], "785968")
    nodes, doc, rej = result_rows(r, 2011, keep_text=False)
    assert nodes and not rej, "the fixture parses clean; rejected rows are synthesised below"
    # Two rejected rows in REJECTED_SCHEMA shape so the rejected half of the hash is exercised
    # (same raw_start on purpose: the sort must still be a total order).
    rej = [dict(accession_number=doc["accession_number"], sequence=3, block_idx=7, kind="section", label_canon="SECTION 2",
                score=0.25, reason="toc", raw_start=120, text="Section 2 ... 4"),
           dict(accession_number=doc["accession_number"], sequence=3, block_idx=9, kind="article", label_canon="ARTICLE I",
                score=-0.5, reason="dup_label", raw_start=120, text="Article I \u00e9")]
    return nodes, doc, rej


# ---------------------------------------------------------------------------
# document_hash
# ---------------------------------------------------------------------------


def test_hash_is_hex_sha256_and_empty_constant():
    assert document_hash([], []) == EMPTY_DOCUMENT_HASH
    assert len(EMPTY_DOCUMENT_HASH) == 64 and int(EMPTY_DOCUMENT_HASH, 16)


def test_hash_stable_across_row_and_key_order(fixture_rows_parsed):
    nodes, _doc, rej = fixture_rows_parsed
    h = document_hash(nodes, rej)
    shuffled_nodes = list(reversed(nodes))
    shuffled_rej = rej[1:] + rej[:1]
    rekeyed = [dict(sorted(r.items(), reverse=True)) for r in shuffled_nodes]
    assert document_hash(rekeyed, shuffled_rej) == h
    # the canonical bytes are ASCII lines: nodes header, node rows, rejected header, rows
    b = canonical_bytes(nodes, rej)
    lines = b.decode("ascii").split("\n")
    assert lines[0] == "nodes" and "rejected" in lines and lines[-1] == ""
    assert lines.index("rejected") == 1 + len(nodes)
    assert len(lines) == 3 + len(nodes) + len(rej)


def test_hash_ignores_version_columns_only(fixture_rows_parsed):
    nodes, _doc, rej = fixture_rows_parsed
    h = document_hash(nodes, rej)
    restamped = [dict(n, parser_version="9.9.9", normalizer_version="9.9.9") for n in nodes]
    assert document_hash(restamped, rej) == h
    for col, new in (("title", "changed"), ("raw_start", 1), ("confidence", 0.123), ("rule_ids", ["x"]), ("path", [1]), ("meta", 0)):
        changed = [dict(n) for n in nodes]
        changed[0][col] = new
        assert document_hash(changed, rej) != h, col
    rj = [dict(x) for x in rej]
    rj[0]["reason"] = "changed"
    assert document_hash(nodes, rj) != h
    assert document_hash(nodes, rej[:-1]) != h
    assert document_hash(nodes[:-1], rej) != h


def test_hash_same_from_parser_rows_and_parquet_rows(tmp_path, fixture_rows_parsed):
    """Rows straight out of result_rows and the same rows read back from a run directory
    hash identically (float32 confidence/score normalised through the schema), and
    manifest_hashes reports them."""
    nodes, doc, rej = fixture_rows_parsed
    write_run(tmp_path, nodes, [doc], rej, part="text")
    back_n = pq.read_table(tmp_path / "nodes-text.parquet").to_pylist()
    back_r = pq.read_table(tmp_path / "rejected-text.parquet").to_pylist()
    assert document_hash(back_n, back_r) == document_hash(nodes, rej)
    t = manifest_hashes(tmp_path, "text")
    assert t.num_rows == 1
    row = t.to_pylist()[0]
    assert row["output_sha256"] == document_hash(nodes, rej)
    assert row["n_nodes"] == len(nodes) and row["n_rejected"] == len(rej)
    assert row["input_sha256"] == doc["input_sha256"] and row["input_bytes"] == doc["input_bytes"]


def test_manifest_hashes_error_row_and_missing_input_columns(tmp_path, fixture_rows_parsed):
    nodes, doc, rej = fixture_rows_parsed
    err = dict(accession_number="0000000000-00-000000", cik="0", sequence=None, error="boom")
    write_run(tmp_path, nodes, [doc, err], rej, part="text")
    # a pre-R0 run: drop the two input columns
    t = pq.read_table(tmp_path / "documents-text.parquet").drop_columns(["input_sha256", "input_bytes"])
    pq.write_table(t, tmp_path / "documents-text.parquet")
    h = manifest_hashes(tmp_path, "text").to_pylist()
    by = {r["accession_number"]: r for r in h}
    assert by["0000000000-00-000000"]["output_sha256"] is None and by["0000000000-00-000000"]["n_nodes"] == 0
    assert by["0001161697-11-000584"]["output_sha256"] == document_hash(nodes, rej)
    assert all(r["input_sha256"] is None and r["input_bytes"] is None for r in h)


# ---------------------------------------------------------------------------
# the draw
# ---------------------------------------------------------------------------


def test_largest_remainder():
    assert largest_remainder({"a": 50, "b": 30, "c": 20}, 10) == {"a": 5, "b": 3, "c": 2}
    assert largest_remainder({"a": 5, "b": 5, "c": 5}, 5) == {"a": 2, "b": 2, "c": 1}
    assert sum(largest_remainder({"a": 7, "b": 1, "c": 1}, 8).values()) == 8
    assert largest_remainder({"a": 1, "b": 100}, 50) == {"a": 0, "b": 50}  # remainders .495 < .5
    assert largest_remainder({"a": 1, "b": 99}, 50) == {"a": 1, "b": 49}  # equal remainders: smaller key first
    assert largest_remainder({"a": 3}, 10) == {"a": 3}
    assert largest_remainder({}, 10) == {}


def _synthetic(n_per_era=(("text", 40), ("html_early", 25), ("ixbrl", 10)), years=(2001, 2002), with_seq=False):
    """A tiny manifest + documents pair with a few error rows and one manifest row without a
    documents row."""
    man, docs = [], []
    i = 0
    for era, n in n_per_era:
        for j in range(n):
            i += 1
            acc = f"{1000 + i:010d}-{years[j % len(years)] % 100:02d}-{i:06d}"
            cik = str(100 + i)
            man.append(dict(accession_number=acc, cik=cik, submission_type="10-K", year=years[j % len(years)],
                            archive_path=f"/somewhere/{cik}/{acc}.txt", **({"sequence": 2} if with_seq else {})))
            docs.append(dict(accession_number=acc, sequence=2 if with_seq else 1, profile_era=era,
                             error="ValueError: x" if j % 9 == 8 else None))
    man.append(dict(accession_number="0000000009-01-000009", cik="9", submission_type="10-K", year=2001,
                    archive_path="/nowhere", **({"sequence": 2} if with_seq else {})))
    return pa.Table.from_pylist(man), pa.Table.from_pylist(docs)


def test_select_rows_deterministic_and_stratified():
    m, d = _synthetic()
    rows, strata = select_rows(m, d, "10k", 20)
    rows2, strata2 = select_rows(m, d, "10k", 20)
    assert rows == rows2 and strata == strata2
    assert len(rows) == 20
    # population excludes the 7 error rows (j % 9 == 8: 4 + 2 + 1) and the manifest row with no document row
    assert sum(v["population"] for v in strata.values()) == 75 - 7
    assert sum(v["drawn"] for v in strata.values()) == 20
    assert all(v["drawn"] <= v["population"] for v in strata.values())
    assert set(strata) == {(e, y) for e in ("text", "html_early", "ixbrl") for y in (2001, 2002)}
    # proportional: the largest stratum gets the most
    big = max(strata, key=lambda k: strata[k]["population"])
    assert strata[big]["drawn"] == max(v["drawn"] for v in strata.values())
    # rows: schema, relative archive path, sequence from the documents table
    t = pa.Table.from_pylist(rows, schema=MANIFEST_SCHEMA)
    assert t.num_rows == 20
    r = rows[0]
    assert r["archive_path"] == f"archives/edgar/data/{int(r['cik'])}/{r['accession_number']}.txt"
    assert r["sequence"] == 1 and r["kind"] == "10k" and r["profile_era"] in ("text", "html_early", "ixbrl")
    assert rows == sorted(rows, key=lambda r: (r["accession_number"], r["sequence"]))
    # the bottom-k rule: inside a stratum, every chosen key is <= every unchosen key
    chosen = {r["accession_number"] for r in rows}
    for (era, year) in strata:
        pool = [x["accession_number"] for x, dd in zip(m.to_pylist(), d.to_pylist() + [dict(profile_era=None, error=None)])
                if dd["error"] is None and dd["profile_era"] == era and x["year"] == year]
        ks_in = sorted(draw_key(a, 1) for a in pool if a in chosen)
        ks_out = sorted(draw_key(a, 1) for a in pool if a not in chosen)
        if ks_in and ks_out:
            assert ks_in[-1] < ks_out[0]


def test_select_rows_seed_and_sequence_join():
    m, d = _synthetic(with_seq=True)
    a, _ = select_rows(m, d, "ex10", 10)
    b, _ = select_rows(m, d, "ex10", 10, seed="another")
    assert {r["accession_number"] for r in a} != {r["accession_number"] for r in b}
    assert all(r["sequence"] == 2 for r in a)


def test_fixture_rows():
    rows = fixture_rows(REPO)
    assert any(r["accession_number"] == "0001161697-11-000584" and r["sequence"] == 3 for r in rows)
    assert all(r["kind"] == "fixtures" and not Path(r["archive_path"]).is_absolute() for r in rows)
    assert all((REPO / r["archive_path"]).exists() for r in rows)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def _make_set(tmp_path: Path) -> tuple[Path, Path]:
    """A two-document set under tmp_path: kind `text` rows resolved against a data root.
    The second document is the fixture with a heading appended so its hashes differ."""
    data_root = tmp_path / "data"
    p1 = data_root / "archives/edgar/data/785968/0001161697-11-000584.txt"
    p2 = data_root / "archives/edgar/data/785968/0001161697-11-000585.txt"
    p1.parent.mkdir(parents=True)
    shutil.copy(FIXTURE, p1)
    p2.write_bytes(FIXTURE.read_bytes() + b"\n\n     SECTION 99.  EXTRA\n\n     Some text.\n")
    rows = [dict(accession_number="0001161697-11-000584", cik="785968", sequence=3, kind="text", year=2011,
                 archive_path="archives/edgar/data/785968/0001161697-11-000584.txt", submission_type=None, profile_era="text"),
            dict(accession_number="0001161697-11-000585", cik="785968", sequence=3, kind="text", year=2011,
                 archive_path="archives/edgar/data/785968/0001161697-11-000585.txt", submission_type=None, profile_era="text")]
    exp = build_expected(rows, data_root, None, workers=1)
    assert all(e["error"] is None for e in exp)
    assert exp[0]["output_sha256"] != exp[1]["output_sha256"] and exp[0]["input_sha256"] != exp[1]["input_sha256"]
    set_dir = tmp_path / "conformance" / "9.9.9"
    set_dir.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=MANIFEST_SCHEMA), set_dir / "manifest.parquet")
    pq.write_table(pa.Table.from_pylist([{k: e[k] for k in EXPECTED_SCHEMA.names} for e in exp], schema=EXPECTED_SCHEMA), set_dir / "expected.parquet")
    return set_dir, data_root


def test_verify_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", None)  # nothing may fall back to a real mirror
    set_dir, data_root = _make_set(tmp_path)
    res = verify_set(set_dir, data_root=data_root, workers=1)
    assert res.ok and (res.documents, res.input_ok, res.output_ok) == (2, 2, 2)
    assert res.input_differs == res.input_missing == res.output_differs == 0 and res.failures == []
    assert res.line() == "verify 9.9.9: 2 documents, 2 input ok, 0 input differs, 0 input missing, 2 output ok, 0 output differs"
    d = res.as_dict()
    assert d["ok"] and d["parser_version"] == __version__ and json.dumps(d)


def test_verify_input_differs_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", None)
    set_dir, data_root = _make_set(tmp_path)
    p1 = data_root / "archives/edgar/data/785968/0001161697-11-000584.txt"
    p1.write_bytes(p1.read_bytes() + b"x")
    res = verify_set(set_dir, data_root=data_root, workers=1)
    assert not res.ok
    assert (res.input_ok, res.input_differs, res.input_missing, res.output_ok, res.output_differs) == (1, 1, 0, 1, 0)
    assert res.failures[0]["category"] == "input differs" and res.failures[0]["accession_number"] == "0001161697-11-000584"
    p1.unlink()
    res = verify_set(set_dir, data_root=data_root, workers=1)
    assert res.ok, "a missing input is reported but does not fail the check"
    assert (res.input_ok, res.input_differs, res.input_missing, res.output_ok, res.output_differs) == (1, 0, 1, 1, 0)
    assert res.failures[0]["category"] == "input missing"


def test_verify_output_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", None)
    set_dir, data_root = _make_set(tmp_path)
    t = pq.read_table(set_dir / "expected.parquet").to_pylist()
    t[1]["output_sha256"] = "0" * 64
    pq.write_table(pa.Table.from_pylist(t, schema=EXPECTED_SCHEMA), set_dir / "expected.parquet")
    res = verify_set(set_dir, data_root=data_root, workers=1)
    assert not res.ok
    assert (res.input_ok, res.input_differs, res.input_missing, res.output_ok, res.output_differs) == (2, 0, 0, 1, 1)
    f = res.failures[0]
    assert f["category"] == "output differs" and f["accession_number"] == "0001161697-11-000585"
    assert f["expected_sha256"] == "0" * 64 and f["got_sha256"] != "0" * 64 and f["got_n_nodes"] == f["expected_n_nodes"]


def test_verify_cli_exit_codes(tmp_path, monkeypatch, capsys):
    from edgar_itemize.cli import main

    monkeypatch.setattr(control, "DATA_ROOT", None)
    set_dir, data_root = _make_set(tmp_path)
    with pytest.raises(SystemExit) as e:
        main(["verify", "--set", str(set_dir), "--data-root", str(data_root)])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("verify 9.9.9: 2 documents, 2 input ok")
    t = pq.read_table(set_dir / "expected.parquet").to_pylist()
    t[0]["output_sha256"] = "0" * 64
    pq.write_table(pa.Table.from_pylist(t, schema=EXPECTED_SCHEMA), set_dir / "expected.parquet")
    with pytest.raises(SystemExit) as e:
        main(["verify", "--set", str(set_dir), "--data-root", str(data_root), "--json"])
    assert e.value.code == 1
    d = json.loads(capsys.readouterr().out)
    assert d["output_differs"] == 1 and d["failures"][0]["category"] == "output differs"


def test_verify_fixture_kind_resolves_under_repo(tmp_path, monkeypatch):
    """A `fixtures` row resolves against the repository root (the set's grandparent by
    default, or --repo-root), never the data root."""
    monkeypatch.setattr(control, "DATA_ROOT", None)
    rows = fixture_rows(REPO)
    exp = build_expected(rows, None, REPO, workers=1)
    set_dir = tmp_path / "conformance" / "x"
    set_dir.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=MANIFEST_SCHEMA), set_dir / "manifest.parquet")
    pq.write_table(pa.Table.from_pylist([{k: e[k] for k in EXPECTED_SCHEMA.names} for e in exp], schema=EXPECTED_SCHEMA), set_dir / "expected.parquet")
    res = verify_set(set_dir, data_root=None, repo_root=REPO, workers=1)
    assert res.ok and res.output_ok == len(rows)
    res = verify_set(set_dir, data_root=None, workers=1)  # tmp_path is not the repo
    assert res.input_missing == len(rows) and res.ok


def test_rewrite_path_relative_joins_data_root(monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", Path("/root/of/mirror"))
    assert control.rewrite_path("archives/edgar/data/1/x.txt") == Path("/root/of/mirror/archives/edgar/data/1/x.txt")
    assert control.rewrite_path("/abs/elsewhere/x.txt") == Path("/abs/elsewhere/x.txt")
