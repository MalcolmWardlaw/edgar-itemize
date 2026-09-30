"""Write docs/RULES.md, the catalogue of rule ids and rejected reasons, from the source.

    uv run python scripts/rules_catalogue.py          # rewrite docs/RULES.md
    uv run python scripts/rules_catalogue.py --check  # exit 1 when docs/RULES.md is stale

Every entry comes from the parser's own source: the script parses each module under
`src/edgar_itemize/` (the viewer excluded) with `ast`, finds every string literal or
f-string shaped like a rule id (`<family>.<name>`, families listed in FAMILIES), and
classifies each occurrence by its syntactic position -- appended, returned or assigned
into a rule list is an *emit*; tested with `in`, `==`, `startswith` or iterated for a
membership test is a *read*; anything else is a *mention*. The rejected table's closed
`reason` vocabulary is collected the same way from `rej(c, "...")` / `Rejected(...)`
calls. Provenance is looked up in the turn plans, reports and decision folders under
`docs/`: the earliest turn that names the id, and the decision documents that do.

The heuristics are syntactic and can misclassify an unusual site; the catalogue says
where an id appears and how, and the source line is one grep away. `tests/
test_rules_catalogue.py` renders the catalogue and compares it with the committed file, so
a change to the vocabulary or its sites fails the test until this script is re-run.
No line numbers are written, so an unrelated edit does not stale the file.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "edgar_itemize"
DOCS = ROOT / "docs"
OUT = DOCS / "RULES.md"

# Modules the scan covers: the parser proper. The viewer only displays ids; the eval
# package reads them for scoring and is included so a reader with no emitter shows up.
SKIP_DIRS = {"viewer", "__pycache__"}

# Rule-id families and what they mean (docs/OUTPUT_CONTRACT.md section 8.2). A literal
# whose family is not listed here is not a rule id (file names, column names, ...).
FAMILIES: dict[str, str] = {
    "lbl": "the grammar level a label matched (`lbl.part`, `lbl.item`, ...), and `lbl.item.multi` for a combined heading",
    "gram": "grammar and label matching: how the label was read, canonicalised or synthesised",
    "sty": "style evidence on the block (bold, caps, centred, larger font, ...)",
    "pos": "position evidence (anchor target, head line only, table row, short block, line k of the block)",
    "pub": "publisher-specific evidence",
    "toc": "table-of-contents evidence and region decisions",
    "ctoc": "contract table-of-contents boundary (Turn 11)",
    "fm": "front-matter table rows (Turn 10)",
    "rej": "penalties and vetoes that were applied to a candidate",
    "seq": "sequencing decisions: chain, out-of-order placement, duplicates, clause sequencing",
    "chain": "numbering restarts in a contract (one chain per restart)",
    "seg": "instrument boundaries in a compound exhibit (segments)",
    "agenda": "back-matter and segment placement (the meta digit)",
    "tree": "span and parent changes after the tree is built",
    "kind": "EX-13 heading classification into the 10-K item it satisfies",
    "multi": "the further items a combined heading covers (`multi.ITEM <k>`)",
    "ocr": "structure recovered from an OCR text layer",
    "omit": "omission statements (read by the evaluation metrics; no current emitter)",
    "doc": "the document root node",
}

# The order stages run in, for the flow diagram and for sorting sites.
STAGES: list[tuple[str, str]] = [
    ("grammar", "grammar/*"),
    ("classify", "classify"),
    ("candidates", "candidates"),
    ("toc", "toc"),
    ("tree", "tree"),
    ("tree_contract", "tree_contract"),
    ("sequence", "sequence"),
    ("agenda", "agenda"),
    ("headings", "headings"),
    ("ars_kind", "ars_kind"),
    ("pipeline", "pipeline"),
    ("eval", "eval/*"),
]
_STAGE_RANK = {name: i for i, (name, _) in enumerate(STAGES)}

_ID_RE = re.compile(r"^(?P<fam>[a-z]+)\.[A-Za-z0-9_*][A-Za-z0-9_. *]*$")
_REASON_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)?$")

# Cross-stage edges drawn although the reader's module sits earlier in STAGES: passes
# hosted by an earlier module but called later in `pipeline.parse_prepared`.
_LATER_PASSES: dict[tuple[str, str], str] = {
    ("toc", "candidates"): "candidates.vetoed_index_row_pass runs after detect_toc",
    ("pipeline", "tree"): "the Reg-AB rebuild: pipeline tags, build_tree runs again",
}
_EMIT_ATTRS = {"append", "extend", "add", "insert"}
_READ_ATTRS = {"startswith", "endswith", "isdisjoint", "intersection", "issubset", "get", "count", "index"}

# Expansions of the dynamic ids whose value set is closed and importable.
def _expansions() -> dict[str, list[str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from edgar_itemize import ars_kind  # noqa: E402
    from edgar_itemize.grammar.contract import ContractGrammar  # noqa: E402
    from edgar_itemize.grammar.form10k import Form10KGrammar  # noqa: E402
    from edgar_itemize.grammar.form10q import Form10QGrammar  # noqa: E402
    kinds: list[str] = []
    for g in (Form10KGrammar(), Form10QGrammar(), ContractGrammar()):
        for lvl in g.levels:
            if lvl.kind not in kinds:
                kinds.append(lvl.kind)
    return {"lbl.*": [f"lbl.{k}" for k in kinds], "kind.*": [ars_kind.KIND_RULE[k] for k in sorted(ars_kind.KIND_ITEM)]}


@dataclass
class Site:
    module: str        # "candidates", "grammar/form10k", "eval/metrics"
    func: str          # enclosing function ("" at module level)
    role: str          # emit | read | mention

    @property
    def stage(self) -> str:
        if self.module.startswith("grammar/"):
            return "grammar"
        if self.module.startswith("eval/"):
            return "eval"
        return self.module

    def key(self) -> tuple:
        return (_STAGE_RANK.get(self.stage, 99), self.module, self.func)

    def label(self) -> str:
        return f"{self.module}.{self.func}" if self.func else f"{self.module} (module level)"


@dataclass
class Entry:
    id: str
    dynamic: bool = False
    sites: list[Site] = field(default_factory=list)

    @property
    def family(self) -> str:
        return self.id.split(".", 1)[0]

    def by_role(self, role: str) -> list[str]:
        seen: list[str] = []
        for s in sorted(self.sites, key=Site.key):
            if s.role == role and s.label() not in seen:
                seen.append(s.label())
        return seen


class _Scanner(ast.NodeVisitor):
    def __init__(self, module: str, entries: dict[str, Entry], reasons: dict[str, Entry]):
        self.module, self.entries, self.reasons = module, entries, reasons
        self.stack: list[ast.AST] = []
        self.funcs: list[str] = []

    # -- traversal with a parent stack and the enclosing function name --
    def visit(self, node: ast.AST):
        is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_func:
            self.funcs.append(node.name)
        self.stack.append(node)
        try:
            super().visit(node)
        finally:
            self.stack.pop()
            if is_func:
                self.funcs.pop()

    def _func(self) -> str:
        return ".".join(self.funcs)

    # -- rule ids --
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            self._consider(node.value, node, dynamic=False)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("*")
        s = "".join(parts)
        if s.startswith("*"):
            return
        self._consider(s, node, dynamic=True)
        # do not descend: the formatted values are not ids

    def _consider(self, s: str, node: ast.AST, *, dynamic: bool) -> None:
        if s == "doc" and self._is_rule_list_literal(node):
            self._record("doc", node, dynamic=False)
            return
        if not dynamic and self._is_reason(node, s):
            e = self.reasons.setdefault(s, Entry(s))
            e.sites.append(Site(self.module, self._func(), "emit"))
            return
        m = _ID_RE.match(s)
        if not m or m.group("fam") not in FAMILIES:
            return
        self._record(s, node, dynamic=dynamic)

    def _record(self, s: str, node: ast.AST, *, dynamic: bool) -> None:
        e = self.entries.setdefault(s, Entry(s, dynamic))
        e.sites.append(Site(self.module, self._func(), self._role(node)))

    def _is_rule_list_literal(self, node: ast.AST) -> bool:
        # ["doc"] as a positional argument of Node(...): the root node's rule list
        parent = self.stack[-2] if len(self.stack) >= 2 else None
        return isinstance(parent, ast.List) and len(parent.elts) == 1

    # -- rejected reasons: the reason argument of rej(c, ...) / Rejected(...), possibly
    # through `.get(i, "...")`, or a store into a name that carries "reason" --
    def _is_reason(self, node: ast.AST, s: str) -> bool:
        if not _REASON_RE.match(s):
            return False
        for a in reversed(self.stack[:-1][-4:]):
            if isinstance(a, ast.Call):
                fn = a.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                if name == "rej" and len(a.args) >= 2 and _contains(a.args[1], node):
                    return True
                if name == "Rejected" and ((len(a.args) >= 5 and _contains(a.args[4], node))
                                           or any(k.arg == "reason" and _contains(k.value, node) for k in a.keywords)):
                    return True
            if isinstance(a, ast.Assign) and a.value is node and not _ID_RE.match(s):
                # `forced_reason[i] = "weak_duplicate"`; a rule id stored in `reason` is a
                # TocRegion reason, which becomes the toc node's rule id, not a rejection
                for t in a.targets:
                    base = t.value if isinstance(t, ast.Subscript) else t
                    if isinstance(base, ast.Name) and "reason" in base.id:
                        return True
        return False

    # -- classify an occurrence by its ancestors --
    def _role(self, node: ast.AST) -> str:
        chain = list(reversed(self.stack[:-1]))  # nearest ancestor first
        for i, a in enumerate(chain):
            if isinstance(a, ast.Compare):
                return "read"
            if isinstance(a, ast.comprehension):
                # `any(r in x.rule_ids for r in (...))` is a membership test; any other
                # comprehension over ids builds a list of them
                outer = chain[i + 2] if i + 2 < len(chain) else None
                if isinstance(outer, ast.Call) and isinstance(outer.func, ast.Name) and outer.func.id in ("any", "all"):
                    return "read"
                return "emit"
            if isinstance(a, ast.For):
                return "emit"  # `for rid in (...): x.rule_ids.append(rid)`
            if isinstance(a, ast.Call):
                f = a.func
                attr = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if attr in _EMIT_ATTRS:
                    return "emit"
                if attr in _READ_ATTRS:
                    return "read"
                if attr in ("Node", "TocRegion", "Rejected"):
                    return "emit"
                if attr in ("any", "all", "sorted", "set", "frozenset", "list", "tuple"):
                    continue
                return "mention"
            if isinstance(a, ast.BinOp) and isinstance(a.op, (ast.BitAnd, ast.BitOr, ast.Sub)):
                return "read"
            if isinstance(a, (ast.Return, ast.AugAssign)):
                return "emit"
            if isinstance(a, (ast.Assign, ast.AnnAssign)):
                # inside a function, `rules = [...]` starts a rule list. At module level a
                # named constant (`CHAIN_RESTART = "chain.restart"`), a dict of ids or a
                # table pairing ids with patterns is a source; a bare collection of ids is
                # a vocabulary some test reads.
                if self.funcs:
                    return "emit"
                v = a.value
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("frozenset", "set", "tuple", "list") and v.args:
                    v = v.args[0]
                if isinstance(v, (ast.Tuple, ast.List, ast.Set)) and all(isinstance(x, ast.Constant) for x in v.elts):
                    return "read"
                return "emit"
            if isinstance(a, ast.Subscript):
                return "read"
            if isinstance(a, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                return "mention"
        return "mention"


def _contains(tree: ast.AST, node: ast.AST) -> bool:
    return any(n is node for n in ast.walk(tree))


def scan(src: Path = SRC) -> tuple[dict[str, Entry], dict[str, Entry]]:
    entries: dict[str, Entry] = {}
    reasons: dict[str, Entry] = {}
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        if any(p in SKIP_DIRS for p in rel.parts):
            continue
        module = str(rel.with_suffix("")).replace("\\", "/")
        if module.endswith("__init__"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _Scanner(module, entries, reasons).visit(tree)
    # a literal that is the fixed prefix of a dynamic pattern (`"pos.line"` tested with
    # startswith, `"multi.ITEM "` split) is a read of that pattern, not an id of its own
    for pat in [e for e in entries.values() if e.dynamic]:
        stem = pat.id.replace("*", "")
        lit = entries.get(stem)
        if lit is not None and not lit.dynamic:
            pat.sites += lit.sites
            del entries[stem]
    return entries, reasons


# ---------------------------------------------------------------- provenance in docs/ --
_TURN_RE = re.compile(r"turn(\d+)", re.IGNORECASE)


def _doc_sources() -> list[Path]:
    pats = ["TURN*_PLAN.md", "TURN*_REPORT.md", "TURN*_DECISIONS.md", "TURN*_LEADS.md", "turn*_decisions/*.md",
            "turn*_leads/*.md", "PILOT_REPORT.md", "OVERNIGHT_RECON.md", "VIEWER_PLAN.md"]
    out: list[Path] = []
    for p in pats:
        out += DOCS.glob(p)

    def turn_of(p: Path) -> int:
        m = _TURN_RE.search(str(p.relative_to(DOCS)))
        return int(m.group(1)) if m else 0

    return sorted(set(out), key=lambda p: (turn_of(p), str(p)))


def provenance(ids: list[str]) -> dict[str, dict]:
    texts = [(p, p.read_text(encoding="utf-8", errors="replace")) for p in _doc_sources()]
    out: dict[str, dict] = {}
    for rid in ids:
        pat = re.compile(re.escape(rid.replace("*", "")) + r"(?![A-Za-z0-9_])")
        turns: list[int] = []
        decisions: list[str] = []
        others: list[str] = []
        for p, t in texts:
            if not pat.search(t):
                continue
            m = _TURN_RE.search(str(p.relative_to(DOCS)))
            if m:
                turns.append(int(m.group(1)))
            rel = str(p.relative_to(ROOT))
            (decisions if "_decisions/" in rel or "_leads/" in rel else others).append(rel)
        since = f"Turn {min(turns)}" if turns else ("pilot" if others else "")
        out[rid] = {"since": since, "docs": decisions[:3] or others[:2]}
    return out


# ---------------------------------------------------------------------------- render --
def _md_list(items: list[str]) -> str:
    return "<br>".join(f"`{x}`" for x in items) if items else ""


def _docs_md(paths: list[str]) -> str:
    return "<br>".join(f"[{Path(p).name}]({Path('..') / Path(p).relative_to('docs') if p.startswith('docs/') else p})" for p in paths)


def _flow_mermaid(entries: dict[str, Entry]) -> str:
    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in sorted(entries.values(), key=lambda e: e.id):
        emit = {s.stage for s in e.sites if s.role == "emit"}
        read = {s.stage for s in e.sites if s.role == "read"}
        for a in emit:
            for b in read:
                forward = _STAGE_RANK.get(a, 99) < _STAGE_RANK.get(b, 99) or (a, b) in _LATER_PASSES
                if a != b and forward and e.id not in edges[(a, b)]:
                    edges[(a, b)].append(e.id)
    lines = ["flowchart LR"]
    used = {n for pair in edges for n in pair}
    for name, _ in STAGES:
        if name in used:
            lines.append(f"    {name}[{name}]")
    for (a, b), ids in sorted(edges.items(), key=lambda kv: (_STAGE_RANK.get(kv[0][0], 99), _STAGE_RANK.get(kv[0][1], 99))):
        label = "<br/>".join(ids[:8]) + (f"<br/>+{len(ids) - 8} more" if len(ids) > 8 else "")
        lines.append(f'    {a} -->|"{label}"| {b}')
    return "\n".join(lines)


def render(entries: dict[str, Entry], reasons: dict[str, Entry]) -> str:
    exp = _expansions()
    prov = provenance(sorted(entries) + sorted(reasons))
    static = [e for e in entries.values() if not e.dynamic]
    dynamic = [e for e in entries.values() if e.dynamic]
    n_emit = sum(1 for e in entries.values() if e.by_role("emit"))
    out: list[str] = []
    w = out.append
    w("# Rule ids and rejected reasons")
    w("")
    w("Generated by `scripts/rules_catalogue.py` from the source under `src/edgar_itemize/`; do not")
    w("edit by hand. `tests/test_rules_catalogue.py` fails when this file is stale, so regenerate it")
    w("with `uv run python scripts/rules_catalogue.py` after adding, retiring or moving a rule id.")
    w("")
    w("`rule_ids` is the provenance column of the node table (`docs/OUTPUT_CONTRACT.md` section")
    w("8.2): every rule that proposed, scored, placed, re-parented or tagged a node, in the order")
    w("they fired. It is an open vocabulary. `rejected.reason` (section 7.6) is the closed")
    w("vocabulary of the rejected table. For where the stages sit, see `docs/ARCHITECTURE.md`.")
    w("")
    w("**Emitted in** is where the id is appended, returned or assigned into a rule list;")
    w("**read in** is where a later stage tests for it. An id with no emitter is read by the")
    w("evaluation code for a rule that no longer fires; an id with no reader is a description")
    w("only, written for the tables and the viewer. The classification is syntactic")
    w("(the script's docstring says how); the source line is one `grep` away.")
    w("")
    w(f"{len(static)} literal ids, {len(dynamic)} dynamic patterns, {len(reasons)} rejected reasons; "
      f"{n_emit} ids have an emitter.")
    w("")
    w("## Families")
    w("")
    w("| family | meaning | ids |")
    w("|---|---|---:|")
    counts = defaultdict(int)
    for e in entries.values():
        counts[e.family] += 1
    for fam, meaning in FAMILIES.items():
        w(f"| `{fam}` | {meaning} | {counts.get(fam, 0)} |")
    w("")
    w("## Which stage reads what another wrote")
    w("")
    w("Every edge is a rule id one stage writes onto a candidate or node and a later stage tests")
    w("for. Ids read only by the stage that wrote them are not drawn. Stages run left to right")
    w("(`grammar` is called from `candidates`; `sequence` from `tree_contract`).")
    w("")
    w("```mermaid")
    w(_flow_mermaid(entries))
    w("```")
    w("")
    w("## Rule ids")
    w("")
    for fam in FAMILIES:
        fam_entries = sorted((e for e in entries.values() if e.family == fam), key=lambda e: e.id)
        if not fam_entries:
            continue
        w(f"### `{fam}.*`")
        w("")
        w(f"{FAMILIES[fam][0].upper() + FAMILIES[fam][1:]}.")
        w("")
        w("| id | emitted in | read in | first named in docs | documented in |")
        w("|---|---|---|---|---|")
        for e in fam_entries:
            rid = f"`{e.id}`" + (" (pattern)" if e.dynamic else "")
            mention = [x for x in e.by_role("mention") if x not in e.by_role("emit") and x not in e.by_role("read")]
            read = e.by_role("read") + ([f"({m})" for m in mention] if mention else [])
            p = prov.get(e.id, {})
            w(f"| {rid} | {_md_list(e.by_role('emit'))} | {_md_list(read)} | {p.get('since', '')} | {_docs_md(p.get('docs', []))} |")
            if e.dynamic and e.id in exp:
                w(f"| | values: {', '.join(f'`{x}`' for x in exp[e.id])} | | | |")
        w("")
    w("## Rejected reasons")
    w("")
    w("The closed vocabulary of `rejected.reason`, collected from every `rej(c, \"...\")` and")
    w("`Rejected(...)` call with a literal reason. `docs/OUTPUT_CONTRACT.md` section 7.6 gives")
    w("the meaning and the baseline counts; a reason passed through a variable (the strong-")
    w("duplicate pass's `forced_reason`) is listed where the literal is.")
    w("")
    w("| reason | written in | first named in docs | documented in |")
    w("|---|---|---|---|")
    for e in sorted(reasons.values(), key=lambda e: e.id):
        p = prov.get(e.id, {})
        w(f"| `{e.id}` | {_md_list(e.by_role('emit'))} | {p.get('since', '')} | {_docs_md(p.get('docs', []))} |")
    w("")
    return "\n".join(out)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/RULES.md differs from the rendering")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    text = render(*scan())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{_rel(args.out)} is stale; run: uv run python scripts/rules_catalogue.py", file=sys.stderr)
            return 1
        print(f"{_rel(args.out)} is current")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {_rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
