"""The /turns endpoints and the viewer package's hygiene.

The endpoint tests run on a temporary summaries directory. The turn summary generator tests
(which read the lab artifacts under runs/) live in tests_lab/ (not part of the public
repository).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# the private corpus slug, built from fragments so this file does not carry it
_PRIVATE_CORPUS = "".join(["si", "enna"])


# ------------------------------------------------------------------ endpoints
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from edgar_itemize.viewer import app as appmod
    from edgar_itemize.viewer import turns

    (tmp_path / "turn12.json").write_text(json.dumps(dict(turn=12, closed="2026-09-24", generated_at="x", report="r")))
    (tmp_path / "turn13.json").write_text(json.dumps(dict(turn=13, closed=None, generated_at="y", report="r13")))
    (tmp_path / "notes.txt").write_text("ignored")
    monkeypatch.setattr(turns, "SUMMARIES", tmp_path)
    return TestClient(appmod.app)


def test_turns_page(client):
    r = client.get("/turns")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "/api/turns" in r.text and 'href="/"' in r.text


def test_viewer_package_names_no_private_corpus():
    """The viewer package ships no registry and names no private corpus or reviewer; the
    registries live in the repo's private viewer_config/ (EDGAR_ITEMIZE_VIEWER_CONFIG)."""
    pkg = ROOT / "src" / "edgar_itemize" / "viewer"
    assert not list(pkg.rglob("*.yaml"))
    for p in pkg.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".html", ".js", ".css"):
            assert _PRIVATE_CORPUS not in p.read_text(errors="replace").lower(), p


def test_api_turns_list(client):
    r = client.get("/api/turns")
    assert r.status_code == 200
    assert [t["turn"] for t in r.json()] == [13, 12]
    assert r.json()[1]["file"] == "docs/turn_summaries/turn12.json"


def test_api_turn_get_and_404(client):
    r = client.get("/api/turns/12")
    assert r.status_code == 200 and r.json()["closed"] == "2026-09-24"
    assert client.get("/api/turns/99").status_code == 404
    assert client.get("/api/turns/abc").status_code == 422
