"""Unlabeled sub-headings (depth 3+) inside 10-K Items.

A block is a heading candidate when it is short, not prose, not table
furniture, and carries heading style (bold / underline / caps / larger font /
centered) or, in the plain-text era, stands alone as a short title-case line.
Relative depth among headings within one Item is inferred from a style
signature: a stronger signature (larger font, caps, bold ...) opens a level
that weaker signatures nest under.
"""

from __future__ import annotations

import regex

from .blocks import Block
from .candidates import CONTINUED_RE
from .tree import Node, Rejected

_RUNIN_STRENGTH = (0.0, 0, 0, 0, 0, 0)  # below every standalone signature: run-ins nest under them

_NUM_ONLY_RE = regex.compile(r"^[\s\d\.\-–—()ivxIVX]+$")
_LEADIN_RE = regex.compile(r"[:;,]$|\b(?:as follows|the following)\b", regex.IGNORECASE)
_SENTENCE_RE = regex.compile(r"[a-z]\.\s+[A-Z]")
_ITEM_LIKE_RE = regex.compile(r"^\s*(?:item|part|table of contents|index|signatures?|exhibit)\b", regex.IGNORECASE)
_NOISE_RE = regex.compile(
    r"/s/|\b(?:LLP|LLC|Inc\.?|Ltd\.?|Corp\.?)$|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}"
    r"|\d{4}\s+and\s+\d{4}|^\(.*\)$|\$|%|^\d",
    regex.IGNORECASE,
)
_PAGE_FURNITURE_RE = regex.compile(r"^\s*(?:-\s*\d+\s*-|page\s+\d+|\d{1,3}|[ivx]{1,5})\s*$", regex.IGNORECASE)
_WS_RE = regex.compile(r"\s+")

# rej.page_banner (Turn 12 B.3, docs/turn12_decisions/a3_subheadings.md section (b)): a
# repeated heading title is a running page header when it is a LATER copy of a run of
# three or more under one parent AND it sits within this many visible characters of the
# preceding page break.  The spec wrote 80, read on a raw-byte proxy that counted the
# matched tag's own attribute tail as text on every HTML page break; measured at block
# level the same bank reads 1.000 at 40 (54/54) and 0.985 at 80 (64/65), and the
# out-of-sample band re-bank of the 41..80 nodes read 0.890 furniture (89/100), below the
# 0.95 bar -- so the constant is 40 (docs/turn12_decisions/b3_page_banner_build.md,
# sections 3 and 3b).
PAGE_BANNER_MIN_RUN = 3
PAGE_BANNER_VISIBLE = 40


def _title_case_ratio(text: str) -> float:
    words = [w for w in regex.findall(r"[A-Za-z][A-Za-z'’\-]*", text) if len(w) > 2 or w[:1].isupper()]
    if not words:
        return 0.0
    return sum(1 for w in words if w[:1].isupper()) / len(words)


def _norm_title(t: str | None) -> str:
    """The census's normalisation: strip, lowercase, collapse internal whitespace."""
    return _WS_RE.sub(" ", (t or "").strip().lower())


def _visible_len(b: Block) -> int:
    """Visible characters of a block: its normalised text (tags and entities are already
    gone), internal whitespace collapsed, stripped -- the block-level twin of the census's
    raw-byte `visible()` (scripts/turn12/a3_score.py)."""
    return len(_WS_RE.sub(" ", b.text).strip())


def page_break_adjacent(blocks: list[Block], idx: int, *, limit: int = PAGE_BANNER_VISIBLE) -> bool:
    """rej.page_banner clause (3): walking back from block `idx`, the nearest page break is
    reached with at most `limit` visible characters of intervening text.

    A page break is a block of kind "page" (`<PAGE>` in either era) or "hr" (`<hr>`), or
    any block carrying `is_page_break` (normalize_html sets it on the first block emitted
    after a CSS `page-break-before/after: always`, so that block's own text lies AFTER the
    break and counts toward the limit; "page"/"hr" marker blocks carry synthetic text --
    "<PAGE>", "----" -- that is not visible and is not counted).  No page break before the
    block, or more than `limit` visible characters before the nearest one, is False.
    """
    if idx < 0 or idx >= len(blocks):
        return False
    if blocks[idx].is_page_break:
        return True  # the heading block is itself the first block after the break
    seen = 0
    k = idx - 1
    while k >= 0:
        b = blocks[k]
        if b.kind in ("page", "hr"):
            return seen <= limit
        seen += _visible_len(b)
        if b.is_page_break:
            return seen <= limit
        if seen > limit:
            return False
        k -= 1
    return False


def _signature(b: Block, era: str) -> tuple | None:
    """Return a style-strength tuple if the block looks like a heading, else None."""
    t = b.text.strip()
    if not t or b.kind != "para" or b.in_table or b.is_page_break:
        return None
    if b.n_lines > 2 or len(t) > 120 or len(t.split()) > 14:
        return None
    if not regex.search(r"[A-Za-z]{2,}", t) or _NUM_ONLY_RE.match(t) or _PAGE_FURNITURE_RE.match(t):
        return None
    if _ITEM_LIKE_RE.match(t) or _LEADIN_RE.search(t) or _SENTENCE_RE.search(t):
        return None
    if CONTINUED_RE.search(t):
        return None  # running page header repeating an open heading (C3 M3)
    if t.endswith(".") and not (b.bold or b.underline or b.caps_ratio >= 0.85):
        return None
    if _NOISE_RE.search(t):
        return None
    caps = b.caps_ratio >= 0.85 and len(t) >= 4
    center_only = b.center and not (b.bold or b.underline or caps or b.font_size_rel > 1.05)
    if center_only and (b.n_lines > 1 or len(t.split()) > 8 or _title_case_ratio(t) < 0.6 or regex.search(r"\d", t)):
        return None
    styled = b.bold or b.underline or caps or b.font_size_rel > 1.05 or (b.center and era != "text")
    rules = []
    if era == "text":
        standalone = b.n_lines == 1 and len(t) <= 70 and b.blank_before and b.blank_after and (_title_case_ratio(t) >= 0.6 or caps)
        if not (styled or standalone):
            return None
        if standalone and not styled:
            rules.append("sty.standalone")
    elif not styled:
        return None
    if b.bold:
        rules.append("sty.bold")
    if b.underline:
        rules.append("sty.underline")
    if caps:
        rules.append("sty.caps")
    if b.center:
        rules.append("sty.center")
    if b.font_size_rel > 1.05:
        rules.append("sty.font_size")
    if b.italic:
        rules.append("sty.italic")
    strength = (round(b.font_size_rel, 2), int(caps), int(b.center), int(b.bold), int(b.underline), int(b.italic))
    return strength, tuple(rules)


def find_headings(blocks: list[Block], nodes: list[Node], era: str, *, max_depth: int = 6,
                  rejected: list[Rejected] | None = None) -> list[Node]:
    """Sub-heading nodes under every item, in document order.

    `rejected`, when given, receives one `Rejected(kind="heading", reason="rej.page_banner")`
    per heading dropped by the page-banner post-pass (below), so the heading layer keeps
    the discipline that every rejected candidate is written with its reason.
    """
    items = sorted((n for n in nodes if n.level_kind == "item"), key=lambda n: n.raw_start)
    if not items:
        return []
    out: list[Node] = []
    bi = 0
    for it in items:
        # blocks inside this item, excluding the heading block itself
        while bi < len(blocks) and blocks[bi].raw_start < it.raw_start:
            bi += 1
        stack: list[tuple[tuple, Node]] = []  # (strength, node), strongest first
        # this item's headings before the banner post-pass: (node, parent key, block index)
        item_out: list[tuple[Node, int, int]] = []
        j = bi
        while j < len(blocks) and blocks[j].raw_start < it.raw_end:
            b = blocks[j]
            j += 1
            if b.raw_start < it.head_raw_end:
                continue
            # a run-in paragraph is a run-in first: its bold lead must not turn the whole
            # paragraph into a standalone heading (short paragraphs inherit the lead's bold)
            if b.runin_text:
                # inside a table it is kept unless other cells of its row carry content
                # (publishers wrap body paragraphs in layout tables, often behind spacer cells)
                if b.in_table and b.row_text and b.row_text.strip() != b.text.strip():
                    continue
                sig = (_RUNIN_STRENGTH, ("sty.runin",))
            else:
                sig = _signature(b, era)
                if sig is None:
                    continue
            strength, rules = sig
            title = b.runin_text.rstrip(" .:") if "sty.runin" in rules else b.text.strip()
            head_end = b.runin_raw_end if "sty.runin" in rules else b.raw_end
            # pop weaker-or-equal open levels
            while stack and stack[-1][0] <= strength:
                stack.pop()
            depth = 3 + len(stack)
            if depth > max_depth:
                depth = max_depth
            n = Node(0, it.node_id if not stack else stack[-1][1].node_id, depth, "heading", None, None, title[:200],
                     b.raw_start, it.raw_end, b.raw_start, head_end, b.norm_start, it.norm_end, 0, 0.55 if "sty.runin" in rules else 0.6, ["hdg"] + list(rules))
            n.parent_id = -2  # placeholder; parent linking is done in renumber() by position/depth
            parent_key = id(it) if not stack else id(stack[-1][1])
            stack.append((strength, n))
            item_out.append((n, parent_key, j - 1))
        # rej.page_banner (Turn 12 B.3, docs/turn12_decisions/a3_subheadings.md section (b)):
        # drop a heading when all three hold -- (1) its normalised title has already
        # appeared as a heading under the same parent in this item (it is not the first
        # copy); (2) that run has PAGE_BANNER_MIN_RUN or more copies; (3) its block is
        # page-break-adjacent (`page_break_adjacent`).  A post-pass because the run length
        # needs the whole item, which the stack walk above does not have.  Surviving nodes
        # keep their depths; merge_and_renumber re-derives parents and ends by position, so
        # a heading that was nested under a dropped copy re-attaches to the nearest open
        # node above it (the copy that survives, in the running-header case).  Must not
        # condemn: the first copy of any run, any run of two, any copy with body text
        # between it and the page break, any single-occurrence heading.
        runs: dict[tuple[int, str], list[int]] = {}
        for k, (n, parent_key, _bpos) in enumerate(item_out):
            runs.setdefault((parent_key, _norm_title(n.title)), []).append(k)
        drop: set[int] = set()
        for members in runs.values():
            if len(members) < PAGE_BANNER_MIN_RUN:
                continue
            for k in members[1:]:
                n, _pk, bpos = item_out[k]
                if page_break_adjacent(blocks, bpos):
                    drop.add(k)
                    if rejected is not None:
                        rejected.append(Rejected(bpos, "heading", "", n.confidence, "rej.page_banner",
                                                 n.raw_start, blocks[bpos].text[:120]))
        out.extend(n for k, (n, _pk, _bpos) in enumerate(item_out) if k not in drop)
    return out


def merge_and_renumber(nodes: list[Node], headings: list[Node]) -> list[Node]:
    """Insert heading nodes, assign pre-order ids and parents, and close ends.

    Parents are re-derived from document position: the nearest node still open at a
    smaller depth.  `tree.out_of_order_items` is the one placement that position cannot
    reproduce — a `seq.out_of_order` item is under its statutory Part wherever the filer
    typeset it, and 51% of them sit in front matter, before any Part heading exists, where
    this pass would hand them to the document root at depth 2 (a depth-2 node with a
    one-ordinal path, and top-level ordinals stolen from the real Parts).  Their parent is
    therefore carried over from build_tree rather than recomputed.  The alternative, the
    `gram.part_after_item` convention of opening the Part early, would move Part II's start
    into the front matter and out of order with Part I, which reorders and re-spans nodes
    the chain got right.
    """
    all_nodes = [n for n in nodes if n.node_id != 0 or n.level_kind == "document"]
    root = next(n for n in all_nodes if n.level_kind == "document")
    by_old_id = {n.node_id: n for n in all_nodes}
    keep_parent = {id(n): by_old_id.get(n.parent_id) for n in all_nodes
                   if "seq.out_of_order" in (n.rule_ids or [])}
    others = [n for n in all_nodes if n is not root] + headings
    others.sort(key=lambda n: (n.raw_start, n.depth))
    root.node_id = 0
    root.parent_id = -1
    ordered = [root]
    open_by_depth: list[Node] = [root]  # index = depth
    for n in others:
        n.node_id = len(ordered)
        d = n.depth
        if n.level_kind == "toc":
            # stack-neutral: a mid-document TOC must not pop the open part/item,
            # or every node after it is orphaned to the root (G1 orphan bug)
            n.parent_id = open_by_depth[min(d, len(open_by_depth)) - 1].node_id if open_by_depth else root.node_id
            ordered.append(n)
            continue
        # parent = nearest open node with smaller depth
        while len(open_by_depth) > d:
            open_by_depth.pop()
        # find nearest ancestor: last open node with depth < d
        parent = open_by_depth[-1] if open_by_depth else root
        n.parent_id = parent.node_id
        while len(open_by_depth) < d:
            open_by_depth.append(parent)
        open_by_depth.append(n)
        ordered.append(n)
    for n in ordered:  # seq.out_of_order keeps the Part build_tree put it under
        p = keep_parent.get(id(n))
        if p is not None:
            n.parent_id = p.node_id
    # ends: a node ends where the next node at depth <= its own begins
    seq = ordered[1:]
    for i, n in enumerate(seq):
        if n.level_kind == "toc":
            continue  # TOC ends are capped at the last member block (B2 hygiene)
        for m in seq[i + 1:]:
            if m.depth <= n.depth and m.raw_start > n.raw_start:
                n.raw_end = min(n.raw_end, m.raw_start) if n.level_kind == "heading" else m.raw_start
                n.norm_end = min(n.norm_end, m.norm_start) if n.level_kind == "heading" else m.norm_start
                break
    return ordered
