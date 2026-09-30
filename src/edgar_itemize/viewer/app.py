"""Read-only browser viewer for parsed filings. Parses on demand from the raw file.

    uv run edgar-itemize serve --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import csv
import getpass
import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pyarrow.parquet as pq
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .. import control
from ..agenda import META_FRONT, META_TOC, META_NAMES, PATH_LEN, path_str
from ..pipeline import covers_items_of, parse_document
from ..select import select_primary
from ..grammar.form10k import Form10KGrammar
from ..sgml import load_submission, load_text_submission
from . import runread
from .original import RENDER_CAP, inject_markers, marker_tag

STATIC = Path(__file__).parent / "static"
RUNS = Path(os.environ.get("EDGAR_ITEMIZE_RUNS", "runs"))
GOLD = Path(os.environ.get("EDGAR_ITEMIZE_GOLD", "gold"))
DECISIONS_PATH = GOLD / "review" / "decisions.jsonl"
GOLD_DIRS = {"10k": GOLD / "labels" / "10k", "ex10": GOLD / "labels" / "ex10", "llm": GOLD / "labels_llm"}
_GOLD_NAME = re.compile(r"^\d{10}-\d{2}-\d{6}_\d+\.yaml$")
REVIEWER = os.environ.get("EDGAR_ITEMIZE_REVIEWER") or getpass.getuser()
DECISION_VALUES = ("heading", "toc", "xref", "continued", "not_heading", "unsure")
app = FastAPI(title="edgar-itemize viewer")
runread.registry()  # logs one line at startup when EDGAR_ITEMIZE_VIEWER_CONFIG names no runs.yaml


# Corpora the viewer resolves filings through. The four EDGAR corpora are built in; any other
# corpus (e.g. a raw-text `--kind text` corpus) comes from the run registry (runs.yaml under
# EDGAR_ITEMIZE_VIEWER_CONFIG, runread.corpora()) with its manifest name and key mode, and is
# appended after them. Order is the precedence of the merged lookup used when a caller names
# no corpus: an accession resolves to a 10-K before a 10-Q, and an accession:sequence to an
# EX-10 before an EX-13 or a registry corpus (text-corpus files that are also EX-10 exhibits
# carry file offsets, not submission offsets, so they need that corpus named explicitly).
BUILTIN_MANIFESTS = (
    ("10k", "full_manifest_10k.parquet", "accession"),
    ("ex10", "full_manifest_ex10.parquet", "accession_sequence"),
    ("10k", "../gold/sample_manifest_10k.parquet", "accession"),
    ("10q", "full_manifest_10q.parquet", "accession"),
    ("ex13", "full_manifest_ex13_v2.parquet", "accession_sequence"),
)
BUILTIN_CORPORA = ("10k", "10q", "ex10", "ex13")
_KIND_ALIAS = {"10-k": "10k", "10-q": "10q", "ex-10": "ex10", "ex-13": "ex13"}


def _extra_corpora() -> dict[str, dict]:
    return {c: spec for c, spec in runread.corpora().items() if c not in BUILTIN_CORPORA}


def manifest_specs() -> list[tuple[str, str, str]]:
    out = list(BUILTIN_MANIFESTS)
    for c, spec in _extra_corpora().items():
        if spec.get("manifest"):
            out.append((c, spec["manifest"], spec.get("key") or "accession_sequence"))
    return out


def corpora() -> tuple[str, ...]:
    return BUILTIN_CORPORA + tuple(_extra_corpora())


def text_corpus() -> str | None:
    """The first registry corpus parsed with --kind text (what the alias `text` names)."""
    return next((c for c, spec in runread.corpora().items() if spec.get("kind") == "text"), None)


def _corpus_name(kind: str | None) -> str | None:
    if kind is None or kind == "":
        return None
    k = kind.lower()
    names = corpora()
    if k == "text" and k not in names:
        k = text_corpus() or k
    k = _KIND_ALIAS.get(k, k)
    if k not in names:
        raise HTTPException(422, f"corpus must be one of {names} (or 'text' for the registry's text corpus)")
    return k


class _Manifest:
    """One manifest held as an arrow table with a key -> row index; rows materialise on
    demand (the 10-Q manifest alone is 736,835 rows)."""

    def __init__(self, corpus: str, path: Path, keymode: str, names: dict | None):
        self.corpus, self.keymode = corpus, keymode
        self.t = pq.read_table(path)
        accs = self.t["accession_number"].to_pylist()
        if keymode == "accession_sequence":
            seqs = self.t["sequence"].to_pylist()
            self.keys = [f"{a}:{q}" for a, q in zip(accs, seqs)]
        else:
            self.keys = accs
        self.index: dict[str, int] = {}
        for i, k in enumerate(self.keys):
            self.index.setdefault(k, i)
        self.names = names

    def row(self, i: int) -> dict:
        r = {c: self.t.column(c)[i].as_py() for c in self.t.column_names}
        r["corpus"] = self.corpus
        r.setdefault("company_name", (self.names or {}).get(r["accession_number"]))
        return r

    def cols(self) -> dict[str, list]:
        """The columns search reads, as python lists (cached; None-filled when absent)."""
        if getattr(self, "_cols", None) is None:
            n = self.t.num_rows
            self._cols = {c: (self.t[c].to_pylist() if c in self.t.column_names else [None] * n)
                          for c in ("accession_number", "sequence", "year", "agent_cik", "description", "filename",
                                    "doc_type", "submission_type")}
        return self._cols

    def get(self, key: str) -> dict | None:
        i = self.index.get(key)
        return None if i is None else self.row(i)

    def key_for(self, accession: str, sequence: int | None) -> str:
        return accession if self.keymode == "accession" else f"{accession}:{sequence}"


@lru_cache(maxsize=1)
def _manifests() -> list[_Manifest]:
    try:
        idx = control.load_index_cik()
        names = dict(zip(idx["accession_number"].to_pylist(), idx["company_name"].to_pylist()))
    except Exception:  # noqa: BLE001
        names = {}
    out = []
    for corpus, name, keymode in manifest_specs():
        p = RUNS / name
        if p.exists():
            out.append(_Manifest(corpus, p, keymode, names))
    return out


class _Merged:
    """The merged key -> row lookup (first manifest in manifest_specs() order wins)."""

    def get(self, key: str) -> dict | None:
        for m in _manifests():
            r = m.get(key)
            if r is not None:
                return r
        return None

    def items(self, corpus: str | None = None):
        seen: set[str] = set()
        for m in _manifests():
            if corpus and m.corpus != corpus:
                continue
            for k, i in m.index.items():
                if k in seen:
                    continue
                seen.add(k)
                yield k, m, i


def _manifest() -> _Merged:
    return _Merged()


@lru_cache(maxsize=1)
def _summary() -> dict:
    p = RUNS / "summary_10k.csv"
    if not p.exists():
        return {}
    with open(p) as f:
        return {r["filed_year"]: r for r in csv.DictReader(f)}


def _norm(t: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _resolve(accession: str, sequence: int | None, corpus: str | None = None):
    corpus = _corpus_name(corpus)
    if corpus:
        for mf in _manifests():
            if mf.corpus != corpus:
                continue
            row = mf.get(mf.key_for(accession, sequence))
            if row is None and mf.keymode == "accession_sequence" and sequence is None:
                i = next((i for k, i in mf.index.items() if k.startswith(accession + ":")), None)
                row = None if i is None else mf.row(i)
            if row is not None:
                return row
        if corpus not in ("10k", "10q"):
            raise HTTPException(404, f"{accession}:{sequence} not in the {corpus} manifest")
        row = None
    else:
        m = _manifest()
        row = m.get(f"{accession}:{sequence}") if sequence else None
        row = row or m.get(accession)
    if row is None:
        # fall back to the control index
        idx = control.load_index_cik()
        hits = [(a, c) for a, c in zip(idx["accession_number"].to_pylist(), idx["cik"].to_pylist()) if a == accession]
        if not hits:
            raise HTTPException(404, f"{accession} not found")
        row = dict(accession_number=accession, cik=str(hits[0][1]), archive_path=str(control.archive_path_for(accession, hits[0][1])),
                   submission_type="10-Q" if corpus == "10q" else "10-K", corpus=corpus or "10k")
    return row


@app.get("/api/corpora")
def list_corpora():
    """The corpora the search box offers: built-ins plus the run registry's, and the one the
    `text` alias names (null when the registry has no text corpus)."""
    return dict(corpora=list(corpora()), text=text_corpus())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# The Step 1 curated filings; served read-only by sets.py as the legacy set
# `legacy-step-1-review-set`.
SETS = {
    "Step 1 review set": [
        ("0000912057-95-006316", None, "1995 text, AAR Corp"),
        ("0000704469-97-000002", None, "1997 self-filed text, run-in headings"),
        ("0000893220-04-000596", None, "2004 Bowne HTML, K-Tron"),
        ("0001193125-06-069078", None, "2006 Donnelley HTML"),
        ("0000002178-20-000013", None, "2020 Workiva iXBRL, 'Items 1 and 2'"),
        ("0000950170-25-029207", None, "2025 bank annual-report style (known miss)"),
        ("0000068270-05-000096", None, "2005 self-filed HTML, Part+Item on one line (Ruby Tuesday)"),
        ("0001144204-14-017487", None, "2014 Vintage, 'Item No. 1' labels"),
        ("0000002178-21-000047", 2, "2021 EX-10 credit agreement, HTML"),
        ("0001354488-12-004074", 2, "2012 EX-10 credit agreement, text, flat sections"),
        ("0000085974-99-000022", 2, "1999 EX-10, pre-wrapped text"),
    ],
}


# /api/sets lives in sets.py: saved sets under gold/review/sets/ plus SETS above, served
# read-only as legacy sets.


@app.get("/api/search")
def search(q: str = "", year: str = "", agent: str = "", kind: str = "10k", limit: int = 200):
    """kind: 10k, 10q, ex10, ex13 or a registry corpus ('text' names the registry's text corpus)."""
    corpus = _corpus_name(kind) or "10k"
    rows = []
    y = int(year) if year.strip().isdigit() else None
    agent = agent.strip()
    nq = _norm(q) if q else ""
    for key, mf, i in _manifest().items(corpus):
        c = mf.cols()
        if y and c["year"][i] != y:
            continue
        if agent and c["agent_cik"][i] != agent:
            continue
        acc = c["accession_number"][i]
        company = (mf.names or {}).get(acc)
        if nq:
            hay = _norm(f"{key} {company or ''} {c['description'][i] or ''} {c['filename'][i] or ''}")
            if nq not in hay:
                continue
        rows.append(dict(key=key, accession=acc, sequence=c["sequence"][i], year=c["year"][i], agent=c["agent_cik"][i],
                         company=company, description=c["description"][i], doc_type=c["doc_type"][i] or c["submission_type"][i],
                         corpus=corpus))
        if len(rows) >= limit:
            break
    return rows


def _load(row: dict, sequence: int | None):
    """(submission, document block) for a manifest row, the way cli._parse_one reads it:
    a text corpus's bare files through load_text_submission (file offsets), everything else
    through the SGML submission."""
    if (runread.corpus_spec(row.get("corpus") or "") or {}).get("kind") == "text":
        sub = load_text_submission(row["archive_path"], row["accession_number"], row.get("sequence"))
        return sub, sub.documents[0]
    sub = load_submission(control.rewrite_path(row["archive_path"]))
    seq = sequence or (row.get("sequence") if row.get("corpus") in ("ex10", "ex13") else None)
    if seq:
        d = next((x for x in sub.documents if x.sequence == seq), None)
        if d is None:
            raise HTTPException(404, f"sequence {seq} not in {row['accession_number']}")
    else:
        d = select_primary(sub.documents, row.get("submission_type", "10-K"))
    return sub, d


@lru_cache(maxsize=4)
def _parsed(accession: str, sequence: int | None, headings: bool, corpus: str | None = None):
    """(manifest row, submission, document block, parse result); cached so the
    original-document pane does not re-parse what the main pane just showed. EX-13 is
    parsed as the CLI's ex13 kind parses it (10-K grammar, document-root heading pass)."""
    row = _resolve(accession, sequence, corpus)
    sub, d = _load(row, sequence)
    if row.get("corpus") == "ex13":
        r = parse_document(sub, d, row["cik"], grammar=Form10KGrammar(), synth_root=True, headings=headings)
    else:
        r = parse_document(sub, d, row["cik"], headings=headings)
    return row, sub, d, r


@lru_cache(maxsize=4)
def _stored(accession: str, sequence: int | None, corpus: str | None, run: str):
    """(manifest row, submission, document block, RunResult) for one document of a stored run."""
    row = _resolve(accession, sequence, corpus)
    sub, d = _load(row, sequence)
    try:
        r = runread.run_result(RUNS, run, row.get("corpus") or "10k", row, sub, d)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return row, sub, d, r


def _view(accession: str, sequence: int | None, headings: bool, corpus: str | None, source: str, run: str | None):
    """The live parse (source=live) or a stored run's rows (source=run) behind /api/doc and
    /api/original; both return the same (row, sub, d, result) shape."""
    corpus = _corpus_name(corpus)
    if source == "live":
        return _parsed(accession, sequence, headings, corpus)
    if source != "run":
        raise HTTPException(422, "source must be live or run")
    if not run:
        c = corpus or _resolve(accession, sequence).get("corpus") or "10k"
        run = runread.latest_baseline(c)
        if not run:
            raise HTTPException(422, f"no baseline of record for corpus {c}; pass run=")
    return _stored(accession, sequence, corpus, run)


@app.get("/api/doc/{accession}")
def doc(accession: str, sequence: int | None = None, headings: bool = True, corpus: str | None = None,
        source: str = "live", run: str | None = None):
    """source=live parses with the checked-out code; source=run reads the document's nodes and
    rejected rows from runs/<run> (default: the corpus's latest baseline of record) and lays
    them over the current normalizer's blocks. Same JSON shape either way."""
    row, sub, d, r = _view(accession, sequence, headings, corpus, source, run)
    bounds = r.paths["_bounds"]
    # deepest node containing each block -> block path
    nodes = sorted((n for n in r.nodes if n.node_id != 0), key=lambda n: (n.raw_start, -n.depth))
    blocks_out = []
    for b in r.blocks:
        best = None
        for n in nodes:
            if n.raw_start <= b.raw_start < n.raw_end and (best is None or n.depth >= best.depth):
                best = n
            if n.raw_start > b.raw_start:
                break
        if best is None:
            p = [META_FRONT] + [0] * (PATH_LEN - 1) if b.raw_start < bounds["front_end"] else [2] + [0] * (PATH_LEN - 1)
        else:
            p = r.paths[best.node_id]
        is_head = best is not None and best.head_raw_start == b.raw_start
        blocks_out.append(dict(idx=b.idx, text=b.text, kind=b.kind, raw_start=b.raw_start, raw_end=b.raw_end, path=p,
                               node_id=best.node_id if best else None, is_heading=is_head,
                               bold=b.bold, underline=b.underline, center=b.center, caps=round(b.caps_ratio, 2), in_table=b.in_table))
    nodes_out = [dict(node_id=n.node_id, parent_id=n.parent_id, depth=n.depth, kind=n.level_kind, label=n.label_canon, title=n.title,
                      raw_start=n.raw_start, raw_end=n.raw_end, head_start=n.head_raw_start, head_end=n.head_raw_end, confidence=n.confidence,
                      rules=n.rule_ids, covers=covers_items_of(n.rule_ids), path=r.paths.get(n.node_id), path_str=path_str(r.paths.get(n.node_id, []))) for n in r.nodes]
    rej = [dict(block_idx=x.block_idx, kind=x.kind, label=x.label_canon, score=x.score, reason=x.reason, raw_start=x.raw_start, text=x.text) for x in r.rejected]
    cands = [dict(block_idx=c.block_idx, kind=c.kind, label=c.label_canon, score=c.score, rules=c.rule_ids, title=c.title) for c in r.candidates]
    return JSONResponse(dict(
        accession=r.accession, cik=r.cik, sequence=d.sequence, doc_type=d.type, grammar=r.grammar,
        profile=dict(era=r.profile.era, publisher=r.profile.publisher, agent=r.profile.agent_cik, signals=r.profile.signals),
        bounds=bounds, meta_names=META_NAMES, level_profile=r.profile_levels, n_blocks=len(r.blocks),
        blocks=blocks_out, nodes=nodes_out, rejected=rej, candidates=cands, manifest=row,
        payload=dict(start=d.text_start, end=d.text_end, is_html=d.is_html, render_cap=RENDER_CAP),
        corpus=row.get("corpus"), source=source, run=getattr(r, "run", None), run_info=getattr(r, "run_info", None),
    ))


_ORIGINAL_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"


@app.get("/api/original/{accession}")
def original(accession: str, sequence: int | None = None, headings: bool = True, corpus: str | None = None,
             source: str = "live", run: str | None = None):
    """The document's <TEXT> payload as a page with sync markers at every block
    offset and a chip marker at every node heading. Rendered inside a sandboxed
    iframe by the viewer; scripts never run (sandbox + CSP)."""
    row, sub, d, r = _view(accession, sequence, headings, corpus, source, run)
    if not d.has_text:
        raise HTTPException(404, f"{accession} sequence {d.sequence} has no <TEXT> payload")
    size = d.text_end - d.text_start
    if size > RENDER_CAP:
        raise HTTPException(413, f"payload is {size} bytes, over the render cap of {RENDER_CAP}; use the source view")
    with open(control.rewrite_path(row["archive_path"]), "rb") as f:
        f.seek(d.text_start)
        payload = f.read(size).decode("latin-1")
    heads = {n.head_raw_start: n for n in r.nodes if n.node_id != 0 and n.head_raw_start is not None}
    markers: list[tuple[int, str]] = []
    seen: set[int] = set()
    for b in r.blocks:
        if b.raw_start in seen:
            continue
        seen.add(b.raw_start)
        n = heads.get(b.raw_start)
        if n is not None:
            markers.append((b.raw_start, marker_tag("head", b.raw_start, label=n.label_canon or ("#" * n.depth),
                                                   depth=min(6, n.depth), node=n.node_id, path=path_str(r.paths.get(n.node_id, [])))))
        else:
            markers.append((b.raw_start, marker_tag("block", b.raw_start, idx=b.idx)))
    for start, n in heads.items():
        if start not in seen:
            seen.add(start)
            markers.append((start, marker_tag("head", start, label=n.label_canon or ("#" * n.depth), depth=min(6, n.depth),
                                              node=n.node_id, path=path_str(r.paths.get(n.node_id, [])))))
    page = inject_markers(payload, d.text_start, d.is_html, markers)
    return HTMLResponse(page, headers={"Content-Security-Policy": _ORIGINAL_CSP, "X-Content-Type-Options": "nosniff"})


@app.get("/api/raw/{accession}")
def raw(accession: str, start: int, end: int, sequence: int | None = None, corpus: str | None = None):
    row = _resolve(accession, sequence, corpus)
    with open(control.rewrite_path(row["archive_path"]), "rb") as f:
        f.seek(start)
        return JSONResponse(dict(start=start, end=end, text=f.read(max(0, end - start)).decode("latin-1")))


@app.get("/api/summary")
def summary():
    return _summary()


def _decisions() -> dict[tuple, dict]:
    """Latest decision per (accession, raw_start, label); the file is append-only."""
    out: dict[tuple, dict] = {}
    if DECISIONS_PATH.exists():
        for line in open(DECISIONS_PATH):
            if not line.strip():
                continue
            d = json.loads(line)
            out[(d["accession"], d["raw_start"], d["label"])] = d
    return out


@app.get("/api/review/queue")
def review_queue():
    p = RUNS / "judge" / "review_queue.json"
    if not p.exists():
        raise HTTPException(404, f"{p} not found; run scripts/review_queue.py")
    q = json.loads(p.read_text())
    dec = _decisions()
    for it in q["items"]:
        it["decision"] = dec.get((it["accession"], it["raw_start"], it["label"]))
    return q


@app.post("/api/review/decision")
def review_decision(payload: dict):
    rec = {k: payload.get(k) for k in ("accession", "raw_start", "label", "decision", "note")}
    if not rec["accession"] or rec["raw_start"] is None or not rec["label"]:
        raise HTTPException(422, "accession, raw_start, label are required")
    if rec["decision"] not in DECISION_VALUES:
        raise HTTPException(422, f"decision must be one of {DECISION_VALUES}")
    rec["reviewer"] = REVIEWER
    rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


# ---- gold editing ----

def _gold_path(kind: str, name: str) -> Path:
    if kind not in GOLD_DIRS:
        raise HTTPException(422, f"kind must be one of {sorted(GOLD_DIRS)}")
    if not _GOLD_NAME.match(name):
        raise HTTPException(422, "bad gold file name")
    return GOLD_DIRS[kind] / name


def _gold_load(p: Path) -> dict:
    g = yaml.safe_load(p.read_text()) or {}
    g.setdefault("nodes", [])
    g.setdefault("absent", [])
    g["nodes"] = [n for n in (g["nodes"] or []) if isinstance(n, dict) and n.get("label") and n.get("raw_start") is not None]
    g["absent"] = list(g["absent"] or [])
    return g


def _gold_dump(g: dict) -> str:
    """Stable one-line-per-node YAML, same shape as eval.gold.skeleton()."""
    lines = [f"accession: {g['accession']}", f"sequence: {int(g.get('sequence') or 1)}", f"doc_type: {g.get('doc_type') or ''}",
             f"reviewed: {'true' if g.get('reviewed') else 'false'}"]
    for k in ("reviewer", "reviewed_at", "source"):
        if g.get(k):
            lines.append(f"{k}: {json.dumps(str(g[k]))}")
    if g.get("notes"):
        lines.append(f"notes: {json.dumps(str(g['notes']))}")
    lines.append("nodes:")
    for n in sorted(g["nodes"], key=lambda n: (int(n["raw_start"]), str(n["label"]))):
        t = (n.get("title") or "")[:60]
        lines.append(f"  - {{label: {json.dumps(str(n['label']).upper())}, raw_start: {int(n['raw_start'])}, title: {json.dumps(t)}}}")
    lines.append("absent: [" + ", ".join(json.dumps(str(a).upper()) for a in g["absent"]) + "]")
    return "\n".join(lines) + "\n"


@app.get("/api/gold/files")
def gold_files(kind: str = "10k"):
    d = GOLD_DIRS.get(kind)
    if d is None:
        raise HTTPException(422, f"kind must be one of {sorted(GOLD_DIRS)}")
    out = []
    for p in (sorted(d.glob("*.yaml")) if d.exists() else []):
        try:
            g = _gold_load(p)
        except Exception as e:  # noqa: BLE001
            out.append(dict(name=p.name, error=str(e)))
            continue
        out.append(dict(name=p.name, accession=g.get("accession"), sequence=int(g.get("sequence") or 1), doc_type=g.get("doc_type"),
                        reviewed=bool(g.get("reviewed")), reviewer=g.get("reviewer"), n_nodes=len(g["nodes"]), n_absent=len(g["absent"]),
                        notes=g.get("notes")))
    return out


@app.get("/api/gold/file")
def gold_file(kind: str, name: str):
    p = _gold_path(kind, name)
    if not p.exists():
        raise HTTPException(404, f"{p} not found")
    g = _gold_load(p)
    g["_name"] = name
    g["_kind"] = kind
    return g


@app.put("/api/gold/file")
def gold_save(payload: dict):
    p = _gold_path(str(payload.get("kind", "")), str(payload.get("name", "")))
    g = _gold_load(p) if p.exists() else {}
    for k in ("accession", "sequence", "doc_type", "notes"):
        if k in payload:
            g[k] = payload[k]
    if not g.get("accession"):
        raise HTTPException(422, "accession required")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise HTTPException(422, "nodes must be a list")
    for n in nodes:
        if not isinstance(n, dict) or not n.get("label") or n.get("raw_start") is None:
            raise HTTPException(422, f"bad node {n!r}")
        try:
            n["raw_start"] = int(n["raw_start"])
        except (TypeError, ValueError):
            raise HTTPException(422, f"bad raw_start in {n!r}")
    g["nodes"] = nodes
    g["absent"] = [str(a) for a in payload.get("absent", []) if a]
    g["reviewed"] = bool(payload.get("reviewed"))
    if g["reviewed"]:
        g["reviewer"] = REVIEWER
        g["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        g.pop("reviewer", None)
        g.pop("reviewed_at", None)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(_gold_dump(g))
    tmp.replace(p)
    return dict(ok=True, name=p.name, reviewed=g["reviewed"], reviewer=g.get("reviewer"), n_nodes=len(nodes), n_absent=len(g["absent"]))

from .turns import router as turns_router; app.include_router(turns_router)  # noqa: E402,E702
from .windex import router as windex_router; app.include_router(windex_router)  # noqa: E402,E702
from .sets import router as sets_router; app.include_router(sets_router)  # noqa: E402,E702
