"""Coverage of a parse run: how often the core Items were found, per year or per profile.

    uv run python examples/coverage.py --run RUN_DIR [--by year|profile_era|profile_publisher] \\
        [--core 1,2,3,5,7,8] [--csv coverage.csv]

Input
    --run   a parse output directory (``nodes-<part>.parquet``, ``documents-<part>.parquet``).
    --by    grouping: ``year`` (``documents.filed_year``, the default), ``profile_era`` or
            ``profile_publisher``.
    --core  the core item keys. Default from the run's first parsed document: 10-K
            ``1,2,3,5,7,8``; 10-Q (``grammar`` ``form10q``) ``I.1,I.2,II.6`` (the items
            ``Form10QGrammar.REQUIRED`` never lets a 10-Q omit); EX-13 ``6,7,8``.

How an item is credited (read off the ``nodes`` table, docs/OUTPUT_CONTRACT.md sections 2 and 8):
    * an ``item`` node credits its own key (``label_canon`` without the leading ``ITEM ``),
      except a node whose ``rule_ids`` carry ``omit.stmt`` (an omission statement, "Item 4.
      Not applicable", is an agenda node but not a found item);
    * a multi-item heading credits each key in its ``covers_items`` (``Items 1 and 2``);
    * an EX-13 ``heading`` node credits ``satisfies_item`` (``ITEM 6/7/8``) once per document
      however many headings carry it, since an annual report spreads one item over several
      headings.
    ``documents.items_found`` is not used for the counts because it is de-duplicated, so it
    cannot say "exactly once".

Output (printed table, and ``--csv`` if given), one row per group:
    documents (error rows included), errors, core_complete (share where every core key is
    credited exactly once and their first offsets run in core order), one ``item_<key>``
    column per core key (share with that key credited exactly once), nodes_per_doc (mean
    node rows per parsed document, root included), items_per_doc, toc_share (share of parsed
    documents with a ``toc`` node).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

BY = {"year": "filed_year", "profile_era": "profile_era", "profile_publisher": "profile_publisher"}
CORE = {"10k": ["1", "2", "3", "5", "7", "8"], "10q": ["I.1", "I.2", "II.6"], "ex13": ["6", "7", "8"]}
NODE_COLS = ["accession_number", "sequence", "level_kind", "label_canon", "raw_start", "rule_ids", "covers_items", "satisfies_item"]


def _key(label: str | None) -> str | None:
    return label.split(" ", 1)[1] if label and label.startswith("ITEM ") else None


def credits(nodes: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """Per item key: how many times it is credited, and the first raw_start crediting it."""
    count: dict[str, int] = defaultdict(int)
    first: dict[str, int] = {}
    satisfied: set[str] = set()
    for n in sorted(nodes, key=lambda n: n["raw_start"]):
        keys: list[str] = []
        if n["level_kind"] == "item" and "omit.stmt" not in (n.get("rule_ids") or []):
            keys = [k for k in [_key(n["label_canon"])] if k] + list(n.get("covers_items") or [])
        k = _key(n.get("satisfies_item"))
        if k and k not in satisfied:
            satisfied.add(k)
            keys.append(k)
        for k in keys:
            count[k] += 1
            first.setdefault(k, n["raw_start"])
    return count, first


def default_core(run: Path) -> list[str]:
    for p in sorted(run.glob("documents-*.parquet")):
        for d in pq.read_table(p, columns=["doc_type", "grammar", "error"]).to_pylist():
            if d["error"]:
                continue
            if d["grammar"] == "form10q":
                return CORE["10q"]
            return CORE["ex13"] if (d["doc_type"] or "").upper().startswith("EX-13") else CORE["10k"]
    return CORE["10k"]


def coverage(run: Path, by: str = "year", core: list[str] | None = None) -> list[dict]:
    core = core or default_core(run)
    col = BY[by]
    g: dict = defaultdict(lambda: dict(n=0, err=0, parsed=0, complete=0, nodes=0, items=0, toc=0, once=defaultdict(int)))
    for docf in sorted(run.glob("documents-*.parquet")):
        part = docf.name[len("documents-"):-len(".parquet")]
        docs = pq.read_table(docf, columns=["accession_number", "sequence", "error", col]).to_pylist()
        nodef = run / f"nodes-{part}.parquet"
        by_doc: dict[tuple, list[dict]] = defaultdict(list)
        if nodef.exists():
            names = pq.read_schema(nodef).names
            for b in pq.ParquetFile(nodef).iter_batches(columns=[c for c in NODE_COLS if c in names], batch_size=65536):
                for n in b.to_pylist():
                    by_doc[(n["accession_number"], n["sequence"])].append(n)
        for d in docs:
            s = g[d[col]]
            s["n"] += 1
            if d["error"]:
                s["err"] += 1
                continue
            ns = by_doc.pop((d["accession_number"], d["sequence"]), [])
            count, first = credits(ns)
            s["parsed"] += 1
            s["nodes"] += len(ns)
            s["items"] += sum(1 for n in ns if n["level_kind"] == "item")
            s["toc"] += any(n["level_kind"] == "toc" for n in ns)
            once = [k for k in core if count.get(k) == 1]
            for k in once:
                s["once"][k] += 1
            offs = [first[k] for k in core if k in first]
            s["complete"] += len(once) == len(core) and offs == sorted(offs)
    rows = []
    for key in sorted(g, key=lambda x: (x is None, str(x))):
        s = g[key]
        n, p = s["n"], max(1, s["parsed"])
        row = {by: key, "documents": n, "errors": s["err"], "core_complete": round(s["complete"] / n, 3)}
        row.update({f"item_{k}": round(s["once"][k] / n, 3) for k in core})
        row.update(nodes_per_doc=round(s["nodes"] / p, 1), items_per_doc=round(s["items"] / p, 1), toc_share=round(s["toc"] / p, 3))
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--by", choices=sorted(BY), default="year")
    ap.add_argument("--core", default=None, help="comma-separated item keys, e.g. 1,2,3,5,7,8")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args(argv)
    rows = coverage(Path(a.run), a.by, a.core.split(",") if a.core else None)
    if not rows:
        sys.exit(f"no documents-*.parquet under {a.run}")
    hdr = list(rows[0])
    w = [max(len(h), *(len(str(r[h])) for r in rows)) for h in hdr]
    print("  ".join(h.rjust(x) for h, x in zip(hdr, w)))
    for r in rows:
        print("  ".join(str(r[h]).rjust(x) for h, x in zip(hdr, w)))
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            cw = csv.DictWriter(f, fieldnames=hdr)
            cw.writeheader()
            cw.writerows(rows)
    return rows


if __name__ == "__main__":
    main()
