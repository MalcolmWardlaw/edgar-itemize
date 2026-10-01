"""The static demo (manual/demo/): scripts/demo_bake.py writes exactly what the viewer app
serves; the shim's rewrite table covers the viewer's per-document calls; the build hook
adds only the shim to the viewer page; the shipped data matches its manifest. Offline: the
bake runs on the tests/data fixture under tmp_path."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "data" / "0001161697-11-000584_3.txt"
ACC, CIK, SEQ = "0001161697-11-000584", "785968", 3
DEMO = REPO / "manual" / "demo"
VIEWER = REPO / "src" / "edgar_itemize" / "viewer" / "static" / "index.html"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _routes() -> list[dict]:
    src = (DEMO / "shim.js").read_text(encoding="utf-8")
    m = re.search(r"/\*ROUTES\*/(.*?)/\*END\*/", src, re.S)
    assert m, "shim.js has no /*ROUTES*/ ... /*END*/ table"
    return json.loads(m.group(1))


def _route(path: str) -> tuple[dict, str | None] | None:
    for r in _routes():
        m = re.search(r["re"], path)
        if m:
            return r, (m.group(1) if m.groups() else None)
    return None


@pytest.fixture()
def baked(tmp_path, monkeypatch):
    """The fixture parsed into a one-document text run, baked into tmp_path/out; returns
    (out, data_root, run_dir, bake module)."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from edgar_itemize.pipeline import parse_document, result_rows
    from edgar_itemize.sgml import load_text_submission
    from edgar_itemize.writer import write_run

    data_root = tmp_path / "data"
    data_root.mkdir()
    raw = data_root / f"{ACC}_{SEQ}.txt"
    shutil.copy(FIXTURE, raw)
    sub = load_text_submission(raw, ACC, SEQ)
    nodes, doc, rej = result_rows(parse_document(sub, sub.documents[0], CIK), 2011, keep_text=False)
    run_dir = tmp_path / "fixture_run"
    write_run(run_dir, nodes, [doc], rej, part="text-2011")
    bake = _load(REPO / "scripts" / "demo_bake.py", "demo_bake")
    out = tmp_path / "out"
    before = _state()
    bake.bake([dict(kind="text", run=str(run_dir), accession=ACC, sequence=SEQ, note="fixture")], out, data_root,
              panel=[f"{ACC}_{SEQ}"])
    assert _state() == before  # the bake puts the environment and the app module back
    return out, data_root, run_dir, bake


def _state():
    import os

    from edgar_itemize import control
    from edgar_itemize.viewer import app as viewer

    env = tuple(os.environ.get(k) for k in ("EDGAR_ITEMIZE_RUNS", "EDGAR_ITEMIZE_VIEWER_CONFIG", "EDGAR_ITEMIZE_DATA_ROOT"))
    return env, viewer.RUNS, control.DATA_ROOT


def test_bake_matches_the_live_app(baked, tmp_path, monkeypatch):
    out, data_root, run_dir, bake = baked
    from fastapi.testclient import TestClient

    from edgar_itemize import control
    from edgar_itemize.viewer import app as viewer

    # an app set up independently of the bake: its own runs root and registry
    runs, cfg = tmp_path / "runs", tmp_path / "cfg"
    runs.mkdir()
    cfg.mkdir()
    (runs / run_dir.name).symlink_to(run_dir, target_is_directory=True)
    doc = bake.run_document(run_dir, "text", ACC, SEQ)
    row = bake.manifest_row("text", doc, str(data_root / f"{ACC}_{SEQ}.txt"))
    pq.write_table(pa.Table.from_pylist([row]), runs / "m.parquet")
    (cfg / "runs.yaml").write_text("corpora:\n  text: {kind: text, key: accession_sequence, manifest: m.parquet}\n")
    monkeypatch.setenv("EDGAR_ITEMIZE_VIEWER_CONFIG", str(cfg))
    monkeypatch.setattr(viewer, "RUNS", runs)
    monkeypatch.setattr(control, "DATA_ROOT", data_root)
    for fn in (viewer._manifests, viewer._parsed, viewer._stored):
        fn.cache_clear()
    try:
        c = TestClient(viewer.app)
        q = dict(headings="true", sequence=str(SEQ), corpus="text", source="run", run=run_dir.name)
        d = out / f"{ACC}_{SEQ}"
        live = c.get(f"/api/doc/{ACC}", params=q)
        assert live.status_code == 200
        assert (d / "doc.json").read_bytes() == live.content
        orig = c.get(f"/api/original/{ACC}", params=q)
        assert orig.status_code == 200
        assert (d / "original.html").read_bytes() == orig.content

        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        (e,) = m["documents"]
        assert e["key"] == f"{ACC}_{SEQ}" and e["original_status"] == 200 and m["panel"] == [e["key"]]
        assert e["n_nodes"] == len(live.json()["nodes"]) > 1
        text = (d / "raw.txt").read_text(encoding="utf-8")
        assert len(text) == e["raw_end"] - e["raw_base"]
        # the shim's /api/raw: text.slice(start - raw_base, end - raw_base)
        for b in live.json()["blocks"][:: max(1, len(live.json()["blocks"]) // 7)]:
            r = c.get(f"/api/raw/{ACC}", params=dict(start=b["raw_start"], end=b["raw_end"], sequence=SEQ, corpus="text")).json()
            assert text[b["raw_start"] - e["raw_base"]:b["raw_end"] - e["raw_base"]] == r["text"]
    finally:
        for fn in (viewer._manifests, viewer._parsed, viewer._stored):
            fn.cache_clear()


def test_shim_routes():
    doc, acc = _route("/api/doc/0000950144-97-003175")
    assert (doc["file"], acc) == ("doc.json", "0000950144-97-003175")
    assert _route("/api/original/0000320193-24-000123")[0]["file"] == "original.html"
    assert _route("/api/raw/0000320193-24-000123")[0]["name"] == "raw"
    assert _route("/api/corpora")[0]["body"]["text"] is None
    assert _route("/api/search")[0]["body"] == []
    for p in ("/api/windows", "/api/windows/facets", "/api/window/x", "/api/sets", "/api/review/queue",
              "/api/gold/files", "/api/doc/not-an-accession", "/api/summary"):
        assert _route(p) is None, p


def test_shim_covers_the_viewers_document_calls():
    """Every /api/ path the viewer requests is routed by the shim or is one the demo leaves
    unbaked on purpose (it then gets a 404); a new endpoint fails here until it is classified."""
    src = VIEWER.read_text(encoding="utf-8")
    called = set(re.findall(r"[`'\"]/api/([a-z]+)", src))  # every /api/<name> literal the page builds a URL from
    routed = {r["re"].split("/")[2].rstrip("$").split("(")[0] for r in _routes()}
    unbaked = {"windows", "window", "sets", "review", "gold"}
    assert {"doc", "original", "raw"} <= called
    assert called <= routed | unbaked, called - routed - unbaked


def test_hook_injects_only_the_shim():
    hooks = _load(REPO / "manual" / "_hooks.py", "manual_hooks")
    src = VIEWER.read_text(encoding="utf-8")
    out = hooks.demo_viewer_html(src)
    assert out.replace(hooks.SHIM_TAG, "", 1) == src
    assert out.index(hooks.SHIM_TAG) < out.index("<script>")
    with pytest.raises(ValueError):
        hooks.demo_viewer_html(src.replace(hooks.SHIM_ANCHOR, ""))


def test_shipped_demo_data_matches_its_manifest():
    m = json.loads((DEMO / "data" / "manifest.json").read_text(encoding="utf-8"))
    keys = [d["key"] for d in m["documents"]]
    assert len(keys) == len(set(keys)) >= 8
    assert set(m["panel"]) <= set(keys) and len(m["panel"]) == 3
    total = 0
    for d in m["documents"]:
        for f in d["files"].values():
            p = DEMO / "data" / d["key"] / f
            assert p.is_file(), p
            total += p.stat().st_size
        assert d["original_status"] == 200
    assert total < 16_000_000
