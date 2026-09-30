"""edgar_itemize: deterministic agenda-structure parser for SEC EDGAR filings."""

from __future__ import annotations

import importlib.metadata as _md
from pathlib import Path as _Path


def _package_version() -> str:
    """The one version: ``pyproject.toml`` ``version``, read through package metadata.

    Falls back to reading ``pyproject.toml`` itself only when the package is not
    installed (a source checkout on ``sys.path`` without ``uv sync``)."""
    try:
        return _md.version("edgar-itemize")
    except _md.PackageNotFoundError:
        pass
    import tomllib

    pyproject = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    with open(pyproject, "rb") as f:
        return tomllib.load(f)["project"]["version"]


__version__ = _package_version()

# Both stamps are the package version (docs/VERSIONING.md). The two names and the two
# output columns (`parser_version`, `normalizer_version`) stay for schema stability.
PARSER_VERSION = __version__
NORMALIZER_VERSION = __version__
