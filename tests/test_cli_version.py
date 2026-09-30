"""`edgar-itemize --version` prints the package version (CONTRIBUTING.md, reporting a wrong parse)."""
import subprocess
import sys

from edgar_itemize import __version__


def test_version_flag():
    out = subprocess.run([sys.executable, "-m", "edgar_itemize.cli", "--version"], capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.strip() == f"edgar-itemize {__version__}"
