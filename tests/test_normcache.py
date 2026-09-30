"""The per-document normalisation cache (normcache.py, prepare.py, cli --norm-cache).

Real documents from the corpus manifests (skipped where the archive is not on this
machine) plus the contracts-text fixture: a 2015 HTML 10-K, a 1996 text 10-K, a 10-Q, an
EX-10, an EX-13 and a --kind text contract.
"""

from __future__ import annotations

import copy
import os
import pickle
import shutil
from pathlib import Path

import pytest

import edgar_itemize
from edgar_itemize import normcache
from edgar_itemize.cli import _parse_one, _parse_one_cached
from edgar_itemize.normcache import NormCache
from edgar_itemize.prepare import load_row, prepare_document, source_path
from edgar_itemize.pipeline import parse_prepared

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "data" / "0001161697-11-000584_3.txt"
# The EDGAR mirror root comes from EDGAR_ITEMIZE_DATA_ROOT; with it unset every
# corpus-backed case skips in _require before the path is touched.
ARCH = f"{os.environ.get('EDGAR_ITEMIZE_DATA_ROOT', '')}/archives/edgar/data"

ROWS = {
    "10k_html": ("10k", dict(accession_number="0000002178-15-000014", cik="2178", submission_type="10-K", year=2015,
                             archive_path=f"{ARCH}/2178/0000002178-15-000014.txt")),
    "10k_text_1996": ("10k", dict(accession_number="0000001952-96-000002", cik="1952", submission_type="10-K", year=1996,
                                  archive_path=f"{ARCH}/1952/0000001952-96-000002.txt")),
    "10q": ("10k", dict(accession_number="0000002178-10-000018", cik="2178", submission_type="10-Q", year=2010,
                        archive_path=f"{ARCH}/2178/0000002178-10-000018.txt")),
    "ex10": ("ex10", dict(accession_number="0000950144-05-010573", cik="217084", sequence=4, year=2005,
                          archive_path=f"{ARCH}/217084/0000950144-05-010573.txt")),
    "ex13": ("ex13", dict(accession_number="0000002969-01-500028", cik="2969", submission_type="10-K", sequence=5, year=2001,
                          archive_path=f"{ARCH}/2969/0000002969-01-500028.txt")),
}


def test_runs_the_worktree_source():
    # the package under test is this checkout's src/, not another checkout's
    assert Path(edgar_itemize.__file__).resolve().parent == (REPO / "src" / "edgar_itemize").resolve()


def _text_row(tmp_path: Path) -> dict:
    p = tmp_path / "src" / FIXTURE.name
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, p)
    return dict(accession_number="0001161697-11-000584", cik="785968", sequence=3, year=2011, archive_path=str(p))


def _cases(tmp_path):
    out = [("text", _text_row(tmp_path), "contracts_text")]
    for name, (kind, row) in ROWS.items():
        out.append((kind, row, name))
    return out


def _require(row):
    from edgar_itemize import control

    if control.DATA_ROOT is None:
        pytest.skip(f"{control.DATA_ROOT_ENV} not set: corpus-backed case")
    if not Path(row["archive_path"]).exists():
        pytest.skip(f"archive not on this machine: {row['archive_path']}")


@pytest.mark.parametrize("case", ["contracts_text", *ROWS])
def test_hit_equals_miss_equals_off(tmp_path, case):
    kind, row = ("text", _text_row(tmp_path)) if case == "contracts_text" else ROWS[case]
    _require(row)
    cache = NormCache(str(tmp_path / "cache"), "readwrite")
    off = _parse_one(row, kind, False)
    assert off[1].get("error") is None, off[1].get("error")
    *cold, st1 = _parse_one_cached(row, kind, False, cache)
    *warm, st2 = _parse_one_cached(row, kind, False, cache)
    *ro, st3 = _parse_one_cached(row, kind, False, NormCache(cache.root, "read"))
    assert (st1, st2, st3) == ("write", "hit", "hit")
    assert tuple(cold) == off and tuple(warm) == off and tuple(ro) == off
    assert len(off[0]) > 1  # a real tree, not an empty parse


@pytest.mark.parametrize("case", ["contracts_text", *ROWS])
def test_round_trip_and_later_passes_do_not_mutate_prepared(tmp_path, case):
    kind, row = ("text", _text_row(tmp_path)) if case == "contracts_text" else ROWS[case]
    _require(row)
    sub, doc = load_row(row, kind)
    prep = prepare_document(sub, doc, row["cik"])
    before = copy.deepcopy(prep)
    cache = NormCache(str(tmp_path / "cache"), "readwrite")
    src = source_path(row, kind)
    assert cache.put(row, kind, src, prep)
    hit, back = cache.get(row, kind, src)
    # (pickle bytes are not compared: memo sharing of equal strings differs after a round trip)
    assert hit and back is not prep and back == before
    assert back.doc == prep.doc and back.profile == prep.profile and back.normalized_text == prep.normalized_text
    assert back.blocks == prep.blocks and back.doc_types == prep.doc_types and back.header_type == prep.header_type
    # every later pass reads blocks and profile but must not write them
    parse_prepared(prep, row["cik"])
    parse_prepared(back, row["cik"], synth_root=(kind == "ex13"))
    assert prep == before and back == before


def test_modes(tmp_path):
    row = _text_row(tmp_path)
    root = str(tmp_path / "cache")
    off = _parse_one(row, "text", False)
    *r, st = _parse_one_cached(row, "text", False, NormCache(root, "read"))
    assert st == "miss" and tuple(r) == off and not any((tmp_path / "cache").rglob("*.nc"))
    *r, st = _parse_one_cached(row, "text", False, NormCache(root, "write"))
    assert st == "write" and tuple(r) == off
    *r, st = _parse_one_cached(row, "text", False, NormCache(root, "write"))  # write never reads
    assert st == "write"
    *r, st = _parse_one_cached(row, "text", False, NormCache(root, "off"))
    assert st == "off" and tuple(r) == off
    assert len(list((tmp_path / "cache").rglob("*.nc"))) == 1
    assert not list((tmp_path / "cache").rglob(".tmp-*"))


@pytest.mark.parametrize("damage", ["truncate", "garbage", "empty", "bad_magic", "wrong_key", "wrong_digest"])
def test_corrupt_or_partial_file_is_a_clean_miss(tmp_path, damage):
    row = _text_row(tmp_path)
    cache = NormCache(str(tmp_path / "cache"), "readwrite")
    off = _parse_one(row, "text", False)
    _parse_one_cached(row, "text", False, cache)
    p = cache.path_for(row, "text")
    raw = p.read_bytes()
    if damage == "truncate":
        p.write_bytes(raw[: len(raw) // 2])
    elif damage == "garbage":
        p.write_bytes(normcache.MAGIC + b"\x00not zlib" * 50)
    elif damage == "empty":
        p.write_bytes(b"")
    elif damage == "bad_magic":
        p.write_bytes(b"XXXXX\n" + raw[len(normcache.MAGIC):])
    else:
        import zlib
        obj = pickle.loads(zlib.decompress(raw[len(normcache.MAGIC):]))
        if damage == "wrong_key":
            obj["meta"]["key"] = ("text", "elsewhere", "x", ("seq", 1))
        else:
            obj["meta"]["digest"] = "0" * 20
        p.write_bytes(normcache.MAGIC + zlib.compress(pickle.dumps(obj), 1))
    assert cache.get(row, "text", row["archive_path"]) == (False, None)
    *r, st = _parse_one_cached(row, "text", False, cache)
    assert st == "write" and tuple(r) == off  # re-parsed and the bad file replaced
    *r, st = _parse_one_cached(row, "text", False, cache)
    assert st == "hit" and tuple(r) == off


def test_changed_source_file_is_a_miss(tmp_path):
    row = _text_row(tmp_path)
    cache = NormCache(str(tmp_path / "cache"), "readwrite")
    _parse_one_cached(row, "text", False, cache)
    p = Path(row["archive_path"])
    p.write_bytes(p.read_bytes() + b"\nappended\n")
    *r, st = _parse_one_cached(row, "text", False, cache)
    assert st == "write" and tuple(r) == _parse_one(row, "text", False)


def test_distinct_rows_distinct_files(tmp_path):
    row = _text_row(tmp_path)
    c = NormCache(str(tmp_path), "readwrite")
    assert c.path_for(row, "text") != c.path_for(dict(row, sequence=4), "text")
    assert c.path_for(row, "text") != c.path_for(dict(row, archive_path=row["archive_path"] + "x"), "text")
    assert c.path_for(row, "text") != c.path_for(row, "ex10")
    k = dict(ROWS["10k_html"][1])
    assert c.path_for(k, "10k") != c.path_for(dict(k, submission_type="10-K405"), "10k")
    assert f"/{normcache.prefix_digest()}/text/2011/" in str(c.path_for(row, "text"))


def _pkg_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "pkg" / "edgar_itemize"
    shutil.copytree(normcache.PKG_DIR, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def test_digest_covers_the_prefix_closure():
    mods, external = normcache.import_closure()
    need = {"sgml", "select", "classify", "normalize_text", "normalize_html", "offsets", "blocks", "control",
            "prepare", "normcache"}
    assert {f"edgar_itemize.{m}" for m in need} <= set(mods)
    assert "edgar_itemize" in mods  # the package __init__ runs on every import
    # nothing downstream of normalisation is in it, or every rule change would flush the cache
    for m in ("candidates", "toc", "tree", "tree_contract", "headings", "agenda", "pipeline", "cli", "grammar"):
        assert f"edgar_itemize.{m}" not in mods
    assert "regex" in external
    assert "regex" in normcache.digest_parts()["third_party"]


@pytest.mark.parametrize("module", ["sgml.py", "select.py", "classify.py", "normalize_text.py", "normalize_html.py",
                                    "offsets.py", "blocks.py", "control.py", "prepare.py", "normcache.py", "__init__.py"])
def test_digest_changes_when_a_prefix_source_changes(tmp_path, module):
    pkg = _pkg_copy(tmp_path)
    d0 = normcache.compute_digest(pkg)
    assert d0 == normcache.compute_digest(normcache.PKG_DIR)
    f = pkg / module
    f.write_text(f.read_text() + "\n# edited\n")
    assert normcache.compute_digest(pkg) != d0


@pytest.mark.parametrize("module", ["candidates.py", "tree.py", "pipeline.py", "cli.py", "grammar/form10k.py"])
def test_digest_ignores_downstream_sources(tmp_path, module):
    pkg = _pkg_copy(tmp_path)
    d0 = normcache.compute_digest(pkg)
    f = pkg / module
    f.write_text(f.read_text() + "\n# edited\n")
    assert normcache.compute_digest(pkg) == d0


def test_cli_parse_with_cache_and_sidecar(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from edgar_itemize.cli import main

    row = _text_row(tmp_path)
    man = tmp_path / "m.parquet"
    pq.write_table(pa.Table.from_pylist([row]), man)
    outs = {}
    for mode in ("off", "write", "read"):
        out = tmp_path / f"out_{mode}"
        main(["parse", "--manifest", str(man), "--out", str(out), "--kind", "text", "--no-text",
              "--norm-cache", str(tmp_path / "cache"), "--norm-cache-mode", mode])
        outs[mode] = {n: pq.read_table(out / f"{n}-text.parquet") for n in ("nodes", "documents", "rejected")}
        side = out / "normcache-text.parquet"
        assert side.exists() == (mode != "off")
        if mode != "off":
            assert pq.read_table(side).column("norm_cache").to_pylist() == ["write" if mode == "write" else "hit"]
    for mode in ("write", "read"):
        for n in ("nodes", "documents", "rejected"):
            assert outs[mode][n].equals(outs["off"][n]) and outs[mode][n].schema == outs["off"][n].schema
