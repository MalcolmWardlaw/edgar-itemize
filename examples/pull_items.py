"""Pull the text of chosen Items out of a parse run, by byte offset into the raw filings.

    uv run python examples/pull_items.py --run RUN_DIR --items "ITEM 1,ITEM 7,ITEM 8" \\
        --out items.parquet [--data-root DIR] [--kind 10k|10q] [--grammar form10k] \\
        [--part 10k-2005] [--limit N]

Inputs
    --run        a parse output directory (``nodes-<part>.parquet`` and
                 ``documents-<part>.parquet``, docs/OUTPUT_CONTRACT.md section 1).
    --items      comma-separated ``label_canon`` values (``ITEM 1A``, ``ITEM 7``; on a
                 10-Q ``ITEM I.2``; on a contract ``ARTICLE 5``).
    --data-root  the EDGAR mirror root holding ``archives/edgar/data/<CIK>/<accession>.txt``
                 (default ``$EDGAR_ITEMIZE_DATA_ROOT``). The CIK directory is
                 ``documents.manifest_cik`` when the run has it (1.0.0 and later), else
                 ``documents.cik``.
    --kind       keep documents whose ``doc_type`` is a 10-K family form (``10-K``,
                 ``10-K405``, ``10-KSB``, ...) or a 10-Q family form.
    --grammar    keep documents parsed with this grammar (``form10k``, ``form10q``, ``contract``).
    --part       only read partition files whose ``<part>`` starts with this (``10k-2005``).
    --limit      stop after this many documents (after the filters), for a quick look.

Output
    ``--out`` parquet, one row per (document, requested item, occurrence): ``accession_number,
    cik, sequence, doc_type, label_canon, ordinal, covered_by, title, raw_start, raw_end,
    text``. ``text`` is ``raw[raw_start:raw_end]`` of the raw submission file decoded as
    latin-1 (one byte per character), i.e. the Item's full span as filed, HTML tags and all.
    ``ordinal`` numbers repeated occurrences of one label in a document (1 when unique).
    ``covered_by`` is set when the Item is credited through a multi-item heading
    (``Items 1 and 2``, ``covers_items``): the row then carries that heading's span and
    ``covered_by`` names the heading's own ``label_canon``.

Prints one summary line: documents, rows written, files missing. A missing raw file is
counted and skipped, never fatal.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

OUT_SCHEMA = pa.schema([
    ("accession_number", pa.string()), ("cik", pa.string()), ("sequence", pa.int32()), ("doc_type", pa.string()),
    ("label_canon", pa.string()), ("ordinal", pa.int32()), ("covered_by", pa.string()), ("title", pa.string()),
    ("raw_start", pa.int64()), ("raw_end", pa.int64()), ("text", pa.large_string()),
])
NODE_COLS = ["accession_number", "sequence", "label_canon", "covers_items", "title", "raw_start", "raw_end"]
KIND_PREFIX = {"10k": "10-K", "10q": "10-Q"}


def partitions(run: Path, part: str | None = None) -> list[str]:
    """Partition suffixes present in a run directory, sorted."""
    parts = sorted(p.name[len("documents-"):-len(".parquet")] for p in run.glob("documents-*.parquet"))
    return [p for p in parts if not part or p.startswith(part)]


def archive_path(data_root: Path, doc: dict) -> Path:
    cik = doc.get("manifest_cik") or doc["cik"]
    return data_root / "archives" / "edgar" / "data" / str(int(cik)) / f"{doc['accession_number']}.txt"


def select_documents(run: Path, part: str, kind: str | None, grammar: str | None) -> list[dict]:
    path = run / f"documents-{part}.parquet"
    names = pq.read_schema(path).names
    cols = [c for c in ("accession_number", "cik", "manifest_cik", "sequence", "doc_type", "grammar", "error") if c in names]
    docs = pq.read_table(path, columns=cols).to_pylist()
    out = []
    for d in docs:
        if d.get("error") or d["sequence"] is None:
            continue
        if kind and not (d.get("doc_type") or "").upper().startswith(KIND_PREFIX[kind]):
            continue
        if grammar and d.get("grammar") != grammar:
            continue
        out.append(d)
    return out


def matching_nodes(run: Path, part: str, items: list[str], keys: set[tuple]) -> dict[tuple, list[dict]]:
    """Item nodes of one partition whose label (or ``covers_items`` credit) is requested,
    read in record batches and grouped by (accession_number, sequence)."""
    path = run / f"nodes-{part}.parquet"
    by_doc: dict[tuple, list[dict]] = defaultdict(list)
    if not path.exists():
        return by_doc
    names = pq.read_schema(path).names
    cols = [c for c in NODE_COLS if c in names]
    want = pa.array(items, pa.string())
    for batch in pq.ParquetFile(path).iter_batches(columns=cols, batch_size=65536):
        mask = pc.is_in(batch.column("label_canon"), value_set=want)
        if "covers_items" in names:
            mask = pc.or_kleene(mask, pc.greater(pc.fill_null(pc.list_value_length(batch.column("covers_items")), 0), 0))
        for n in batch.filter(pc.fill_null(mask, False)).to_pylist():
            k = (n["accession_number"], n["sequence"])
            if k not in keys:
                continue
            if n["label_canon"] in items:
                by_doc[k].append(dict(n, covered_by=None))
            for key in n.get("covers_items") or ():  # "Items 1 and 2": credit ITEM 2 with the same span
                if f"ITEM {key}" in items:
                    by_doc[k].append(dict(n, label_canon=f"ITEM {key}", covered_by=n["label_canon"]))
    return by_doc


def pull(run: Path, data_root: Path, items: list[str], out: Path, kind: str | None = None, grammar: str | None = None,
         part: str | None = None, limit: int | None = None) -> dict:
    """Write the item rows to ``out``; return the counts printed in the summary line."""
    stats = dict(documents=0, rows=0, missing=0)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(out, OUT_SCHEMA, compression="zstd") as w:
        for p in partitions(run, part):
            if limit is not None and stats["documents"] >= limit:
                break
            docs = select_documents(run, p, kind, grammar)
            if limit is not None:
                docs = docs[: limit - stats["documents"]]
            stats["documents"] += len(docs)
            by_doc = matching_nodes(run, p, items, {(d["accession_number"], d["sequence"]) for d in docs})
            rows: list[dict] = []
            for d in docs:
                nodes = sorted(by_doc.get((d["accession_number"], d["sequence"]), []), key=lambda n: (n["raw_start"], n["label_canon"]))
                if not nodes:
                    continue
                path = archive_path(data_root, d)
                try:
                    raw = path.read_bytes()
                except OSError:
                    stats["missing"] += 1
                    continue
                seen: dict[str, int] = defaultdict(int)
                for n in nodes:
                    seen[n["label_canon"]] += 1
                    rows.append(dict(accession_number=d["accession_number"], cik=d["cik"], sequence=d["sequence"], doc_type=d.get("doc_type"),
                                     label_canon=n["label_canon"], ordinal=seen[n["label_canon"]], covered_by=n["covered_by"],
                                     title=n.get("title"), raw_start=n["raw_start"], raw_end=n["raw_end"],
                                     text=raw[n["raw_start"]:n["raw_end"]].decode("latin-1")))
            if rows:
                w.write_table(pa.Table.from_pylist(rows, schema=OUT_SCHEMA))
                stats["rows"] += len(rows)
    return stats


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--items", required=True, help='comma-separated label_canon values, e.g. "ITEM 1A,ITEM 7"')
    ap.add_argument("--out", required=True)
    ap.add_argument("--data-root", default=os.environ.get("EDGAR_ITEMIZE_DATA_ROOT"))
    ap.add_argument("--kind", choices=sorted(KIND_PREFIX), default=None)
    ap.add_argument("--grammar", default=None)
    ap.add_argument("--part", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    if not a.data_root:
        ap.error("--data-root not given and EDGAR_ITEMIZE_DATA_ROOT is not set")
    items = [s.strip() for s in a.items.split(",") if s.strip()]
    s = pull(Path(a.run), Path(a.data_root), items, Path(a.out), a.kind, a.grammar, a.part, a.limit)
    print(f"pull_items: {s['documents']} documents, {s['rows']} rows written, {s['missing']} files missing -> {a.out}")
    return s


if __name__ == "__main__":
    main()
