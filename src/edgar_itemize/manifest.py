"""``edgar-itemize manifest``: build a parse manifest from the SEC's public full index.

Source: ``https://www.sec.gov/Archives/edgar/full-index/<year>/QTR<n>/master.idx``, one
pipe-delimited file per quarter (``CIK|Company Name|Form Type|Date Filed|Filename``, the
filename being ``edgar/data/<cik>/<accession>.txt``). The files are cached under
``--index-dir`` (default ``<data-root>/full-index/<year>/QTR<n>/master.idx``) so a re-run is
offline. No dependence on the control tables.

The output schema is exactly what ``scripts/full_manifest.py`` writes, so ``parse`` is
unchanged: ``accession_number, cik, submission_type, year, agent_cik, archive_path``. ``year``
is the filing year (the year of ``Date Filed``), which is the convention of every manifest of
record; ``agent_cik`` is the accession's 10-digit prefix (the filer agent).
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq

from . import control
from .fetch import SEC_ARCHIVES, Opener, Throttle, download_to, is_present, resolve_user_agent

TEN_K_FORMS = control.TEN_K_TYPES  # ("10-K", "10-K405", "10-KT", "10-KSB", "10-KSB40")
TEN_Q_FORMS = ("10-Q", "10QSB", "10-QSB", "10-QT")  # scripts/full_manifest_10q.py's TYPES less the /A
FORM_FAMILIES = {"10-K": TEN_K_FORMS, "10-Q": TEN_Q_FORMS}
FIRST_YEAR = 1993  # first full year of the EDGAR full index
DEFAULT_YEARS = f"{FIRST_YEAR}-2026"

SCHEMA = pa.schema([
    ("accession_number", pa.string()),
    ("cik", pa.string()),
    ("submission_type", pa.string()),
    ("year", pa.int64()),
    ("agent_cik", pa.string()),
    ("archive_path", pa.string()),
])


class IndexRow(NamedTuple):
    cik: int
    company: str
    form: str
    filed: str  # YYYY-MM-DD as printed
    filename: str  # edgar/data/<cik>/<accession>.txt


# --- the index -----------------------------------------------------------------------------

def parse_years(spec: str) -> list[int]:
    """``"2019-2020"`` -> [2019, 2020]; ``"2019,2021"`` -> [2019, 2021]; mixtures allowed."""
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if hi < lo:
                raise ValueError(f"bad year range {part!r}")
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    if not out:
        raise ValueError(f"no years in {spec!r}")
    return sorted(out)


def quarters_for(years: Iterable[int]) -> list[tuple[int, int]]:
    return [(y, q) for y in years for q in (1, 2, 3, 4)]


def index_url(year: int, quarter: int) -> str:
    return f"{SEC_ARCHIVES}edgar/full-index/{year}/QTR{quarter}/master.idx"


def index_path(index_dir: Path, year: int, quarter: int) -> Path:
    return index_dir / str(year) / f"QTR{quarter}" / "master.idx"


def parse_master_idx(text: str) -> Iterator[IndexRow]:
    """Rows of a master.idx. The header block (description lines, the column line, the dashed
    rule) is skipped by shape: a data row has a numeric CIK first and a filename last. A
    company name containing ``|`` is re-joined from the middle fields."""
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5 or not parts[0].strip().isdigit():
            continue
        filename = parts[-1].strip()
        if not filename.startswith("edgar/data/"):
            continue
        yield IndexRow(int(parts[0]), "|".join(parts[1:-3]).strip(), parts[-3].strip(), parts[-2].strip(), filename)


def accession_of(filename: str) -> str | None:
    """``edgar/data/1000045/0001193125-19-039489.txt`` -> ``0001193125-19-039489``."""
    stem = filename.rsplit("/", 1)[-1]
    if stem.endswith(".txt"):
        stem = stem[:-4]
    if len(stem) == 20 and stem[10] == "-" and stem[13] == "-" and stem.replace("-", "").isdigit():
        return stem
    return None


# --- form selection ------------------------------------------------------------------------

def is_amendment(form: str) -> bool:
    return form.upper().endswith("/A")


def base_form(form: str) -> str:
    return form[:-2] if is_amendment(form) else form


def select_form(form: str, forms: Iterable[str], *, include_amendments: bool) -> bool:
    """Keep ``form`` if it is listed, or if it is the ``/A`` of a listed form and amendments
    are wanted. A form named explicitly with its ``/A`` is kept regardless."""
    fs = set(forms)
    if form in fs:
        return True
    return include_amendments and is_amendment(form) and base_form(form) in fs


def expand_forms(spec: str) -> tuple[str, ...]:
    """Comma list of form types; ``10-K-family`` / ``10-Q-family`` expand to the families."""
    out: list[str] = []
    for f in spec.split(","):
        f = f.strip()
        if not f:
            continue
        fam = f[:-len("-family")] if f.lower().endswith("-family") else None
        if fam is not None:
            out.extend(FORM_FAMILIES[fam.upper()])
        else:
            out.append(f)
    return tuple(dict.fromkeys(out))


# --- rows ----------------------------------------------------------------------------------

def year_of(filed: str) -> int:
    return int(filed[:4])


def manifest_row(r: IndexRow, accession: str, data_root: Path) -> dict:
    cik = str(int(r.cik))
    return dict(accession_number=accession, cik=cik, submission_type=r.form, year=year_of(r.filed),
                agent_cik=accession[:10],
                archive_path=str(data_root / "archives" / "edgar" / "data" / cik / f"{accession}.txt"))


def build_rows(index_rows: Iterable[IndexRow], forms: Iterable[str], data_root: Path, *,
               include_amendments: bool = False, only_present: bool = True, years: set[int] | None = None,
               stats: Counter | None = None) -> list[dict]:
    """Filter, de-duplicate and shape index rows into manifest rows, sorted by (year, accession).

    An accession listed under several CIKs (co-registrants) yields one row: the CIK whose
    file is present under ``data_root`` if any, else the smallest CIK."""
    stats = stats if stats is not None else Counter()
    forms = tuple(forms)
    by_acc: dict[str, dict] = {}
    for r in index_rows:
        stats["index_rows"] += 1
        if not select_form(r.form, forms, include_amendments=include_amendments):
            if is_amendment(r.form) and base_form(r.form) in forms:
                stats["amendments_dropped"] += 1
            continue
        acc = accession_of(r.filename)
        if acc is None:
            stats["bad_filename"] += 1
            continue
        if years is not None and year_of(r.filed) not in years:
            stats["outside_years"] += 1
            continue
        stats["form_matched"] += 1
        row = manifest_row(r, acc, data_root)
        prev = by_acc.get(acc)
        if prev is None:
            by_acc[acc] = row
            continue
        stats["duplicate_accession"] += 1
        prev_present = is_present(Path(prev["archive_path"]))
        new_present = is_present(Path(row["archive_path"]))
        if (new_present and not prev_present) or (new_present == prev_present and int(row["cik"]) < int(prev["cik"])):
            by_acc[acc] = row
    rows = list(by_acc.values())
    if only_present:
        kept = []
        for row in rows:
            if is_present(Path(row["archive_path"])):
                kept.append(row)
            else:
                stats["not_present"] += 1
        rows = kept
    rows.sort(key=lambda r: (r["year"], r["accession_number"]))
    stats["written"] = len(rows)
    return rows


def rows_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


# --- fetching the index --------------------------------------------------------------------

def ensure_index(year: int, quarter: int, index_dir: Path, user_agent: str | None, *, refresh: bool = False,
                 opener: Opener | None = None, throttle: Throttle | None = None) -> Path | None:
    """The cached master.idx for a quarter, downloading it if absent (or ``refresh``).
    Returns None for a quarter the SEC does not have (404, i.e. the future)."""
    p = index_path(index_dir, year, quarter)
    if is_present(p) and not refresh:
        return p
    if user_agent is None:
        user_agent = resolve_user_agent(None)
    try:
        n, _sha = download_to(index_url(year, quarter), p, user_agent, opener=opener, throttle=throttle)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    print(f"  fetched {year}/QTR{quarter}/master.idx ({n:,} bytes)", file=sys.stderr, flush=True)
    return p


def build_manifest(forms: Iterable[str], years: Iterable[int], data_root: Path, index_dir: Path, *,
                   include_amendments: bool = False, only_present: bool = True, user_agent: str | None = None,
                   refresh: bool = False, opener: Opener | None = None, throttle: Throttle | None = None,
                   stats: Counter | None = None) -> pa.Table:
    stats = stats if stats is not None else Counter()
    years = list(years)

    def index_rows() -> Iterator[IndexRow]:
        for y, q in quarters_for(years):
            p = ensure_index(y, q, index_dir, user_agent, refresh=refresh, opener=opener, throttle=throttle)
            if p is None:
                stats["quarters_unavailable"] += 1
                continue
            stats["quarters_read"] += 1
            yield from parse_master_idx(p.read_text(encoding="latin-1"))

    rows = build_rows(index_rows(), forms, data_root, include_amendments=include_amendments,
                      only_present=only_present, years=set(years), stats=stats)
    return rows_table(rows)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--forms", default=",".join(TEN_K_FORMS),
                   help=f"comma list of form types (default: the 10-K family; `10-Q-family` = {','.join(TEN_Q_FORMS)})")
    p.add_argument("--years", default=DEFAULT_YEARS, help="range 1993-2026 or list 2019,2020 (filing years)")
    p.add_argument("--data-root", default=None, metavar="DIR",
                   help=f"mirror root holding archives/edgar/data/ (default: ${control.DATA_ROOT_ENV})")
    p.add_argument("--index-dir", default=None, metavar="DIR", help="master.idx cache (default: <data-root>/full-index)")
    p.add_argument("--out", required=True, metavar="FILE.parquet")
    p.add_argument("--include-amendments", action="store_true", help="keep the /A forms of the listed forms")
    p.add_argument("--only-present", action=argparse.BooleanOptionalAction, default=True,
                   help="keep only rows whose submission file exists under the data root (default on)")
    p.add_argument("--user-agent", default=None, metavar="UA",
                   help='"<name> <email>", required by the SEC for any download (default: $EDGAR_ITEMIZE_USER_AGENT)')
    p.add_argument("--refresh", action="store_true", help="re-download the index files even if cached")


def main(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root) if args.data_root else control.data_root()
    index_dir = Path(args.index_dir) if args.index_dir else data_root / "full-index"
    years = parse_years(args.years)
    forms = expand_forms(args.forms)
    # Only quarters not already cached need the network, and only then a User-Agent.
    missing = [(y, q) for y, q in quarters_for(years) if args.refresh or not is_present(index_path(index_dir, y, q))]
    user_agent = resolve_user_agent(args.user_agent) if missing else None
    stats: Counter = Counter()
    t = build_manifest(forms, years, data_root, index_dir, include_amendments=args.include_amendments,
                       only_present=args.only_present, user_agent=user_agent, refresh=args.refresh, stats=stats)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out)
    print(f"{t.num_rows} filings -> {out}")
    print(f"  forms {','.join(forms)}{' (+ amendments)' if args.include_amendments else ''}; years {years[0]}-{years[-1]}; "
          f"quarters read {stats['quarters_read']}, unavailable {stats['quarters_unavailable']}")
    print(f"  index rows {stats['index_rows']}, form matched {stats['form_matched']}, amendments dropped "
          f"{stats['amendments_dropped']}, duplicate accessions collapsed {stats['duplicate_accession']}, "
          f"bad filenames {stats['bad_filename']}, not present under {data_root} dropped {stats['not_present']}"
          + ("" if args.only_present else " (--no-only-present: none dropped)"))
