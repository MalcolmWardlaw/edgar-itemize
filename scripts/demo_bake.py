#!/usr/bin/env python3
"""Bake the static demo (manual/demo/): the viewer's JSON for a handful of filings.

    uv run python scripts/demo_bake.py --out manual/demo/data --spec manual/demo/bake.json
    uv run python scripts/demo_bake.py --out DIR KIND:RUN_DIR:ACCESSION[:SEQUENCE] ...

Each document is fetched from the viewer's FastAPI app in process (fastapi.testclient), so
the files are exactly what a running `edgar-itemize serve` returns for that document read
from the stored run (`source=run`):

    DIR/<key>/doc.json        GET /api/doc/<accession>?headings=true[&sequence=]&corpus=&source=run&run=
    DIR/<key>/original.html   GET /api/original/<accession>?... (the original-document pane)
    DIR/<key>/raw.txt         the document's <TEXT> payload, raw bytes decoded as latin-1 and
                              written as UTF-8; /api/raw?start=&end= is a slice of it at
                              offset `raw_base` (the static page's shim slices client-side)
    DIR/manifest.json         one entry per document (key, form, year, era, company, files,
                              the original pane's HTTP status) plus the `panel` keys

<key> is the accession, or `<accession>_<sequence>` for EX-10 / EX-13 / text documents.

KIND is 10k, 10q, ex10, ex13 or text; RUN_DIR a parse output directory (documents-, nodes-,
rejected-<part>.parquet). The raw filings are read from EDGAR_ITEMIZE_DATA_ROOT
(archives/edgar/data/<cik>/<accession>.txt); a `text` document's file is
<data root>/<accession>_<sequence>.txt. The app runs against a scratch runs directory that
holds only the named runs and a one-row-per-document manifest whose archive paths are
relative to the data root, so nothing machine-specific reaches the baked JSON.

The spec file is JSON: {"docs": [{"kind", "run", "accession", "sequence"?, "note"?}, ...],
"panel": [key, key, key]?}; run paths are relative to the spec file's directory's
repository root (the current directory).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

KINDS = ("10k", "10q", "ex10", "ex13", "text")
KEYED = ("ex10", "ex13", "text")  # corpora keyed by accession:sequence
# The viewer's built-in manifest file names (viewer/app.py BUILTIN_MANIFESTS); `text` gets a
# registry entry under its own name.
MANIFEST_NAME = {"10k": "full_manifest_10k.parquet", "10q": "full_manifest_10q.parquet",
                 "ex10": "full_manifest_ex10.parquet", "ex13": "full_manifest_ex13_v2.parquet",
                 "text": "demo_manifest_text.parquet"}
# The run's partition prefix for a corpus (a 10-Q run is written with --kind 10k).
RUN_KIND = {"10k": "10k", "10q": "10k", "ex10": "ex10", "ex13": "ex13", "text": "text"}


def parse_spec(s: str) -> dict:
    parts = s.split(":")
    if len(parts) not in (3, 4) or parts[0] not in KINDS:
        raise SystemExit(f"bad document spec {s!r}: KIND:RUN_DIR:ACCESSION[:SEQUENCE], KIND one of {KINDS}")
    d = dict(kind=parts[0], run=parts[1], accession=parts[2])
    if len(parts) == 4:
        d["sequence"] = int(parts[3])
    return d


def doc_key(accession: str, sequence: int | None, kind: str) -> str:
    return f"{accession}_{sequence}" if kind in KEYED else accession


def run_document(run: Path, kind: str, accession: str, sequence: int | None) -> dict:
    """The run's documents row for one document (primary document for 10-K / 10-Q)."""
    f = [("accession_number", "=", accession)]
    if sequence is not None and kind in KEYED:
        f.append(("sequence", "=", int(sequence)))
    for p in sorted(glob.glob(str(run / f"documents-{RUN_KIND[kind]}-*.parquet"))):
        rows = pq.read_table(p, filters=f).to_pylist()
        if rows:
            return rows[0]
    raise SystemExit(f"{accession}:{sequence} not in {run} ({RUN_KIND[kind]})")


def archive_rel(kind: str, cik: str, accession: str, sequence: int | None) -> str:
    if kind == "text":
        return f"{accession}_{sequence}.txt"
    return f"archives/edgar/data/{int(cik)}/{accession}.txt"


def manifest_row(kind: str, doc: dict, rel: str) -> dict:
    cik = str(doc.get("manifest_cik") or doc["cik"])
    row = dict(accession_number=doc["accession_number"], cik=cik, year=doc.get("filed_year"),
               agent_cik=doc.get("agent_cik"), archive_path=rel)
    if kind in ("10k", "10q"):
        row["submission_type"] = "10-Q" if kind == "10q" else "10-K"
    else:
        row["sequence"] = int(doc["sequence"])
        row["doc_type"] = doc.get("doc_type")
        if kind == "ex13":
            row["submission_type"] = "10-K"
    return row


def bake(docs: list[dict], out: Path, data_root: Path, panel: list[str] | None = None) -> dict:
    """Bake `docs` into `out`; returns the manifest written to out/manifest.json."""
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="demo_bake_"))
    try:
        return _bake(docs, out, data_root, panel or [], scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _bake(docs: list[dict], out: Path, data_root: Path, panel: list[str], scratch: Path) -> dict:
    runs_root, cfg = scratch / "runs", scratch / "config"
    runs_root.mkdir()
    cfg.mkdir()
    run_names: dict[str, Path] = {}
    rows_by_kind: dict[str, list[dict]] = {}
    plan = []
    for spec in docs:
        kind = spec["kind"]
        run = Path(spec["run"]).resolve()
        name = run.name
        if run_names.setdefault(name, run) != run:
            raise SystemExit(f"two runs named {name}: {run_names[name]} and {run}")
        doc = run_document(run, kind, spec["accession"], spec.get("sequence"))
        seq = int(doc["sequence"]) if kind in KEYED else None
        rel = archive_rel(kind, str(doc.get("manifest_cik") or doc["cik"]), doc["accession_number"], seq)
        rows_by_kind.setdefault(kind, []).append(manifest_row(kind, doc, str(data_root / rel) if kind == "text" else rel))
        plan.append((spec, kind, name, doc, seq))
    for name, run in run_names.items():
        (runs_root / name).symlink_to(run, target_is_directory=True)
    for kind, rows in rows_by_kind.items():
        pq.write_table(pa.Table.from_pylist(rows), runs_root / MANIFEST_NAME[kind])
    if "text" in rows_by_kind:
        (cfg / "runs.yaml").write_text("corpora:\n  text: {kind: text, key: accession_sequence, "
                                       f"manifest: {MANIFEST_NAME['text']}}}\n")

    env = {"EDGAR_ITEMIZE_RUNS": str(runs_root), "EDGAR_ITEMIZE_VIEWER_CONFIG": str(cfg),
           "EDGAR_ITEMIZE_DATA_ROOT": str(data_root)}
    saved_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from edgar_itemize import control
    from edgar_itemize.viewer import app as viewer

    # The module may have been imported before (tests): point it at the scratch tree, and put
    # everything back afterwards.
    saved = (control.DATA_ROOT, viewer.RUNS)
    control.DATA_ROOT, viewer.RUNS = data_root, runs_root
    caches = (viewer._manifests, viewer._summary, viewer._parsed, viewer._stored)
    for fn in caches:
        fn.cache_clear()
    try:
        return _fetch_all(viewer, control, plan, rows_by_kind, out, panel)
    finally:
        control.DATA_ROOT, viewer.RUNS = saved
        for fn in caches:
            fn.cache_clear()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _fetch_all(viewer, control, plan, rows_by_kind, out: Path, panel: list[str]) -> dict:
    from fastapi.testclient import TestClient

    from edgar_itemize.sgml import conformed_name, header_text

    client = TestClient(viewer.app)
    entries = []
    for spec, kind, run_name, doc, seq in plan:
        acc = doc["accession_number"]
        key = doc_key(acc, seq, kind)
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        q = dict(headings="true")
        if seq is not None:
            q["sequence"] = str(seq)
        q.update(corpus=kind, source="run", run=run_name)
        r = client.get(f"/api/doc/{acc}", params=q)
        if r.status_code != 200:
            raise SystemExit(f"/api/doc/{acc} {q}: {r.status_code} {r.text[:300]}")
        (d / "doc.json").write_bytes(r.content)
        j = r.json()
        # what the viewer's original pane requests: headings, the rendered document's
        # sequence, then docQS() (corpus, source, run)
        oq = dict(headings="true")
        if j.get("sequence"):
            oq["sequence"] = str(j["sequence"])
        oq.update(corpus=j["corpus"], source="run", run=run_name)
        ro = client.get(f"/api/original/{acc}", params=oq)
        (d / "original.html").write_bytes(ro.content)
        p = j["payload"]
        path = control.rewrite_path(next(x for x in rows_by_kind[kind] if x["accession_number"] == acc
                                         and x.get("sequence", seq) == seq)["archive_path"])
        with open(path, "rb") as f:
            head = f.read(min(p["start"], 1 << 16)).decode("latin-1")
            f.seek(p["start"])
            raw = f.read(p["end"] - p["start"]).decode("latin-1")
        (d / "raw.txt").write_text(raw, encoding="utf-8", newline="")
        company = conformed_name(header_text(head)) if kind != "text" else ""
        nodes = j["nodes"]
        entries.append(dict(
            key=key, accession=acc, sequence=j.get("sequence"), corpus=j["corpus"], kind=kind,
            form=j.get("doc_type"), year=doc.get("filed_year"), era=j["profile"]["era"],
            publisher=j["profile"]["publisher"], company=company, grammar=j.get("grammar"),
            run=run_name, parser_version=(j.get("run_info") or {}).get("parser_version"),
            n_nodes=len(nodes), n_rejected=len(j["rejected"]), raw_base=p["start"], raw_end=p["end"],
            original_status=ro.status_code, original_type=ro.headers.get("content-type", ""),
            note=spec.get("note", ""),
            files=dict(doc="doc.json", original="original.html", raw="raw.txt"),
        ))
        print(f"{key}: {kind} {j.get('doc_type')} {doc.get('filed_year')} {j['profile']['era']} "
              f"{len(nodes)} nodes, original {ro.status_code}, {company}", file=sys.stderr)
    manifest = dict(generator="scripts/demo_bake.py", documents=entries, panel=panel)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("docs", nargs="*", help="KIND:RUN_DIR:ACCESSION[:SEQUENCE]")
    ap.add_argument("--spec", help="JSON spec file (docs, panel)")
    ap.add_argument("--out", required=True, help="output directory (e.g. manual/demo/data)")
    ap.add_argument("--panel", help="comma-separated keys of the panel documents")
    a = ap.parse_args(argv)
    docs = [parse_spec(s) for s in a.docs]
    panel = a.panel.split(",") if a.panel else []
    if a.spec:
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        docs += spec.get("docs", [])
        panel = panel or spec.get("panel", [])
    if not docs:
        ap.error("no documents")
    root = os.environ.get("EDGAR_ITEMIZE_DATA_ROOT")
    if not root:
        raise SystemExit("EDGAR_ITEMIZE_DATA_ROOT is not set (the directory holding archives/edgar/data/)")
    bake(docs, Path(a.out), Path(root), panel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
