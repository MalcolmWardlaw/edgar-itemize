"""Conformance: canonical per-document output hashes, the shipped conformance set, and
``edgar-itemize verify`` (docs/RELEASE_PLAN.md section 4 items 5-7, docs/VERSIONING.md
section 6).

Three CLI entry points live here and are registered by :func:`register_cli`:

* ``edgar-itemize conformance draw --out conformance/<version>/`` draws the set from the
  baseline manifests and runs, parses it fresh and writes ``manifest.parquet``,
  ``expected.parquet`` and ``README.md``;
* ``edgar-itemize conformance hashes --run DIR --kind K --out FILE.parquet`` writes the
  per-document hash manifest of a full baseline run;
* ``edgar-itemize verify [--data-root DIR] [--set DIR] [--workers N] [--json]`` re-parses
  the set and compares input and output hashes. Offline; exit code is the verdict.

Nothing here touches the parser: the hash is computed over the rows ``pipeline.result_rows``
already produces, and the draw uses a keyed SHA-256 bottom-k with a fixed seed (no RNG).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import __version__, control
from .schema import NODE_SCHEMA, REJECTED_SCHEMA

# ---------------------------------------------------------------------------
# 1. Canonical output hash
# ---------------------------------------------------------------------------

#: Columns left out of the canonical serialisation, so that a patch release (output-identical
#: by definition, docs/VERSIONING.md section 2) hashes identically to the release it patches.
HASH_EXCLUDE = frozenset({"parser_version", "normalizer_version"})

#: Hex SHA-256 of the canonical serialisation of a document with no node and no rejected row.
#: Exposed so a caller can recognise "parsed to nothing" without recomputing it.
_NODES_HDR = b"nodes\n"
_REJECTED_HDR = b"rejected\n"


def canonical_json(row: dict) -> str:
    """One row as canonical JSON: sorted keys, ASCII only, no whitespace, ``None`` as
    ``null``, lists in order, floats by Python ``repr`` (what ``json.dumps`` emits), and the
    two version columns dropped."""
    return json.dumps({k: v for k, v in row.items() if k not in HASH_EXCLUDE},
                      sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _through_schema(rows: list[dict], schema: pa.Schema) -> list[dict]:
    """Normalise row values through the output schema (int widths, float32, list types) so
    that rows fresh from ``result_rows`` and rows read back from parquet serialise the same:
    ``confidence`` and ``score`` are float32 columns, and a Python float that has not been
    through float32 would print differently."""
    if not rows:
        return []
    return pa.Table.from_pylist(rows, schema=schema).to_pylist()


def _rejected_key(r: dict) -> tuple:
    bi = r.get("block_idx")
    rs = r.get("raw_start")
    return (-1 if bi is None else bi, -1 if rs is None else rs, r.get("kind") or "", r.get("label_canon") or "",
            r.get("reason") or "", canonical_json(r))


def canonical_bytes(nodes_rows: list[dict], rejected_rows: list[dict]) -> bytes:
    """The exact byte string :func:`document_hash` digests. Documented in
    ``docs/VERSIONING.md`` ("How to check an install")."""
    nodes = sorted(_through_schema(nodes_rows, NODE_SCHEMA), key=lambda r: (r["node_id"], canonical_json(r)))
    rejected = sorted(_through_schema(rejected_rows, REJECTED_SCHEMA), key=_rejected_key)
    out = bytearray(_NODES_HDR)
    for r in nodes:
        out += canonical_json(r).encode("ascii") + b"\n"
    out += _REJECTED_HDR
    for r in rejected:
        out += canonical_json(r).encode("ascii") + b"\n"
    return bytes(out)


def document_hash(nodes_rows: list[dict], rejected_rows: list[dict]) -> str:
    """Hex SHA-256 over the canonical serialisation of one document's node rows (sorted by
    ``node_id``) followed by its rejected rows (sorted by ``block_idx``, ``raw_start``,
    ``kind``, ``label_canon``, ``reason``, then the serialised row), excluding
    ``parser_version`` and ``normalizer_version``. Independent of the order rows are given
    in and of dict key order."""
    return hashlib.sha256(canonical_bytes(nodes_rows, rejected_rows)).hexdigest()


EMPTY_DOCUMENT_HASH = hashlib.sha256(_NODES_HDR + _REJECTED_HDR).hexdigest()

HASH_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("sequence", pa.int32()),
        ("input_sha256", pa.string()),
        ("input_bytes", pa.int64()),
        ("output_sha256", pa.string()),
        ("n_nodes", pa.int32()),
        ("n_rejected", pa.int32()),
    ]
)


def _run_parts(run_dir: Path, kind: str) -> list[str]:
    """Partition suffixes of a run directory for ``kind``: ``documents-<kind>.parquet`` gives
    ``[kind]``, ``documents-<kind>-<key>.parquet`` gives ``[kind-key, ...]`` sorted."""
    parts = []
    for p in sorted(run_dir.glob(f"documents-{kind}*.parquet")):
        part = p.name[len("documents-") : -len(".parquet")]
        if part == kind or part.startswith(kind + "-"):
            parts.append(part)
    return parts


def _grouped_rows(path: Path, columns: list[str] | None = None):
    """Yield ``((accession, sequence), rows)`` for a nodes or rejected parquet file, grouping
    consecutive rows after sorting by ``(accession_number, sequence)``. Batches keep memory
    bounded on the 10-K partitions."""
    if not path.exists():
        return
    t = pq.read_table(path, columns=columns)
    if t.num_rows == 0:
        return
    t = t.sort_by([("accession_number", "ascending"), ("sequence", "ascending")])
    key, rows = None, []
    for batch in t.to_batches(max_chunksize=65536):
        for r in batch.to_pylist():
            k = (r["accession_number"], r["sequence"])
            if k != key:
                if key is not None:
                    yield key, rows
                key, rows = k, []
            rows.append(r)
    if key is not None:
        yield key, rows


def manifest_hashes(run_dir: str | Path, kind: str) -> pa.Table:
    """Per-document hash manifest of a run directory (``HASH_SCHEMA``): one row per
    ``documents`` row of every ``<kind>`` partition, ``output_sha256`` null where the
    document row carries an error, ``input_sha256``/``input_bytes`` null when the run
    predates R0 (no such columns)."""
    run_dir = Path(run_dir)
    parts = _run_parts(run_dir, kind)
    if not parts:
        raise SystemExit(f"no documents-{kind}*.parquet under {run_dir}")
    out: dict[str, list] = {n: [] for n in HASH_SCHEMA.names}
    for part in parts:
        docs = pq.read_table(run_dir / f"documents-{part}.parquet",
                             columns=[c for c in ("accession_number", "sequence", "error", "input_sha256", "input_bytes")
                                      if c in pq.read_schema(run_dir / f"documents-{part}.parquet").names])
        nodes_by = dict(_grouped_rows(run_dir / f"nodes-{part}.parquet"))
        rej_by = dict(_grouped_rows(run_dir / f"rejected-{part}.parquet"))
        for d in docs.to_pylist():
            k = (d["accession_number"], d["sequence"])
            n, rj = nodes_by.pop(k, []), rej_by.pop(k, [])
            out["accession_number"].append(k[0])
            out["sequence"].append(k[1])
            out["input_sha256"].append(d.get("input_sha256"))
            out["input_bytes"].append(d.get("input_bytes"))
            out["output_sha256"].append(None if d.get("error") else document_hash(n, rj))
            out["n_nodes"].append(len(n))
            out["n_rejected"].append(len(rj))
        stray = set(nodes_by) | set(rej_by)
        if stray:
            raise RuntimeError(f"{part}: {len(stray)} (accession, sequence) keys in nodes/rejected without a documents row, e.g. {sorted(stray)[:3]}")
    t = pa.table(out, schema=HASH_SCHEMA)
    return t.sort_by([("accession_number", "ascending"), ("sequence", "ascending")])


# ---------------------------------------------------------------------------
# 2. The conformance set
# ---------------------------------------------------------------------------

#: ``kind`` values in a conformance manifest and the ``parse --kind`` each is parsed with.
PARSE_KIND = {"10k": "10k", "10q": "10k", "ex10": "ex10", "ex13": "ex13", "text": "text", "fixtures": "text"}

#: The keyed-hash seed of the draw. Changing it is a new set, never a new version of one.
DRAW_SEED = "edgar-itemize-conformance-1"

MANIFEST_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("cik", pa.string()),
        ("sequence", pa.int32()),
        ("kind", pa.string()),
        ("year", pa.int32()),
        ("archive_path", pa.string()),
        ("submission_type", pa.string()),
        ("profile_era", pa.string()),
    ]
)

EXPECTED_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("sequence", pa.int32()),
        ("kind", pa.string()),
        ("input_sha256", pa.string()),
        ("input_bytes", pa.int64()),
        ("output_sha256", pa.string()),
        ("n_nodes", pa.int32()),
        ("n_rejected", pa.int32()),
    ]
)


@dataclass(frozen=True)
class CorpusSpec:
    kind: str  # conformance kind (10k, 10q, ex10, ex13)
    manifest: Path
    run_dir: Path
    n: int
    part_kind: str = ""  # run partition prefix; defaults to PARSE_KIND[kind]


# The Turn 13 baselines of record (docs/PROJECT_GUIDE.md) and the four target sizes.
DEFAULT_CORPORA = (
    ("10k", "runs/full_manifest_10k.parquet", "runs/full_v30", 800),
    ("10q", "runs/full_manifest_10q.parquet", "runs/full_10q_v11", 600),
    ("ex10", "runs/full_manifest_ex10.parquet", "runs/full_ex10_v24", 400),
    ("ex13", "runs/full_manifest_ex13_v2.parquet", "runs/full_ex13_v12", 200),
)


def draw_key(accession: str, sequence: int | None, seed: str = DRAW_SEED) -> str:
    """The keyed hash a document is ranked by inside its stratum."""
    return hashlib.sha256(f"{seed}:{accession}:{'' if sequence is None else int(sequence)}".encode("ascii")).hexdigest()


def largest_remainder(pop: dict, n: int) -> dict:
    """Allocate ``n`` draws across strata proportionally to ``pop`` (stratum -> count) by the
    largest-remainder method; ties on the remainder go to the smaller stratum key. A stratum
    never receives more than its population."""
    total = sum(pop.values())
    if total == 0 or n <= 0:
        return {k: 0 for k in pop}
    n = min(n, total)
    exact = {k: n * v / total for k, v in pop.items()}
    alloc = {k: min(int(exact[k]), pop[k]) for k in pop}
    left = n - sum(alloc.values())
    order = sorted(pop, key=lambda k: (-(exact[k] - int(exact[k])), k))
    i = 0
    while left > 0:
        k = order[i % len(order)]
        if alloc[k] < pop[k]:
            alloc[k] += 1
            left -= 1
        i += 1
        if i > 10 * len(order) + n:  # cannot happen (n <= total); guard against a loop
            raise RuntimeError("largest_remainder did not converge")
    return alloc


def relative_archive_path(accession: str, cik: str | int) -> str:
    """``archives/edgar/data/<cik>/<accession>.txt`` relative to the data root."""
    return f"archives/edgar/data/{int(cik)}/{accession}.txt"


def _run_documents(run_dir: Path, part_kind: str) -> pa.Table:
    cols = ["accession_number", "sequence", "profile_era", "error"]
    tables = []
    for part in _run_parts(run_dir, part_kind):
        tables.append(pq.read_table(run_dir / f"documents-{part}.parquet", columns=cols))
    if not tables:
        raise SystemExit(f"no documents-{part_kind}*.parquet under {run_dir}")
    return pa.concat_tables(tables)


def select_rows(manifest: pa.Table, documents: pa.Table, kind: str, n: int, *, seed: str = DRAW_SEED) -> tuple[list[dict], dict]:
    """Stratified deterministic draw of ``n`` documents from ``manifest`` joined to the run's
    ``documents`` table (``profile_era`` and ``error``). Returns the chosen manifest rows
    (``MANIFEST_SCHEMA`` dicts, sorted by accession and sequence) and the stratum table
    ``{(era, year): {"population": p, "drawn": k}}``.

    Population: manifest rows whose document row exists and has a null ``error``. The join is
    on ``accession_number`` alone for ``10k``/``10q`` manifests (no ``sequence`` column: the
    parser picks the primary document) and on ``(accession_number, sequence)`` otherwise.
    Strata: ``(profile_era, year)``; allocation by largest remainder; inside a stratum the
    ``k`` smallest :func:`draw_key` values."""
    has_seq = "sequence" in manifest.column_names
    docs_ok: dict = {}
    for d in documents.to_pylist():
        if d["error"] is not None:
            continue
        k = (d["accession_number"], d["sequence"]) if has_seq else d["accession_number"]
        docs_ok[k] = d
    strata: dict[tuple, list[tuple[str, dict]]] = defaultdict(list)
    for m in manifest.to_pylist():
        k = (m["accession_number"], m["sequence"]) if has_seq else m["accession_number"]
        d = docs_ok.get(k)
        if d is None:
            continue
        seq = d["sequence"]
        row = dict(accession_number=m["accession_number"], cik=str(m["cik"]), sequence=seq, kind=kind,
                   year=int(m["year"]) if m.get("year") is not None else None,
                   archive_path=relative_archive_path(m["accession_number"], m["cik"]),
                   submission_type=m.get("submission_type"), profile_era=d["profile_era"])
        strata[(d["profile_era"] or "", row["year"] if row["year"] is not None else -1)].append((draw_key(m["accession_number"], seq, seed), row))
    pop = {s: len(v) for s, v in strata.items()}
    alloc = largest_remainder(pop, n)
    chosen: list[dict] = []
    table = {}
    for s in sorted(strata):
        picks = sorted(strata[s], key=lambda kv: kv[0])[: alloc[s]]
        chosen += [r for _, r in picks]
        table[s] = dict(population=pop[s], drawn=len(picks))
    chosen.sort(key=lambda r: (r["accession_number"], r["sequence"] if r["sequence"] is not None else -1))
    return chosen, table


def fixture_rows(repo_root: Path) -> list[dict]:
    """Every ``tests/data/*.txt`` fixture as a ``fixtures`` row: bare text (``--kind text``),
    ``archive_path`` relative to the repository root, accession and sequence from the file
    name ``<accession>_<sequence>.txt``."""
    rows = []
    for p in sorted((repo_root / "tests" / "data").glob("*.txt")):
        stem = p.stem
        acc, _, seq = stem.rpartition("_")
        if not acc or not seq.isdigit():
            acc, seq = stem, "0"
        rows.append(dict(accession_number=acc, cik="0", sequence=int(seq), kind="fixtures", year=None,
                         archive_path=p.relative_to(repo_root).as_posix(), submission_type=None, profile_era="text"))
    return rows


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_input(row: dict, data_root: Path | None, repo_root: Path | None) -> Path:
    """Where a conformance row's input file lives: ``fixtures`` under the repository,
    everything else under the data root (absolute paths pass through)."""
    p = Path(row["archive_path"])
    if p.is_absolute():
        return p
    if row["kind"] == "fixtures":
        return (repo_root or _repo_root()) / p
    return (data_root if data_root is not None else control.data_root()) / p


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def hash_one(row: dict) -> dict:
    """Parse one conformance row (``archive_path`` already absolute) and return its
    ``EXPECTED_SCHEMA`` values plus ``error``. Runs in a worker process."""
    from .cli import _parse_one  # lazy: cli imports this module

    prow = dict(row)
    prow["archive_path"] = str(row["archive_path"])
    if row["kind"] in ("10k", "10q"):
        prow.pop("sequence", None)  # the primary document is selected, as in the baselines
    n, d, rj = _parse_one(prow, PARSE_KIND[row["kind"]], False)
    err = d.get("error")
    return dict(accession_number=row["accession_number"], sequence=d.get("sequence") if not err else row.get("sequence"),
                kind=row["kind"], input_sha256=d.get("input_sha256"), input_bytes=d.get("input_bytes"),
                output_sha256=None if err else document_hash(n, rj), n_nodes=len(n), n_rejected=len(rj), error=err)


def _pool_context():
    """An explicit multiprocessing start method for the hashing pool: ``forkserver`` where
    the platform offers it (Linux, macOS), else ``spawn``. Never the interpreter's default,
    which is ``fork`` on Linux through 3.13 and ``forkserver`` from 3.14, so that the pool
    behaves the same on every verified interpreter. Both methods start workers from a fresh
    interpreter, so a worker must get everything it needs from its task (:func:`_hash_task`)
    and the calling program needs an importable ``__main__`` (a script file or ``-c``, not
    ``python -``), as multiprocessing has always required of them."""
    import multiprocessing

    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")


def _hash_task(task: tuple[dict, str | None]) -> dict:
    """Pool entry point: ``(row, data_root)``. The data root travels in the task because a
    forkserver/spawn worker does not inherit the parent's ``control.DATA_ROOT`` (only the
    environment), and ``load_row`` re-roots archive paths through it."""
    row, data_root = task
    if data_root is not None:
        control.DATA_ROOT = Path(data_root)
    return hash_one(row)


def hash_rows(rows: list[dict], workers: int = 1, progress: bool = False, data_root: Path | None = None) -> list[dict]:
    """:func:`hash_one` over rows, in manifest order, optionally in a process pool
    (:func:`_pool_context`). ``data_root`` is what the workers use as ``control.DATA_ROOT``;
    None means the caller's current value."""
    out: list[dict] = []
    if workers > 1 and len(rows) > 1:
        from concurrent.futures import ProcessPoolExecutor

        dr = data_root if data_root is not None else control.DATA_ROOT
        tasks = [(r, str(dr) if dr is not None else None) for r in rows]
        with ProcessPoolExecutor(max_workers=workers, mp_context=_pool_context()) as ex:
            for i, r in enumerate(ex.map(_hash_task, tasks, chunksize=4)):
                out.append(r)
                if progress and (i + 1) % 200 == 0:
                    print(f"  {i + 1}/{len(rows)}", file=sys.stderr, flush=True)
    else:
        for i, row in enumerate(rows):
            out.append(hash_one(row))
            if progress and (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(rows)}", file=sys.stderr, flush=True)
    return out


def build_expected(rows: list[dict], data_root: Path | None, repo_root: Path | None, workers: int = 1,
                   progress: bool = False) -> list[dict]:
    """Parse conformance rows fresh and return ``EXPECTED_SCHEMA`` dicts (+ ``error``)."""
    absrows = [dict(r, archive_path=str(resolve_input(r, data_root, repo_root))) for r in rows]
    return hash_rows(absrows, workers, progress, data_root=data_root)


def _write_readme(out: Path, corpora: list[CorpusSpec], strata: dict, rows: list[dict], expected: list[dict],
                  dropped: list[dict], n_fixtures: int) -> None:
    lines = [f"# edgar-itemize conformance set {__version__}", "",
             f"Drawn by `edgar-itemize conformance draw` from the Turn 13 baselines with edgar-itemize {__version__}.",
             "`manifest.parquet` lists the documents (`archive_path` relative to the data root, so it resolves on any",
             "mirror); `expected.parquet` holds each document's `input_sha256`/`input_bytes` and the canonical",
             "`output_sha256` (`docs/VERSIONING.md`, \"How to check an install\") produced by parsing every document",
             "fresh with this version. The inputs are not redistributed. Check an install with `edgar-itemize verify`.", "",
             "## Draw parameters", "",
             f"* seed `{DRAW_SEED}`; rank inside a stratum = SHA-256 of `<seed>:<accession>:<sequence>`, bottom-k",
             "* strata `(profile_era, year)` read from the baseline run's `documents` table; allocation by largest",
             "  remainder proportional to the stratum population; documents whose baseline `error` is non-null excluded",
             "* corpora:", ""]
    lines.append("| kind | manifest | run | target | drawn |")
    lines.append("|---|---|---|---|---|")
    by_kind = Counter(r["kind"] for r in rows)
    for c in corpora:
        lines.append(f"| {c.kind} | `{c.manifest}` | `{c.run_dir}` | {c.n} | {by_kind.get(c.kind, 0)} |")
    if n_fixtures:
        lines.append(f"| fixtures | `tests/data/*.txt` | - | all | {n_fixtures} |")
    lines += ["", f"Total {len(rows)} documents; `expected.parquet` has {len(expected)} rows."]
    if dropped:
        lines += ["", f"{len(dropped)} drawn documents failed to parse fresh and were dropped from both files:", ""]
        lines += [f"* {d['kind']} {d['accession_number']} seq {d['sequence']}: {str(d['error']).splitlines()[0][:120]}" for d in dropped]
    lines += ["", "## Strata", "", "| kind | profile_era | year | population | drawn |", "|---|---|---|---|---|"]
    for kind in [c.kind for c in corpora]:
        for (era, year), v in sorted(strata.get(kind, {}).items()):
            lines.append(f"| {kind} | {era or '(null)'} | {year if year >= 0 else '(null)'} | {v['population']} | {v['drawn']} |")
    lines += ["", "## Per-era totals", "", "| kind | profile_era | population | drawn |", "|---|---|---|---|"]
    for kind in [c.kind for c in corpora]:
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for (era, _y), v in strata.get(kind, {}).items():
            agg[era][0] += v["population"]; agg[era][1] += v["drawn"]
        for era in sorted(agg):
            lines.append(f"| {kind} | {era or '(null)'} | {agg[era][0]} | {agg[era][1]} |")
    (out / "README.md").write_text("\n".join(lines) + "\n")


def draw_set(corpora: list[CorpusSpec], out: Path, *, data_root: Path | None = None, repo_root: Path | None = None,
             workers: int = 1, include_fixtures: bool = True, progress: bool = False, seed: str = DRAW_SEED) -> dict:
    """Draw, parse and write a conformance set to ``out``. Returns a summary dict."""
    rows: list[dict] = []
    strata: dict[str, dict] = {}
    for c in corpora:
        part_kind = c.part_kind or PARSE_KIND[c.kind]
        chosen, table = select_rows(pq.read_table(c.manifest), _run_documents(c.run_dir, part_kind), c.kind, c.n, seed=seed)
        rows += chosen
        strata[c.kind] = table
        if progress:
            print(f"{c.kind}: {len(chosen)} drawn from {sum(v['population'] for v in table.values())} in {len(table)} strata", file=sys.stderr, flush=True)
    n_fixtures = 0
    if include_fixtures:
        fx = fixture_rows(repo_root or _repo_root())
        rows += fx
        n_fixtures = len(fx)
    expected = build_expected(rows, data_root, repo_root, workers, progress)  # in row order
    assert len(expected) == len(rows)
    dropped = [e for e in expected if e["error"]]
    pairs = [(r, e) for r, e in zip(rows, expected) if not e["error"]]
    # The manifest's sequence is the document the parser chose (from the baseline for
    # 10-K/10-Q, where the parser selects the primary document); assert the fresh parse
    # chose the same one, so verify can key expected rows on (accession, sequence, kind).
    mism = [r for r, e in pairs if e["sequence"] != r["sequence"]]
    if mism:
        raise RuntimeError(f"{len(mism)} documents parsed to a different sequence than the baseline, e.g. {mism[:3]}")
    rows = [r for r, _ in pairs]
    expected = [e for _, e in pairs]
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=MANIFEST_SCHEMA).replace_schema_metadata({}), out / "manifest.parquet", compression="zstd", write_statistics=False)
    pq.write_table(pa.Table.from_pylist([{k: e[k] for k in EXPECTED_SCHEMA.names} for e in expected], schema=EXPECTED_SCHEMA).replace_schema_metadata({}),
                   out / "expected.parquet", compression="zstd", write_statistics=False)
    _write_readme(out, corpora, strata, rows, expected, dropped, n_fixtures)
    return dict(rows=len(rows), expected=len(expected), dropped=len(dropped), fixtures=n_fixtures, strata=strata)


# ---------------------------------------------------------------------------
# 3. verify
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    version: str
    documents: int = 0
    input_ok: int = 0
    input_differs: int = 0
    input_missing: int = 0
    output_ok: int = 0
    output_differs: int = 0
    failures: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.input_differs == 0 and self.output_differs == 0

    def line(self) -> str:
        return (f"verify {self.version}: {self.documents} documents, {self.input_ok} input ok, "
                f"{self.input_differs} input differs, {self.input_missing} input missing, "
                f"{self.output_ok} output ok, {self.output_differs} output differs")

    def as_dict(self) -> dict:
        return dict(version=self.version, parser_version=__version__, documents=self.documents, input_ok=self.input_ok,
                    input_differs=self.input_differs, input_missing=self.input_missing, output_ok=self.output_ok,
                    output_differs=self.output_differs, ok=self.ok, failures=self.failures)


def packaged_set_dir(version: str = __version__) -> Path:
    """The conformance set shipped inside the package (``conformance_sets/<version>/``), a
    byte-identical copy of the repository's ``conformance/<version>/``; what a pip install
    verifies against."""
    return Path(__file__).resolve().parent / "conformance_sets" / version


def default_set_dir(version: str = __version__) -> Path:
    """``conformance/<version>/`` under the current directory or the repository, else the
    copy inside the package (the only one present after ``pip install``)."""
    for base in (Path.cwd(), _repo_root()):
        p = base / "conformance" / version
        if p.is_dir():
            return p
    p = packaged_set_dir(version)
    if p.is_dir():
        return p
    return Path.cwd() / "conformance" / version


def verify_set(set_dir: Path, *, data_root: Path | None = None, repo_root: Path | None = None, workers: int = 1,
               progress: bool = False) -> VerifyResult:
    """Check every input hash of the set, then re-parse the documents whose input matches and
    compare output hashes. Never touches the network."""
    set_dir = Path(set_dir)
    if not (set_dir / "manifest.parquet").exists() or not (set_dir / "expected.parquet").exists():
        raise SystemExit(f"{set_dir} is not a conformance set (needs manifest.parquet and expected.parquet)")
    res = VerifyResult(version=set_dir.name)
    manifest = pq.read_table(set_dir / "manifest.parquet").to_pylist()
    expected = {(e["accession_number"], e["sequence"], e["kind"]): e for e in pq.read_table(set_dir / "expected.parquet").to_pylist()}
    repo_root = repo_root or set_dir.resolve().parents[1]
    res.documents = len(manifest)
    to_parse: list[dict] = []
    for r in manifest:
        key = (r["accession_number"], r["sequence"], r["kind"])
        exp = expected.get(key)
        if exp is None:
            res.output_differs += 1
            res.failures.append(dict(accession_number=key[0], sequence=key[1], kind=key[2], category="no expected row"))
            continue
        path = resolve_input(r, data_root, repo_root)
        if not path.exists():
            res.input_missing += 1
            res.failures.append(dict(accession_number=key[0], sequence=key[1], kind=key[2], category="input missing", path=str(path)))
            continue
        sha, nbytes = _sha256_file(path)
        if sha != exp["input_sha256"]:
            res.input_differs += 1
            res.failures.append(dict(accession_number=key[0], sequence=key[1], kind=key[2], category="input differs",
                                     path=str(path), expected_sha256=exp["input_sha256"], got_sha256=sha,
                                     expected_bytes=exp["input_bytes"], got_bytes=nbytes))
            continue
        res.input_ok += 1
        to_parse.append(dict(r, archive_path=str(path)))
    for got in hash_rows(to_parse, workers, progress, data_root=data_root):
        key = (got["accession_number"], got["sequence"], got["kind"])
        exp = expected.get(key)
        if exp is not None and not got["error"] and got["output_sha256"] == exp["output_sha256"]:
            res.output_ok += 1
        else:
            res.output_differs += 1
            f = dict(accession_number=got["accession_number"], sequence=got["sequence"], kind=got["kind"], category="output differs",
                     expected_sha256=exp["output_sha256"] if exp else None, got_sha256=got["output_sha256"],
                     expected_n_nodes=exp["n_nodes"] if exp else None, got_n_nodes=got["n_nodes"],
                     expected_n_rejected=exp["n_rejected"] if exp else None, got_n_rejected=got["n_rejected"])
            if got["error"]:
                f["error"] = str(got["error"]).splitlines()[0][:200]
            if exp is None:
                f["note"] = "parsed to a sequence the set does not expect"
            res.failures.append(f)
    res.failures.sort(key=lambda f: (f["kind"], f["accession_number"], f["sequence"] if f["sequence"] is not None else -1))
    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root) if args.data_root else None
    set_dir = Path(args.set) if args.set else default_set_dir()
    res = verify_set(set_dir, data_root=data_root, repo_root=Path(args.repo_root) if args.repo_root else None,
                     workers=args.workers, progress=args.progress)
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    else:
        print(res.line())
        if res.version != __version__:
            print(f"  (set {res.version}, installed edgar-itemize {__version__})")
        for f in res.failures[:50]:
            extra = " ".join(f"{k}={v}" for k, v in f.items() if k not in ("accession_number", "sequence", "kind", "category"))
            print(f"  {f['category']:15s} {f['kind']:8s} {f['accession_number']} seq={f['sequence']} {extra}")
        if len(res.failures) > 50:
            print(f"  ... {len(res.failures) - 50} more (use --json for all)")
    sys.exit(0 if res.ok else 1)


def _cmd_draw(args: argparse.Namespace) -> None:
    corpora = [CorpusSpec(k, Path(m), Path(r), n) for k, m, r, n in DEFAULT_CORPORA]
    if args.corpus:
        want = set(args.corpus.split(","))
        corpora = [c for c in corpora if c.kind in want]
    if args.n:
        corpora = [CorpusSpec(c.kind, c.manifest, c.run_dir, min(c.n, args.n)) for c in corpora]
    out = Path(args.out) if args.out else default_set_dir().parent / __version__
    data_root = Path(args.data_root) if args.data_root else None
    s = draw_set(corpora, out, data_root=data_root, workers=args.workers, include_fixtures=not args.no_fixtures, progress=args.progress)
    print(f"wrote {s['rows']} rows ({s['fixtures']} fixtures, {s['dropped']} dropped) -> {out}")


def _cmd_hashes(args: argparse.Namespace) -> None:
    t = manifest_hashes(Path(args.run), args.kind)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t.replace_schema_metadata({}), out, compression="zstd", write_statistics=False)
    print(f"wrote {t.num_rows} rows ({t['output_sha256'].null_count} without output hash, {t['input_sha256'].null_count} without input hash) -> {out}")


def register_cli(sp: argparse._SubParsersAction) -> None:
    """Register ``verify`` and ``conformance {draw,hashes}`` on the main parser's subparsers."""
    v = sp.add_parser("verify", help="re-parse the conformance set and compare input and output hashes (offline)")
    v.add_argument("--data-root", default=None, help=f"EDGAR mirror root (default ${control.DATA_ROOT_ENV})")
    v.add_argument("--set", default=None, help="conformance set directory (default conformance/<installed version>)")
    v.add_argument("--repo-root", default=None, help="where `fixtures` rows resolve (default: the set's grandparent directory)")
    v.add_argument("--workers", type=int, default=1)
    v.add_argument("--json", action="store_true", help="print the full result as JSON (no 50-failure cap)")
    v.add_argument("--progress", action="store_true")
    v.set_defaults(fn=_cmd_verify)
    c = sp.add_parser("conformance", help="build the conformance set and per-run hash manifests")
    csp = c.add_subparsers(dest="conformance_cmd", required=True)
    d = csp.add_parser("draw", help="draw the set from the baseline manifests and runs, parse it fresh, write manifest/expected/README")
    d.add_argument("--out", default=None, help="output directory (default conformance/<version>/)")
    d.add_argument("--data-root", default=None)
    d.add_argument("--workers", type=int, default=1)
    d.add_argument("--corpus", default="", help="comma-separated subset of 10k,10q,ex10,ex13")
    d.add_argument("--n", type=int, default=0, help="cap every corpus at N (smoke tests)")
    d.add_argument("--no-fixtures", action="store_true")
    d.add_argument("--progress", action="store_true")
    d.set_defaults(fn=_cmd_draw)
    h = csp.add_parser("hashes", help="per-document hash manifest of a run directory")
    h.add_argument("--run", required=True)
    h.add_argument("--kind", required=True, help="run partition prefix: 10k (also the 10-Q runs), ex10, ex13, text")
    h.add_argument("--out", required=True)
    h.set_defaults(fn=_cmd_hashes)
