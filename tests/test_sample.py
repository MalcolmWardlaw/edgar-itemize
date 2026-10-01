"""examples/: the starter set (sample manifests and their hash parquets).

Network-free. The first group reads only the parquets in examples/ and manual/demo/bake.json.
The mirror-backed test parses the sample manifests from EDGAR_ITEMIZE_DATA_ROOT into a
temporary run and checks every output hash; it is skipped when the variable is unset or a
sample file is missing from the mirror (public CI has no mirror)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from edgar_itemize import cli, control
from edgar_itemize.conformance import HASH_SCHEMA

REPO = Path(__file__).resolve().parents[1]
EX = REPO / "examples"
DEMO_MANIFESTS = {"10k": EX / "sample_manifest_10k.parquet", "ex10": EX / "sample_manifest_ex10.parquet",
                  "ex13": EX / "sample_manifest_ex13.parquet"}
CONTRACTS = EX / "sample_manifest_contracts.parquet"
HASHES, HASHES_CONTRACTS = EX / "sample_hashes.parquet", EX / "sample_hashes_contracts.parquet"
MANIFEST_COLUMNS = ["accession_number", "cik", "sequence", "kind", "year", "submission_type", "agent_cik", "archive_path"]
ARCHIVE = re.compile(r"^archives/edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt$")
KIND_OF_FILE = {"10k": {"10k", "10q"}, "ex10": {"ex10"}, "ex13": {"ex13"}}


def _rows(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def _keys(rows: list[dict]) -> set:
    return {(r["accession_number"], r["sequence"]) for r in rows}


def _demo_rows() -> list[dict]:
    return [r for p in DEMO_MANIFESTS.values() for r in _rows(p)]


def _load_check_hashes():
    spec = importlib.util.spec_from_file_location("examples_check_hashes", EX / "check_hashes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_files_and_columns():
    for p in [*DEMO_MANIFESTS.values(), CONTRACTS]:
        assert p.exists(), p
        assert pq.read_schema(p).names == MANIFEST_COLUMNS, p
    for p in (HASHES, HASHES_CONTRACTS):
        assert p.exists(), p
        assert pq.read_schema(p).names == HASH_SCHEMA.names, p
        for r in _rows(p):
            assert re.fullmatch(r"[0-9a-f]{64}", r["input_sha256"]) and re.fullmatch(r"[0-9a-f]{64}", r["output_sha256"])
            assert r["input_bytes"] > 0 and r["n_nodes"] > 0


def test_manifest_kinds_match_their_parse_kind():
    for kind, p in DEMO_MANIFESTS.items():
        rows = _rows(p)
        assert rows and {r["kind"] for r in rows} <= KIND_OF_FILE[kind], p
    assert {r["kind"] for r in _rows(CONTRACTS)} == {"ex10"}
    for r in _demo_rows() + _rows(CONTRACTS):
        assert r["sequence"] is not None and r["year"] is not None
        if r["kind"] in ("10k", "10q"):
            assert r["submission_type"] == {"10k": "10-K", "10q": "10-Q"}[r["kind"]]


def test_archive_paths_relative_and_well_formed():
    for r in _demo_rows() + _rows(CONTRACTS):
        m = ARCHIVE.match(r["archive_path"])
        assert m, r["archive_path"]
        assert m.group(1) == str(int(r["cik"])) and m.group(2) == r["accession_number"]
        assert not Path(r["archive_path"]).is_absolute()


def test_manifest_keys_equal_hash_keys():
    demo = _demo_rows()
    assert len(_keys(demo)) == len(demo) == 9
    assert _keys(demo) == _keys(_rows(HASHES))
    contracts = _rows(CONTRACTS)
    assert _keys(contracts) == _keys(_rows(HASHES_CONTRACTS))


def test_demo_accessions_in_sample():
    spec = json.loads((REPO / "manual" / "demo" / "bake.json").read_text())
    want = {(d["accession"], d["kind"]) for d in spec["docs"]}
    assert len(want) == 9
    assert {(r["accession_number"], r["kind"]) for r in _demo_rows()} == want
    for d in spec["docs"]:
        if "sequence" in d:
            assert (d["accession"], d["sequence"]) in _keys(_demo_rows())


def test_contracts_ten_distinct_and_include_demo_agreements():
    rows = _rows(CONTRACTS)
    assert len(rows) == 10
    assert len({r["accession_number"] for r in rows}) == 10
    assert len({r["cik"] for r in rows}) == 10  # no filer twice
    assert _keys(_rows(DEMO_MANIFESTS["ex10"])) <= _keys(rows)


def test_fetch_dry_run_counts_without_network(tmp_path, capsys):
    """`fetch --dry-run` against an empty data root: every row would be fetched, nothing is
    opened (dry run returns before any request)."""
    for p in [*DEMO_MANIFESTS.values(), CONTRACTS]:
        n = len(_rows(p))
        cli.main(["fetch", "--manifest", str(p), "--data-root", str(tmp_path), "--dry-run"])
        out = capsys.readouterr().out
        assert f"{n} manifest rows, 0 present, {n} would be fetched, 0 failed" in out
    assert not any(tmp_path.iterdir())


def _mirror_or_skip() -> Path:
    root = os.environ.get(control.DATA_ROOT_ENV)
    if not root:
        pytest.skip(f"{control.DATA_ROOT_ENV} not set (no EDGAR mirror)")
    root = Path(root)
    missing = [r["archive_path"] for r in _demo_rows() + _rows(CONTRACTS) if not (root / r["archive_path"]).is_file()]
    if missing:
        pytest.skip(f"{len(missing)} sample files not on the mirror, e.g. {missing[0]}")
    return root


def test_parse_sample_matches_hashes(tmp_path, monkeypatch):
    root = _mirror_or_skip()
    monkeypatch.setattr(control, "DATA_ROOT", root)
    monkeypatch.delenv(control.PREFIX_MAP_ENV, raising=False)
    check = _load_check_hashes()
    run = tmp_path / "sample_run"
    for kind, p in DEMO_MANIFESTS.items():
        cli.main(["parse", "--kind", kind, "--manifest", str(p), "--out", str(run), "--no-text"])
    contracts = tmp_path / "contracts_run"
    cli.main(["parse", "--kind", "ex10", "--manifest", str(CONTRACTS), "--out", str(contracts), "--no-text"])
    for run_dir, expected, n in ((run, HASHES, 9), (contracts, HASHES_CONTRACTS, 10)):
        exp = {(r["accession_number"], r["sequence"]): r for r in _rows(expected)}
        c = check.compare(check.run_hashes(run_dir), exp)
        assert c["ok"] and c["both"] == n and c["only_run"] == c["only_expected"] == 0, c
        assert c["input_equal"] == c["output_equal"] == n and c["output_differs"] == 0, c
        assert check.main(["--run", str(run_dir), "--expected", str(expected)]) == 0
