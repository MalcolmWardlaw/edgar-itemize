"""Stored-run reads for the viewer (docs/VIEWER_PLAN.md phase 2).

A run directory holds one year partition per table (documents-, nodes-, rejected-
<kind>-<year>.parquet, see the run registry runs.yaml). This module reads one document's rows from a run
with a pyarrow filter on accession and sequence and wraps them in an object shaped like
pipeline.ParseResult, so the viewer's /api/doc and /api/original render a stored run
through exactly the code that renders a live parse.

Runs are written with --no-text, so the block list (and every block's text) is not
stored. It is recomputed from the raw file by the normalizer alone -- the first steps of
pipeline.parse_document, with no candidate, TOC or tree pass -- and the stored nodes are
laid over it. Nothing here changes parser behaviour.

The run registry (runs.yaml: corpus layouts and baselines of record) is not part of the
package. It is read from the directory named by EDGAR_ITEMIZE_VIEWER_CONFIG; when that is
unset or holds no runs.yaml, the registry is empty: the four EDGAR corpora keep their
built-in layouts, no baselines are known, and the viewer serves live parses only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import yaml

from .. import NORMALIZER_VERSION, PARSER_VERSION
from ..classify import classify
from ..pipeline import normalize
from ..sgml import blank_ix_header, conformed_name, document_text

CONFIG_ENV = "EDGAR_ITEMIZE_VIEWER_CONFIG"
_RUN_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
log = logging.getLogger("edgar_itemize.viewer")

# The EDGAR corpora's run layouts, used when the registry does not name them.
BUILTIN_CORPORA = {
    "10k": {"kind": "10k", "key": "accession"},
    "10q": {"kind": "10k", "key": "accession"},
    "ex10": {"kind": "ex10", "key": "accession_sequence"},
    "ex13": {"kind": "ex13", "key": "accession_sequence"},
}
_noted: set[str] = set()


def config_dir() -> Path | None:
    env = os.environ.get(CONFIG_ENV)
    return Path(env) if env else None


def config_file(name: str) -> Path | None:
    """<config dir>/<name> when the variable is set and the file exists, else None (and one
    log line per process and file saying so)."""
    d = config_dir()
    p = d / name if d else None
    if p is not None and p.is_file():
        return p
    if name not in _noted:
        _noted.add(name)
        where = f"{CONFIG_ENV} is unset" if d is None else f"{p} not found"
        log.warning("viewer: %s; no %s registry: no stored runs or baselines, live parse only", where, name)
    return None


@lru_cache(maxsize=4)
def _load_yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def registry() -> dict:
    p = config_file("runs.yaml")
    reg = _load_yaml(str(p)) if p is not None else {}
    return {"corpora": reg.get("corpora") or {}, "baselines": reg.get("baselines") or {}}


def corpora() -> dict[str, dict]:
    """The registry's corpora, in registry order (may be empty)."""
    return dict(registry()["corpora"])


def corpus_spec(corpus: str) -> dict | None:
    return corpora().get(corpus) or BUILTIN_CORPORA.get(corpus)


def corpus_kind(corpus: str) -> str:
    spec = corpus_spec(corpus)
    if spec is None:
        raise KeyError(f"corpus {corpus!r} is not in the run registry")
    return spec["kind"]


def baselines() -> dict[int, dict[str, str]]:
    return {int(k): v for k, v in (registry().get("baselines") or {}).items()}


def latest_baseline(corpus: str) -> str | None:
    b = baselines()
    for turn in sorted(b, reverse=True):
        if b[turn].get(corpus):
            return b[turn][corpus]
    return None


def run_dir(runs_root: Path, run: str) -> Path:
    run = run[len("runs/"):] if run.startswith("runs/") else run
    if not _RUN_NAME.match(run) or run in (".", ".."):
        raise ValueError(f"bad run name {run!r}")
    return runs_root / run


class RunMissing(LookupError):
    pass


class DocMissing(LookupError):
    pass


def _filters(accession: str, sequence: int | None):
    f = [("accession_number", "=", accession)]
    if sequence is not None:
        f.append(("sequence", "=", int(sequence)))
    return f


def _partitions(d: Path, table: str, kind: str, year: int | None) -> list[Path]:
    first = [d / f"{table}-{kind}-{year}.parquet"] if year is not None else []
    rest = sorted(p for p in d.glob(f"{table}-{kind}-*.parquet") if p not in first)
    return [p for p in first if p.exists()] + rest


def read_run_doc(runs_root: Path, run: str, corpus: str, accession: str, sequence: int | None,
                 year: int | None) -> tuple[dict, list[dict], list[dict]]:
    """(documents row, node rows, rejected rows) for one document of a stored run.

    The year partition named by the manifest is read first; if the document is not
    there (a manifest year that disagrees with the run's partition), every partition is
    scanned. `sequence` None matches the accession's primary document (10-K / 10-Q)."""
    d = run_dir(runs_root, run)
    if not d.is_dir():
        raise RunMissing(f"run {run} not found under {runs_root}")
    kind = corpus_kind(corpus)
    for p in _partitions(d, "documents", kind, year):
        docs = pq.read_table(p, filters=_filters(accession, sequence)).to_pylist()
        if not docs:
            continue
        doc = docs[0]
        seq = doc["sequence"]
        y = p.stem.rsplit("-", 1)[1]
        f = _filters(accession, seq)
        nodes = pq.read_table(d / f"nodes-{kind}-{y}.parquet", filters=f).to_pylist() if (d / f"nodes-{kind}-{y}.parquet").exists() else []
        rej = pq.read_table(d / f"rejected-{kind}-{y}.parquet", filters=f).to_pylist() if (d / f"rejected-{kind}-{y}.parquet").exists() else []
        nodes.sort(key=lambda n: n["node_id"])
        return doc, nodes, rej
    raise DocMissing(f"{accession} sequence {sequence} not in {run} ({kind})")


@dataclass
class RunResult:
    """Duck-types the pipeline.ParseResult fields the viewer reads."""
    accession: str
    cik: str
    doc: object
    profile: object
    blocks: list
    normalized_text: str
    nodes: list
    rejected: list
    grammar: str
    paths: dict
    profile_levels: list
    candidates: list = field(default_factory=list)
    run: str = ""
    run_info: dict = field(default_factory=dict)


def normalize_only(sub, d, cik):
    """The normalizer steps of pipeline.parse_document, nothing after them."""
    body = document_text(sub.text, d)
    profile = classify(sub.accession, cik, d, body, company_name=conformed_name(sub.header))
    if profile.era == "ixbrl":
        body = blank_ix_header(body)
    blocks, norm, _omap = normalize(body, d.text_start, profile)
    return profile, blocks, norm


def run_result(runs_root: Path, run: str, corpus: str, row: dict, sub, d) -> RunResult:
    keyed = (corpus_spec(corpus) or {}).get("key") == "accession_sequence"
    seq = d.sequence if keyed else (d.sequence or None)
    try:
        doc, nodes, rej = read_run_doc(runs_root, run, corpus, row["accession_number"], seq, row.get("year"))
    except DocMissing:
        if seq is None:
            raise
        # 10-K/10-Q runs store the primary document's own sequence; fall back to accession only
        doc, nodes, rej = read_run_doc(runs_root, run, corpus, row["accession_number"], None, row.get("year"))
    live_profile, blocks, norm = normalize_only(sub, d, row["cik"])
    signals = doc.get("signals")
    try:
        signals = json.loads(signals) if isinstance(signals, str) else (signals or {})
    except ValueError:
        signals = {}
    profile = SimpleNamespace(era=doc.get("profile_era"), publisher=doc.get("profile_publisher"),
                              agent_cik=doc.get("agent_cik"), signals=signals)
    ns = [SimpleNamespace(node_id=n["node_id"], parent_id=n["parent_id"], depth=n["depth"], level_kind=n["level_kind"],
                          label_canon=n["label_canon"], title=n["title"], raw_start=n["raw_start"], raw_end=n["raw_end"],
                          head_raw_start=n["head_raw_start"], head_raw_end=n["head_raw_end"], confidence=n["confidence"],
                          rule_ids=list(n["rule_ids"] or [])) for n in nodes]
    paths: dict = {n["node_id"]: list(n["path"]) if n["path"] is not None else None for n in nodes}
    paths["_bounds"] = {"front_end": doc.get("front_end"), "back_start": doc.get("back_start"),
                        "back_norm_start": None, "back_rule": None}
    rj = [SimpleNamespace(block_idx=x["block_idx"], kind=x["kind"], label_canon=x["label_canon"], score=x["score"],
                          reason=x["reason"], raw_start=x["raw_start"], text=x["text"] or "") for x in rej]
    lp = doc.get("level_profile")
    try:
        lp = json.loads(lp) if isinstance(lp, str) else (lp or [])
    except ValueError:
        lp = []
    info = dict(run=run, corpus=corpus, parser_version=doc.get("parser_version"), normalizer_version=doc.get("normalizer_version"),
                current_parser_version=PARSER_VERSION, current_normalizer_version=NORMALIZER_VERSION,
                n_blocks_run=doc.get("n_blocks"), n_blocks_now=len(blocks), error=doc.get("error"),
                live_era=live_profile.era)
    warn = []
    if doc.get("n_blocks") is not None and doc["n_blocks"] != len(blocks):
        warn.append(f"the run normalised {doc['n_blocks']} blocks, the current normalizer {len(blocks)}; "
                    "block indices on rejected rows refer to the run's blocks")
    if doc.get("normalizer_version") and doc["normalizer_version"] != NORMALIZER_VERSION:
        warn.append(f"run normalizer {doc['normalizer_version']} != current {NORMALIZER_VERSION}")
    info["warnings"] = warn
    return RunResult(accession=row["accession_number"], cik=str(doc.get("cik") or row["cik"]), doc=d, profile=profile,
                     blocks=blocks, normalized_text=norm, nodes=ns, rejected=rj, grammar=doc.get("grammar"), paths=paths,
                     profile_levels=lp, run=run, run_info=info)


def anchor_status(nodes: list[dict], rejected: list[dict], anchor: int | None, label: str | None) -> dict:
    """An anchor's status in one run's rows: accepted (a node heads there), rejected (with
    the reason), or absent. A same-label match is preferred at the offset."""
    if anchor is None:
        return dict(status="no_anchor")
    heads = [n for n in nodes if n.get("head_raw_start") == anchor and n.get("node_id") != 0]
    if heads:
        n = next((h for h in heads if h.get("label_canon") == label), heads[0])
        return dict(status="accepted", node_id=n["node_id"], kind=n["level_kind"], label=n["label_canon"],
                    title=n.get("title"), depth=n["depth"], path_str=n.get("path_str"), rules=list(n.get("rule_ids") or []))
    rs = [x for x in rejected if x.get("raw_start") == anchor]
    if rs:
        x = next((r for r in rs if r.get("label_canon") == label), rs[0])
        return dict(status="rejected", reason=x["reason"], label=x["label_canon"], score=x["score"], kind=x["kind"])
    return dict(status="absent")
