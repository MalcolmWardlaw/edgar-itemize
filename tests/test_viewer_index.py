"""Viewer: the app with and without a viewer config.

The registries (banks.yaml, runs.yaml) live in a private viewer_config/ directory, read
through EDGAR_ITEMIZE_VIEWER_CONFIG; the module points the variable there unless it already
names a directory holding them. With no config the viewer serves live parses of the four
built-in EDGAR corpora only, which is what this file checks.

The window-index, stored-run and registry tests that read the private registries and the
lab runs live in tests_lab/ (not part of the public repository).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

CONFIG = REPO / "viewer_config"


@pytest.fixture(scope="module", autouse=True)
def viewer_config():
    """EDGAR_ITEMIZE_VIEWER_CONFIG -> the repo's viewer_config/ unless the caller set it to a
    directory that holds the registries."""
    import os

    with pytest.MonkeyPatch.context() as mp:
        cur = os.environ.get("EDGAR_ITEMIZE_VIEWER_CONFIG")
        if not cur or not (Path(cur) / "runs.yaml").is_file():
            mp.setenv("EDGAR_ITEMIZE_VIEWER_CONFIG", str(CONFIG))
        from edgar_itemize.viewer import app as appmod

        appmod._manifests.cache_clear()
        yield
        appmod._manifests.cache_clear()


# ------------------------------------------------------------------ no viewer config

def test_no_viewer_config_is_empty(monkeypatch, tmp_path):
    """With EDGAR_ITEMIZE_VIEWER_CONFIG unset (or naming a directory without the files) the
    app imports, its registries are empty and the viewer serves live parses only."""
    import importlib

    from fastapi.testclient import TestClient

    from edgar_itemize.viewer import runread, sets

    for val in (None, str(tmp_path)):
        if val is None:
            monkeypatch.delenv("EDGAR_ITEMIZE_VIEWER_CONFIG", raising=False)
        else:
            monkeypatch.setenv("EDGAR_ITEMIZE_VIEWER_CONFIG", val)
        appmod = importlib.import_module("edgar_itemize.viewer.app")
        assert runread.registry() == {"corpora": {}, "baselines": {}}
        assert runread.baselines() == {} and runread.latest_baseline("10k") is None
        assert runread.corpus_kind("ex10") == "ex10"  # the built-in layouts survive
        assert appmod.corpora() == ("10k", "10q", "ex10", "ex13") and appmod.text_corpus() is None
        assert [c for c, _, _ in appmod.manifest_specs()] == ["10k", "ex10", "10k", "10q", "ex13"]
        assert sets.doc_artifacts() == sets.DOC_ARTIFACTS
        client = TestClient(appmod.app)
        assert client.get("/api/corpora").json() == dict(corpora=["10k", "10q", "ex10", "ex13"], text=None)
        assert client.get("/api/search", params=dict(kind="text")).status_code == 422
