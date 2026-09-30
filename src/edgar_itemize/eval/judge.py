"""Shared helpers for the LLM-judge scripts: verdict keys, loading, and the
current parser's status for a stored window (task-aware).

Tasks: heading (item/part candidates, the original bank), subheading (depth>=3
blocks inside 10-K items), contract / parent (EX-10 headings and clause
placement), boundary (segment boundaries). Only heading/subheading/contract
carry an is_heading verdict that can be scored against the parser.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

SCORED_TASKS = ("heading", "subheading", "contract")


def key(d: dict) -> tuple:
    return (d.get("task", "heading"), d["accession"], d.get("sequence") or 0, d["raw_start"], d["label"])


def load_verdicts(path: str | Path, *, scored_only: bool = True) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for line in open(path):
        d = json.loads(line)
        if d.get("error"):
            continue
        if scored_only and d.get("task", "heading") in SCORED_TASKS and d.get("is_heading") is None:
            continue
        out[key(d)] = d
    return out


def block_at(blocks, raw_off: int):
    """The block whose raw span contains raw_off (blocks sorted by raw_start)."""
    starts = [b.raw_start for b in blocks]
    i = bisect_right(starts, raw_off) - 1
    if i >= 0 and blocks[i].raw_start <= raw_off < max(blocks[i].raw_end, blocks[i].raw_start + 1):
        return blocks[i]
    return None


def block_near(blocks, raw_off: int, *, ahead: int = 400, lead: str = ""):
    """block_at, or the next block starting within `ahead` bytes (HTML detectors report the
    offset of the opening tag, which precedes the block's first text byte)."""
    b = block_at(blocks, raw_off)
    if b is not None:
        return b
    starts = [x.raw_start for x in blocks]
    i = bisect_right(starts, raw_off)
    if i < len(blocks) and blocks[i].raw_start - raw_off <= ahead:
        if not lead or blocks[i].text.lstrip().startswith(lead[:12]):
            return blocks[i]
    return None


def current_status(r, rec: dict) -> str:
    """'accepted' or the rejection reason / 'no_candidate' for a stored window under parse result r."""
    task = rec.get("task", "heading")
    start, lab = rec["raw_start"], rec["label"]
    if task == "subheading":
        lo = rec.get("block_raw_start", start)
        hi = rec.get("block_raw_end", start + 1)
        for n in r.nodes:
            if n.level_kind == "heading" and lo <= n.head_raw_start < hi:
                return "accepted"
        return "no_candidate"
    if task == "contract":
        if any((n.level_kind in ("article", "section") or n.level_kind.startswith("clause")) and n.head_raw_start == start for n in r.nodes):
            return "accepted"
    elif any(n.level_kind in ("item", "part") and n.head_raw_start == start for n in r.nodes):
        return "accepted"
    for x in r.rejected:
        if x.raw_start == start and x.label_canon == lab:
            return x.reason
    for x in r.rejected:
        if x.raw_start == start:
            return x.reason
    return "no_candidate"


def mark_window(norm: str, norm_start: int, norm_end: int, window: int, marker: str = ">>>") -> str:
    lo, hi = max(0, norm_start - window), min(len(norm), norm_end + window)
    return norm[lo:norm_start] + f"\n{marker} " + norm[norm_start:norm_end] + "\n" + norm[norm_end:hi]
