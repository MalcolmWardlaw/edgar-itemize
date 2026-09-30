"""edgar-itemize command line."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import pyarrow.parquet as pq

from . import control, fetch, manifest
from .grammar.form10k import Form10KGrammar
from . import __version__
from .normcache import MODES as NORM_CACHE_MODES, NormCache
from .pipeline import parse_document, parse_prepared, result_rows
from .prepare import load_row, prepare_document, source_path
from .select import select_credit_agreements, select_primary
from .sgml import load_submission, load_text_submission
from . import control
from .writer import write_run
from .conformance import register_cli as _register_conformance


def _show(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.exists():
        # treat as accession: find via manifest
        m = pq.read_table(args.manifest).to_pylist()
        row = next((r for r in m if r["accession_number"] == args.path), None)
        if row is None:
            sys.exit(f"accession {args.path} not in {args.manifest}")
        path = control.rewrite_path(row["archive_path"]); cik = row["cik"]; stype = row.get("submission_type", "10-K")
    else:
        cik = args.cik or "0"; stype = args.type
    sub = load_submission(path)
    if args.sequence:
        doc = next(d for d in sub.documents if d.sequence == args.sequence)
    else:
        doc = select_primary(sub.documents, stype)
    r = parse_document(sub, doc, cik)
    print(f"# {sub.accession} seq={doc.sequence} type={doc.type} era={r.profile.era} publisher={r.profile.publisher} blocks={len(r.blocks)} cands={len(r.candidates)} toc={[(t.block_start,t.block_end,t.reason) for t in r.toc]}")
    print(f"# input sha256={sub.input_sha256} bytes={sub.input_bytes}")
    if args.candidates:
        print("## candidates")
        for c in r.candidates:
            b = r.blocks[c.block_idx]
            print(f"  blk{c.block_idx:5d} raw={c.head_raw_start:8d} {c.kind:5s} {c.label_canon:9s} s={c.score:+.2f} {','.join(c.rule_ids)} | {b.text[:80]!r}")
    print("## tree")
    for n in r.nodes:
        print(f"  {'  '*n.depth}{n.level_kind:8s} {n.label_canon or '':9s} [{n.raw_start},{n.raw_end}) conf={n.confidence:.2f} {n.title or ''!s:.70s}  <{','.join(n.rule_ids)}>")
    if args.rejected:
        print("## rejected")
        for x in r.rejected:
            print(f"  blk{x.block_idx:5d} raw={x.raw_start:8d} {x.kind:5s} {x.label_canon:9s} s={x.score:+.2f} {x.reason:12s} | {x.text[:80]!r}")


def _parse_one(row: dict, kind: str, keep_text: bool) -> tuple[list[dict], dict, list[dict]]:
    n, d, rj, _status = _parse_one_cached(row, kind, keep_text, None)
    return n, d, rj


def _parse_one_cached(row: dict, kind: str, keep_text: bool, cache: NormCache | None) -> tuple[list[dict], dict, list[dict], str]:
    """One manifest row. ``cache`` (normcache.NormCache or None) may supply or record the
    load/select/classify/normalise prefix; the returned status is off | hit | miss | write |
    write_failed and never enters the node, document or rejected rows."""
    status = "off"
    try:
        hit, prep = False, None
        if cache is not None and cache.mode != "off":
            src = source_path(row, kind)
            status = "miss"
            if cache.reads:
                hit, prep = cache.get(row, kind, src)
                if hit:
                    status = "hit"
        if not hit:
            sub, doc = load_row(row, kind)
            prep = prepare_document(sub, doc, row["cik"]) if doc is not None else None
            if cache is not None and cache.writes:
                status = "write" if cache.put(row, kind, src, prep) else "write_failed"
        if prep is None:
            return [], dict(accession_number=row["accession_number"], cik=str(row["cik"]), manifest_cik=str(row["cik"]), sequence=None, filed_year=row.get("year"), error="no_primary_document"), [], status
        if kind == "ex13":
            # EX-13 (annual report) is parsed with the 10-K grammar and a document-root
            # heading pass, since it never carries "ITEM N" text (Turn 7 b spec).
            r = parse_prepared(prep, row["cik"], grammar=Form10KGrammar(), synth_root=True)
        else:
            r = parse_prepared(prep, row["cik"])
        return (*result_rows(r, row.get("year"), keep_text=keep_text), status)
    except Exception as e:  # noqa: BLE001
        return [], dict(accession_number=row["accession_number"], cik=str(row["cik"]), manifest_cik=str(row["cik"]), sequence=row.get("sequence"),
                        error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}"), [], status


def _run_rows(rows: list[dict], args: argparse.Namespace, label: str, status: list | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    nodes_all, docs_all, rej_all = [], [], []
    keep_text = not args.no_text
    cache = _norm_cache(args)
    if args.workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        from .conformance import _pool_context  # explicit start method, the same on 3.11-3.14

        with ProcessPoolExecutor(max_workers=args.workers, mp_context=_pool_context()) as ex:
            it = ex.map(_parse_one_cached, rows, [args.kind] * len(rows), [keep_text] * len(rows), [cache] * len(rows), chunksize=4)
            for i, (n, d, rj, st) in enumerate(it):
                nodes_all += n; docs_all.append(d); rej_all += rj
                if status is not None: status.append(st)
                if args.progress and (i + 1) % 500 == 0:
                    print(f"{label}: {i+1}/{len(rows)}", file=sys.stderr, flush=True)
    else:
        for i, row in enumerate(rows):
            n, d, rj, st = _parse_one_cached(row, args.kind, keep_text, cache)
            nodes_all += n; docs_all.append(d); rej_all += rj
            if status is not None: status.append(st)
            if args.progress and (i + 1) % 25 == 0:
                print(f"{label}: {i+1}/{len(rows)}", file=sys.stderr, flush=True)
    return nodes_all, docs_all, rej_all


def _norm_cache(args: argparse.Namespace) -> NormCache | None:
    mode = getattr(args, "norm_cache_mode", "off") or "off"
    if mode == "off":
        return None
    if not getattr(args, "norm_cache", None):
        sys.exit("--norm-cache-mode other than off needs --norm-cache DIR")
    return NormCache(str(Path(args.norm_cache).resolve()), mode)


def _write_cache_sidecar(out: Path, part: str, rows: list[dict], status: list[str]) -> None:
    """normcache-<part>.parquet: per row, whether the normalisation cache supplied it. A
    sidecar, so the node/document/rejected tables stay identical with and without the cache
    (run_diff and the viewer glob only nodes-/documents-/rejected-)."""
    import pyarrow as pa
    from collections import Counter

    from .normcache import prefix_digest

    t = pa.table(dict(accession_number=[r["accession_number"] for r in rows],
                      sequence=pa.array([r.get("sequence") for r in rows], pa.int32()),
                      archive_path=[str(r["archive_path"]) for r in rows],
                      norm_cache=status))
    t = t.replace_schema_metadata({"digest": prefix_digest()})
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out / f"normcache-{part}.parquet", compression="zstd")
    print(f"  normcache {part}: {dict(sorted(Counter(status).items()))} digest {prefix_digest()}", file=sys.stderr, flush=True)


def _parse(args: argparse.Namespace) -> None:
    rows = pq.read_table(args.manifest).to_pylist()
    if args.limit:
        rows = rows[: args.limit]
    out = Path(args.out)
    if args.partition_by:
        from collections import defaultdict

        groups: dict = defaultdict(list)
        for r in rows:
            groups[r[args.partition_by]].append(r)
        for key in sorted(groups):
            part = f"{args.kind}-{key}"
            if (out / f"documents-{part}.parquet").exists() and not args.overwrite:
                print(f"skip {part} (exists)", file=sys.stderr)
                continue
            st: list[str] = []
            n, d, rj = _run_rows(groups[key], args, str(key), st)
            write_run(out, n, d, rj, part=part)
            if _norm_cache(args) is not None:
                _write_cache_sidecar(out, part, groups[key], st)
            errs = sum(1 for x in d if x.get("error"))
            print(f"{part}: {len(d)} docs ({errs} errors), {len(n)} nodes", flush=True)
        return
    st: list[str] = []
    n, d, rj = _run_rows(rows, args, "all", st)
    write_run(out, n, d, rj, part=args.kind)
    if _norm_cache(args) is not None:
        _write_cache_sidecar(out, args.kind, rows, st)
    errs = sum(1 for x in d if x.get("error"))
    print(f"wrote {len(d)} docs ({errs} errors), {len(n)} nodes, {len(rj)} rejected -> {args.out}")


def _report(args: argparse.Namespace) -> None:
    from .eval.metrics import print_summary, run_metrics, summarize

    rows, _ = run_metrics(Path(args.run), args.kind)
    by = tuple(args.by.split(","))
    print_summary(summarize(rows, by), by)
    if args.worst:
        bad = [r for r in rows if not r["core_complete"] or not r["size_sanity"] or r["error"]]
        print(f"\n## {len(bad)} docs failing core_complete/size_sanity/error")
        for r in bad[: args.worst]:
            print(f"  {r['accession_number']} y={r['filed_year']} {r['profile_era']}/{r['profile_publisher']} n_items={r['n_items']} missing_core={r['missing_core']} tiny={r['tiny']} huge={r['huge']} i7={r['item7_share']} i8={r['item8_share']} err={(r['error'] or '')[:60]!r}")


def _gold_init(args: argparse.Namespace) -> None:
    from .eval.gold import skeleton

    rows = pq.read_table(args.manifest).to_pylist()
    if args.accessions:
        want = set(args.accessions.split(","))
        rows = [r for r in rows if r["accession_number"] in want]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    for row in rows[: args.limit or None]:
        sub = load_submission(control.rewrite_path(row["archive_path"]))
        doc = next(d for d in sub.documents if d.sequence == row["sequence"]) if args.kind == "ex10" else select_primary(sub.documents, row.get("submission_type", "10-K"))
        r = parse_document(sub, doc, row["cik"])
        n, _, _ = result_rows(r, row.get("year"), keep_text=False)
        p = out / f"{row['accession_number']}_{doc.sequence}.yaml"
        if p.exists() and not args.force:
            continue
        p.write_text(skeleton(row["accession_number"], doc.sequence or 0, doc.type or "", n))
        print("wrote", p)


def _eval(args: argparse.Namespace) -> None:
    from collections import defaultdict

    from .eval.gold import load_gold, score

    gold = load_gold(Path(args.gold))
    nodes = pq.read_table(Path(args.run) / f"nodes-{args.kind}.parquet").to_pylist()
    by = defaultdict(list)
    for n in nodes:
        by[(n["accession_number"], n["sequence"])].append(n)
    tot = dict(tp=0, fp=0, fn=0); n_docs = 0
    for key, g in gold.items():
        if not g.get("reviewed", True):
            continue
        sc = score(g, by.get(key, []), tol=args.tol)
        n_docs += 1
        for k in tot: tot[k] += sc[k]
        if args.verbose or sc["fp"] or sc["fn"]:
            print(f"  {key[0]} seq={key[1]} P={sc['precision']:.2f} R={sc['recall']:.2f} tp={sc['tp']} fp={sc['fp']} fn={sc['fn']}")
    p = tot["tp"] / max(1, tot["tp"] + tot["fp"]); r = tot["tp"] / max(1, tot["tp"] + tot["fn"])
    print(f"{n_docs} reviewed gold docs: precision={p:.3f} recall={r:.3f} (tp={tot['tp']} fp={tot['fp']} fn={tot['fn']})")


def _judge(args: argparse.Namespace) -> None:
    """Ask the local LLM about rejected/near-threshold candidates for one filing."""
    import json as _json

    try:
        from .llm import HEADING_SCHEMA, HEADING_SYSTEM, chat_json, heading_prompt
    except ImportError as e:
        sys.exit(f"{e}\nthe judge needs the optional dependencies: uv sync --extra judge  (or pip install 'edgar-itemize[judge]')")

    row = next((r for r in pq.read_table(args.manifest).to_pylist() if r["accession_number"] == args.accession), None)
    if row is None:
        sys.exit(f"{args.accession} not in {args.manifest}")
    sub = load_submission(control.rewrite_path(row["archive_path"]))
    doc = next(d for d in sub.documents if d.sequence == row["sequence"]) if args.kind == "ex10" else select_primary(sub.documents, row.get("submission_type", "10-K"))
    r = parse_document(sub, doc, row["cik"])
    accepted = {n.head_raw_start for n in r.nodes if n.level_kind in ("item", "part")}
    targets = [x for x in r.rejected if x.reason != "toc" and x.kind in ("item", "part")]
    if args.all_candidates:
        targets = [type("R", (), dict(block_idx=c.block_idx, kind=c.kind, label_canon=c.label_canon, score=c.score, reason="accepted" if r.blocks[c.block_idx].raw_start in accepted else "rejected", raw_start=r.blocks[c.block_idx].raw_start, text=r.blocks[c.block_idx].text[:120]))() for c in r.candidates]
    out = []
    for x in targets[: args.limit or None]:
        b = r.blocks[x.block_idx]
        lo, hi = max(0, b.norm_start - args.window), min(len(r.normalized_text), b.norm_end + args.window)
        window = r.normalized_text[lo:b.norm_start] + "\n>>> " + r.normalized_text[b.norm_start:b.norm_end] + "\n" + r.normalized_text[b.norm_end:hi]
        v = chat_json(heading_prompt(x.label_canon, window), system=HEADING_SYSTEM, model=args.model or __import__("edgar_itemize.llm", fromlist=["DEFAULT_MODEL"]).DEFAULT_MODEL, schema=HEADING_SCHEMA)
        rec = dict(accession=r.accession, sequence=doc.sequence, label=x.label_canon, raw_start=x.raw_start, parser=x.reason, parser_score=x.score,
                   model=v.model, prompt_sha=v.prompt_sha, **{k: v.answer.get(k) for k in ("is_heading", "kind", "reason")})
        out.append(rec)
        print(f"{x.label_canon:9s} parser={x.reason:12s} s={x.score:+.2f} | llm={'HEADING' if rec['is_heading'] else 'no':7s} {rec['kind']} — {(rec['reason'] or '')[:100]}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "a") as f:
            for rec in out:
                f.write(_json.dumps(rec) + "\n")


def _serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
        import edgar_itemize.viewer.app  # noqa: F401  (fails early with a clear message)
    except ImportError as e:
        sys.exit(f"{e}\nthe viewer needs the optional dependencies: uv sync --extra viewer  (or pip install 'edgar-itemize[viewer]')")

    uvicorn.run("edgar_itemize.viewer.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="edgar-itemize")
    ap.add_argument("--version", action="version", version=f"edgar-itemize {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("show", help="parse one filing and print candidates/tree")
    s.add_argument("path", help="submission .txt path or accession number (looked up in --manifest)")
    s.add_argument("--manifest", default="gold/sample_manifest_10k.parquet")
    s.add_argument("--cik", default=None)
    s.add_argument("--type", default="10-K")
    s.add_argument("--sequence", type=int, default=None)
    s.add_argument("-c", "--candidates", action="store_true")
    s.add_argument("-r", "--rejected", action="store_true")
    s.set_defaults(fn=_show)
    p = sp.add_parser("parse", help="parse every document in a manifest")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--kind", choices=["10k", "ex10", "ex13", "text"], default="10k")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-text", action="store_true")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--partition-by", default="", help="manifest column to partition output by (e.g. year); resumable")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--norm-cache", default=None, metavar="DIR",
                   help="normalisation cache root directory; see normcache.py")
    p.add_argument("--norm-cache-mode", choices=list(NORM_CACHE_MODES), default="off",
                   help="off (default: no cache, behaviour unchanged) | read | write | readwrite")
    p.set_defaults(fn=_parse)
    rp = sp.add_parser("report", help="sanity metrics for a run")
    rp.add_argument("run")
    rp.add_argument("--kind", default="10k")
    rp.add_argument("--by", default="profile_era")
    rp.add_argument("--worst", type=int, default=0)
    rp.set_defaults(fn=_report)
    gi = sp.add_parser("gold-init", help="write gold YAML skeletons from parser output for hand correction")
    gi.add_argument("--manifest", default="gold/sample_manifest_10k.parquet")
    gi.add_argument("--out", default="gold/labels")
    gi.add_argument("--kind", choices=["10k", "ex10"], default="10k")
    gi.add_argument("--accessions", default="")
    gi.add_argument("--limit", type=int, default=0)
    gi.add_argument("--force", action="store_true")
    gi.set_defaults(fn=_gold_init)
    ev = sp.add_parser("eval", help="score a run against reviewed gold YAML files")
    ev.add_argument("run")
    ev.add_argument("--gold", default="gold/labels")
    ev.add_argument("--kind", choices=["10k", "ex10"], default="10k")
    ev.add_argument("--tol", type=int, default=50)
    ev.add_argument("--verbose", action="store_true")
    ev.set_defaults(fn=_eval)
    jg = sp.add_parser("judge", help="ask the local LLM about heading candidates in one filing")
    jg.add_argument("accession")
    jg.add_argument("--manifest", default="runs/full_manifest_10k.parquet")
    jg.add_argument("--kind", choices=["10k", "ex10"], default="10k")
    jg.add_argument("--model", default=None)
    jg.add_argument("--window", type=int, default=900)
    jg.add_argument("--limit", type=int, default=0)
    jg.add_argument("--all-candidates", action="store_true")
    jg.add_argument("--out", default="")
    jg.set_defaults(fn=_judge)
    mf = sp.add_parser("manifest", help="build a parse manifest from the SEC full index (master.idx)")
    manifest.add_arguments(mf); mf.set_defaults(fn=manifest.main)
    ft = sp.add_parser("fetch", help="download the raw submission files a manifest names")
    fetch.add_arguments(ft); ft.set_defaults(fn=fetch.main)
    sv = sp.add_parser("serve", help="run the browser viewer")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--reload", action="store_true")
    sv.set_defaults(fn=_serve)
    _register_conformance(sp)  # verify, conformance draw|hashes (conformance.py)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
