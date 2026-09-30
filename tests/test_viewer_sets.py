"""Saved review sets and the selection builder (src/edgar_itemize/viewer/sets.py).

Endpoint tests run on a synthetic window index, a synthetic runs/judge/ and a temporary
sets directory (EDGAR_ITEMIZE_WINDOWS, EDGAR_ITEMIZE_RUNS, EDGAR_ITEMIZE_SETS), so nothing
lands in gold/. The last test reads the committed turn12.json against the real index.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from urllib.parse import parse_qs

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _rows() -> list[dict]:
    out = []
    for i in range(40):
        corpus = ["10k", "10q", "ex10"][i % 3]
        acc = f"0000000001-20-{i // 2:06d}"
        seq = None if corpus == "10k" else (1 if corpus == "10q" else 2 + i % 2)
        out.append(dict(
            window_id=f"bank{i % 2}:{acc}:{seq or 0}:{1000 + i}:h{i:07d}", bank=f"bank{i % 2}", turn=12 - i % 2,
            corpus=corpus, accession=acc, sequence=seq, anchor=1000 + i, label=f"ITEM {i}", marked_line=f"Item {i}. Title",
            side="accepted" if i % 4 < 2 else "rejected", reason="toc" if i % 5 == 0 else "deep",
            verdict="heading" if i % 3 else "not_heading", phasec_status=None if i % 2 else "accepted",
            era="text", year=1995 + i % 4, company=f"CO {i}", raters=None, stratum=None, task="heading"))
    return out


def _write_index(path: Path, rows: list[dict]):
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    idx = tmp_path / "windows.parquet"
    _write_index(idx, _rows())
    runs = tmp_path / "runs"
    (runs / "judge").mkdir(parents=True)
    # a changed-docs artifact (10-K: sequence 1 in the file, null in the index) and a losses one
    pq.write_table(pa.Table.from_pylist([
        dict(accession_number="0000000001-20-000000", sequence=1, year="1995"),
        dict(accession_number="0000000001-20-000003", sequence=1, year="1995"),
    ]), runs / "judge" / "turn12-b1-10k-changed-docs.parquet")
    pq.write_table(pa.Table.from_pylist([
        dict(accession_number="0000000001-20-000001", sequence=3, cls="collateral", kind="lost"),
        dict(accession_number="0000000001-20-000004", sequence=2, cls="chain_resolve", kind="moved_later"),
        dict(accession_number="0000000001-20-000004", sequence=3, cls="collateral", kind="moved_later"),
    ]), runs / "judge" / "turn12-b1-contract-losses-ex10.parquet")
    monkeypatch.setenv("EDGAR_ITEMIZE_WINDOWS", str(idx))
    monkeypatch.setenv("EDGAR_ITEMIZE_RUNS", str(runs))
    monkeypatch.setenv("EDGAR_ITEMIZE_SETS", str(tmp_path / "sets"))
    return tmp_path


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from edgar_itemize.viewer import app as appmod

    return TestClient(appmod.app)


def test_sets_dir_env(monkeypatch, tmp_path):
    from edgar_itemize.viewer import sets

    monkeypatch.delenv("EDGAR_ITEMIZE_SETS", raising=False)
    monkeypatch.setenv("EDGAR_ITEMIZE_GOLD", str(tmp_path / "g"))
    assert sets.sets_dir() == tmp_path / "g" / "review" / "sets"
    monkeypatch.setenv("EDGAR_ITEMIZE_SETS", str(tmp_path / "s"))
    assert sets.sets_dir() == tmp_path / "s"


def test_save_and_reload_round_trip(client, env):
    ids = [r["window_id"] for r in _rows()[:3]]
    r = client.post("/api/sets", json=dict(name="Three windows", description="d",
                                           items=[dict(window_id=ids[0], note="look here"), dict(window_id=ids[1]), ids[2]]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["slug"] == "three-windows" and j["n"] == 3 and j["link"] == "/?set=three-windows"
    f = env / "sets" / "three-windows.json"
    assert f.exists() and not (ROOT / "gold" / "review" / "sets" / "three-windows.json").exists()
    s = client.get("/api/sets/three-windows").json()
    on_disk = json.loads(f.read_text())
    assert s["items"] == on_disk["items"]
    assert [i["window_id"] for i in s["items"]] == ids
    first = s["items"][0]
    assert set(first) == {"window_id", "bank", "corpus", "accession", "sequence", "anchor", "label", "note"}
    assert first["note"] == "look here" and s["items"][1]["note"] is None
    assert (first["bank"], first["corpus"], first["anchor"], first["label"]) == ("bank0", "10k", 1000, "ITEM 0")
    for k in ("name", "slug", "created", "author", "description", "query", "seed", "sample_n"):
        assert k in s
    assert s["created"].endswith("+00:00") and s["author"] and s["legacy"] is False
    lst = client.get("/api/sets").json()
    assert lst[0]["slug"] == "three-windows" and lst[0]["n"] == 3 and lst[0]["legacy"] is False
    leg = [x for x in lst if x["legacy"]]
    assert [x["slug"] for x in leg] == ["legacy-step-1-review-set"] and leg[0]["n"] == 11
    L = client.get("/api/sets/legacy-step-1-review-set").json()
    assert L["legacy"] and L["items"][0]["accession"] == "0000912057-95-006316"


def test_immutable(client, env):
    wid = _rows()[0]["window_id"]
    assert client.post("/api/sets", json=dict(name="x", slug="fixed", items=[wid])).status_code == 200
    before = (env / "sets" / "fixed.json").read_text()
    r = client.post("/api/sets", json=dict(name="other", slug="fixed", items=[_rows()[1]["window_id"]]))
    assert r.status_code == 409
    assert r.json()["detail"]["suggest"] == "fixed-2"
    assert (env / "sets" / "fixed.json").read_text() == before
    assert client.post("/api/sets", json=dict(name="x", slug="legacy-step-1-review-set", items=[wid])).status_code == 409


@pytest.mark.parametrize("slug", ["../escape", "Bad Slug", "a/b", "a--b", "-a", "a_b", "x" * 81, "..", "a.json"])
def test_slug_validation(client, env, slug):
    r = client.post("/api/sets", json=dict(name="n", slug=slug, items=[_rows()[0]["window_id"]]))
    assert r.status_code == 422
    assert not (env / "sets").exists() or not any((env / "sets").iterdir())


def test_get_rejects_bad_slug(client):
    assert client.get("/api/sets/Bad_Slug").status_code == 422
    assert client.get("/api/sets/..%2F..%2Fetc").status_code in (404, 422)
    assert client.get("/api/sets/no-such-set").status_code == 404


def test_slugify():
    from edgar_itemize.viewer.sets import SLUG, slugify

    assert slugify("  Turn 12: recall costs (B.3)! ") == "turn-12-recall-costs-b-3"
    assert SLUG.match(slugify("x" * 200)) and len(slugify("x" * 200)) == 80


def test_unknown_window_ids_rejected(client, env):
    good = _rows()[0]["window_id"]
    r = client.post("/api/sets", json=dict(name="bad", items=[good, "bank9:nope:0:1:deadbeef"]))
    assert r.status_code == 422
    assert r.json()["detail"]["unknown"] == ["bank9:nope:0:1:deadbeef"]
    assert not (env / "sets" / "bad.json").exists()
    assert client.post("/api/sets", json=dict(name="dup", items=[good, good])).status_code == 422
    assert client.post("/api/sets", json=dict(name="empty", items=[])).status_code == 422
    assert client.post("/api/sets", json=dict(name="q", query=dict(nosuchcol=["x"]))).status_code == 422


def test_deterministic_sampling(client, env):
    q = "side=accepted&sample_n=5&seed=7"
    a = client.get(f"/api/builder/select?{q}").json()
    b = client.get(f"/api/builder/select?{q}").json()
    assert a["total"] == 20 and a["n"] == 5 and a["rows"] == b["rows"]
    ids = [r["window_id"] for r in a["rows"]]
    assert ids == sorted(ids)
    assert ids != [r["window_id"] for r in client.get("/api/builder/select?side=accepted&sample_n=5&seed=8").json()["rows"]]
    # the index's row order does not matter
    rows = _rows()
    random.Random(1).shuffle(rows)
    _write_index(env / "windows.parquet", rows)
    import os
    import time

    t = time.time() + 5
    os.utime(env / "windows.parquet", (t, t))
    assert [r["window_id"] for r in client.get(f"/api/builder/select?{q}").json()["rows"]] == ids
    # saving the query draws the same list
    r = client.post("/api/sets", json=dict(name="sampled", query=dict(side=["accepted"]), sample_n=5, seed=7))
    assert r.status_code == 200, r.text
    s = client.get("/api/sets/sampled").json()
    assert [i["window_id"] for i in s["items"]] == ids
    assert (s["sample_n"], s["seed"], s["query"]) == (5, 7, dict(side=["accepted"]))
    # take all
    r = client.post("/api/sets", json=dict(name="all accepted", query=dict(side=["accepted"])))
    assert r.json()["n"] == 20 and client.get("/api/sets/all-accepted").json()["sample_n"] is None


def test_builder_doc_facets(client):
    j = client.get("/api/builder/select?doc=t12-b1-10k-changed&limit=0").json()
    # the artifact is a 10-K one: ...000 holds window 0 (10-K) and 1 (10-Q), ...003 windows
    # 6 (10-K) and 7 (10-Q); only the 10-K windows match, on accession alone
    assert sorted(r["label"] for r in j["rows"]) == ["ITEM 0", "ITEM 6"]
    j = client.get("/api/builder/select?doc=t12-b1-ex10-losses&limit=0").json()
    # EX-10 windows are keyed by accession and sequence: window 2 is (...001, 2), not the
    # artifact's (...001, 3); window 8 is (...004, 2), which matches
    got = {(r["accession"], r["sequence"]) for r in j["rows"]}
    assert got <= {("0000000001-20-000001", 3), ("0000000001-20-000004", 2), ("0000000001-20-000004", 3)}
    assert j["total"] == len(got) == 1
    assert client.get("/api/builder/select?doc=t12-b1-ex10-losses&doc_class=chain_resolve").json()["total"] == 1
    assert client.get("/api/builder/select?doc=t12-b1-ex10-losses&doc_class=collateral").json()["total"] == 0
    assert client.get("/api/builder/select?doc=nope").status_code == 422
    assert client.get("/api/builder/select?doc_class=collateral").status_code == 422
    f = client.get("/api/builder/facets?doc=t12-b1-ex10-losses").json()
    assert f["total"] == 1
    d = {x["id"]: x for x in f["docs"]}
    assert d["t12-b1-10k-changed"]["windows"] == 2 and d["t12-b1-10q-changed"]["missing"]
    assert d["t12-b1-ex10-losses"]["classes"] == [dict(value="chain_resolve", count=1)]
    assert set(f["facets"]) == {"bank", "turn", "corpus", "side", "verdict", "reason", "era", "year", "phasec_status"}
    # a column's counts skip its own filter
    f = client.get("/api/builder/facets?side=accepted").json()
    assert {x["value"]: x["count"] for x in f["facets"]["side"]} == dict(accepted=20, rejected=20)
    assert f["total"] == 20


# ------------------------------------------------------------------ the committed turn summary
@pytest.mark.skipif(not (ROOT / "runs/viewer/windows.parquet").exists(), reason="window index absent")
def test_turn12_json_resolves_in_the_index():
    """Every key and every link in docs/turn_summaries/turn12.json resolves in the real
    window index: keys to exactly one row each, filters to exactly their count."""
    s = json.loads((ROOT / "docs/turn_summaries/turn12.json").read_text())
    rows = pq.read_table(ROOT / "runs/viewer/windows.parquet").to_pylist()
    ids = {}
    for r in rows:
        ids[r["window_id"]] = ids.get(r["window_id"], 0) + 1

    def walk(o):
        if isinstance(o, dict):
            if "href" in o and "filter" in o:
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    links = list(walk(s))
    keys = [k for d in links for k in d.get("keys", [])]
    assert len(keys) > 100 and all(ids.get(k) == 1 for k in keys)
    params = set(s["filter_contract"]["params"])
    for d in links:
        inner = parse_qs(parse_qs(d["href"][2:])["windows"][0])
        assert set(inner) <= params
        n = sum(1 for r in rows if all(str(r.get(k)) in v for k, v in inner.items()))
        want = d["k"] if "k" in d else d["n"]
        assert n == want, (inner, n, want)
