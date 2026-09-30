"""Check a full parse run against a release's per-document hashes.

    uv run python examples/check_hashes.py --run RUN_DIR --expected hashes.parquet \\
        [--data-root DIR] [--show 20]

This is the full-corpus counterpart of ``edgar-itemize verify``: ``verify`` re-parses the
shipped conformance set; this compares a run you parsed yourself with the hash parquet a
release publishes for its baselines.

Inputs
    --run        your parse output directory (every ``documents-<part>.parquet`` in it).
    --expected   the release's hash parquet, as ``edgar-itemize conformance hashes`` writes it
                 (``accession_number, sequence, input_sha256, input_bytes, output_sha256,
                 n_nodes, n_rejected``; a conformance ``expected.parquet`` works too).
    --data-root  optional EDGAR mirror root. Used only when the run has no
                 ``documents.input_sha256`` (runs written before 1.0.0): the raw file at
                 ``archives/edgar/data/<CIK>/<accession>.txt`` is then hashed here.
    --show       how many mismatches to print (default 20).

The output hash of each run document is computed by the package itself
(``edgar_itemize.conformance.manifest_hashes``, which calls ``document_hash``), so this
script and the release cannot disagree on what the hash is.

Output: counts of documents in both / only in the run / only in expected; among those in
both, input hash equal / differs / unknown and output hash equal / differs; then the first
mismatches. Exit code 0 only when every document present in both with an equal input hash
has an equal output hash, and at least one document was compared.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pyarrow.parquet as pq

from edgar_itemize.conformance import manifest_hashes


def run_kinds(run: Path) -> list[str]:
    """Parse kinds in a run: ``documents-<kind>[-<value>].parquet`` (kinds carry no hyphen)."""
    return sorted({p.name[len("documents-"):-len(".parquet")].split("-", 1)[0] for p in run.glob("documents-*.parquet")})


def run_hashes(run: Path, data_root: Path | None = None) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for kind in run_kinds(run):
        for r in manifest_hashes(run, kind).to_pylist():
            out[(r["accession_number"], r["sequence"])] = r
    if data_root is not None and any(r["input_sha256"] is None for r in out.values()):
        for docf in sorted(run.glob("documents-*.parquet")):
            cols = [c for c in ("accession_number", "sequence", "cik", "manifest_cik") if c in pq.read_schema(docf).names]
            for d in pq.read_table(docf, columns=cols).to_pylist():
                r = out.get((d["accession_number"], d["sequence"]))
                if r is None or r["input_sha256"] is not None:
                    continue
                cik = str(int(d.get("manifest_cik") or d["cik"]))
                path = data_root / "archives" / "edgar" / "data" / cik / f"{d['accession_number']}.txt"
                if path.exists():
                    r["input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def compare(run: dict[tuple, dict], expected: dict[tuple, dict]) -> dict:
    both = sorted(set(run) & set(expected), key=lambda k: (k[0], -1 if k[1] is None else k[1]))
    c = dict(both=len(both), only_run=len(set(run) - set(expected)), only_expected=len(set(expected) - set(run)),
             input_equal=0, input_differs=0, input_unknown=0, output_equal=0, output_differs=0, failing=0, mismatches=[])
    for k in both:
        r, e = run[k], expected[k]
        if r["input_sha256"] is None or e.get("input_sha256") is None:
            c["input_unknown"] += 1
            inp = "unknown"
        elif r["input_sha256"] == e["input_sha256"]:
            c["input_equal"] += 1
            inp = "equal"
        else:
            c["input_differs"] += 1
            inp = "differs"
        if r["output_sha256"] == e["output_sha256"]:
            c["output_equal"] += 1
            continue
        c["output_differs"] += 1
        c["failing"] += inp == "equal"
        c["mismatches"].append(dict(accession_number=k[0], sequence=k[1], input=inp, got=r["output_sha256"], expected=e["output_sha256"],
                                    n_nodes=(r["n_nodes"], e.get("n_nodes")), n_rejected=(r["n_rejected"], e.get("n_rejected"))))
    c["ok"] = c["failing"] == 0 and c["input_equal"] > 0
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args(argv)
    run = Path(a.run)
    if not run_kinds(run):
        sys.exit(f"no documents-*.parquet under {run}")
    got = run_hashes(run, Path(a.data_root) if a.data_root else None)
    exp = {(r["accession_number"], r["sequence"]): r for r in pq.read_table(a.expected).to_pylist()}
    c = compare(got, exp)
    print(f"check_hashes: {c['both']} in both, {c['only_run']} only in run, {c['only_expected']} only in expected; "
          f"input {c['input_equal']} equal, {c['input_differs']} differs, {c['input_unknown']} unknown; "
          f"output {c['output_equal']} equal, {c['output_differs']} differs ({c['failing']} with equal input)")
    for m in c["mismatches"][: a.show]:
        print(f"  {m['accession_number']} seq={m['sequence']} input {m['input']:7s} output {m['got']} != {m['expected']} "
              f"nodes {m['n_nodes'][0]}/{m['n_nodes'][1]} rejected {m['n_rejected'][0]}/{m['n_rejected'][1]}")
    if len(c["mismatches"]) > a.show:
        print(f"  ... {len(c['mismatches']) - a.show} more")
    print("OK" if c["ok"] else "FAIL")
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
