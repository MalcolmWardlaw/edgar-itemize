#!/usr/bin/env python3
"""Build the starter set under examples/: sample manifests and their per-document hashes.

    # 1. the manifests, from the reference runs and the demo spec
    uv run python scripts/sample_manifests.py manifests --out examples \\
        [--bake manual/demo/bake.json] [--source-manifest KIND=PATH ...] [--ex10-run RUN_DIR]

    # 2. parse them fresh (any machine with the files), then the hash parquets
    uv run python scripts/sample_manifests.py hashes --run RUN_DIR --out examples/sample_hashes.parquet \\
        --manifest examples/sample_manifest_10k.parquet --manifest ... [--reference KIND=RUN_DIR ...]

``manifests`` writes four parquets, all in ``SAMPLE_SCHEMA`` (``accession_number, cik,
sequence, kind, year, submission_type, agent_cik, archive_path``; ``archive_path`` relative
to the data root, ``archives/edgar/data/<cik>/<accession>.txt``, so it resolves on any mirror):

* ``sample_manifest_10k.parquet``, ``sample_manifest_ex10.parquet``,
  ``sample_manifest_ex13.parquet``: the nine documents of the public demo
  (``manual/demo/bake.json``), split by the ``parse --kind`` each needs (the 10-K and 10-Q rows
  both parse with ``--kind 10k``). ``cik``, ``year``, ``submission_type`` and ``agent_cik`` are
  copied from the manifest the reference run was parsed from (``--source-manifest``; defaults
  are the baseline manifests under ``runs/``); ``sequence`` is the reference run's (for 10-K and
  10-Q rows the primary document the parser selected, which ``--kind 10k`` ignores on input).
* ``sample_manifest_contracts.parquet``: ten EX-10 credit agreements for the covenant use case,
  drawn from the reference EX-10 run (``--ex10-run``): the two demo agreements, then one per
  bucket of ``CONTRACT_BUCKETS`` (era and filing-year range). A candidate qualifies when its
  document row has no error, it has exactly one Article or Section titled "Affirmative
  Covenants" and exactly one titled "Negative Covenants", each with at least
  ``MIN_COVENANT_DESCENDANTS`` nodes beneath it, at least ``MIN_CLAUSES`` clause nodes,
  a tree depth of at least ``MIN_DEPTH``, a raw file of at most ``MAX_BYTES`` bytes that is
  present under the data root, and a filer (manifest CIK) not already chosen. Inside a bucket
  the candidate with the smallest ``conformance.draw_key`` is taken (deterministic, no RNG).
  One line per choice is printed as a Markdown table row for ``examples/README.md``.

``hashes`` computes the per-document hash rows (``conformance.HASH_SCHEMA``) of a run with the
package's own ``conformance.manifest_hashes`` over every kind in the run, checks that the
keys equal the manifests' ``(accession_number, sequence)`` keys, and writes the parquet. With
``--reference`` it also recomputes each document's hash from the reference run's own node and
rejected rows (``conformance.document_hash``, the year partition named by the manifest) and
prints how many match. Exit status 1 on a key mismatch or a reference mismatch.

The data root comes from ``EDGAR_ITEMIZE_DATA_ROOT`` (or ``--data-root``); only
``manifests`` reads it (presence and size of the contract files, the filer names printed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from edgar_itemize.conformance import HASH_SCHEMA, PARSE_KIND, document_hash, draw_key, manifest_hashes, relative_archive_path

SAMPLE_SCHEMA = pa.schema([
    ("accession_number", pa.string()),
    ("cik", pa.string()),
    ("sequence", pa.int32()),
    ("kind", pa.string()),
    ("year", pa.int32()),
    ("submission_type", pa.string()),
    ("agent_cik", pa.string()),
    ("archive_path", pa.string()),
])

#: Source manifest of each corpus: what the reference run was parsed from.
DEFAULT_SOURCES = {
    "10k": "runs/full_manifest_10k.parquet",
    "10q": "runs/full_manifest_10q.parquet",
    "ex10": "runs/full_manifest_ex10.parquet",
    "ex13": "runs/full_manifest_ex13_v2.parquet",
}
#: Output file per parse kind for the demo rows.
DEMO_FILES = {"10k": "sample_manifest_10k.parquet", "ex10": "sample_manifest_ex10.parquet",
              "ex13": "sample_manifest_ex13.parquet"}
CONTRACTS_FILE = "sample_manifest_contracts.parquet"

#: (label, profile_era, first year, last year). One agreement per bucket, after the two demo ones.
CONTRACT_BUCKETS = (
    ("text, mid-1990s", "text", 1994, 1996),
    ("text, 2000s", "text", 2001, 2006),
    ("early HTML, 2000s", "html_early", 2000, 2009),
    ("early HTML, 2010s", "html_early", 2010, 2018),
    ("publisher HTML, 2004-2007", "html_publisher", 2004, 2007),
    ("publisher HTML, 2010-2014", "html_publisher", 2010, 2014),
    ("recent publisher HTML, 2019-", "html_publisher", 2019, 9999),
    ("recent generic HTML, 2019-", "html_generic", 2019, 9999),
)
MIN_CLAUSES = 250
MIN_DEPTH = 5
MIN_COVENANT_DESCENDANTS = 10  # nodes beneath each covenant Article or Section
MAX_BYTES = 2_000_000
AFFIRMATIVE = re.compile(r"affirmative\s+covenants?", re.I)
NEGATIVE = re.compile(r"negative\s+covenants?", re.I)
_CONFORMED = re.compile(rb"COMPANY CONFORMED NAME:\s*([^\r\n]+)")


def data_root(arg: str | None) -> Path:
    v = arg or os.environ.get("EDGAR_ITEMIZE_DATA_ROOT")
    if not v:
        sys.exit("set EDGAR_ITEMIZE_DATA_ROOT or pass --data-root")
    return Path(v)


def kv(pairs: list[str], defaults: dict) -> dict:
    out = dict(defaults)
    for p in pairs or []:
        k, _, v = p.partition("=")
        if not v:
            sys.exit(f"expected KIND=PATH, got {p!r}")
        out[k] = v
    return out


def part_files(run: Path, table: str, part_kind: str) -> list[Path]:
    return sorted(p for p in run.glob(f"{table}-{part_kind}*.parquet")
                  if p.name[len(table) + 1:-len(".parquet")].split("-", 1)[0] == part_kind)


def read_filtered(files: list[Path], accessions: list[str], columns: list[str] | None = None) -> list[dict]:
    rows = []
    for f in files:
        t = pq.read_table(f, columns=columns, filters=[("accession_number", "in", accessions)])
        rows += t.to_pylist()
    return rows


def source_row(manifests: dict[str, pa.Table], kind: str, accession: str, sequence: int | None) -> dict:
    t = manifests[kind]
    m = pc.equal(t["accession_number"], accession)
    if "sequence" in t.column_names and kind in ("ex10", "ex13"):
        m = pc.and_(m, pc.equal(t["sequence"], sequence))
    rows = t.filter(m).to_pylist()
    if len(rows) != 1:
        sys.exit(f"{kind} {accession} seq {sequence}: {len(rows)} rows in the source manifest")
    return rows[0]


def sample_row(src: dict, kind: str, sequence: int | None) -> dict:
    return dict(accession_number=src["accession_number"], cik=str(int(src["cik"])), sequence=sequence, kind=kind,
                year=None if src.get("year") is None else int(src["year"]), submission_type=src.get("submission_type"),
                agent_cik=src.get("agent_cik") or src["accession_number"][:10],
                archive_path=relative_archive_path(src["accession_number"], src["cik"]))


def write(rows: list[dict], path: Path) -> None:
    rows = sorted(rows, key=lambda r: (r["accession_number"], -1 if r["sequence"] is None else r["sequence"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SAMPLE_SCHEMA), path, compression="zstd", write_statistics=False)
    print(f"wrote {len(rows)} rows -> {path}")


def company_name(path: Path) -> str:
    with open(path, "rb") as f:
        m = _CONFORMED.search(f.read(1 << 16))
    return m.group(1).decode("latin-1").strip() if m else ""


# --------------------------------------------------------------------------- manifests


def demo_rows(bake: Path, manifests: dict[str, pa.Table]) -> dict[str, list[dict]]:
    spec = json.loads(bake.read_text())
    out: dict[str, list[dict]] = defaultdict(list)
    for d in spec["docs"]:
        kind, acc, run = d["kind"], d["accession"], Path(d["run"])
        seq = d.get("sequence")
        docs = read_filtered(part_files(run, "documents", PARSE_KIND[kind]), [acc], ["accession_number", "sequence", "error"])
        docs = [x for x in docs if seq is None or x["sequence"] == seq]
        if len(docs) != 1 or docs[0]["error"]:
            sys.exit(f"{kind} {acc}: {len(docs)} document rows in {run} (or an error)")
        out[PARSE_KIND[kind]].append(sample_row(source_row(manifests, kind, acc, seq), kind, docs[0]["sequence"]))
    return out


def contract_stats(run: Path) -> list[dict]:
    """One dict per document of the EX-10 run: document fields plus clause count, depth, and
    for each Article or Section titled Affirmative / Negative Covenants its label, title and
    number of descendant nodes."""
    docs = []
    for f in part_files(run, "documents", "ex10"):
        docs += pq.read_table(f, columns=["accession_number", "sequence", "manifest_cik", "filed_year", "profile_era",
                                          "profile_publisher", "n_nodes", "error", "input_bytes"]).to_pylist()
    agg: dict = {}
    for f in part_files(run, "nodes", "ex10"):
        t = pq.read_table(f, columns=["accession_number", "sequence", "node_id", "parent_id", "level_kind", "depth",
                                      "label_canon", "title"])
        by: dict = defaultdict(list)
        for n in t.to_pylist():
            by[(n["accession_number"], n["sequence"])].append(n)
        for key, ns in by.items():
            children: dict = defaultdict(list)
            for n in ns:
                children[n["parent_id"]].append(n["node_id"])

            def descendants(i):
                return sum(1 + descendants(c) for c in children[i])

            a = dict(clauses=sum(n["level_kind"].startswith("clause") for n in ns), depth=max(n["depth"] for n in ns),
                     aff=[], neg=[])
            for n in ns:
                if n["level_kind"] in ("article", "section") and n["title"]:
                    for name, rx in (("aff", AFFIRMATIVE), ("neg", NEGATIVE)):
                        if rx.search(n["title"]):
                            a[name].append((f"{n['label_canon']} {n['title'].strip()}", descendants(n["node_id"])))
            agg[key] = a
    empty = dict(clauses=0, depth=0, aff=[], neg=[])
    return [{**d, **agg.get((d["accession_number"], d["sequence"]), empty)} for d in docs]


def qualifies(s: dict, root: Path, used: set) -> bool:
    if s["error"] is not None or len(s["aff"]) != 1 or len(s["neg"]) != 1:
        return False
    if min(s["aff"][0][1], s["neg"][0][1]) < MIN_COVENANT_DESCENDANTS:
        return False
    if s["clauses"] < MIN_CLAUSES or s["depth"] < MIN_DEPTH or (s["input_bytes"] or 0) > MAX_BYTES:
        return False
    if str(int(s["manifest_cik"])) in used:
        return False
    return (root / relative_archive_path(s["accession_number"], s["manifest_cik"])).is_file()


def choose_contracts(stats: list[dict], demo: list[tuple[str, int]], root: Path) -> list[tuple[str, dict]]:
    by_key = {(s["accession_number"], s["sequence"]): s for s in stats}
    chosen: list[tuple[str, dict]] = []
    used: set = set()
    for acc, seq in demo:
        s = by_key[(acc, seq)]
        chosen.append(("demo", s))
        used.add(str(int(s["manifest_cik"])))
    for label, era, y0, y1 in CONTRACT_BUCKETS:
        cands = [s for s in stats if s["profile_era"] == era and y0 <= (s["filed_year"] or 0) <= y1]
        cands.sort(key=lambda s: draw_key(s["accession_number"], s["sequence"]))
        pick = next((s for s in cands if qualifies(s, root, used)), None)
        if pick is None:
            sys.exit(f"no qualifying agreement in bucket {label}")
        chosen.append((label, pick))
        used.add(str(int(pick["manifest_cik"])))
    return chosen


def cmd_manifests(a: argparse.Namespace) -> None:
    root = data_root(a.data_root)
    sources = kv(a.source_manifest, DEFAULT_SOURCES)
    manifests = {k: pq.read_table(v) for k, v in sources.items()}
    out = Path(a.out)
    demo = demo_rows(Path(a.bake), manifests)
    for kind, fname in DEMO_FILES.items():
        write(demo.get(kind, []), out / fname)
    demo_ex10 = [(r["accession_number"], r["sequence"]) for r in demo.get("ex10", [])]
    chosen = choose_contracts(contract_stats(Path(a.ex10_run)), demo_ex10, root)
    rows = [sample_row(source_row(manifests, "ex10", s["accession_number"], s["sequence"]), "ex10", s["sequence"])
            for _, s in chosen]
    write(rows, out / CONTRACTS_FILE)
    print("\n| accession, seq | filer | year | era (publisher) | nodes | clauses | depth | covenant headings (nodes beneath) | bytes | bucket |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for label, s in chosen:
        name = company_name(root / relative_archive_path(s["accession_number"], s["manifest_cik"]))
        cov = "; ".join(f"{h} ({k})" for h, k in s["aff"] + s["neg"]).replace("|", "/")
        print(f"| {s['accession_number']}, {s['sequence']} | {name} | {s['filed_year']} | {s['profile_era']} "
              f"({s['profile_publisher']}) | {s['n_nodes']} | {s['clauses']} | {s['depth']} | {cov} | "
              f"{s['input_bytes']:,} | {label} |")


# --------------------------------------------------------------------------- hashes


def run_kinds(run: Path) -> list[str]:
    return sorted({p.name[len("documents-"):-len(".parquet")].split("-", 1)[0] for p in run.glob("documents-*.parquet")})


def reference_hash(run: Path, part_kind: str, year: int | None, acc: str, seq: int) -> str | None:
    """document_hash of one document recomputed from a reference run's own rows, read from the
    year partition (or every partition when that file is absent)."""
    def files(table):
        f = run / f"{table}-{part_kind}-{year}.parquet"
        return [f] if year is not None and f.exists() else part_files(run, table, part_kind)

    def rows(table):
        return [r for r in read_filtered(files(table), [acc]) if r["sequence"] == seq]

    docs = rows("documents")
    if len(docs) != 1 or docs[0].get("error"):
        return None
    return document_hash(rows("nodes"), rows("rejected"))


def cmd_hashes(a: argparse.Namespace) -> None:
    run = Path(a.run)
    kinds = run_kinds(run)
    if not kinds:
        sys.exit(f"no documents-*.parquet under {run}")
    t = pa.concat_tables([manifest_hashes(run, k) for k in kinds])
    t = t.sort_by([("accession_number", "ascending"), ("sequence", "ascending")])
    status = 0
    mrows = [r for m in a.manifest or [] for r in pq.read_table(m).to_pylist()]
    if mrows:
        want = {(r["accession_number"], r["sequence"]) for r in mrows}
        got = set(zip(t["accession_number"].to_pylist(), t["sequence"].to_pylist()))
        if want != got:
            print(f"KEY MISMATCH: {len(want - got)} manifest rows not in the run, {len(got - want)} run rows not in the manifests")
            status = 1
    nulls = t["output_sha256"].null_count
    if nulls:
        print(f"{nulls} documents without an output hash (error rows)")
        status = 1
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t.replace_schema_metadata({}), out, compression="zstd", write_statistics=False)
    print(f"wrote {t.num_rows} rows ({sum(t['input_bytes'].to_pylist()):,} input bytes) -> {out}")
    if a.reference:
        refs = kv(a.reference, {})
        fresh = {(r["accession_number"], r["sequence"]): r["output_sha256"] for r in t.to_pylist()}
        match = differ = missing = 0
        for r in mrows:
            ref = refs.get(r["kind"])
            if ref is None:
                missing += 1
                continue
            h = reference_hash(Path(ref), PARSE_KIND[r["kind"]], r["year"], r["accession_number"], r["sequence"])
            if h is None:
                missing += 1
            elif h == fresh.get((r["accession_number"], r["sequence"])):
                match += 1
            else:
                differ += 1
                print(f"  differs: {r['kind']} {r['accession_number']} seq {r['sequence']}")
        print(f"reference cross-check: {match} equal, {differ} differ, {missing} without a reference row")
        status = status or int(differ > 0 or missing > 0)
    sys.exit(status)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    m = sp.add_parser("manifests", help="write the four sample manifests")
    m.add_argument("--out", default="examples")
    m.add_argument("--bake", default="manual/demo/bake.json")
    m.add_argument("--ex10-run", default="runs/full_ref_ex10")
    m.add_argument("--source-manifest", action="append", metavar="KIND=PATH",
                   help="source manifest per corpus (10k, 10q, ex10, ex13); defaults under runs/")
    m.add_argument("--data-root", default=None)
    m.set_defaults(fn=cmd_manifests)
    h = sp.add_parser("hashes", help="per-document hash parquet of a fresh parse of the sample manifests")
    h.add_argument("--run", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--manifest", action="append", help="the manifests parsed into --run (key check, reference years)")
    h.add_argument("--reference", action="append", metavar="KIND=RUN_DIR",
                   help="reference run per manifest kind (10k, 10q, ex10, ex13) to cross-check against")
    h.set_defaults(fn=cmd_hashes)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
