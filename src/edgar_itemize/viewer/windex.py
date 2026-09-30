"""The window index endpoints: /api/windows, /api/windows/facets, /api/window/{window_id}.

Serves runs/viewer/windows.parquet, which scripts/viewer_index.py builds from the bank
registry (banks.yaml in the private viewer config, not part of the package). The file is loaded once and reloaded when its mtime changes.
Read-only; mounted on the viewer app by one `include_router` line at the end of app.py.
"""

from __future__ import annotations

import gzip
import json
import os
import threading
from pathlib import Path

import pyarrow.parquet as pq
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import runread

router = APIRouter()

RESERVED = {"limit", "offset", "q", "with_raters", "fields", "line_max"}
GROUPABLE = ("bank", "turn", "corpus", "side", "reason", "verdict", "verdict_kind", "decided_by", "era", "year",
             "stratum", "phasec_status", "task", "judged_run")
_lock = threading.Lock()
_cache: dict = {"mtime": None, "path": None, "rows": [], "by_id": {}, "columns": []}


def windows_path() -> Path:
    env = os.environ.get("EDGAR_ITEMIZE_WINDOWS")
    if env:
        return Path(env)
    return Path(os.environ.get("EDGAR_ITEMIZE_RUNS", "runs")) / "viewer" / "windows.parquet"


def _rows() -> tuple[list[dict], dict, list[str]]:
    p = windows_path()
    if not p.exists():
        raise HTTPException(404, f"{p} not found; run scripts/viewer_index.py")
    m = p.stat().st_mtime
    with _lock:
        if _cache["mtime"] != m or _cache["path"] != p:
            t = pq.read_table(p)
            rows = t.to_pylist()
            _cache.update(mtime=m, path=p, rows=rows, by_id={r["window_id"]: r for r in rows}, columns=t.column_names)
        return _cache["rows"], _cache["by_id"], _cache["columns"]


def _filters(request: Request, columns: list[str]) -> tuple[dict[str, set[str]], str]:
    f: dict[str, set[str]] = {}
    for k, v in request.query_params.multi_items():
        if k in RESERVED:
            continue
        if k not in columns:
            raise HTTPException(422, f"unknown column {k!r}; columns are {columns}")
        f.setdefault(k, set()).add(v)
    return f, (request.query_params.get("q") or "").strip().lower()


def _val(x) -> str:
    return "null" if x is None else str(x)


def _match(r: dict, f: dict[str, set[str]], q: str, skip: str | None = None) -> bool:
    for k, vals in f.items():
        if k != skip and _val(r.get(k)) not in vals:
            return False
    if q:
        hay = " ".join(str(r.get(c) or "") for c in ("accession", "company", "marked_line", "label", "window_id")).lower()
        if q not in hay:
            return False
    return True


def _out(r: dict, with_raters: bool) -> dict:
    o = dict(r)
    if with_raters:
        o["raters"] = json.loads(r["raters"]) if r.get("raters") else {}
    else:
        o.pop("raters", None)
    return o


def _json(request: Request, payload: dict) -> Response:
    """JSON, gzipped when the client accepts it and the body is large (the browse drawer
    loads every window at once: about 43 MB uncompressed with full marked lines)."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) > 65536 and "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gzip.compress(body, 5), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json")


@router.get("/api/windows")
def windows(request: Request, limit: int = 500, offset: int = 0, with_raters: bool = False,
            fields: str | None = None, line_max: int = 0):
    """Every window, or those matching the query: `column=value` on any column (repeat a
    column for OR; `null` matches a missing value), `q` free text over accession, company,
    marked line, label and id. limit=0 returns every match. `fields` (comma-separated)
    keeps only those columns of each row; `line_max` > 0 cuts marked_line to that many
    characters (display only; filtering always reads the full line)."""
    rows, _, cols = _rows()
    f, q = _filters(request, cols)
    keep = [c for c in fields.split(",") if c] if fields else None
    if keep and (bad := [c for c in keep if c not in cols and c != "raters"]):
        raise HTTPException(422, f"unknown field(s) {bad}; columns are {cols}")
    hits = [r for r in rows if _match(r, f, q)]
    page = hits[offset:] if limit <= 0 else hits[offset:offset + limit]
    out = []
    for r in page:
        o = _out(r, with_raters)
        if keep:
            o = {c: o.get(c) for c in keep}
        if line_max > 0 and isinstance(o.get("marked_line"), str) and len(o["marked_line"]) > line_max:
            o["marked_line"] = o["marked_line"][:line_max]
        out.append(o)
    return _json(request, dict(total=len(hits), offset=offset, limit=limit, rows=out))


@router.get("/api/windows/facets")
def facets(request: Request):
    """Distinct values with counts for each groupable column. Each column's counts apply
    every current filter except the column's own, so a chosen value's alternatives stay
    visible (standard faceted search); `total` applies them all."""
    rows, _, cols = _rows()
    f, q = _filters(request, cols)
    out = {}
    for c in GROUPABLE:
        counts: dict[str, int] = {}
        for r in rows:
            if _match(r, f, q, skip=c):
                v = _val(r.get(c))
                counts[v] = counts.get(v, 0) + 1
        out[c] = [dict(value=v, count=n) for v, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return dict(total=sum(1 for r in rows if _match(r, f, q)), facets=out)


def _status_in(run: str | None, corpus: str, accession: str, sequence: int | None, year: int | None,
               anchor: int | None, label: str | None) -> dict:
    if not run:
        return dict(run=None, status="unknown_run")
    from .app import RUNS

    try:
        try:
            _doc, nodes, rej = runread.read_run_doc(RUNS, run, corpus, accession, sequence, year)
        except runread.DocMissing:
            if sequence is None or corpus not in ("10k", "10q"):
                raise
            _doc, nodes, rej = runread.read_run_doc(RUNS, run, corpus, accession, None, year)
    except runread.RunMissing:
        return dict(run=run, status="run_missing")
    except runread.DocMissing:
        return dict(run=run, status="doc_missing")
    except ValueError as e:
        return dict(run=run, status="bad_run", error=str(e))
    st = runread.anchor_status(nodes, rej, anchor, label)
    st["run"] = run
    return st


@router.get("/api/window/{window_id}")
def window(window_id: str):
    """One window's record (raters expanded) with the anchor's status in the run it was
    judged on and in the latest baseline of record: accepted (with the node), rejected
    (with the reason), or absent."""
    _, by_id, _ = _rows()
    r = by_id.get(window_id)
    if r is None:
        raise HTTPException(404, f"window {window_id} not in the index")
    out = _out(r, True)
    corpus = r.get("corpus")
    year = r.get("year")
    try:
        from .app import _resolve

        row = _resolve(r["accession"], r.get("sequence"), corpus)
        year = row.get("year", year)
    except HTTPException:
        row = None
    latest = runread.latest_baseline(corpus) if corpus else None
    seq = r.get("sequence")
    out["status"] = dict(
        judged=_status_in(r.get("judged_run"), corpus, r["accession"], seq, year, r.get("anchor"), r.get("label")) if corpus else None,
        latest=_status_in(latest, corpus, r["accession"], seq, year, r.get("anchor"), r.get("label")) if corpus else None,
    )
    qs = f"?corpus={corpus}" + (f"&sequence={seq}" if seq else "")
    out["open"] = dict(doc=f"/api/doc/{r['accession']}{qs}", original=f"/api/original/{r['accession']}{qs}",
                       judged=(f"/api/doc/{r['accession']}{qs}&source=run&run={r['judged_run']}" if r.get("judged_run") else None),
                       latest=(f"/api/doc/{r['accession']}{qs}&source=run&run={latest}" if latest else None),
                       in_manifest=row is not None)
    return out
