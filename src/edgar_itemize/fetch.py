"""``edgar-itemize fetch``: download the raw full-submission files a manifest names.

Also holds the SEC fair-access HTTP helpers shared with ``manifest.py``: a declared
``User-Agent`` of the form ``"<name> <email>"`` (``--user-agent`` or
``EDGAR_ITEMIZE_USER_AGENT``), at most 10 requests per second, and retry with backoff on
429 / 503. Stdlib ``urllib.request`` only; nothing here is a core dependency.

Files are written atomically (``<dest>.part`` then ``os.replace``) and the SHA-256 of every
fetched file is appended to ``<data-root>/fetch_log.jsonl``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pyarrow.parquet as pq

from . import control

USER_AGENT_ENV = "EDGAR_ITEMIZE_USER_AGENT"
SEC_ARCHIVES = "https://www.sec.gov/Archives/"
MIN_INTERVAL = 0.1  # seconds between requests: the SEC's 10 requests/second ceiling
RETRY_STATUSES = (429, 503)
MAX_TRIES = 6
FETCH_LOG = "fetch_log.jsonl"

Opener = Callable[..., object]  # urllib.request.urlopen or a test double with the same signature


def resolve_user_agent(explicit: str | None) -> str:
    """The declared User-Agent, from the flag or the environment; exits if neither gives one.

    The SEC asks for "<company or person name> <contact email>"; the check here is only that
    both parts are present (some whitespace and an ``@``)."""
    ua = (explicit or os.environ.get(USER_AGENT_ENV) or "").strip()
    if not ua:
        raise SystemExit(f"a User-Agent is required by the SEC's fair-access policy: pass --user-agent "
                         f'"Name email@example.org" or set {USER_AGENT_ENV}')
    if "@" not in ua or len(ua.split()) < 2:
        raise SystemExit(f'User-Agent must be of the form "<name> <email>", got {ua!r}')
    return ua


def submission_url(cik: str | int, accession: str) -> str:
    return f"{SEC_ARCHIVES}edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt"


class Throttle:
    """Spaces requests at least ``min_interval`` seconds apart (process-local)."""

    def __init__(self, min_interval: float = MIN_INTERVAL, sleeper: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self.sleeper = sleeper
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = self._last + self.min_interval - now
        if gap > 0:
            self.sleeper(gap)
        self._last = time.monotonic()


_THROTTLE = Throttle()


def _decode_body(resp) -> bytes:
    body = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower() if getattr(resp, "headers", None) else ""
    if enc == "gzip":
        return gzip.decompress(body)
    if enc == "deflate":
        return zlib.decompress(body)
    return body


def http_get(url: str, user_agent: str, *, opener: Opener | None = None, throttle: Throttle | None = None,
             timeout: float = 120.0, sleeper: Callable[[float], None] = time.sleep, max_tries: int = MAX_TRIES) -> bytes:
    """GET ``url`` under the fair-access rules. Retries 429/503 (and connection errors) with
    exponential backoff, honouring ``Retry-After``; any other HTTP error propagates."""
    opener = opener or urllib.request.urlopen
    throttle = throttle or _THROTTLE
    delay = 1.0
    for attempt in range(1, max_tries + 1):
        throttle.wait()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        try:
            with opener(req, timeout=timeout) as resp:
                return _decode_body(resp)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUSES or attempt == max_tries:
                raise
            ra = e.headers.get("Retry-After") if e.headers else None
            wait = float(ra) if ra and ra.strip().isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == max_tries:
                raise
            wait = delay
        sleeper(wait)
        delay = min(delay * 2, 60.0)
    raise RuntimeError("unreachable")


def write_atomic(dest: Path, data: bytes) -> str:
    """Write ``data`` to ``dest`` through ``<dest>.part`` + rename; returns the SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def download_to(url: str, dest: Path, user_agent: str, **kw) -> tuple[int, str]:
    """GET ``url`` into ``dest`` atomically; returns (bytes, sha256)."""
    data = http_get(url, user_agent, **kw)
    return len(data), write_atomic(dest, data)


def is_present(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


@dataclass
class FetchSummary:
    rows: int = 0
    present: int = 0
    fetched: int = 0
    failed: int = 0
    planned: int = 0  # dry-run: files that would be fetched
    skipped_limit: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def line(self, dry_run: bool) -> str:
        if dry_run:
            return (f"fetch (dry run): {self.rows} manifest rows, {self.present} present, "
                    f"{self.planned} would be fetched, 0 failed")
        return (f"fetch: {self.rows} manifest rows, {self.present} present, {self.fetched} fetched, "
                f"{self.failed} failed" + (f", {self.skipped_limit} left by --limit" if self.skipped_limit else ""))


def fetch_rows(rows: list[dict], data_root: Path, user_agent: str, *, limit: int = 0, dry_run: bool = False,
               opener: Opener | None = None, throttle: Throttle | None = None, log_path: Path | None = None,
               progress: bool = False, **http_kw) -> FetchSummary:
    """Fetch every manifest row whose ``<data-root>/archives/edgar/data/<cik>/<accession>.txt``
    is absent or empty. ``limit`` caps the number of downloads attempted (present files do not
    count). In ``dry_run`` nothing is opened or written."""
    s = FetchSummary(rows=len(rows))
    log_path = log_path or data_root / FETCH_LOG
    logf = None
    try:
        for row in rows:
            acc, cik = row["accession_number"], row["cik"]
            dest = data_root / "archives" / "edgar" / "data" / str(int(cik)) / f"{acc}.txt"
            if is_present(dest):
                s.present += 1
                continue
            if dry_run:
                s.planned += 1
                continue
            if limit and s.fetched + s.failed >= limit:
                s.skipped_limit += 1
                continue
            url = submission_url(cik, acc)
            try:
                n, sha = download_to(url, dest, user_agent, opener=opener, throttle=throttle, **http_kw)
            except Exception as e:  # noqa: BLE001  (one bad file must not stop the run)
                s.failed += 1
                s.failures.append((acc, f"{type(e).__name__}: {e}"))
                print(f"  failed {acc}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                continue
            s.fetched += 1
            if logf is None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                logf = open(log_path, "a", encoding="utf-8")
            logf.write(json.dumps(dict(accession=acc, cik=str(int(cik)), bytes=n, sha256=sha, url=url,
                                       timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))) + "\n")
            logf.flush()
            if progress and s.fetched % 100 == 0:
                print(f"  fetched {s.fetched}", file=sys.stderr, flush=True)
    finally:
        if logf is not None:
            logf.close()
    return s


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", required=True, help="parse manifest (accession_number, cik, ...)")
    p.add_argument("--data-root", default=None, metavar="DIR",
                   help=f"mirror root holding archives/edgar/data/ (default: ${control.DATA_ROOT_ENV})")
    p.add_argument("--user-agent", default=None, metavar="UA",
                   help=f'"<name> <email>", required by the SEC (default: ${USER_AGENT_ENV})')
    p.add_argument("--limit", type=int, default=0, help="stop after N downloads attempted (0 = no limit)")
    p.add_argument("--dry-run", action="store_true", help="count present/missing files; no network, no writes")
    p.add_argument("--progress", action="store_true")


def main(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root) if args.data_root else control.data_root()
    rows = pq.read_table(args.manifest, columns=["accession_number", "cik"]).to_pylist()
    user_agent = None if args.dry_run else resolve_user_agent(args.user_agent)
    s = fetch_rows(rows, data_root, user_agent or "", limit=args.limit, dry_run=args.dry_run, progress=args.progress)
    print(s.line(args.dry_run))
    if s.failed:
        sys.exit(1)
