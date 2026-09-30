"""Per-turn summary page: /turns, /api/turns, /api/turns/{n}.

Serves the JSON files `scripts/turn_summary.py` writes under docs/turn_summaries/ and the
static page that renders them. Read-only; mounted on the viewer app by one
`include_router` line at the end of app.py.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).parent / "static"
_REPO = Path(__file__).resolve().parents[3]
SUMMARIES = Path(os.environ.get("EDGAR_ITEMIZE_TURN_SUMMARIES") or (_REPO / "docs" / "turn_summaries"))
_NAME = re.compile(r"^turn(\d+)\.json$")

router = APIRouter()


def _files() -> dict[int, Path]:
    if not SUMMARIES.is_dir():
        return {}
    out = {}
    for p in SUMMARIES.iterdir():
        m = _NAME.match(p.name)
        if m:
            out[int(m.group(1))] = p
    return out


@router.get("/turns")
def turns_page():
    return FileResponse(STATIC / "turns.html")


@router.get("/api/turns")
def list_turns():
    out = []
    for n, p in sorted(_files().items(), reverse=True):
        try:
            s = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        out.append(dict(turn=n, closed=s.get("closed"), generated_at=s.get("generated_at"),
                        report=s.get("report"), file=f"docs/turn_summaries/{p.name}"))
    return out


@router.get("/api/turns/{n}")
def get_turn(n: int):
    p = _files().get(n)
    if p is None:
        raise HTTPException(404, f"no summary for turn {n}; run scripts/turn_summary.py --turn {n}")
    return json.loads(p.read_text())
