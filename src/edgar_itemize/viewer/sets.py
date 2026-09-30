"""Saved review sets and the selection builder: /api/sets, /api/sets/{slug}, /api/builder/*.

A set is one immutable JSON file, gold/review/sets/<slug>.json (the directory is
EDGAR_ITEMIZE_SETS, else $EDGAR_ITEMIZE_GOLD/review/sets):

    name, slug, created (UTC ISO), author (the viewer's REVIEWER), description,
    query       the selection parameters the set was drawn with: /api/windows parameters
                (index columns and q) plus the document-level ones (doc, doc_class,
                doc_kind), each a list of values
    seed, sample_n   null for "take all"
    items       [{window_id, bank, corpus, accession, sequence, anchor, label, note}]

Saving under an existing slug is refused (409); an edit is saved as a new file. Every
item's window_id must be in the window index (runs/viewer/windows.parquet). The old
hard-coded sets (app.SETS) are served read-only as legacy sets.

The builder selects windows from the index by the same column filters as /api/windows
(AND across columns, OR within one), plus document-level filters from the Turn 12 gate
artifacts listed in DOC_ARTIFACTS (plus any in doc_artifacts.yaml under
EDGAR_ITEMIZE_VIEWER_CONFIG, see doc_artifacts()): a window matches when its document (corpus,
accession, and for exhibit corpora the sequence) is one the artifact lists. Sampling is
deterministic: the matching windows sorted by window_id, then random.Random(seed).sample,
then sorted by window_id again. Deep link to a saved set in the viewer: /?set=<slug>.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from fastapi import APIRouter, HTTPException, Request

from . import runread, windex

router = APIRouter()

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 80
ITEM_FIELDS = ("window_id", "bank", "corpus", "accession", "sequence", "anchor", "label")
BUILDER_FACETS = ("bank", "turn", "corpus", "side", "verdict", "reason", "era", "year", "phasec_status")
DOC_PARAMS = ("doc", "doc_class", "doc_kind")
CONTROL = {"sample_n", "seed", "limit", "offset", "with_raters"}
DEFAULT_SEED = 12
MAX_ITEMS = 20000
# corpora whose document is the whole submission (the index leaves sequence null for
# most 10-K windows); exhibit corpora are keyed by accession and sequence
_ACC_ONLY = ("10k", "10q")


def sets_dir() -> Path:
    env = os.environ.get("EDGAR_ITEMIZE_SETS")
    if env:
        return Path(env)
    return Path(os.environ.get("EDGAR_ITEMIZE_GOLD", "gold")) / "review" / "sets"


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (name or "").lower())).strip("-")[:SLUG_MAX].strip("-")


def _check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not slug or len(slug) > SLUG_MAX or not SLUG.match(slug):
        raise HTTPException(422, f"slug must match {SLUG.pattern} and be at most {SLUG_MAX} characters")
    return slug


# ----------------------------------------------------------------------------- legacy

def _legacy() -> dict[str, dict]:
    from .app import SETS

    out = {}
    for name, rows in SETS.items():
        slug = "legacy-" + slugify(name)
        out[slug] = dict(
            name=name, slug=slug, legacy=True, created=None, author=None,
            description="hard-coded set from the Step 1 viewer (read-only); filings, not windows",
            query=None, seed=None, sample_n=None,
            items=[dict(window_id=None, bank=None, corpus=("ex10" if s else "10k"), accession=a, sequence=s,
                        anchor=None, label=None, note=n) for a, s, n in rows],
        )
    return out


# ----------------------------------------------------------------------------- set files

def _read(p: Path) -> dict:
    s = json.loads(p.read_text())
    s["legacy"] = False
    return s


def _summary(s: dict) -> dict:
    return dict(slug=s["slug"], name=s["name"], legacy=bool(s.get("legacy")), n=len(s.get("items") or []),
                created=s.get("created"), author=s.get("author"), description=s.get("description"),
                sample_n=s.get("sample_n"), seed=s.get("seed"), link=f"/?set={s['slug']}")


@router.get("/api/sets")
def list_sets():
    """Every saved set (newest first) then the legacy sets, with item counts."""
    out = []
    d = sets_dir()
    if d.is_dir():
        for p in d.glob("*.json"):
            if not SLUG.match(p.stem):
                continue
            try:
                out.append(_summary(_read(p)))
            except (OSError, ValueError, KeyError):
                continue
    out.sort(key=lambda s: (s["created"] or "", s["slug"]), reverse=True)
    out += [_summary(s) for s in _legacy().values()]
    return out


@router.get("/api/sets/{slug}")
def get_set(slug: str):
    _check_slug(slug)
    leg = _legacy()
    if slug in leg:
        return leg[slug]
    p = sets_dir() / f"{slug}.json"
    if not p.is_file():
        raise HTTPException(404, f"no set {slug!r}")
    return _read(p)


def _item(r: dict, note) -> dict:
    it = {k: r.get(k) for k in ITEM_FIELDS}
    it["note"] = None if note in (None, "") else str(note)
    return it


@router.post("/api/sets")
def save_set(payload: dict):
    """Save a new set. Body: name (required), slug (default: from name), description,
    and either items ([{window_id, note?}]) or query (+ sample_n, seed) from which the
    server draws the items exactly as /api/builder/select would. Refuses an existing slug
    (409), a bad slug (422) and any window_id not in the index (422)."""
    from .app import REVIEWER

    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    slug = _check_slug(payload.get("slug") or slugify(name))
    if slug.startswith("legacy-") or slug in _legacy():
        raise HTTPException(409, f"slug {slug!r} is reserved for the legacy sets")
    rows, by_id, cols = windex._rows()
    query = payload.get("query") or {}
    if not isinstance(query, dict):
        raise HTTPException(422, "query must be an object of parameter -> list of values")
    query = {k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in query.items() if k not in CONTROL}
    _split(query, cols)  # rejects parameters that are neither index columns nor document filters
    sample_n, seed = _sample_args(payload.get("sample_n"), payload.get("seed"))
    items_in = payload.get("items")
    if items_in is None:
        if not query and sample_n is None:
            raise HTTPException(422, "give items, or a query (and optionally sample_n / seed) to draw them from")
        hits, _ = select(rows, cols, query)
        items = [_item(r, None) for r in _sample(hits, sample_n, seed)]
    else:
        if not isinstance(items_in, list):
            raise HTTPException(422, "items must be a list")
        unknown, items, seen = [], [], set()
        for it in items_in:
            wid = it.get("window_id") if isinstance(it, dict) else it
            if not isinstance(wid, str) or wid not in by_id:
                unknown.append(wid)
                continue
            if wid in seen:
                raise HTTPException(422, f"window_id {wid} listed twice")
            seen.add(wid)
            items.append(_item(by_id[wid], it.get("note") if isinstance(it, dict) else None))
        if unknown:
            raise HTTPException(422, dict(error="window_ids not in the index", unknown=unknown[:50], n_unknown=len(unknown)))
    if not items:
        raise HTTPException(422, "the set would be empty")
    if len(items) > MAX_ITEMS:
        raise HTTPException(422, f"{len(items)} items; a set holds at most {MAX_ITEMS} (sample it)")
    rec = dict(name=name, slug=slug, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               author=REVIEWER, description=str(payload.get("description") or ""),
               query=query, seed=seed, sample_n=sample_n, index=str(windex.windows_path()), items=items)
    d = sets_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.json"
    try:
        with open(p, "x") as f:  # exclusive create: a set is never overwritten
            f.write(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    except FileExistsError:
        raise HTTPException(409, dict(error=f"set {slug!r} exists and sets are immutable; save under a new slug",
                                      suggest=_free_slug(slug)))
    return dict(_summary(rec), file=str(p))


def _free_slug(slug: str) -> str:
    base = re.sub(r"-\d+$", "", slug)
    d = sets_dir()
    for i in range(2, 1000):
        s = f"{base}-{i}"[:SLUG_MAX]
        if not (d / f"{s}.json").exists():
            return s
    return base


# ----------------------------------------------------------------------------- document-level facets

# Turn 12 gate artifacts under runs/judge/ that list documents. `type` changed: every row is
# a document the gate changed; losses: one row per lost / moved label, with its class
# (`cls`) and, where the file has it, its kind (lost, moved_later, moved_earlier, ...).
DOC_ARTIFACTS = [
    dict(id="t12-b1-10k-changed", build="B.1", corpus="10k", role="gate", runs="full_v26 -> full_v27",
         file="turn12-b1-10k-changed-docs.parquet", type="changed"),
    dict(id="t12-b1-10q-changed", build="B.1", corpus="10q", role="gate", runs="full_10q_v6 -> full_10q_v7",
         file="turn12-b1-10q-changed-docs.parquet", type="changed"),
    dict(id="t12-b4-10q-v9-changed", build="B.4", corpus="10q", role="gate", runs="full_10q_v7 -> full_10q_v9",
         file="turn12-b4-10q-v9-changed-docs.parquet", type="changed"),
    dict(id="t12-b4-10q-v8-changed", build="B.4", corpus="10q", role="as built (failed)", runs="full_10q_v7 -> full_10q_v8",
         file="turn12-b4-10q-changed-docs.parquet", type="changed"),
    dict(id="t12-b4-10k-v28-changed", build="B.4", corpus="10k", role="as built (failed; Reg-AB withdrawn)",
         runs="full_v27 -> full_v28", file="turn12-b4-10k-changed-docs.parquet", type="changed"),
    dict(id="t12-b1-10k-losses", build="B.1", corpus="10k", role="gate", runs="full_v26 -> full_v27",
         file="turn12-b1-10k-losses.parquet", type="losses", cls="cls", kind="kind"),
    dict(id="t12-b1-10q-losses", build="B.1", corpus="10q", role="gate", runs="full_10q_v6 -> full_10q_v7",
         file="turn12-b1-10q-losses.parquet", type="losses", cls="cls", kind="kind"),
    dict(id="t12-b1-ex10-losses", build="B.1", corpus="ex10", role="gate", runs="full_ex10_v20 -> full_ex10_v21",
         file="turn12-b1-contract-losses-ex10.parquet", type="losses", cls="cls", kind="kind"),
    dict(id="t12-b4-10q-v9-lost", build="B.4", corpus="10q", role="gate", runs="full_10q_v7 -> full_10q_v9",
         file="turn12-b4-10q-v9-lost.parquet", type="losses", cls="cls", kind="kind"),
    dict(id="t12-b4-10k-v28-lost", build="B.4", corpus="10k", role="as built (failed; Reg-AB withdrawn)",
         runs="full_v27 -> full_v28", file="turn12-b4-10k-lost.parquet", type="losses", cls="cls", kind="kind"),
]


def doc_artifacts() -> list[dict]:
    """DOC_ARTIFACTS plus the `artifacts` list of doc_artifacts.yaml in the viewer config
    directory (EDGAR_ITEMIZE_VIEWER_CONFIG), when there is one."""
    p = runread.config_dir()
    p = p / "doc_artifacts.yaml" if p else None
    extra = (runread._load_yaml(str(p)).get("artifacts") or []) if p is not None and p.is_file() else []
    return DOC_ARTIFACTS + [dict(a) for a in extra]


_doc_lock = threading.Lock()
_doc_cache: dict[str, tuple] = {}


def judge_dir() -> Path:
    return Path(os.environ.get("EDGAR_ITEMIZE_RUNS", "runs")) / "judge"


def doc_key(corpus, accession, sequence) -> tuple:
    if corpus in _ACC_ONLY:
        return (corpus, accession)
    return (corpus, accession, int(sequence) if sequence is not None else None)


def _artifact(a: dict) -> dict | None:
    """{doc_key: [(cls, kind), ...]} for one artifact, or None when its file is missing."""
    p = judge_dir() / a["file"]
    if not p.exists():
        return None
    m = p.stat().st_mtime
    with _doc_lock:
        hit = _doc_cache.get(a["id"])
        if hit and hit[0] == (p, m):
            return hit[1]
        want = [a.get("acc", "accession_number"), "sequence"] + [a[k] for k in ("cls", "kind", "corpus_col") if a.get(k)]
        t = pq.read_table(p, columns=want).to_pydict()
        n = len(t[want[0]])
        out: dict[tuple, list] = {}
        for i in range(n):
            corpus = t[a["corpus_col"]][i] if a.get("corpus_col") else a["corpus"]
            k = doc_key(corpus, t[want[0]][i], t["sequence"][i])
            out.setdefault(k, []).append((t[a["cls"]][i] if a.get("cls") else None, t[a["kind"]][i] if a.get("kind") else None))
        _doc_cache[a["id"]] = ((p, m), out)
        return out


def _doc_sets(ids: list[str], classes: set[str], kinds: set[str]) -> set[tuple]:
    known = {a["id"]: a for a in doc_artifacts()}
    bad = [i for i in ids if i not in known]
    if bad:
        raise HTTPException(422, f"unknown doc artifact(s) {bad}; known: {sorted(known)}")
    keys: set[tuple] = set()
    for i in ids:
        docs = _artifact(known[i])
        if docs is None:
            raise HTTPException(404, f"doc artifact {i}: runs/judge/{known[i]['file']} not found")
        for k, tags in docs.items():
            if (not classes or any(c in classes for c, _ in tags)) and (not kinds or any(kd in kinds for _, kd in tags)):
                keys.add(k)
    return keys


def _wkey(r: dict) -> tuple:
    return doc_key(r.get("corpus"), r.get("accession"), r.get("sequence"))


# ----------------------------------------------------------------------------- selection

def _sample_args(sample_n, seed) -> tuple[int | None, int | None]:
    if sample_n in (None, "", 0, "0"):
        return None, None
    try:
        n = int(sample_n)
        s = DEFAULT_SEED if seed in (None, "") else int(seed)
    except (TypeError, ValueError):
        raise HTTPException(422, "sample_n and seed must be integers")
    if n < 0:
        raise HTTPException(422, "sample_n must be positive")
    return n, s


def _split(query: dict[str, list[str]], cols: list[str]) -> tuple[dict[str, set[str]], str, dict[str, list[str]]]:
    f: dict[str, set[str]] = {}
    docs: dict[str, list[str]] = {k: [] for k in DOC_PARAMS}
    q = ""
    for k, vals in query.items():
        if k in DOC_PARAMS:
            docs[k] += [v for v in vals if v != ""]
        elif k == "q":
            q = " ".join(v for v in vals if v).strip().lower()
        elif k in CONTROL:
            continue
        elif k in cols:
            f.setdefault(k, set()).update(vals)
        else:
            raise HTTPException(422, f"unknown parameter {k!r}; index columns are {cols}, document filters {DOC_PARAMS}")
    return f, q, docs


def select(rows: list[dict], cols: list[str], query: dict[str, list[str]], skip: str | None = None,
           skip_docs: bool = False) -> tuple[list[dict], set | None]:
    """The index rows matching `query`, sorted by window_id, and the document key set used
    (None when no document filter applies)."""
    f, q, docs = _split(query, cols)
    keys = None
    if docs["doc"] and not skip_docs:
        keys = _doc_sets(docs["doc"], set(docs["doc_class"]), set(docs["doc_kind"]))
    elif (docs["doc_class"] or docs["doc_kind"]) and not skip_docs:
        raise HTTPException(422, "doc_class / doc_kind need a doc artifact")
    hits = [r for r in rows if windex._match(r, f, q, skip=skip) and (keys is None or _wkey(r) in keys)]
    hits.sort(key=lambda r: r["window_id"])
    return hits, keys


def _sample(hits: list[dict], n: int | None, seed: int | None) -> list[dict]:
    if n is None or n >= len(hits):
        return list(hits)
    pick = random.Random(seed).sample(hits, n)
    return sorted(pick, key=lambda r: r["window_id"])


def _query(request: Request) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        out.setdefault(k, []).append(v)
    return out


@router.get("/api/builder/select")
def builder_select(request: Request, limit: int = 200):
    """The selection: `total` windows match the query (index columns as /api/windows, plus
    doc / doc_class / doc_kind); `n` are selected (all, or sample_n drawn with seed);
    `rows` previews the first `limit` selected (limit=0 for all)."""
    rows, _, cols = windex._rows()
    query = _query(request)
    sample_n, seed = _sample_args(request.query_params.get("sample_n"), request.query_params.get("seed"))
    hits, _ = select(rows, cols, query)
    pick = _sample(hits, sample_n, seed)
    prev = pick if limit <= 0 else pick[:limit]
    keep = ("window_id", "bank", "turn", "corpus", "accession", "sequence", "anchor", "label", "marked_line", "side",
            "reason", "verdict", "phasec_status", "year", "company")
    return dict(total=len(hits), n=len(pick), sample_n=sample_n, seed=seed,
                query={k: v for k, v in query.items() if k not in CONTROL},
                rows=[{k: r.get(k) for k in keep} for r in prev])


@router.get("/api/builder/facets")
def builder_facets(request: Request):
    """/api/windows/facets over the builder's facets with the document filters applied too
    (each column's counts skip its own filter), plus `docs`: for every gate artifact, the
    windows under the current index filters whose document it lists, and per class and
    kind for the artifacts chosen in `doc`."""
    rows, _, cols = windex._rows()
    query = _query(request)
    _, keys = select(rows, cols, query)  # validates the query
    out = {}
    for c in BUILDER_FACETS:
        counts: dict[str, int] = {}
        for r in select(rows, cols, query, skip=c)[0]:
            v = windex._val(r.get(c))
            counts[v] = counts.get(v, 0) + 1
        out[c] = [dict(value=v, count=n) for v, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    base, _ = select(rows, cols, query, skip_docs=True)
    chosen = set(query.get("doc") or [])
    docs = []
    for a in doc_artifacts():
        d = _artifact(a)
        info = {k: a.get(k) for k in ("id", "build", "corpus", "role", "runs", "file", "type")}
        info["file"] = f"runs/judge/{a['file']}"
        if d is None:
            docs.append(dict(info, missing=True, documents=0, windows=0))
            continue
        hit = [r for r in base if _wkey(r) in d]
        info.update(documents=len(d), windows=len(hit), docs_with_windows=len({_wkey(r) for r in hit}))
        if a["id"] in chosen and a["type"] == "losses":
            cls_n: dict[str, int] = {}
            kind_n: dict[str, int] = {}
            for r in hit:
                tags = d[_wkey(r)]
                for c in {c for c, _ in tags if c is not None}:
                    cls_n[c] = cls_n.get(c, 0) + 1
                for k in {k for _, k in tags if k is not None}:
                    kind_n[k] = kind_n.get(k, 0) + 1
            info["classes"] = [dict(value=k, count=v) for k, v in sorted(cls_n.items(), key=lambda kv: (-kv[1], kv[0]))]
            info["kinds"] = [dict(value=k, count=v) for k, v in sorted(kind_n.items(), key=lambda kv: (-kv[1], kv[0]))]
        docs.append(info)
    total = len(select(rows, cols, query)[0])
    return dict(total=total, facets=out, docs=docs)
