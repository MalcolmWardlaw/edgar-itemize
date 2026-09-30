"""Access to the edgar-pipeline control tables and deterministic pilot sampling.

The control tables are parquet datasets produced by an external pipeline; this
module only reads them.  Paths stored in the tables may carry another machine's
root prefix, which is rewritten here per EDGAR_ITEMIZE_CONTROL_PREFIX_MAP.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

DATA_ROOT_ENV = "EDGAR_ITEMIZE_DATA_ROOT"
# No default: the data root is local to whoever holds the mirror. Commands that need it
# (parse from a manifest, show by accession, manifest building) exit through data_root().
DATA_ROOT: Path | None = Path(os.environ[DATA_ROOT_ENV]) if os.environ.get(DATA_ROOT_ENV) else None
# Local-mirror support: archive paths stored in manifests and control tables carry the
# prefix of the machine that wrote them. EDGAR_ITEMIZE_CONTROL_PREFIX_MAP lists prefix
# rewrites as `old=new` pairs separated by `;` (first matching `old` wins); unset or empty
# means no rewriting. An empty `new` strips the prefix, so the tail joins DATA_ROOT.
PREFIX_MAP_ENV = "EDGAR_ITEMIZE_CONTROL_PREFIX_MAP"


def prefix_map() -> list[tuple[str, str]]:
    """The (old, new) prefix pairs from EDGAR_ITEMIZE_CONTROL_PREFIX_MAP, in order."""
    pairs = []
    for item in os.environ.get(PREFIX_MAP_ENV, "").split(";"):
        item = item.strip()
        if not item:
            continue
        old, sep, new = item.partition("=")
        if not sep or not old:
            raise SystemExit(f"{PREFIX_MAP_ENV}: malformed entry {item!r} (expected old=new)")
        pairs.append((old, new))
    return pairs


def data_root() -> Path:
    """DATA_ROOT, or a one-line exit telling the user how to set it."""
    if DATA_ROOT is None:
        raise SystemExit(f"{DATA_ROOT_ENV} is not set: export {DATA_ROOT_ENV}=/path/to/edgar "
                         "(the directory holding archives/edgar/data/<cik>/<accession>.txt and control/)")
    return DATA_ROOT


def control_dir() -> Path:
    return data_root() / "control"


def archive_root() -> Path:
    return data_root() / "archives"

TEN_K_TYPES = ("10-K", "10-K405", "10-KT", "10-KSB", "10-KSB40")


def rewrite_path(p: str | Path) -> Path:
    """Resolve a stored archive path on this machine.

    Control tables and manifests written elsewhere carry the writing machine's absolute
    prefix. The first ``old`` prefix from EDGAR_ITEMIZE_CONTROL_PREFIX_MAP
    (``old=new[;old=new...]``) that the path starts with is replaced by its ``new``, e.g.
    ``/srv/sec/=/data/edgar/``; with the variable unset no prefix is rewritten. A path
    that is relative after that step (``archives/edgar/data/<cik>/<accession>.txt``, what
    the conformance set and public manifests carry, or the tail left by an empty ``new``)
    is joined onto the data root; any other absolute path passes through.
    """
    p = str(p)
    for old, new in prefix_map():
        if p.startswith(old):
            p = new + p[len(old) :]
            break
    if not Path(p).is_absolute():
        return data_root() / p
    return Path(p)


def archive_path_for(accession: str, cik: str | int) -> Path:
    return archive_root() / "edgar" / "data" / str(int(cik)) / f"{accession}.txt"


def _dataset(name: str) -> ds.Dataset:
    return ds.dataset(control_dir() / name, format="parquet")


def filings_snapshot_name() -> str:
    files = sorted((control_dir() / "filings").glob("*.parquet"))
    return files[-1].name if files else ""


def load_filings(types: tuple[str, ...] = ("10-K",), *, include_amendments: bool = False) -> pa.Table:
    d = _dataset("filings")
    flt = ds.field("submission_type").isin(list(types))
    if not include_amendments:
        flt = flt & (ds.field("is_amendment") == False)  # noqa: E712
    t = d.to_table(
        columns=["accession_number", "submission_type", "is_amendment", "period_of_report", "filed_date", "archive_path", "primary_doc_filename", "sgml_bytes"],
        filter=flt,
    )
    return t


def load_index_cik() -> pa.Table:
    """accession -> cik, company_name (first row per accession)."""
    t = _dataset("index").to_table(columns=["cik", "form_type", "filed_date", "source_path", "company_name"])
    acc = pc.replace_substring_regex(t["source_path"], r"^.*/([0-9-]{20})\.txt$", r"\1")
    t = t.append_column("accession_number", acc)
    return t


def load_documents(filter_expr: ds.Expression | None = None, columns: list[str] | None = None) -> pa.Table:
    return _dataset("documents").to_table(columns=columns, filter=filter_expr)


def primary_doc_is_html(accessions: pa.Array | list[str], types: tuple[str, ...]) -> dict[str, bool]:
    """Map accession -> is_html of the first document whose TYPE is in `types`."""
    t = load_documents(
        ds.field("type").isin(list(types)) & ds.field("accession_number").isin(list(accessions)),
        columns=["accession_number", "sequence", "type", "is_html"],
    )
    out: dict[str, tuple[int, bool]] = {}
    for a, s, h in zip(t["accession_number"].to_pylist(), t["sequence"].to_pylist(), t["is_html"].to_pylist()):
        s = int(s) if s is not None and str(s).isdigit() else 1 << 30
        if a not in out or s < out[a][0]:
            out[a] = (s, bool(h))
    return {a: v[1] for a, v in out.items()}


def positional_picks(items: list[str], k: int) -> list[str]:
    """Deterministic evenly spaced picks from a sorted list; no RNG."""
    items = sorted(items)
    n = len(items)
    if n == 0:
        return []
    if n <= k:
        return items
    return [items[i * n // k] for i in range(k)]


def stratify_by_year_agent(
    rows: list[tuple[str, int]], *, per_cell: int = 3, top_agents: int = 3
) -> list[tuple[str, int, str]]:
    """rows: (accession, year).  Returns (accession, year, stratum) picks.

    Strata per year: each of the `top_agents` most frequent accession prefixes
    (filer agent CIK) within that year, plus one 'other' bucket.
    """
    by_year: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for acc, y in rows:
        by_year[y][acc[:10]].append(acc)
    picks: list[tuple[str, int, str]] = []
    for y in sorted(by_year):
        agents = by_year[y]
        ranked = sorted(agents.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        top = [a for a, _ in ranked[:top_agents]]
        for a in top:
            for acc in positional_picks(agents[a], per_cell):
                picks.append((acc, y, a))
        other = [acc for a, accs in agents.items() if a not in top for acc in accs]
        for acc in positional_picks(other, per_cell):
            picks.append((acc, y, "other"))
    return picks
