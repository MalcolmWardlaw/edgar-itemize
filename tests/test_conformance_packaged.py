"""The conformance set inside the package equals the repository's canonical copy, and
`edgar-itemize verify` finds it when no conformance/ directory is in reach (a pip install)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from edgar_itemize import __version__, conformance

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_set_equals_repository_set():
    repo = ROOT / "conformance" / __version__
    pkg = conformance.packaged_set_dir()
    assert pkg.is_dir(), pkg
    for name in ("manifest.parquet", "expected.parquet"):
        assert (pkg / name).read_bytes() == (repo / name).read_bytes(), name


def test_default_set_dir_falls_back_to_the_package(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(conformance, "_repo_root", lambda: tmp_path / "nowhere")
    assert conformance.default_set_dir() == conformance.packaged_set_dir()


def test_verify_runs_from_a_bare_directory(tmp_path):
    """From a directory with no conformance/ (and an empty mirror), verify reports every
    mirror-backed row as input missing and the fixture row as missing too, and exits 0."""
    (tmp_path / "empty").mkdir()
    out = subprocess.run([sys.executable, "-m", "edgar_itemize.cli", "verify", "--data-root", str(tmp_path / "empty"),
                          "--repo-root", str(tmp_path), "--workers", "1"], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"verify {__version__}: 2001 documents" in out.stdout and "2001 input missing" in out.stdout, out.stdout
