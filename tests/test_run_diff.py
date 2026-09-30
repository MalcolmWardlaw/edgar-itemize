"""Tests for scripts/run_diff.py (Turn 7 (c)/cross-cutting finding 1 extension):
multi-item credit in core_ok() and the new raw_end/meta/path_str/back_start/segment
comparisons, on small synthetic frames (no parquet, no corpus needed).

scripts/ isn't an importable package, so the module is loaded from its file path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_diff.py"
_spec = importlib.util.spec_from_file_location("run_diff", SCRIPT)
run_diff = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_diff", run_diff)
_spec.loader.exec_module(run_diff)


# ---------------------------------------------------------------------------
# multi_covered_keys / core_ok: multi-item credit
# ---------------------------------------------------------------------------

def test_multi_covered_keys_extracts_item_suffixes():
    assert run_diff.multi_covered_keys(["lbl.item.multi", "multi.ITEM 2"]) == ["ITEM 2"]
    assert run_diff.multi_covered_keys(["multi.ITEM 11", "multi.ITEM 12", "multi.ITEM 13"]) == ["ITEM 11", "ITEM 12", "ITEM 13"]
    assert run_diff.multi_covered_keys(["multi.ITEM 7A"]) == ["ITEM 7A"]


def test_multi_covered_keys_empty():
    assert run_diff.multi_covered_keys(None) == []
    assert run_diff.multi_covered_keys([]) == []
    assert run_diff.multi_covered_keys(["lbl.item", "sty.runin"]) == []


def _core_items(skip=()):
    return {lab: i * 100 for i, lab in enumerate(x for x in run_diff.CORE if x not in skip)}


def test_core_ok_true_when_all_core_present_directly():
    assert run_diff.core_ok(_core_items(), "10k")


def test_core_ok_false_missing_item_no_credit():
    items = _core_items(skip=("ITEM 2",))
    assert not run_diff.core_ok(items, "10k")
    assert not run_diff.core_ok(items, "10k", covered=set())
    assert not run_diff.core_ok(items, "10k", covered=None)


def test_core_ok_credits_multi_item_covered_key():
    # "Items 1 and 2" heading: only ITEM 1 is a label_canon key, ITEM 2 comes from
    # the anchor's multi.ITEM rule ids -- this is the 2,787-filing fix from
    # docs/turn7_decisions/c_multi_item.md.
    items = {k: v for k, v in _core_items().items() if k != "ITEM 2"}
    assert run_diff.core_ok(items, "10k", covered={"ITEM 2"})


def test_core_ok_ex10_ignores_covered_uses_section_count():
    items = {f"SECTION {i}": i for i in range(10)}
    assert run_diff.core_ok(items, "ex10")
    assert not run_diff.core_ok({f"SECTION {i}": i for i in range(9)}, "ex10")


# ---------------------------------------------------------------------------
# _last_label
# ---------------------------------------------------------------------------

def test_last_label_picks_max_head_raw_start():
    assert run_diff._last_label({"ITEM 1": 5, "ITEM 2": 50, "ITEM 3": 20}) == "ITEM 2"


def test_last_label_empty_is_none():
    assert run_diff._last_label({}) is None


# ---------------------------------------------------------------------------
# End-to-end over synthetic parquet: core_ok multi-credit flag, and comparisons
# (a) raw_end, (b) meta, (c) path_str, (d) back_start, (e) segment absence.
# ---------------------------------------------------------------------------

import pyarrow as pa
import pyarrow.parquet as pq


def _node(acc, label, title, raw_start, raw_end, head_raw_start, path_str, meta, rule_ids=(), depth=1, level_kind="item", seq=0):
    return dict(accession_number=acc, sequence=seq, depth=depth, level_kind=level_kind, label_canon=label,
                title=title, rule_ids=list(rule_ids), raw_start=raw_start, raw_end=raw_end,
                head_raw_start=head_raw_start, path_str=path_str, meta=meta)


def _doc(acc, back_start, error=None, seq=0, toc_found=True, items_found=()):
    return dict(accession_number=acc, sequence=seq, filed_year=2005, profile_era="html", toc_found=toc_found,
                items_found=list(items_found), back_start=back_start, error=error)


def _write_year(tmp_path, name, nodes, docs, kind="10k", year="2005"):
    run = tmp_path / name
    run.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(nodes), run / f"nodes-{kind}-{year}.parquet")
    pq.write_table(pa.Table.from_pylist(docs), run / f"documents-{kind}-{year}.parquet")
    return run


def test_load_year_and_core_ok_multi_credit_end_to_end(tmp_path):
    # One filing "A": Item 1 heading covers Item 2 via multi.ITEM; no standalone
    # ITEM 2 node exists. With credit, core_ok is True; without, False.
    nodes = [
        _node("A", "ITEM 1", "Items 1 and 2. Business and Properties", 0, 100, 0, "2", 2, ["lbl.item.multi", "multi.ITEM 2"]),
        _node("A", "ITEM 3", "Legal Proceedings", 100, 200, 100, "3", 3),
        _node("A", "ITEM 5", "Market", 200, 300, 200, "4", 4),
        _node("A", "ITEM 7", "MDA", 300, 400, 300, "5", 5),
        _node("A", "ITEM 8", "Financials", 400, 900, 400, "6", 6),
    ]
    docs = [_doc("A", back_start=850)]
    run = _write_year(tmp_path, "run1", nodes, docs)

    node_cols = run_diff.NODE_COLS
    _, per = run_diff.load_year(run, "10k", "2005", node_cols=node_cols, has_segment=False)
    p = per[("A", 0)]
    assert "ITEM 2" not in p["items"]  # not a real node
    assert p["multi_covered"] == {"ITEM 2": 0}
    assert not run_diff.core_ok(p["items"], "10k")  # no credit -> missing Item 2
    assert run_diff.core_ok(p["items"], "10k", set(p["multi_covered"]))  # credited -> complete


def test_run_diff_main_self_diff_is_all_zero(tmp_path, capsys):
    nodes = [
        _node("A", "ITEM 1", "Business", 0, 100, 0, "2", 2),
        _node("A", "ITEM 2", "Properties", 100, 200, 100, "3", 3),
        _node("A", "ITEM 3", "Legal", 200, 300, 200, "4", 4),
        _node("A", "ITEM 5", "Market", 300, 400, 300, "5", 5),
        _node("A", "ITEM 7", "MDA", 400, 500, 400, "6", 6),
        _node("A", "ITEM 8", "Financials", 500, 900, 500, "7", 7),
    ]
    docs = [_doc("A", back_start=850)]
    run = _write_year(tmp_path, "runX", nodes, docs)

    sys.argv = ["run_diff.py", str(run), str(run), "--years", "2005", "--out", str(tmp_path / "self.json")]
    run_diff.main()
    out = capsys.readouterr().out
    assert "core_complete 1 -> 1 (gain 0, lose 0)" in out
    assert "raw_end changed: items 0" in out
    assert "meta digit changed: 0 nodes" in out
    assert "path_str changed with label/raw_start unchanged: 0 nodes" in out
    assert "back_start: appeared 0, disappeared 0, moved earlier 0, moved later 0" in out

    import json
    result = json.loads((tmp_path / "self.json").read_text())
    for key in ("raw_end_changed_items", "raw_end_changed_parts", "meta_changed", "path_str_changed",
                "back_start_appeared", "back_start_disappeared", "back_start_moved_earlier", "back_start_moved_later"):
        assert result[key] == []
    assert result["total"]["core_old"] == result["total"]["core_new"] == 1
    assert result["multi_credit"] is True
    assert result["segment_available"] is False


def test_run_diff_main_detects_raw_end_meta_path_back_start_changes(tmp_path, capsys):
    old_nodes = [
        _node("B", "ITEM 1", "Business", 0, 100, 0, "2", 2),
        _node("B", "ITEM 2", "Properties", 100, 200, 100, "3", 3),
        _node("B", "ITEM 3", "Legal", 200, 300, 200, "4", 4),
        _node("B", "ITEM 5", "Market", 300, 400, 300, "5", 5),
        _node("B", "ITEM 7", "MDA", 400, 500, 400, "6", 6),
        _node("B", "ITEM 8", "Financials", 500, 900, 500, "7", 7),
    ]
    new_nodes = [
        _node("B", "ITEM 1", "Business", 0, 100, 0, "2", 2),
        _node("B", "ITEM 2", "Properties", 100, 200, 100, "3.1", 2),  # path_str changed, raw_start stable
        _node("B", "ITEM 3", "Legal", 200, 300, 200, "4", 4),
        _node("B", "ITEM 5", "Market", 300, 400, 300, "5", 5),
        _node("B", "ITEM 7", "MDA", 400, 500, 400, "6", 6),
        _node("B", "ITEM 8", "Financials", 500, 850, 500, "7", 7),  # raw_end shrank; last item
    ]
    old_docs = [_doc("B", back_start=None)]
    new_docs = [_doc("B", back_start=800)]
    old_run = _write_year(tmp_path, "old", old_nodes, old_docs)
    new_run = _write_year(tmp_path, "new", new_nodes, new_docs)

    out_path = tmp_path / "diff.json"
    sys.argv = ["run_diff.py", str(old_run), str(new_run), "--years", "2005", "--out", str(out_path)]
    run_diff.main()

    import json
    result = json.loads(out_path.read_text())
    assert len(result["raw_end_changed_items"]) == 1
    assert result["raw_end_changed_items"][0]["label"] == "ITEM 8"
    assert result["raw_end_changed_items"][0]["is_last_item"] is True
    assert result["total"]["raw_end_changed_items_last"] == 1
    assert result["total"].get("raw_end_changed_items_other", 0) == 0

    assert len(result["meta_changed"]) == 1
    assert result["meta_changed"][0] == dict(accession="B", sequence=0, year="2005", label="ITEM 2", kind="item", old_meta=3, new_meta=2)
    assert result["meta_transitions"] == {"3->2": 1}

    assert len(result["path_str_changed"]) == 1
    assert result["path_str_changed"][0]["old_path_str"] == "3"
    assert result["path_str_changed"][0]["new_path_str"] == "3.1"

    assert result["back_start_appeared"] == [dict(accession="B", sequence=0, year="2005", new_back_start=800)]
    assert result["back_start_disappeared"] == []
    assert result["back_start_moved_earlier"] == []
    assert result["back_start_moved_later"] == []


def test_no_multi_credit_flag_reproduces_historical_core_ok(tmp_path, capsys):
    nodes = [
        _node("A", "ITEM 1", "Items 1 and 2. Business and Properties", 0, 100, 0, "2", 2, ["lbl.item.multi", "multi.ITEM 2"]),
        _node("A", "ITEM 3", "Legal Proceedings", 100, 200, 100, "3", 3),
        _node("A", "ITEM 5", "Market", 200, 300, 200, "4", 4),
        _node("A", "ITEM 7", "MDA", 300, 400, 300, "5", 5),
        _node("A", "ITEM 8", "Financials", 400, 900, 400, "6", 6),
    ]
    docs = [_doc("A", back_start=850)]
    run = _write_year(tmp_path, "run2", nodes, docs)

    sys.argv = ["run_diff.py", str(run), str(run), "--years", "2005", "--no-multi-credit"]
    run_diff.main()
    out = capsys.readouterr().out
    assert "core_complete 0 -> 0 (gain 0, lose 0)" in out
    assert "multi-item credit OFF" in out

    sys.argv = ["run_diff.py", str(run), str(run), "--years", "2005"]
    run_diff.main()
    out = capsys.readouterr().out
    assert "core_complete 1 -> 1 (gain 0, lose 0)" in out
    assert "multi-item credit ON" in out


def test_segment_column_distribution_when_present(tmp_path, capsys):
    old_nodes = [
        dict(_node("C", "ITEM 1", "Business", 0, 100, 0, "2", 2), segment=1),
        dict(_node("C", "ITEM 2", "Properties", 100, 200, 100, "3", 3), segment=1),
    ]
    new_nodes = [
        dict(_node("C", "ITEM 1", "Business", 0, 100, 0, "2", 2), segment=1),
        dict(_node("C", "ITEM 2", "Properties", 100, 200, 100, "3", 3), segment=2),
    ]
    docs = [_doc("C", back_start=None)]
    old_run = _write_year(tmp_path, "old_seg", old_nodes, docs)
    new_run = _write_year(tmp_path, "new_seg", new_nodes, docs)

    out_path = tmp_path / "seg.json"
    sys.argv = ["run_diff.py", str(old_run), str(new_run), "--years", "2005", "--out", str(out_path)]
    run_diff.main()

    import json
    result = json.loads(out_path.read_text())
    assert result["segment_available"] is True
    assert result["segment_dist_old"] == {"1": 1}  # one document, one distinct segment id
    assert result["segment_dist_new"] == {"2": 1}  # one document, two distinct segment ids
