"""Gold labels: one YAML per document listing expected heading starts.

    accession: 0000912057-95-006316
    sequence: 1
    nodes:
      - {label: "PART I", raw_start: 7356}
      - {label: "ITEM 1", raw_start: 7364, title: "BUSINESS"}
    absent: ["ITEM 1A"]        # items legitimately missing from this filing

`edgar-itemize gold-init` writes a skeleton from the parser's own output for a
human to correct; `edgar-itemize eval` scores a run against the gold directory.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_gold(gold_dir: Path) -> dict[tuple[str, int], dict]:
    out = {}
    for p in sorted(gold_dir.glob("*.yaml")):
        g = yaml.safe_load(p.read_text())
        if not g or "accession" not in g:
            continue
        out[(g["accession"], int(g.get("sequence", 1)))] = g
    return out


def score(gold: dict, nodes: list[dict], *, tol: int = 50) -> dict:
    """Per-document precision/recall on labeled heading starts (Part/Item/Article/Section)."""
    pred = {(n["label_canon"], n["head_raw_start"]) for n in nodes if n["level_kind"] in ("part", "item", "article", "section") and "gram.synth_part" not in (n["rule_ids"] or [])}
    want = [(g["label"].upper(), int(g["raw_start"])) for g in gold.get("nodes", [])]
    tp = 0
    matched_pred = set()
    for lab, start in want:
        hit = next((p for p in pred if p[0] == lab and abs(p[1] - start) <= tol and p not in matched_pred), None)
        if hit:
            tp += 1
            matched_pred.add(hit)
    fp = len(pred) - len(matched_pred)
    fn = len(want) - tp
    return dict(tp=tp, fp=fp, fn=fn, precision=tp / max(1, tp + fp), recall=tp / max(1, tp + fn))


def skeleton(accession: str, sequence: int, doc_type: str, nodes: list[dict]) -> str:
    lines = [f"accession: {accession}", f"sequence: {sequence}", f"doc_type: {doc_type}", "reviewed: false", "nodes:"]
    for n in nodes:
        if n["level_kind"] in ("part", "item", "article", "section") and "gram.synth_part" not in (n["rule_ids"] or []):
            t = (n.get("title") or "").replace('"', "'")[:60]
            lines.append(f'  - {{label: "{n["label_canon"]}", raw_start: {n["head_raw_start"]}, title: "{t}"}}')
    lines.append("absent: []")
    return "\n".join(lines) + "\n"
