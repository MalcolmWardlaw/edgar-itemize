"""Gold-free sanity metrics over a parse run."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from ..classify import abs_two_tier
from ..grammar.form10k import Form10KGrammar
from ..grammar.form10q import Form10QGrammar

_SMALL_OK = {"1B", "1C", "3", "4", "6", "9", "9B", "9C", "16"}


def _is_abs(doc: dict) -> bool:
    """Two-tier ABS flag (case A1): specific-vocabulary tier or name tier over the
    stored signals; legacy reg_ab>=3 fallback for parquet written before the fix."""
    try:
        import json

        return abs_two_tier(json.loads(doc.get("signals") or "{}"))
    except Exception:  # noqa: BLE001
        return False


def doc_metrics(doc: dict, nodes: list[dict], omitted_items: set[str] | None = None) -> dict:
    """Per-document sanity metrics.

    `omitted_items` is an optional set of item keys ("2", "7A", ...) that the
    filing itself declares omitted (case A4's omission-statement recognizer,
    Turn 4).  It defaults to none; `omit.stmt` nodes emitted by that recognizer
    are also consumed here — they count as an omission signal, never as a found
    item.  `omitted_ok` is True when every missing core item is covered by the
    signal, so the adjusted metric can treat the filing as legitimately complete.
    """
    g = Form10KGrammar()
    if doc.get("error") or doc.get("doc_raw_end") is None:
        return dict(n_items=0, core_complete=False, missing_core=[], missing_expected=[], unexpected=[], monotone=False, tiny=[], huge=[],
                    item7_share=0.0, item8_share=0.0, size_sanity=False, n_synth_parts=0, coverage=0.0, toc_found=False, abs_filing=False, tiny_doc=False,
                    omitted_ok=False, error=doc.get("error") or "no doc")
    all_items = [n for n in nodes if n["level_kind"] == "item"]
    items = [n for n in all_items if "omit.stmt" not in (n["rule_ids"] or [])]
    omitted = set(omitted_items or ())
    omitted |= {g.item_key(n["label_canon"]) for n in all_items if "omit.stmt" in (n["rule_ids"] or [])}
    keys = [g.item_key(n["label_canon"]) for n in items]
    for n in items:  # multi-item headings ("Items 1 and 2") cover extra keys
        if "covers_items" in n:  # Turn 7 (c): typed column, same data as the rule-id tags below
            keys += list(n["covers_items"] or ())
        else:  # legacy parquet written before covers_items existed
            keys += [r.split(" ", 1)[1] for r in (n["rule_ids"] or []) if r.startswith("multi.ITEM ")]
    year = doc.get("filed_year") or 0
    expected = set(g.expected_items(year))
    found = set(keys)
    # core items expected in every 10-K era
    core = {"1", "2", "3", "5", "7", "8"}
    doc_len = max(1, doc["doc_raw_end"] - doc["doc_raw_start"])
    sizes = {g.item_key(n["label_canon"]): n["raw_end"] - n["raw_start"] for n in items}
    monotone = all(items[i]["raw_start"] < items[i + 1]["raw_start"] for i in range(len(items) - 1))
    missing_core = sorted(core - found)
    tiny = sorted(k for k, s in sizes.items() if s < 200 and k not in _SMALL_OK)
    last_key = keys[-1] if keys else None
    huge = sorted(k for k, s in sizes.items() if s > 0.6 * doc_len and k not in (last_key, "8", "15", "14"))
    item7 = sizes.get("7", 0) / doc_len
    item8 = sizes.get("8", 0) / doc_len
    n_synth = sum(1 for n in nodes if n["level_kind"] == "part" and "gram.synth_part" in (n["rule_ids"] or []))
    covered = sum(sizes.values()) / doc_len
    return dict(
        n_items=len(items),
        core_complete=not missing_core,
        missing_core=missing_core,
        missing_expected=sorted(expected - found),
        unexpected=sorted(found - expected),
        monotone=monotone,
        tiny=tiny,
        huge=huge,
        item7_share=round(item7, 3),
        item8_share=round(item8, 3),
        size_sanity=(not huge and sizes.get("1", 1000) >= 200 and (item7 <= 0.6)),
        n_synth_parts=n_synth,
        coverage=round(covered, 3),
        toc_found=bool(doc.get("toc_found")),
        abs_filing=_is_abs(doc),
        tiny_doc=doc_len < 20_000,
        omitted_ok=bool(missing_core) and set(missing_core) <= omitted,
        error=doc.get("error"),
    )


def doc_metrics_10q(doc: dict, nodes: list[dict]) -> dict:
    """Per-document sanity metrics for Form 10-Q (Turn 7 (e)).

    10-K's `doc_metrics` has no 10-Q equivalent today (`Form10QGrammar` is
    referenced only by `pipeline.py` and its own grammar module; G1's
    completeness numbers were produced by a one-off script). This mirrors its
    shape, scoped to the three-tier expected-items table:
    `core_complete` requires every `REQUIRED`/`ERA_REQUIRED` item
    (`Form10QGrammar.expected_items`); `complete_with_omissions` is the
    researcher-facing "pass" case — core-complete, but one or more
    `OMITTABLE` Part II items (answer-if-applicable by SEC instruction) are
    genuinely absent, distinct from a real defect.
    """
    g = Form10QGrammar()
    if doc.get("error") or doc.get("doc_raw_end") is None:
        return dict(n_items=0, core_complete=False, complete_with_omissions=False, missing_core=[], missing_expected=[],
                    unexpected=[], error=doc.get("error") or "no doc")
    items = [n for n in nodes if n["level_kind"] == "item"]
    # Full keys ("I.1", "II.1A", ...) are label_canon with the leading "ITEM "
    # stripped -- already Part-disambiguated, unlike Form10QGrammar.item_key()
    # alone. (Multi-item `covers_items` credit, decision (c), does not apply
    # here yet: form10q's ITEM_RE has no Part-aware plural/range handling, so
    # it cannot currently tag a combined Part II omission statement.)
    found = {n["label_canon"].split(" ", 1)[1] for n in items if n["label_canon"]}
    year = doc.get("filed_year") or 0
    required = set(g.expected_items(year))
    omittable = set(g.omittable_items(year))
    missing_core = sorted(required - found)
    missing_expected = sorted(omittable - found)  # informative only; never fails completeness
    core_complete = not missing_core
    return dict(
        n_items=len(items),
        core_complete=core_complete,
        complete_with_omissions=core_complete and bool(missing_expected),
        missing_core=missing_core,
        missing_expected=missing_expected,
        unexpected=sorted(found - required - omittable),
        toc_found=bool(doc.get("toc_found")),
        error=doc.get("error"),
    )


def run_metrics(run_dir: Path, part: str = "10k") -> tuple[list[dict], dict]:
    docs = pq.read_table(run_dir / f"documents-{part}.parquet", columns=[c for c in pq.read_schema(run_dir / f"documents-{part}.parquet").names if c != "normalized_text"]).to_pylist()
    nodes = pq.read_table(run_dir / f"nodes-{part}.parquet").to_pylist()
    by_doc: dict[tuple, list[dict]] = defaultdict(list)
    for n in nodes:
        by_doc[(n["accession_number"], n["sequence"])].append(n)
    rows = []
    for d in docs:
        m = doc_metrics(d, by_doc.get((d["accession_number"], d["sequence"]), []))
        rows.append({**{k: d.get(k) for k in ("accession_number", "sequence", "filed_year", "profile_era", "profile_publisher", "agent_cik")}, **m})
    return rows, summarize(rows)


def summarize(rows: list[dict], by: tuple[str, ...] = ("profile_era",)) -> dict:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k) for k in by)].append(r)
    out = {}
    for k, rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        n = len(rs)
        out[k] = dict(
            n=n,
            errors=sum(1 for r in rs if r["error"]),
            core_complete=round(sum(r["core_complete"] for r in rs) / n, 3),
            omitted_ok=round(sum(r.get("omitted_ok", False) for r in rs) / n, 3),
            monotone=round(sum(r["monotone"] for r in rs) / n, 3),
            size_sanity=round(sum(r["size_sanity"] for r in rs) / n, 3),
            toc_found=round(sum(r["toc_found"] for r in rs) / n, 3),
            synth_parts=round(sum(r["n_synth_parts"] for r in rs) / n, 2),
            coverage=round(sum(r["coverage"] for r in rs) / n, 3),
        )
    return out


def print_summary(summary: dict, by_names: tuple[str, ...]) -> None:
    hdr = " | ".join(by_names) + " | n | err | core_complete | omitted_ok | monotone | size_sanity | toc_found | synth_parts | coverage"
    print(hdr)
    for k, v in summary.items():
        print(" | ".join(str(x) for x in k), "|", " | ".join(str(v[c]) for c in ("n", "errors", "core_complete", "omitted_ok", "monotone", "size_sanity", "toc_found", "synth_parts", "coverage")))
