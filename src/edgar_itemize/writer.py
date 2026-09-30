from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .schema import DOC_SCHEMA, NODE_SCHEMA, REJECTED_SCHEMA


def write_run(out_dir: Path, nodes: list[dict], docs: list[dict], rejected: list[dict], *, part: str = "all") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows, schema in (("nodes", nodes, NODE_SCHEMA), ("documents", docs, DOC_SCHEMA), ("rejected", rejected, REJECTED_SCHEMA)):
        t = pa.Table.from_pylist(rows, schema=schema)
        # deterministic: sort and fixed metadata
        keys = ["accession_number", "sequence"] + (["node_id"] if name == "nodes" else ["raw_start"] if name == "rejected" else [])
        t = t.sort_by([(k, "ascending") for k in keys])
        pq.write_table(t.replace_schema_metadata({}), out_dir / f"{name}-{part}.parquet", compression="zstd", write_statistics=False)
