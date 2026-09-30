"""R1 items 1 and 2: the public-index manifest builder and the fetch helper. No network."""
from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from edgar_itemize import fetch, manifest as m

DATA = Path(__file__).parent / "data"
SNIPPET = (DATA / "master_idx_snippet.idx").read_text(encoding="latin-1")


def _readable(p: Path) -> bool:
    """`p.exists()` that treats an unreadable path as absent: a path under another user's
    home raises PermissionError from `exists()` rather than returning False (a second user's
    clone against the main checkout's runs/, 2026-09-30)."""
    try:
        return p.exists()
    except OSError:
        return False


def _first_readable(*candidates: Path) -> Path:
    """The first candidate that exists and is readable; else the first, which then skips."""
    return next((c for c in candidates if _readable(c)), candidates[0])


# The manifest of record lives in the main checkout's runs/ (a worktree or another clone
# reaches it through EDGAR_ITEMIZE_RUNS, the shared runs directory); absent both, the
# tests that need it are skipped.
CONTROL_MANIFEST = _first_readable(
    Path(__file__).resolve().parents[1] / "runs" / "full_manifest_10k.parquet",
    Path(os.environ.get("EDGAR_ITEMIZE_RUNS", "/nonexistent")) / "full_manifest_10k.parquet",
)


# --- master.idx parsing --------------------------------------------------------------------

def test_parse_master_idx_skips_header_and_keeps_rows():
    rows = list(m.parse_master_idx(SNIPPET))
    assert len(rows) == 13
    assert rows[0] == m.IndexRow(1000045, "NICHOLAS FINANCIAL INC", "10-Q", "2020-02-14",
                                 "edgar/data/1000045/0001193125-20-039489.txt")
    assert all(isinstance(r.cik, int) for r in rows)


def test_parse_master_idx_company_name_with_pipe():
    r = next(r for r in m.parse_master_idx(SNIPPET) if r.cik == 1003410)
    assert r.company == "ODD | PIPE NAME LP"
    assert r.form == "10-K" and r.filed == "2020-01-15"
    assert m.accession_of(r.filename) == "0000783280-20-000010"


def test_accession_of():
    assert m.accession_of("edgar/data/1000045/0001193125-20-039489.txt") == "0001193125-20-039489"
    assert m.accession_of("edgar/data/1/garbage.txt") is None


# --- form and amendment filters -----------------------------------------------------------

def test_is_amendment_and_base_form():
    assert m.is_amendment("10-K/A") and m.is_amendment("10-KT/A")
    assert not m.is_amendment("10-K") and not m.is_amendment("10-KSB40")
    assert m.base_form("10-K/A") == "10-K" and m.base_form("10-K") == "10-K"


def test_select_form_amendment_handling():
    forms = m.TEN_K_FORMS
    assert m.select_form("10-K", forms, include_amendments=False)
    assert m.select_form("10-KSB40", forms, include_amendments=False)
    assert not m.select_form("10-K/A", forms, include_amendments=False)
    assert m.select_form("10-K/A", forms, include_amendments=True)
    assert m.select_form("10-KT/A", forms, include_amendments=True)
    assert not m.select_form("10-Q", forms, include_amendments=True)
    assert not m.select_form("10-Q/A", forms, include_amendments=True)
    # a form named with its /A explicitly is kept regardless of the flag
    assert m.select_form("10-Q/A", ("10-Q/A",), include_amendments=False)


def test_expand_forms_families():
    assert m.expand_forms("10-K,10-K405") == ("10-K", "10-K405")
    assert m.expand_forms("10-Q-family") == m.TEN_Q_FORMS
    assert m.expand_forms("10-k-family,10-Q") == m.TEN_K_FORMS + ("10-Q",)
    assert m.TEN_Q_FORMS == ("10-Q", "10QSB", "10-QSB", "10-QT")  # scripts/full_manifest_10q.py TYPES less /A


def test_build_rows_form_filter_counts(tmp_path):
    stats = Counter()
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, only_present=False, stats=stats)
    types = Counter(r["submission_type"] for r in rows)
    assert types == {"10-K": 4, "10-KT": 1, "10-KSB": 1}
    assert stats["amendments_dropped"] == 2  # 10-K/A and 10-KT/A
    assert stats["duplicate_accession"] == 2  # Ameren under three CIKs -> one row
    assert stats["written"] == 6
    rows_am = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, include_amendments=True, only_present=False)
    assert Counter(r["submission_type"] for r in rows_am) == {"10-K": 4, "10-KT": 1, "10-KSB": 1, "10-K/A": 1, "10-KT/A": 1}
    rows_q = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_Q_FORMS, tmp_path, only_present=False)
    assert Counter(r["submission_type"] for r in rows_q) == {"10-Q": 1, "10QSB": 1}


def test_build_rows_coregistrant_prefers_present_then_smallest_cik(tmp_path):
    rows = m.build_rows(m.parse_master_idx(SNIPPET), ("10-K",), tmp_path, only_present=False)
    amn = next(r for r in rows if r["accession_number"] == "0001002910-20-000094")
    assert amn["cik"] == "18654"  # smallest of 1002910, 100826, 18654 when none is present
    f = tmp_path / "archives" / "edgar" / "data" / "100826" / "0001002910-20-000094.txt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    rows = m.build_rows(m.parse_master_idx(SNIPPET), ("10-K",), tmp_path, only_present=False)
    amn = next(r for r in rows if r["accession_number"] == "0001002910-20-000094")
    assert amn["cik"] == "100826"  # the one whose file is present wins


def test_build_rows_only_present_drops_and_counts(tmp_path):
    f = tmp_path / "archives" / "edgar" / "data" / "1000209" / "0001193125-20-073307.txt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"<SEC-DOCUMENT>")
    empty = tmp_path / "archives" / "edgar" / "data" / "1000228" / "0001000228-20-000015.txt"
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"")  # zero-size counts as absent
    stats = Counter()
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, stats=stats)
    assert [r["accession_number"] for r in rows] == ["0001193125-20-073307"]
    assert stats["not_present"] == 5 and stats["written"] == 1


# --- year convention, ordering, URLs --------------------------------------------------------

def test_year_is_the_filing_year_not_the_period():
    # A 10-K for fiscal 2019 filed in 2020 carries year 2020, as scripts/full_manifest.py (fd.year).
    r = next(r for r in m.parse_master_idx(SNIPPET) if r.cik == 1000209)
    row = m.manifest_row(r, "0001193125-20-073307", Path("/root"))
    assert row["year"] == 2020 and isinstance(row["year"], int)
    assert row["agent_cik"] == "0001193125"
    assert row["archive_path"] == "/root/archives/edgar/data/1000209/0001193125-20-073307.txt"


def test_build_rows_year_window_and_sort(tmp_path):
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, only_present=False, years={2019})
    assert rows == []
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, only_present=False, years={2020})
    keys = [(r["year"], r["accession_number"]) for r in rows]
    assert keys == sorted(keys)


def test_parse_years():
    assert m.parse_years("2019-2020") == [2019, 2020]
    assert m.parse_years("2019,2021") == [2019, 2021]
    assert m.parse_years("2020") == [2020]
    assert m.parse_years("1993-1994, 2000") == [1993, 1994, 2000]
    with pytest.raises(ValueError):
        m.parse_years("2021-2019")


def test_urls_and_index_path(tmp_path):
    assert m.index_url(2019, 3) == "https://www.sec.gov/Archives/edgar/full-index/2019/QTR3/master.idx"
    assert m.index_path(tmp_path, 2019, 3) == tmp_path / "2019" / "QTR3" / "master.idx"
    assert fetch.submission_url("1000209", "0001193125-20-073307") == \
        "https://www.sec.gov/Archives/edgar/data/1000209/000119312520073307/0001193125-20-073307.txt"
    assert fetch.submission_url(20, "0000000020-99-000001") == \
        "https://www.sec.gov/Archives/edgar/data/20/000000002099000001/0000000020-99-000001.txt"
    assert m.quarters_for([2019])[:2] == [(2019, 1), (2019, 2)] and len(m.quarters_for([2019, 2020])) == 8


# --- schema --------------------------------------------------------------------------------

EXPECTED_SCHEMA = pa.schema([("accession_number", pa.string()), ("cik", pa.string()), ("submission_type", pa.string()),
                             ("year", pa.int64()), ("agent_cik", pa.string()), ("archive_path", pa.string())])


def test_manifest_schema_is_the_full_manifest_schema(tmp_path):
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, tmp_path, only_present=False)
    t = m.rows_table(rows)
    assert t.schema.equals(EXPECTED_SCHEMA)
    assert m.rows_table([]).schema.equals(EXPECTED_SCHEMA)


@pytest.mark.skipif(not _readable(CONTROL_MANIFEST), reason="runs/full_manifest_10k.parquet not on this machine")
def test_manifest_schema_equals_control_manifest_row(tmp_path):
    ctl = pq.read_table(CONTROL_MANIFEST)
    assert ctl.schema.equals(EXPECTED_SCHEMA)
    rows = m.build_rows(m.parse_master_idx(SNIPPET), m.TEN_K_FORMS, Path("/data/edgar"), only_present=False)
    t = m.rows_table(rows)
    assert t.schema.equals(ctl.schema)
    ours, theirs = t.slice(0, 1).to_pylist()[0], ctl.slice(0, 1).to_pylist()[0]
    assert list(ours) == list(theirs)
    assert {k: type(v) for k, v in ours.items()} == {k: type(v) for k, v in theirs.items()}
    assert "/archives/edgar/data/" in theirs["archive_path"]
    assert theirs["archive_path"].endswith(f"/{theirs['cik']}/{theirs['accession_number']}.txt")
    assert theirs["agent_cik"] == theirs["accession_number"][:10]


# --- user agent and HTTP -------------------------------------------------------------------

def test_user_agent_required(monkeypatch):
    monkeypatch.delenv(fetch.USER_AGENT_ENV, raising=False)
    with pytest.raises(SystemExit, match="User-Agent is required"):
        fetch.resolve_user_agent(None)
    with pytest.raises(SystemExit, match="<name> <email>"):
        fetch.resolve_user_agent("no-email-here")
    assert fetch.resolve_user_agent("Some Name someone@example.org") == "Some Name someone@example.org"
    monkeypatch.setenv(fetch.USER_AGENT_ENV, "Env Name env@example.org")
    assert fetch.resolve_user_agent(None) == "Env Name env@example.org"


class FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body, self.headers, self.status = body, email.message.Message(), 200
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(script: list, seen: list | None = None):
    """script items: bytes (a 200 body), an int (an HTTP error code) or an Exception."""
    it = iter(script)

    def opener(req, timeout=None):
        if seen is not None:
            seen.append(req)
        item = next(it)
        if isinstance(item, int):
            raise urllib.error.HTTPError(req.full_url, item, "err", email.message.Message(), io.BytesIO(b""))
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)
    return opener


def test_http_get_sends_user_agent_and_retries_on_429_503():
    seen: list = []
    sleeps: list = []
    op = make_opener([429, 503, b"ok"], seen)
    body = fetch.http_get("https://www.sec.gov/x", "N n@e.org", opener=op, throttle=fetch.Throttle(0, sleeper=lambda s: None),
                          sleeper=sleeps.append)
    assert body == b"ok" and len(seen) == 3
    assert all(r.get_header("User-agent") == "N n@e.org" for r in seen)
    assert sleeps == [1.0, 2.0]


def test_http_get_does_not_retry_404():
    seen: list = []
    with pytest.raises(urllib.error.HTTPError):
        fetch.http_get("https://www.sec.gov/x", "N n@e.org", opener=make_opener([404, b"never"], seen),
                       throttle=fetch.Throttle(0), sleeper=lambda s: None)
    assert len(seen) == 1


def test_throttle_spaces_requests():
    sleeps: list = []
    t = fetch.Throttle(0.1, sleeper=sleeps.append)
    t.wait(); t.wait()
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.1


# --- fetch: atomic write, resume, dry run, log ------------------------------------------------

ROWS = [dict(accession_number="0001193125-20-073307", cik="1000209"),
        dict(accession_number="0001000228-20-000015", cik="1000228"),
        dict(accession_number="0001000229-20-000021", cik="1000229")]


def _dest(root: Path, row: dict) -> Path:
    return root / "archives" / "edgar" / "data" / row["cik"] / f"{row['accession_number']}.txt"


def test_fetch_rows_writes_atomically_and_logs_sha(tmp_path):
    seen: list = []
    op = make_opener([b"<SEC-DOCUMENT>a", b"<SEC-DOCUMENT>b", b"<SEC-DOCUMENT>c"], seen)
    s = fetch.fetch_rows(ROWS, tmp_path, "N n@e.org", opener=op, throttle=fetch.Throttle(0))
    assert (s.present, s.fetched, s.failed) == (0, 3, 0)
    assert [r.full_url for r in seen] == [fetch.submission_url(r["cik"], r["accession_number"]) for r in ROWS]
    for row, body in zip(ROWS, (b"<SEC-DOCUMENT>a", b"<SEC-DOCUMENT>b", b"<SEC-DOCUMENT>c")):
        assert _dest(tmp_path, row).read_bytes() == body
    assert not list(tmp_path.rglob("*.part"))
    log = [json.loads(l) for l in (tmp_path / fetch.FETCH_LOG).read_text().splitlines()]
    assert [e["accession"] for e in log] == [r["accession_number"] for r in ROWS]
    import hashlib
    assert log[0]["sha256"] == hashlib.sha256(b"<SEC-DOCUMENT>a").hexdigest() and log[0]["bytes"] == 15
    assert set(log[0]) == {"accession", "cik", "bytes", "sha256", "url", "timestamp"}


def test_fetch_rows_resumes_and_limits(tmp_path):
    d = _dest(tmp_path, ROWS[0]); d.parent.mkdir(parents=True); d.write_bytes(b"already here")
    e = _dest(tmp_path, ROWS[1]); e.parent.mkdir(parents=True); e.write_bytes(b"")  # empty = absent
    seen: list = []
    s = fetch.fetch_rows(ROWS, tmp_path, "N n@e.org", limit=1, opener=make_opener([b"B"], seen), throttle=fetch.Throttle(0))
    assert (s.present, s.fetched, s.failed, s.skipped_limit) == (1, 1, 0, 1)
    assert len(seen) == 1 and seen[0].full_url.endswith("0001000228-20-000015.txt")
    assert d.read_bytes() == b"already here" and e.read_bytes() == b"B"
    assert not _dest(tmp_path, ROWS[2]).exists()


def test_fetch_rows_dry_run_touches_nothing(tmp_path):
    d = _dest(tmp_path, ROWS[0]); d.parent.mkdir(parents=True); d.write_bytes(b"x")

    def opener(req, timeout=None):
        raise AssertionError("network touched in dry run")
    s = fetch.fetch_rows(ROWS, tmp_path, "", dry_run=True, opener=opener)
    assert (s.rows, s.present, s.planned, s.fetched) == (3, 1, 2, 0)
    assert not (tmp_path / fetch.FETCH_LOG).exists()
    assert not _dest(tmp_path, ROWS[1]).exists()
    assert s.line(True).startswith("fetch (dry run): 3 manifest rows, 1 present, 2 would be fetched")


def test_fetch_rows_failure_leaves_no_partial_file(tmp_path, capsys):
    op = make_opener([404, b"ok"])
    s = fetch.fetch_rows(ROWS[:2], tmp_path, "N n@e.org", opener=op, throttle=fetch.Throttle(0), sleeper=lambda s: None)
    assert (s.fetched, s.failed) == (1, 1) and s.failures[0][0] == ROWS[0]["accession_number"]
    assert not _dest(tmp_path, ROWS[0]).exists() and not list(tmp_path.rglob("*.part"))
    assert _dest(tmp_path, ROWS[1]).read_bytes() == b"ok"
    assert "failed 0001193125-20-073307" in capsys.readouterr().err


def test_write_atomic_cleans_up_on_error(tmp_path, monkeypatch):
    dest = tmp_path / "a" / "b.txt"
    real = os.replace

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        fetch.write_atomic(dest, b"x")
    monkeypatch.setattr(os, "replace", real)
    assert not dest.exists() and not list(tmp_path.rglob("*.part"))


# --- ensure_index: cache and 404 -------------------------------------------------------------

def test_ensure_index_caches_and_treats_404_as_unavailable(tmp_path):
    seen: list = []
    op = make_opener([SNIPPET.encode("latin-1"), 404], seen)
    p = m.ensure_index(2020, 1, tmp_path, "N n@e.org", opener=op, throttle=fetch.Throttle(0))
    assert p == tmp_path / "2020" / "QTR1" / "master.idx" and p.read_text(encoding="latin-1") == SNIPPET
    assert seen[0].full_url == m.index_url(2020, 1)
    assert m.ensure_index(2020, 1, tmp_path, "N n@e.org", opener=op) == p  # cached: no request
    assert len(seen) == 1
    assert m.ensure_index(2099, 4, tmp_path, "N n@e.org", opener=op, throttle=fetch.Throttle(0)) is None
    assert not (tmp_path / "2099").exists() or not list((tmp_path / "2099").rglob("master.idx"))


def test_build_manifest_end_to_end_offline(tmp_path):
    idx = m.index_path(tmp_path / "idx", 2020, 1)
    idx.parent.mkdir(parents=True)
    idx.write_text(SNIPPET, encoding="latin-1")
    for q in (2, 3, 4):  # the other quarters: empty index files
        p = m.index_path(tmp_path / "idx", 2020, q); p.parent.mkdir(parents=True); p.write_text("CIK|Company Name|Form Type|Date Filed|Filename\n")
    stats = Counter()
    t = m.build_manifest(m.TEN_K_FORMS, [2020], tmp_path / "root", tmp_path / "idx", only_present=False, stats=stats)
    assert t.num_rows == 6 and stats["quarters_read"] == 4
    assert t.schema.equals(EXPECTED_SCHEMA)
    assert t["archive_path"][0].as_py().startswith(str(tmp_path / "root" / "archives" / "edgar" / "data"))
