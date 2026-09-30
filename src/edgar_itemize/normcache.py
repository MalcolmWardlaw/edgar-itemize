"""Per-document normalisation cache (Turn 13 infrastructure, docs/turn13_decisions/infra_normalize_cache.md).

Caches ``prepare.Prepared`` -- the output of load + select + classify + normalise, ~98% of a
parse -- so a gate re-parse runs only candidates onward.

Key and invalidation
    The cache root is ``<dir>/<digest>/<kind>/<partition>/<hh>/<file>``. ``digest`` hashes
    the source bytes of every edgar_itemize module the prefix can execute (the static import
    closure of ``prepare`` and this module, function-level imports included, plus the package
    ``__init__``), the full Python version, and the installed version of every third-party
    distribution those modules import. Any edit to a prefix module is therefore a clean miss
    into a new directory, never a stale read; nothing relies on NORMALIZER_VERSION being
    bumped. Inside it, one file per manifest row, named from a hash of the row fields that
    determine the document (kind, archive_path, accession, and sequence or submission_type).
    Each file also records its full key, the digest, and the source file's size and mtime_ns;
    any mismatch on read is a miss.

Format
    ``MAGIC`` + zlib(level 1) of a protocol-5 pickle of ``{"meta": ..., "prep": Prepared}``.
    Per-document files, not per-partition shards: ProcessPoolExecutor workers of one
    partition read and write concurrently, and a per-document file needs no lock, no index
    and no append coordination -- the write is temp file + ``os.replace`` (atomic on one
    filesystem), so a reader sees either no file or a whole one, and a crashed writer leaves
    only a ``.tmp-*`` orphan. xfs holds ~1M files without trouble; the two-hex-digit
    sub-directory keeps each directory near a few hundred entries.

Safety
    A hit unpickles fresh objects for every parse, so nothing a later pass could do to a
    Block or Profile reaches another parse or the cache; a write pickles the Prepared before
    ``parse_prepared`` runs. Any exception while reading (missing, truncated, corrupt,
    wrong digest/key, changed source) is a miss, never a crash.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata as md
import os
import pickle
import sys
import tempfile
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MAGIC = b"EANC1\n"
FORMAT = 1
MODES = ("off", "read", "write", "readwrite")
PKG = "edgar_itemize"
PKG_DIR = Path(__file__).resolve().parent
ROOTS = ("prepare", "normcache")  # the prefix and the cache format itself


def _module_file(mod: str, pkg_dir: Path) -> Path | None:
    """edgar_itemize.a.b -> its source file under pkg_dir, or None if not a module."""
    parts = mod.split(".")[1:]
    base = pkg_dir.joinpath(*parts) if parts else pkg_dir
    if base.is_dir() and (base / "__init__.py").exists():
        return base / "__init__.py"
    f = base.with_suffix(".py") if parts else None
    return f if f is not None and f.exists() else None


def import_closure(roots=ROOTS, pkg_dir: Path = PKG_DIR) -> tuple[dict[str, Path], set[str]]:
    """Static import closure inside the package: {module: file}, plus the top-level names of
    every non-package import seen (for third-party versioning). Every Import/ImportFrom node
    anywhere in a module counts, including those inside functions (normalize_html is imported
    lazily), so the closure is a superset of what the prefix can execute."""
    seen: dict[str, Path] = {}
    external: set[str] = set()
    todo = [f"{PKG}.{r}" for r in roots] + [PKG]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        f = _module_file(mod, pkg_dir)
        if f is None:
            continue
        seen[mod] = f
        pkg_parts = mod.split(".") if f.name == "__init__.py" else mod.split(".")[:-1]
        for node in ast.walk(ast.parse(f.read_bytes(), filename=str(f))):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] == PKG:
                        todo.append(a.name)
                    else:
                        external.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = ".".join(pkg_parts[: len(pkg_parts) - node.level + 1])
                    target = f"{base}.{node.module}" if node.module else base
                else:
                    target = node.module or ""
                    if target.split(".")[0] != PKG:
                        external.add(target.split(".")[0])
                        continue
                todo.append(target)
                for a in node.names:  # `from . import control` names a submodule
                    todo.append(f"{target}.{a.name}")
        # every parent package's __init__ runs too
        for i in range(1, len(mod.split("."))):
            todo.append(".".join(mod.split(".")[:i]))
    return seen, external


def _third_party_versions(external: set[str]) -> dict[str, str]:
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    pkgmap = md.packages_distributions()
    out: dict[str, str] = {}
    for name in sorted(external - stdlib):
        for dist in pkgmap.get(name, [name]):
            try:
                out[dist] = md.version(dist)
            except md.PackageNotFoundError:
                out[dist] = "?"
    return out


def digest_parts(pkg_dir: Path = PKG_DIR) -> dict:
    mods, external = import_closure(pkg_dir=pkg_dir)
    return dict(
        format=FORMAT,
        python=sys.version,
        modules={m: hashlib.sha256(f.read_bytes()).hexdigest() for m, f in sorted(mods.items())},
        third_party=_third_party_versions(external),
    )


def compute_digest(pkg_dir: Path = PKG_DIR) -> str:
    parts = digest_parts(pkg_dir)
    blob = repr(sorted((k, sorted(v.items()) if isinstance(v, dict) else v) for k, v in parts.items()))
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


@lru_cache(maxsize=1)
def prefix_digest() -> str:
    return compute_digest()


def row_key(row: dict, kind: str) -> tuple:
    """The manifest fields that determine which document the prefix produces."""
    if kind in ("ex10", "ex13", "text"):
        sel = ("seq", row.get("sequence"))
    else:
        sel = ("type", row.get("submission_type", "10-K"))
    return (kind, str(row["archive_path"]), row["accession_number"], sel)


@dataclass(frozen=True)
class NormCache:
    root: str
    mode: str = "readwrite"

    @property
    def reads(self) -> bool:
        return self.mode in ("read", "readwrite")

    @property
    def writes(self) -> bool:
        return self.mode in ("write", "readwrite")

    def path_for(self, row: dict, kind: str) -> Path:
        key = row_key(row, kind)
        h = hashlib.sha1(repr(key).encode()).hexdigest()
        part = str(row.get("year")) if row.get("year") is not None else "none"
        seq = row.get("sequence")
        name = f"{row['accession_number']}_{seq if seq is not None else 'p'}_{h[:16]}.nc"
        return Path(self.root) / prefix_digest() / kind / part / h[:2] / name

    @staticmethod
    def _source_stat(src) -> tuple[int, int]:
        st = os.stat(src)
        return st.st_size, st.st_mtime_ns

    def get(self, row: dict, kind: str, src):
        """(hit, prep) -- prep may be None on a hit (no primary document). Any failure -> miss."""
        try:
            p = self.path_for(row, kind)
            with open(p, "rb") as f:
                raw = f.read()
            if not raw.startswith(MAGIC):
                return False, None
            obj = pickle.loads(zlib.decompress(raw[len(MAGIC):]))
            meta = obj["meta"]
            if (meta["format"] != FORMAT or meta["digest"] != prefix_digest() or meta["key"] != row_key(row, kind)
                    or tuple(meta["source"]) != self._source_stat(src)):
                return False, None
            return True, obj["prep"]
        except Exception:  # noqa: BLE001 -- a bad cache file is a miss, never a crash
            return False, None

    def put(self, row: dict, kind: str, src, prep) -> bool:
        tmp = None
        try:
            p = self.path_for(row, kind)
            meta = dict(format=FORMAT, digest=prefix_digest(), key=row_key(row, kind), source=self._source_stat(src))
            data = MAGIC + zlib.compress(pickle.dumps(dict(meta=meta, prep=prep), protocol=5), 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=p.parent)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
            return True
        except Exception:  # noqa: BLE001 -- a failed write never fails the parse
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False
