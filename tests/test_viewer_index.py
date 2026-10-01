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
        # nothing lab-side is configured: every capability is off (RUNS, GOLD and the window
        # index pointed at an empty directory so the lab's own runs/ and gold/ are not seen)
        monkeypatch.setattr(appmod, "RUNS", tmp_path / "runs")
        monkeypatch.setattr(appmod, "GOLD", tmp_path / "gold")
        monkeypatch.setenv("EDGAR_ITEMIZE_WINDOWS", str(tmp_path / "runs" / "viewer" / "windows.parquet"))
        appmod._capabilities.cache_clear()
        try:
            assert client.get("/api/capabilities").json() == dict(
                banks=False, runs=False, manifests=False, gold=False, review=False, text_corpus=False)
        finally:
            appmod._capabilities.cache_clear()


# ------------------------------------------------------------------ open by path

FIXTURE = REPO / "tests" / "data" / "0001161697-11-000584_3.txt"
FIX_ACC, FIX_CIK = "0001161697-11-000584", "785968"


def _envelope(text: str) -> str:
    """The fixture exhibit wrapped in a minimal SGML submission (one EX-10 document, seq 3)."""
    return (f"<SEC-DOCUMENT>{FIX_ACC}.txt : 20110815\n<SEC-HEADER>{FIX_ACC}.hdr.sgml : 20110815\n"
            f"ACCESSION NUMBER:\t\t{FIX_ACC}\nCONFORMED SUBMISSION TYPE:\t10-Q\n"
            f"FILER:\n\tCOMPANY DATA:\n\t\tCOMPANY CONFORMED NAME:\t\t\tFIXTURE CO\n"
            f"\t\tCENTRAL INDEX KEY:\t\t\t{FIX_CIK.zfill(10)}\n</SEC-HEADER>\n"
            f"<DOCUMENT>\n<TYPE>10-Q\n<SEQUENCE>1\n<FILENAME>q.txt\n<TEXT>\nQUARTERLY REPORT\n</TEXT>\n</DOCUMENT>\n"
            f"<DOCUMENT>\n<TYPE>EX-10.50\n<SEQUENCE>3\n<FILENAME>ex10.txt\n<TEXT>\n{text}</TEXT>\n</DOCUMENT>\n"
            f"</SEC-DOCUMENT>\n")


def test_doc_by_path(monkeypatch, tmp_path):
    """/api/doc/by-path parses a submission file live and answers in /api/doc's shape: the bare
    fixture by absolute path (no SGML envelope: read as a text-corpus file), the same exhibit in
    an SGML envelope by a path relative to the data root (identical to /api/doc/{accession}
    resolved through a manifest), and 404s for a missing file or any path outside
    EDGAR_ITEMIZE_DATA_ROOT and EDGAR_ITEMIZE_TEXT_CORPUS (an existing file elsewhere included)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from fastapi.testclient import TestClient

    from edgar_itemize import control
    from edgar_itemize.viewer import app as appmod

    data = tmp_path / "data"
    rel = f"archives/edgar/data/{FIX_CIK}/{FIX_ACC}.txt"
    (data / rel).parent.mkdir(parents=True)
    (data / rel).write_bytes(_envelope(FIXTURE.read_bytes().decode("latin-1")).encode("latin-1"))
    runs = tmp_path / "runs"
    runs.mkdir()
    pq.write_table(pa.Table.from_pylist([dict(accession_number=FIX_ACC, sequence=3, cik=FIX_CIK, archive_path=rel,
                                              year=2011, submission_type="10-Q")]), runs / "full_manifest_ex10.parquet")
    monkeypatch.setenv("EDGAR_ITEMIZE_DATA_ROOT", str(data))
    monkeypatch.setattr(control, "DATA_ROOT", data)
    monkeypatch.setenv("EDGAR_ITEMIZE_TEXT_CORPUS", str(FIXTURE.parent))  # the bare fixture is readable only through this root
    monkeypatch.setattr(appmod, "RUNS", runs)
    appmod._manifests.cache_clear()
    appmod._parsed.cache_clear()
    appmod._parsed_path.cache_clear()
    client = TestClient(appmod.app)
    try:
        bare = client.get("/api/doc/by-path", params=dict(path=str(FIXTURE)))
        assert bare.status_code == 200, bare.text
        b = bare.json()
        assert (b["accession"], b["sequence"], b["grammar"], b["source"]) == (FIX_ACC, 3, "contract", "live")
        assert b["manifest"]["path"] == str(FIXTURE) and b["nodes"] and b["payload"]["start"] == 0

        acc = client.get(f"/api/doc/{FIX_ACC}", params=dict(sequence=3, corpus="ex10"))
        assert acc.status_code == 200, acc.text
        a = acc.json()
        assert set(b) == set(a)  # same shape as /api/doc/{accession}
        for k in ("blocks", "nodes", "rejected", "candidates", "payload", "bounds"):
            assert type(b[k]) is type(a[k])
        assert set(b["nodes"][0]) == set(a["nodes"][0]) and set(b["blocks"][0]) == set(a["blocks"][0])

        wrapped = client.get("/api/doc/by-path", params=dict(path=rel, sequence=3)).json()
        for k in ("accession", "cik", "sequence", "doc_type", "grammar", "blocks", "nodes", "rejected", "payload"):
            assert wrapped[k] == a[k], k
        primary = client.get("/api/doc/by-path", params=dict(path=rel)).json()
        assert primary["doc_type"] == "10-Q" and primary["sequence"] == 1  # select_primary on the header's form

        raw = client.get(f"/api/raw/{FIX_ACC}", params=dict(path=str(FIXTURE), start=0, end=40)).json()
        assert raw["text"] == FIXTURE.read_bytes()[:40].decode("latin-1")
        assert client.get(f"/api/original/{FIX_ACC}", params=dict(path=str(FIXTURE))).status_code == 200

        for bad in (str(tmp_path / "nope.txt"), "archives/nope.txt", "../outside.txt",
                    str(Path(__file__).resolve()), "/etc/hostname"):  # outside both roots: never readable, even if the file exists
            r = client.get("/api/doc/by-path", params=dict(path=bad))
            assert r.status_code == 404 and r.json()["detail"], bad
    finally:
        appmod._manifests.cache_clear()
        appmod._parsed.cache_clear()
        appmod._parsed_path.cache_clear()


def test_doc_by_accession_on_a_plain_mirror(tmp_path, monkeypatch):
    """An outsider's mirror has the archive layout and no control tables and no manifests:
    /api/doc/{accession} still opens the filing (the resolver finds archives/edgar/data/*/<acc>.txt)."""
    from fastapi.testclient import TestClient

    from edgar_itemize import control
    from edgar_itemize.viewer import app as appmod

    data = tmp_path / "data"
    rel = f"archives/edgar/data/{FIX_CIK}/{FIX_ACC}.txt"
    (data / rel).parent.mkdir(parents=True)
    (data / rel).write_bytes(_envelope(FIXTURE.read_bytes().decode("latin-1")).encode("latin-1"))
    monkeypatch.setenv("EDGAR_ITEMIZE_DATA_ROOT", str(data))
    monkeypatch.setattr(control, "DATA_ROOT", data)
    monkeypatch.setattr(appmod, "RUNS", tmp_path / "no_runs")
    appmod._manifests.cache_clear()
    appmod._parsed.cache_clear()
    client = TestClient(appmod.app)
    try:
        r = client.get(f"/api/doc/{FIX_ACC}", params=dict(sequence=3))
        assert r.status_code == 200, r.text
        j = r.json()
        assert (j["accession"], j["cik"], j["sequence"]) == (FIX_ACC, FIX_CIK, 3) and j["nodes"]
        missing = client.get("/api/doc/0000000000-00-000000")
        assert missing.status_code == 404 and "not found under the data root" in missing.json()["detail"]
    finally:
        appmod._manifests.cache_clear()
        appmod._parsed.cache_clear()
