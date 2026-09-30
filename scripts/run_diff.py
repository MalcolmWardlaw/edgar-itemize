"""Corpus-wide tree diff between two runs: the gate every tree-behaviour change passes.

    uv run python scripts/run_diff.py runs/full_v7 runs/full_v8 --out runs/diff_v7_v8.json [--years 1994,2020] [--kind 10k]

Reports, per (accession, sequence): item/part label-set gains and losses, anchor
moves (same label, different head_raw_start), core_complete flips, toc_found
flips, and node-count deltas by depth band (<=2 vs >=3). Losses and core flips
are enumerated in the JSON so they can be inspected one by one.

Turn 7 (c) extension: `core_ok()` now credits multi-item labels ("Items 1 and
2. Business and Properties") the same way `eval/metrics.doc_metrics` does --
a node whose `rule_ids` carry `multi.ITEM <k>` counts as covering item k, even
though `label_canon` only names the first item in the range. Pass
`--no-multi-credit` to reproduce the historical (pre-fix) `core_ok()` reading
of `label_canon` alone.

Also extends the comparison beyond label sets and anchor starts, per Turn 7
cross-cutting finding 1 (docs/TURN7_DECISIONS.md): item/part `raw_end` moves,
`meta`-digit transitions, `path_str` churn with a stable label/raw_start, and
document-level `back_start` moves/appearances/disappearances. A `segment`
node column, if present on both runs, gets a before/after distribution of
segment counts per document; its absence is reported, not an error.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

CORE = ("ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 7", "ITEM 8")
NODE_COLS = ["accession_number", "sequence", "depth", "level_kind", "label_canon", "title", "rule_ids",
             "raw_start", "raw_end", "head_raw_start", "path_str", "meta"]
DOC_COLS = ["accession_number", "sequence", "filed_year", "profile_era", "toc_found", "items_found", "back_start", "error"]


def _years(run: Path, kind: str) -> list[str]:
    return sorted(re.search(rf"documents-{kind}-(\w+)\.parquet$", f).group(1) for f in glob.glob(str(run / f"documents-{kind}-*.parquet")))


# per kind: (primary labelled level, secondary labelled level). For EX-10 the "items" are
# sections and the "parts" are articles; depth>=3 rows are clauses.
# "text" is the raw-text adapter's kind (`--kind text`, the private contracts-text corpus): the
# contract grammar again, so the same two levels as EX-10 (Turn 10 B.3, its contracts-text gate).
LEVELS = {"10k": ("item", "part"), "ex10": ("section", "article"), "ex13": ("item", "part"),
          "text": ("section", "article")}


def _last_label(items: dict) -> str | None:
    """Label with the largest head_raw_start in a {label: head_raw_start} dict -- the
    document's last item (or last part), used to split raw_end churn (comparison a)."""
    return max(items, key=items.get) if items else None


def multi_covered_keys(rule_ids) -> list[str]:
    """The additional item keys ('ITEM 2', 'ITEM 7A', ...) a node's heading covers via
    a plural+range label ("Items 1 and 2"), read off its `multi.ITEM <k>` rule ids --
    same source `pipeline.result_rows`'s `items_found` and `eval/metrics.doc_metrics`
    both credit from. Does not include the node's own `label_canon`."""
    if not rule_ids:
        return []
    return [f"ITEM {rid.split(' ', 1)[1]}" for rid in rule_ids if rid.startswith("multi.ITEM ")]


def load_year(run: Path, kind: str, year: str, *, node_cols: list[str] | None = None, has_segment: bool = False):
    docs = {}
    cols = [c for c in DOC_COLS if kind in ("10k", "ex13") or c != "items_found"]
    for d in pq.read_table(run / f"documents-{kind}-{year}.parquet", columns=cols).to_pylist():
        docs[(d["accession_number"], d["sequence"] or 0)] = d
    per = defaultdict(lambda: dict(items={}, parts={}, item_info={}, part_info={}, multi_covered={},
                                    n_le2=0, n_ge3=0, n_runin=0, n_cont=0, segment_by_node=Counter()))
    primary, secondary = LEVELS[kind]
    ncols = list(node_cols or NODE_COLS)
    if has_segment and "segment" not in ncols:
        ncols = ncols + ["segment"]
    for n in pq.read_table(run / f"nodes-{kind}-{year}.parquet", columns=ncols).to_pylist():
        k = (n["accession_number"], n["sequence"] or 0)
        p = per[k]
        lk = n["level_kind"]
        if lk == primary or lk == secondary:
            info = dict(raw_start=n.get("raw_start"), raw_end=n.get("raw_end"),
                        path_str=n.get("path_str"), meta=n.get("meta"), head_raw_start=n["head_raw_start"])
            if lk == primary:
                p["items"].setdefault(n["label_canon"], n["head_raw_start"])
                p["item_info"].setdefault(n["label_canon"], info)
                for lab in multi_covered_keys(n.get("rule_ids")):
                    p["multi_covered"].setdefault(lab, n["head_raw_start"])
            else:
                p["parts"].setdefault(n["label_canon"], n["head_raw_start"])
                p["part_info"].setdefault(n["label_canon"], info)
        if n["depth"] is not None and n["depth"] >= 3:
            p["n_ge3"] += 1
            if n["rule_ids"] and ("sty.runin" in n["rule_ids"] or "seq.restart" in n["rule_ids"]):
                p["n_runin"] += 1  # new-rule nodes: run-ins (10-K) or restarts (EX-10)
            if n["title"] and re.search(r"\((?:continued|cont'?d\.?|con'?t\.?)\)|\bcontinued\b", n["title"], re.I):
                p["n_cont"] += 1
        else:
            p["n_le2"] += 1
        if has_segment:
            seg = n.get("segment")
            if seg is not None:
                p["segment_by_node"][seg] += 1
    return docs, per


def core_ok(items: dict, kind: str = "10k", covered: set | None = None) -> bool:
    """True iff every CORE item is reachable, either as a direct `label_canon` key in
    `items` or -- unless the caller passes `covered=None`/an empty set to reproduce the
    historical behaviour -- as a `multi.ITEM <k>` credit in `covered`. EX-10 keeps the
    pilot's >=10-sections sanity metric, unaffected by multi-item credit."""
    if kind != "10k":
        return len(items) >= 10
    keys = set(items) | (covered or set())
    return all(c in keys for c in CORE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("old"); ap.add_argument("new")
    ap.add_argument("--kind", default="10k")
    ap.add_argument("--years", default="", help="comma-separated subset")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-list", type=int, default=400)
    ap.add_argument("--no-multi-credit", action="store_true",
                     help="reproduce the pre-Turn7(c) core_ok(): ignore multi.ITEM rule-id credit and read "
                          "label_canon alone (historical run_diff numbers, undercounts core_complete by the "
                          "filings whose core coverage comes only from a multi-item heading)")
    a = ap.parse_args()
    old, new = Path(a.old), Path(a.new)
    years = a.years.split(",") if a.years else [y for y in _years(new, a.kind) if y in set(_years(old, a.kind))]
    credit_multi = not a.no_multi_credit
    tot = Counter(); by_year = {}
    lose_core, gain_core, lost_items, moved_later, moved_earlier, lost_parts = [], [], [], [], [], []
    raw_end_changed_items_ex, raw_end_changed_parts_ex = [], []
    meta_changed_ex, path_str_changed_ex = [], []
    back_appeared_ex, back_disappeared_ex, back_earlier_ex, back_later_ex = [], [], [], []
    meta_transitions: Counter = Counter()
    seg_dist_old: Counter = Counter(); seg_dist_new: Counter = Counter()
    segment_available = True
    for y in years:
        old_names = set(pq.read_schema(old / f"nodes-{a.kind}-{y}.parquet").names)
        new_names = set(pq.read_schema(new / f"nodes-{a.kind}-{y}.parquet").names)
        has_segment = "segment" in old_names and "segment" in new_names
        segment_available = segment_available and has_segment
        ocols = [c for c in NODE_COLS if c in old_names]
        ncols_ = [c for c in NODE_COLS if c in new_names]
        od, op = load_year(old, a.kind, y, node_cols=ocols, has_segment=has_segment)
        nd, np_ = load_year(new, a.kind, y, node_cols=ncols_, has_segment=has_segment)
        c = Counter()
        for k in od.keys() & nd.keys():
            if od[k]["error"] or nd[k]["error"]:
                c["errors"] += 1; continue
            o, n = op[k], np_[k]
            c["docs"] += 1
            oi, ni = o["items"], n["items"]
            # a `gram.synth_section` node (Turn 12 B.2) has label_canon None; sort it last so a
            # document that gains or loses one beside a labelled section does not crash (Turn 13 B.4)
            _lk = lambda x: (x is None, x or "")  # noqa: E731
            gained = sorted(set(ni) - set(oi), key=_lk); lost = sorted(set(oi) - set(ni), key=_lk)
            c["items_gained"] += len(gained); c["items_lost"] += len(lost)
            for lab in lost:
                lost_items.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, old_start=oi[lab]))
            for lab in set(oi) & set(ni):
                if oi[lab] != ni[lab]:
                    (moved_earlier if ni[lab] < oi[lab] else moved_later).append(dict(accession=k[0], sequence=k[1], year=y, label=lab, old_start=oi[lab], new_start=ni[lab]))
                    c["moved_earlier" if ni[lab] < oi[lab] else "moved_later"] += 1
            for lab in set(o["parts"]) - set(n["parts"]):
                lost_parts.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, old_start=o["parts"][lab]))
            c["parts_lost"] += len(set(o["parts"]) - set(n["parts"])); c["parts_gained"] += len(set(n["parts"]) - set(o["parts"]))

            ocov = set(o["multi_covered"]) if credit_multi else set()
            ncov = set(n["multi_covered"]) if credit_multi else set()
            oc, nc = core_ok(oi, a.kind, ocov), core_ok(ni, a.kind, ncov)
            c["core_old"] += oc; c["core_new"] += nc
            if oc and not nc:
                lose_core.append(dict(accession=k[0], sequence=k[1], year=y, missing=[x for x in CORE if x not in ni and x not in ncov] if a.kind in ("10k", "ex13") else len(ni)))
            elif nc and not oc:
                gain_core.append(dict(accession=k[0], sequence=k[1], year=y))
            c["toc_old"] += bool(od[k]["toc_found"]); c["toc_new"] += bool(nd[k]["toc_found"])
            c["n_le2_old"] += o["n_le2"]; c["n_le2_new"] += n["n_le2"]
            c["n_ge3_old"] += o["n_ge3"]; c["n_ge3_new"] += n["n_ge3"]
            c["runin_new"] += n["n_runin"]; c["cont_old"] += o["n_cont"]; c["cont_new"] += n["n_cont"]
            c["docs_changed"] += (oi != ni) or (o["parts"] != n["parts"]) or (o["n_ge3"] != n["n_ge3"])
            c["docs_changed_le2"] += (oi != ni) or (o["parts"] != n["parts"])

            # (a) item/part raw_end churn, split by whether the label is the document's last item/part
            last_item_old, last_item_new = _last_label(oi), _last_label(ni)
            for lab in set(o["item_info"]) & set(n["item_info"]):
                oe, ne = o["item_info"][lab]["raw_end"], n["item_info"][lab]["raw_end"]
                if oe != ne:
                    c["raw_end_changed_items"] += 1
                    is_last = lab in (last_item_old, last_item_new)
                    c["raw_end_changed_items_last" if is_last else "raw_end_changed_items_other"] += 1
                    if len(raw_end_changed_items_ex) < a.max_list:
                        raw_end_changed_items_ex.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, old_raw_end=oe, new_raw_end=ne, is_last_item=is_last))
            last_part_old, last_part_new = _last_label(o["parts"]), _last_label(n["parts"])
            for lab in set(o["part_info"]) & set(n["part_info"]):
                oe, ne = o["part_info"][lab]["raw_end"], n["part_info"][lab]["raw_end"]
                if oe != ne:
                    c["raw_end_changed_parts"] += 1
                    is_last = lab in (last_part_old, last_part_new)
                    c["raw_end_changed_parts_last" if is_last else "raw_end_changed_parts_other"] += 1
                    if len(raw_end_changed_parts_ex) < a.max_list:
                        raw_end_changed_parts_ex.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, old_raw_end=oe, new_raw_end=ne, is_last_part=is_last))

            # (b) meta-digit transitions and (c) path_str churn with a stable label/raw_start,
            # over the same matched item/part nodes.
            for tag, oinfo, ninfo in (("item", o["item_info"], n["item_info"]), ("part", o["part_info"], n["part_info"])):
                for lab in set(oinfo) & set(ninfo):
                    om, nm = oinfo[lab]["meta"], ninfo[lab]["meta"]
                    if om != nm:
                        c["meta_changed"] += 1
                        meta_transitions[(om, nm)] += 1
                        if len(meta_changed_ex) < a.max_list:
                            meta_changed_ex.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, kind=tag, old_meta=om, new_meta=nm))
                    osr, nsr = oinfo[lab]["raw_start"], ninfo[lab]["raw_start"]
                    if osr == nsr and oinfo[lab]["path_str"] != ninfo[lab]["path_str"]:
                        c["path_str_changed_stable"] += 1
                        if len(path_str_changed_ex) < a.max_list:
                            path_str_changed_ex.append(dict(accession=k[0], sequence=k[1], year=y, label=lab, kind=tag, raw_start=osr, old_path_str=oinfo[lab]["path_str"], new_path_str=ninfo[lab]["path_str"]))

            # (d) document-level back_start
            obs, nbs = od[k].get("back_start"), nd[k].get("back_start")
            if obs is None and nbs is not None:
                c["back_start_appeared"] += 1
                if len(back_appeared_ex) < a.max_list:
                    back_appeared_ex.append(dict(accession=k[0], sequence=k[1], year=y, new_back_start=nbs))
            elif obs is not None and nbs is None:
                c["back_start_disappeared"] += 1
                if len(back_disappeared_ex) < a.max_list:
                    back_disappeared_ex.append(dict(accession=k[0], sequence=k[1], year=y, old_back_start=obs))
            elif obs is not None and nbs is not None and obs != nbs:
                if nbs < obs:
                    c["back_start_moved_earlier"] += 1
                    if len(back_earlier_ex) < a.max_list:
                        back_earlier_ex.append(dict(accession=k[0], sequence=k[1], year=y, old_back_start=obs, new_back_start=nbs))
                else:
                    c["back_start_moved_later"] += 1
                    if len(back_later_ex) < a.max_list:
                        back_later_ex.append(dict(accession=k[0], sequence=k[1], year=y, old_back_start=obs, new_back_start=nbs))

            # (e) segment count per document, if the column is present on both runs
            if has_segment:
                seg_dist_old[len(o["segment_by_node"])] += 1
                seg_dist_new[len(n["segment_by_node"])] += 1
        by_year[y] = dict(c); tot.update(c)
        print(f"{y}: docs {c['docs']} core {c['core_old']}->{c['core_new']} items +{c['items_gained']}/-{c['items_lost']} moved e/l {c['moved_earlier']}/{c['moved_later']} "
              f"depth>=3 {c['n_ge3_old']}->{c['n_ge3_new']} (runin {c['runin_new']}, continued {c['cont_old']}->{c['cont_new']}) changed {c['docs_changed']} (le2 {c['docs_changed_le2']})", flush=True)
    print("\n=== total ===")
    print(f"docs {tot['docs']}; core_complete {tot['core_old']} -> {tot['core_new']} (gain {len(gain_core)}, lose {len(lose_core)}) "
          f"[multi-item credit {'ON' if credit_multi else 'OFF (--no-multi-credit)'}]")
    print(f"item labels +{tot['items_gained']} / -{tot['items_lost']}; parts +{tot['parts_gained']} / -{tot['parts_lost']}; anchors moved earlier {tot['moved_earlier']}, later {tot['moved_later']}")
    print(f"toc_found {tot['toc_old']} -> {tot['toc_new']}; nodes depth<=2 {tot['n_le2_old']} -> {tot['n_le2_new']}; depth>=3 {tot['n_ge3_old']} -> {tot['n_ge3_new']} (new runin {tot['runin_new']}; continued-titled {tot['cont_old']} -> {tot['cont_new']})")
    print(f"docs with any change {tot['docs_changed']}; with item/part change {tot['docs_changed_le2']}")
    print(f"raw_end changed: items {tot['raw_end_changed_items']} (last-item {tot['raw_end_changed_items_last']}, other {tot['raw_end_changed_items_other']}); "
          f"parts {tot['raw_end_changed_parts']} (last-part {tot['raw_end_changed_parts_last']}, other {tot['raw_end_changed_parts_other']})")
    print(f"meta digit changed: {tot['meta_changed']} nodes; top old->new transitions: {[(f'{om}->{nm}', ct) for (om, nm), ct in meta_transitions.most_common(10)]}")
    print(f"path_str changed with label/raw_start unchanged: {tot['path_str_changed_stable']} nodes")
    print(f"back_start: appeared {tot['back_start_appeared']}, disappeared {tot['back_start_disappeared']}, moved earlier {tot['back_start_moved_earlier']}, moved later {tot['back_start_moved_later']}")
    if segment_available:
        print(f"segment count per doc: old {dict(sorted(seg_dist_old.items()))} -> new {dict(sorted(seg_dist_new.items()))}")
    else:
        print("segment column not present on both runs for the selected years; segment-count comparison unavailable")
    if a.out:
        Path(a.out).write_text(json.dumps(dict(
            old=str(old), new=str(new), years=years, total=dict(tot), by_year=by_year,
            multi_credit=credit_multi,
            lose_core=lose_core, gain_core=gain_core[: a.max_list], lost_items=lost_items[: a.max_list], lost_parts=lost_parts[: a.max_list],
            moved_later=moved_later[: a.max_list], moved_earlier=moved_earlier[: a.max_list],
            raw_end_changed_items=raw_end_changed_items_ex[: a.max_list],
            raw_end_changed_parts=raw_end_changed_parts_ex[: a.max_list],
            meta_changed=meta_changed_ex[: a.max_list],
            meta_transitions={f"{om}->{nm}": ct for (om, nm), ct in meta_transitions.most_common()},
            path_str_changed=path_str_changed_ex[: a.max_list],
            back_start_appeared=back_appeared_ex[: a.max_list],
            back_start_disappeared=back_disappeared_ex[: a.max_list],
            back_start_moved_earlier=back_earlier_ex[: a.max_list],
            back_start_moved_later=back_later_ex[: a.max_list],
            segment_available=segment_available,
            segment_dist_old={str(k2): v for k2, v in sorted(seg_dist_old.items())},
            segment_dist_new={str(k2): v for k2, v in sorted(seg_dist_new.items())},
        ), indent=1))
        print("->", a.out)


if __name__ == "__main__":
    main()
