"""CITATION.cff and CHANGELOG.md agree with the package version.

The 1.0.0 tag went out with CITATION.cff undated and CHANGELOG headed `unreleased`
(docs/release_decisions/r2_tag.md section 4). These checks make that drift a test failure:
the citation's `version` is the package version (pre-release suffix stripped, so an rc
rehearsal tag still passes), `date-released` is present and a real date, and the CHANGELOG
has a section for the version.
"""
from __future__ import annotations

import datetime as dt
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CFF = ROOT / "CITATION.cff"
CHANGELOG = ROOT / "CHANGELOG.md"

pytestmark = pytest.mark.skipif(not (PYPROJECT.is_file() and CFF.is_file()), reason="not a source checkout")


def base_version(v: str) -> str:
    """1.0.0rc3 -> 1.0.0; 1.1.0.dev2 -> 1.1.0; 1.0.0 -> 1.0.0."""
    m = re.match(r"(\d+\.\d+\.\d+)", v)
    assert m, f"unparseable version {v!r}"
    return m.group(1)


def package_version() -> str:
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


def citation() -> dict:
    return yaml.safe_load(CFF.read_text())


def test_citation_version_matches_package():
    assert str(citation()["version"]) == base_version(package_version())


def test_citation_is_dated():
    d = citation().get("date-released")
    assert d, "CITATION.cff has no date-released; set it to the tag date (scripts/release/tag_prep.sh)"
    dt.date.fromisoformat(str(d))  # raises on a malformed date


def test_citation_doi_is_concept_doi():
    doi = str(citation().get("doi", ""))
    assert re.fullmatch(r"10\.5281/zenodo\.\d+", doi), f"doi should be the Zenodo concept DOI, got {doi!r}"


@pytest.mark.skipif(not CHANGELOG.is_file(), reason="no CHANGELOG")
def test_changelog_has_section_for_version():
    v = base_version(package_version())
    text = CHANGELOG.read_text()
    assert re.search(rf"^## \[{re.escape(v)}\] - ", text, re.M), f"CHANGELOG.md has no '## [{v}] - ...' section"
