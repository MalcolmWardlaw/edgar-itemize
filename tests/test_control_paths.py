
from pathlib import Path

import pytest

from edgar_itemize import control

TAIL = "archives/edgar/data/20/0000000020-99-000001.txt"


def test_rewrite_path_reroots_both_prefixes(monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", Path("/home/x/edgar_sample"))
    monkeypatch.setenv(control.PREFIX_MAP_ENV,
                       "/srv/old/sec/=/home/x/edgar_sample/; /data/edgar/=/home/x/edgar_sample/")
    assert control.rewrite_path("/srv/old/sec/" + TAIL) == Path("/home/x/edgar_sample") / TAIL
    assert control.rewrite_path("/data/edgar/" + TAIL) == Path("/home/x/edgar_sample") / TAIL
    assert control.rewrite_path("/elsewhere/" + TAIL) == Path("/elsewhere/" + TAIL)


def test_rewrite_path_empty_new_joins_data_root(monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", Path("/home/x/edgar_sample"))
    monkeypatch.setenv(control.PREFIX_MAP_ENV, "/data/edgar/=")
    assert control.rewrite_path("/data/edgar/" + TAIL) == Path("/home/x/edgar_sample") / TAIL


def test_rewrite_path_identity_on_server(monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", Path("/data/edgar"))
    monkeypatch.setenv(control.PREFIX_MAP_ENV, "/srv/old/sec/=/data/edgar/")
    p = "/data/edgar/" + TAIL
    assert control.rewrite_path(p) == Path(p)


def test_rewrite_path_unset_map_passes_through(monkeypatch):
    monkeypatch.setattr(control, "DATA_ROOT", Path("/home/x/edgar_sample"))
    monkeypatch.delenv(control.PREFIX_MAP_ENV, raising=False)
    for p in ("/srv/old/sec/" + TAIL, "/data/edgar/" + TAIL):
        assert control.rewrite_path(p) == Path(p)


def test_rewrite_path_malformed_map_exits(monkeypatch):
    monkeypatch.setenv(control.PREFIX_MAP_ENV, "/no/equals/sign/")
    with pytest.raises(SystemExit):
        control.rewrite_path("/no/equals/sign/x.txt")
