"""examples/: pull_items, coverage and check_hashes on a one-document run built from the
tests/data fixture. Offline; EDGAR_ITEMIZE_DATA_ROOT is never read (the tests pass their
own data root under tmp_path)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from edgar_itemize.conformance import HASH_SCHEMA, manifest_hashes
from edgar_itemize.pipeline import parse_document, result_rows
from edgar_itemize.sgml import load_text_submission
from edgar_itemize.writer import write_run

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "data" / "0001161697-11-000584_3.txt"
ACC, CIK = "0001161697-11-000584", "785968"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"examples_{name}", REPO / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def run(tmp_path):
    """(run_dir, data_root): the fixture parsed into a `text-2011` partition, and a mirror
    holding it at archives/edgar/data/<CIK>/<accession>.txt."""
    data_root = tmp_path / "data"
    raw = data_root / "archives" / "edgar" / "data" / CIK / f"{ACC}.txt"
    raw.parent.mkdir(parents=True)
    shutil.copy(FIXTURE, raw)
    sub = load_text_submission(raw, ACC, 3)
    nodes, doc, rej = result_rows(parse_document(sub, sub.documents[0], CIK), 2011, keep_text=False)
    run_dir = tmp_path / "run"
    write_run(run_dir, nodes, [doc], rej, part="text-2011")
    return run_dir, data_root


def test_pull_items_slices_raw_bytes(run, tmp_path, capsys):
    run_dir, data_root = run
    out = tmp_path / "items.parquet"
    s = _load("pull_items").main(["--run", str(run_dir), "--data-root", str(data_root), "--out", str(out),
                                  "--items", "ARTICLE 2,SECTION 2.02,ARTICLE 99"])
    assert s == dict(documents=1, rows=2, missing=0)
    assert "1 documents, 2 rows written, 0 files missing" in capsys.readouterr().out
    rows = pq.read_table(out).to_pylist()
    raw = FIXTURE.read_bytes()
    assert [r["label_canon"] for r in rows] == ["ARTICLE 2", "SECTION 2.02"]
    nodes = {n["label_canon"]: n for n in pq.read_table(run_dir / "nodes-text-2011.parquet").to_pylist()}
    for r in rows:
        assert (r["accession_number"], r["cik"], r["sequence"], r["ordinal"], r["covered_by"]) == (ACC, CIK, 3, 1, None)
        assert (r["raw_start"], r["raw_end"]) == (nodes[r["label_canon"]]["raw_start"], nodes[r["label_canon"]]["raw_end"])
        assert r["text"] == raw[r["raw_start"]:r["raw_end"]].decode("latin-1") and r["text"]


def test_pull_items_missing_file_and_filters(run, tmp_path):
    run_dir, data_root = run
    pull = _load("pull_items")
    s = pull.pull(run_dir, tmp_path / "nowhere", ["ARTICLE 2"], tmp_path / "a.parquet")
    assert s == dict(documents=1, rows=0, missing=1)
    assert pull.pull(run_dir, data_root, ["ARTICLE 2"], tmp_path / "b.parquet", kind="10k")["documents"] == 0
    assert pull.pull(run_dir, data_root, ["ARTICLE 2"], tmp_path / "c.parquet", grammar="contract")["rows"] == 1
    assert pull.pull(run_dir, data_root, ["ARTICLE 2"], tmp_path / "d.parquet", limit=0)["documents"] == 0


def test_coverage_one_group(run, tmp_path):
    run_dir, _ = run
    csv_out = tmp_path / "cov.csv"
    rows = _load("coverage").main(["--run", str(run_dir), "--csv", str(csv_out)])
    n_nodes = pq.read_metadata(run_dir / "nodes-text-2011.parquet").num_rows
    assert len(rows) == 1
    r = rows[0]
    assert (r["year"], r["documents"], r["errors"], r["core_complete"], r["toc_share"]) == (2011, 1, 0, 0.0, 0.0)
    assert r["nodes_per_doc"] == n_nodes and r["items_per_doc"] == 0
    assert csv_out.read_text().splitlines()[0].startswith("year,documents,errors,core_complete,item_1,")


def test_coverage_credits():
    cov = _load("coverage")
    nodes = [dict(level_kind="item", label_canon="ITEM 1", raw_start=10, rule_ids=["multi.ITEM 2"], covers_items=["2"]),
             dict(level_kind="item", label_canon="ITEM 3", raw_start=20, rule_ids=[], covers_items=None),
             dict(level_kind="item", label_canon="ITEM 4", raw_start=30, rule_ids=["omit.stmt"], covers_items=None),
             dict(level_kind="item", label_canon="ITEM 3", raw_start=40, rule_ids=[], covers_items=None),
             dict(level_kind="heading", label_canon=None, raw_start=50, rule_ids=[], satisfies_item="ITEM 8"),
             dict(level_kind="heading", label_canon=None, raw_start=60, rule_ids=[], satisfies_item="ITEM 8")]
    count, first = cov.credits(nodes)
    assert dict(count) == {"1": 1, "2": 1, "3": 2, "8": 1}
    assert first == {"1": 10, "2": 10, "3": 20, "8": 50}


def _write_expected(run_dir: Path, path: Path) -> list[dict]:
    rows = manifest_hashes(run_dir, "text").to_pylist()
    pq.write_table(pa.Table.from_pylist(rows, schema=HASH_SCHEMA), path)
    return rows


def test_check_hashes_equal_then_mismatch(run, tmp_path, capsys):
    run_dir, _ = run
    ch = _load("check_hashes")
    exp = tmp_path / "expected.parquet"
    rows = _write_expected(run_dir, exp)
    assert rows[0]["input_sha256"] and rows[0]["output_sha256"]
    assert ch.main(["--run", str(run_dir), "--expected", str(exp)]) == 0
    out = capsys.readouterr().out
    assert "1 in both, 0 only in run, 0 only in expected; input 1 equal" in out and "output 1 equal, 0 differs" in out

    rows[0]["output_sha256"] = "0" * 64
    rows.append(dict(rows[0], accession_number="0000000000-00-000000"))
    pq.write_table(pa.Table.from_pylist(rows, schema=HASH_SCHEMA), exp)
    assert ch.main(["--run", str(run_dir), "--expected", str(exp)]) == 1
    out = capsys.readouterr().out
    assert "1 only in expected" in out and "output 0 equal, 1 differs (1 with equal input)" in out
    assert f"{ACC} seq=3 input equal" in out and out.rstrip().endswith("FAIL")


def test_check_hashes_input_differs_does_not_fail_but_unknown_input_uses_data_root(run, tmp_path):
    run_dir, data_root = run
    ch = _load("check_hashes")
    exp = tmp_path / "expected.parquet"
    rows = _write_expected(run_dir, exp)
    good = dict(rows[0])
    rows[0].update(input_sha256="f" * 64, output_sha256="0" * 64)  # different input: reported, not failing
    exp_rows = {(r["accession_number"], r["sequence"]): r for r in rows}
    c = ch.compare(ch.run_hashes(run_dir), exp_rows)
    assert (c["input_differs"], c["output_differs"], c["failing"], c["ok"]) == (1, 1, 0, False)  # nothing compared on equal input
    # a pre-1.0.0 run without documents.input_sha256: the input hash is recomputed from the mirror
    t = pq.read_table(run_dir / "documents-text-2011.parquet").drop_columns(["input_sha256", "input_bytes"])
    pq.write_table(t, run_dir / "documents-text-2011.parquet")
    exp_rows = {(good["accession_number"], good["sequence"]): good}
    assert ch.compare(ch.run_hashes(run_dir), exp_rows)["input_unknown"] == 1
    c = ch.compare(ch.run_hashes(run_dir, data_root), exp_rows)
    assert (c["input_equal"], c["output_equal"], c["ok"]) == (1, 1, True)
