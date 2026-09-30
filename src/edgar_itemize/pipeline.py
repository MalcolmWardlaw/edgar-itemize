"""Parse one document end to end."""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import NORMALIZER_VERSION, PARSER_VERSION
from . import ars_kind
from .agenda import assign_paths, level_profile, path_str, regab_back_matter
from .blocks import Block
from .candidates import (caption_guard_pass, ctoc_row_pass, fm_table_pass, page_repeat_pass, section_xref_pass,
                         vetoed_index_row_pass, Candidate, find_candidates)
from .classify import Profile, classify, ibr_flags
from .grammar.contract import ContractGrammar
from .grammar.form10k import Form10KGrammar
from .grammar.form10q import Form10QGrammar
from .tree_contract import build_contract_tree
from .prepare import Prepared, normalize, prepare_document  # noqa: F401  (normalize re-exported)
from .sgml import DocumentBlock, Submission
from .headings import find_headings, merge_and_renumber
from .toc import TocRegion, detect_toc
from .tree import _VETOED_INDEX_MIN_ITEMS, Node, Rejected, build_tree, truncate_at_back_start


@dataclass(slots=True)
class ParseResult:
    accession: str
    cik: str
    doc: DocumentBlock
    profile: Profile
    blocks: list[Block]
    normalized_text: str
    candidates: list[Candidate]
    toc: list[TocRegion]
    nodes: list[Node]
    rejected: list[Rejected]
    grammar: str
    paths: dict = None  # node_id -> ordinal path; "_bounds" -> front/back offsets
    profile_levels: list = None
    ibr: dict = None  # node_id -> A2 item-level IBR/xref flags (item nodes with tiny spans only)
    input_sha256: str | None = None  # sha256 of the raw submission file (documents row only)
    input_bytes: int | None = None
    # D9 (docs/RELEASE_PLAN.md section 9): `cik` above is the header's first FILER CIK when
    # the file has one, else the manifest's; `manifest_cik` is always the manifest's (the
    # CIK directory the file was read from), written to `documents` only.
    manifest_cik: str | None = None


def grammar_for(doc: DocumentBlock, submission_type: str = ""):
    """Pick the grammar for one document.

    gram.form_from_header (Turn 4): the per-document <TYPE> tag is filer-supplied and
    is sometimes simply wrong -- 14 of the 228,803 documents in runs/full_v10 are
    10-K submissions whose primary document is tagged <TYPE>10-Q (0001031002-02-000026,
    0000943551-99-000001, 0000946489-99-000004, ...), and every item in them came out
    labelled "ITEM I.1" under the 10-Q grammar. EDGAR's own CONFORMED SUBMISSION TYPE
    settles the annual/quarterly question when the two disagree; the exhibit routing
    still follows the document tag, because a submission holds documents of many types.
    """
    t = (doc.type or "").upper()
    if t.startswith("EX-"):
        return ContractGrammar()
    quarterly = t.replace("-", "").startswith("10Q")  # 10-Q, 10-Q/A, 10QSB, 10-QSB (G1 routing)
    st = (submission_type or "").upper().replace("-", "")
    if st.startswith("10K") and quarterly:
        return Form10KGrammar()
    if quarterly:
        return Form10QGrammar()
    return Form10KGrammar()


def parse_document(sub: Submission, doc: DocumentBlock, cik: str | int, grammar=None, *, headings: bool = True, synth_root: bool = False) -> ParseResult:
    """synth_root: EX-13 (annual report) parsing only (cli.py's ``ex13`` kind forces this
    True alongside Form10KGrammar()). It does two things, both no-ops for every other
    caller so the 10-K/EX-10/10-Q paths are unchanged:

    - when the 10-K grammar's build_tree finds zero item nodes (always true for an
      annual report — there is no "ITEM N" text), synthesize an in-memory anchor item
      node spanning the whole document purely so `find_headings` has something to nest
      under; it is never added to `nodes` / written to the node table.
    - classify each resulting heading's title into the ars kind->item mapping
      (`ars_kind.classify`), setting `satisfies_item` and a `kind.*` rule id.
    """
    return parse_prepared(prepare_document(sub, doc, cik), cik, grammar, headings=headings, synth_root=synth_root)


def parse_prepared(prep: Prepared, cik: str | int, grammar=None, *, headings: bool = True, synth_root: bool = False) -> ParseResult:
    """Everything after normalisation (candidates onward). ``prep`` comes from
    ``prepare.prepare_document`` or, identically, from the normalisation cache."""
    doc, profile, blocks, norm = prep.doc, prep.profile, prep.blocks, prep.normalized_text
    from_header = prep.header_type if grammar is None else ""
    grammar = grammar or grammar_for(doc, from_header)
    routed_by_header = bool(from_header and (doc.type or "").upper().replace("-", "").startswith("10Q")
                            and grammar.name == "form10k")
    cands = find_candidates(blocks, grammar, era=profile.era)
    if grammar.name == "form10k":
        # Turn 9 B.2: rej.caption_omission / rej.caption_ibr / rej.caption_xref, the
        # cover-page caption guard.  Tags only -- no score moves, and only
        # tree.out_of_order_items reads them.  Scoped to the 10-K grammar because that
        # is the corpus the guard was measured and gated on (runs/diff_v19_v20.json);
        # the 10-Q full run is Turn 9 B.4's baseline and gets its own gate.
        caption_guard_pass(cands, norm)
        # Turn 10 B.2 (docs/turn10_decisions/a3_fmtable.md): rej.fm_table, the rows of a
        # two-column front-matter table -- the families the caption's line walk cannot
        # reach.  Same terms as the caption guard (tag only, no score, read only by
        # tree.out_of_order_items) and the same 10-K scope.  It runs BEFORE detect_toc,
        # which consults none of its ids, so every TOC region stays bit-identical.
        fm_table_pass(cands, blocks)
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar=grammar.name)
    toc_idx = {i for t in toc for i in range(t.first_cand, t.last_cand + 1)}
    page_repeat_pass(cands, blocks, toc_idx)
    if grammar.name == "contract":
        # Turn 11 B.1/B.2 (docs/turn11_decisions/a1_ctoc_boundary.md): rej.ctoc_row and
        # ctoc.region_relief, the two sides of the contract table-of-contents boundary.
        # Tags only, contract grammar only, and it runs AFTER detect_toc because B.1 fires
        # only where `_contract_regions` proposed nothing and B.2 only inside a region --
        # it consults the regions and changes none of them, so every TOC region, and the
        # 10-K / 10-Q / EX-13 paths, stay bit-identical.
        ctoc_row_pass(cands, blocks, toc, norm, era=profile.era)
        # Turn 13 B.4 (docs/turn13_decisions/b4_section_xref.md): rej.section_xref, a
        # definitional `Section n.n` cross-reference at the head of a line.  Tag only,
        # contract grammar only; read by build_contract_tree before chaining.
        section_xref_pass(cands, blocks, norm)
        nodes, rejected = build_contract_tree(blocks, cands, toc, grammar, doc_raw_start=doc.text_start, doc_raw_end=doc.text_end, norm_len=len(norm))
    else:
        if grammar.name in ("form10k", "form10q") and not synth_root:
            # Turn 12 B.1 (docs/turn12_decisions/a1_index_anchor.md section 9.1):
            # rej.vetoed_index_row.  Tags only, heading grammars only, and it runs AFTER
            # detect_toc and page_repeat_pass because its whole input is the
            # `toc.vetoed_region` tags detect_toc's veto wrote -- it consults the regions
            # and changes none of them, so every TOC region, and the contract path, stay
            # bit-identical.  Read in build_tree below, before the `toc_idx` branch.
            #
            # `not synth_root` keeps EX-13 out.  EX-13 is parsed with Form10KGrammar (see
            # this function's docstring), so the grammar name alone would reach it, but
            # the rule was measured and banked on the 10-K and 10-Q corpora only and the
            # EX-13 documents that reproduce 10-K item headings (audit class E1) are B.5's
            # population, not this rule's.  A.1's gate requires EX-13 bit-identical; this
            # is what makes it identical by construction rather than by luck.
            vetoed_index_row_pass(cands, min_items=_VETOED_INDEX_MIN_ITEMS)
        regab = grammar.name == "form10k" and not synth_root and getattr(grammar, "is_regab_item", None) is not None
        # build_tree tags the candidates it places (rule_ids) and may rewrite labels; the
        # rej.regab_back_matter rebuild below starts from the candidates as they were
        snap = [(list(c.rule_ids), c.label_canon, c.order_key) for c in cands] if regab else None
        nodes, rejected = build_tree(blocks, cands, toc, grammar, doc_raw_start=doc.text_start, doc_raw_end=doc.text_end, norm_len=len(norm))
        if regab:
            # Turn 13 B.1b: rej.regab_back_matter (agenda.regab_back_matter). 10-K only and
            # not EX-13 (`synth_root`), which holds no Reg-AB node; rebuilt only in a
            # document where an untitled Reg-AB item was accepted past the ordinary
            # back-matter boundary, so every other tree is the one just built.
            veto = regab_back_matter(blocks, cands, nodes, grammar, doc_start=doc.text_start, doc_end=doc.text_end)
            if veto:
                for c, (rids, lab, ok) in zip(cands, snap):
                    c.rule_ids, c.label_canon, c.order_key = list(rids), lab, ok
                for i in veto:
                    cands[i].rule_ids.append("rej.regab_back_matter")
                nodes, rejected = build_tree(blocks, cands, toc, grammar, doc_raw_start=doc.text_start, doc_raw_end=doc.text_end, norm_len=len(norm))
        if headings:
            anchor_nodes = nodes
            if synth_root and grammar.name == "form10k" and not any(n.level_kind == "item" for n in nodes):
                root = next(n for n in nodes if n.level_kind == "document")
                synth = Node(0, root.node_id, 2, "item", None, None, None,
                             doc.text_start, doc.text_end, doc.text_start, doc.text_start,
                             0, len(norm), 0, 0.0, ["gram.synth_doc_root"])
                anchor_nodes = nodes + [synth]
            # rej.page_banner (Turn 12 B.3) drops are appended to `rejected` from inside
            # find_headings, so the rejected table carries them with their reason.
            nodes = merge_and_renumber(nodes, find_headings(blocks, anchor_nodes, profile.era, rejected=rejected))
            if synth_root:
                # Turn 8 (a4_ex13.md): a heading block sitting inside a detected TOC
                # region gets no satisfies_item, on top of ars_kind.classify's own
                # dot-leader/page-number title guard -- a heading's block index isn't
                # visible inside ars_kind (title-only), so that half of the guard lives
                # here, against `toc`'s block ranges.
                toc_block_ranges = [(t.block_start, t.block_end) for t in toc]
                block_idx_by_raw_start = {b.raw_start: b.idx for b in blocks}
                for n in nodes:
                    if n.level_kind != "heading":
                        continue
                    b_idx = block_idx_by_raw_start.get(n.raw_start)
                    in_toc = ars_kind.block_in_toc_region(b_idx, toc_block_ranges)
                    hit = None if in_toc else ars_kind.classify(n.title)
                    if hit is not None:
                        kind, item = hit
                        n.satisfies_item = item
                        n.rule_ids.append(ars_kind.KIND_RULE[kind])
    if profile.era == "image_text":
        # structure recovered from a hidden OCR layer: no style evidence, OCR noise (A6)
        for n in nodes:
            if n.level_kind != "document":
                n.rule_ids.append("ocr.degraded"); n.confidence = min(n.confidence, 0.6)
    if routed_by_header:
        nodes[0].rule_ids.append("gram.form_from_header")
    paths = assign_paths(nodes, blocks, doc_start=doc.text_start, doc_end=doc.text_end, ex13=synth_root)
    _b = paths["_bounds"]
    # tree.back_truncate: main-body spans stop at the back-matter boundary, so the last
    # item no longer runs over the signature page, exhibit index and financial appendix.
    # Runs before ibr_flags so the A2 stub test sees the item's own span.
    # tree.back_truncate_descend (Turn 12 B.0, docs/turn12_decisions/a3_subheadings.md
    # section (a)): the pass above can change a heading's parent_id/depth (re-parenting a
    # back-matter heading detached from its truncated ancestor to the document root), so
    # `paths` -- computed just above from the PRE-truncation tree -- is stale wherever that
    # happened and assign_paths must run again. The second call is guaranteed to reproduce
    # the same bounds: `_back_matter_boundary` reads only part/item/article/section nodes'
    # raw_start/head_raw_start and `compound_restarts` reads only article nodes' rule ids --
    # neither depends on parent_id or depth -- asserted below rather than trusted.
    n_truncate_changed = truncate_at_back_start(nodes, _b["back_start"], _b.get("back_norm_start", -1))
    if n_truncate_changed:
        paths2 = assign_paths(nodes, blocks, doc_start=doc.text_start, doc_end=doc.text_end, ex13=synth_root)
        _b2 = paths2["_bounds"]
        assert _b2["back_start"] == _b["back_start"] and _b2.get("back_norm_start", -1) == _b.get("back_norm_start", -1), \
            "tree.back_truncate_descend moved the back-matter boundary -- it must not"
        paths = paths2
    ibr: dict[int, dict] = {}
    if grammar.name != "contract":
        has_ex13 = any((t or "").upper().startswith("EX-13") for t in prep.doc_types)
        for n in nodes:
            if n.level_kind != "item":
                continue
            f = ibr_flags(norm[n.norm_start : n.norm_end])
            if f is not None:
                f["ibr_target_in_submission"] = has_ex13 if f["item_incorporated_by_reference"] else None
                ibr[n.node_id] = f
    # D9: the stamped cik is the file's own (header first FILER) when present, else the
    # manifest's (`--kind text` input has no header). `agent_cik` (profile) is untouched.
    return ParseResult(prep.accession, prep.header_cik or str(cik), doc, profile, blocks, norm, cands, toc, nodes, rejected, grammar.name, paths,
                       level_profile(nodes, paths), ibr, prep.input_sha256, prep.input_bytes, str(cik))


def covers_items_of(rule_ids: list[str] | None) -> list[str] | None:
    """Turn 7 (c): the additional item keys a multi-item heading ("Items 1 and 2")
    covers, read off the node's own `multi.ITEM <k>` rule ids (candidates.py's
    `_try_match`). None when the node carries no such tag (the common case)."""
    covers = [rid.split(" ", 1)[1] for rid in (rule_ids or []) if rid.startswith("multi.ITEM ")]
    return covers or None


def result_rows(r: ParseResult, filed_year: int | None, *, keep_text: bool = True) -> tuple[list[dict], dict, list[dict]]:
    seq = r.doc.sequence or 0
    common = dict(accession_number=r.accession, cik=r.cik, sequence=seq, doc_type=r.doc.type, grammar=r.grammar,
                  profile_era=r.profile.era, profile_publisher=r.profile.publisher, agent_cik=r.profile.agent_cik,
                  parser_version=PARSER_VERSION, normalizer_version=NORMALIZER_VERSION)
    ibr_all = r.ibr or {}
    seg_info = (r.paths or {}).get("_segments") or {}
    segs = seg_info.get("of_node", {})
    nodes = [
        dict(common, node_id=n.node_id, parent_id=n.parent_id, depth=n.depth, level_kind=n.level_kind, label_canon=n.label_canon,
             label_raw=n.label_raw, title=n.title, raw_start=n.raw_start, raw_end=n.raw_end, head_raw_start=n.head_raw_start,
             head_raw_end=n.head_raw_end, norm_start=n.norm_start, norm_end=n.norm_end, order_key=n.order_key,
             confidence=n.confidence, rule_ids=n.rule_ids, path=r.paths.get(n.node_id), path_str=path_str(r.paths.get(n.node_id, [])),
             meta=(r.paths.get(n.node_id) or [2])[0], satisfies_item=n.satisfies_item,
             segment=segs.get(n.node_id, 0),
             item_incorporated_by_reference=(ibr_all.get(n.node_id) or {}).get("item_incorporated_by_reference"),
             ibr_target=(ibr_all.get(n.node_id) or {}).get("ibr_target"),
             ibr_target_in_submission=(ibr_all.get(n.node_id) or {}).get("ibr_target_in_submission"),
             item_cross_reference=(ibr_all.get(n.node_id) or {}).get("item_cross_reference"),
             covers_items=covers_items_of(n.rule_ids))
        for n in r.nodes
    ]
    # items_found credits items covered by combined headings ("Items 1 and 2. Business
    # and Properties"): the accepted node carries `multi.ITEM <k>` rule ids for the
    # covered keys (cases A4/D3 — previously the second item was never credited).
    #
    # Turn 12 B.4 (A12, docs/turn12_decisions/a4_label_family.md section 6.4): on the
    # 10-Q the bare key was never Part-qualified ("ITEM 2" instead of "ITEM I.2" or
    # "ITEM II.2"), so 6,792 of 8,001 multi-credits in 2,580 documents never matched any
    # entry in `expected_items()`'s Part-qualified vocabulary and were invisible to every
    # completeness/tier report (`turn12-a4-multipart.parquet`). The carrying node `n` IS
    # the accepted item the credit rides on, and on `form10q` its own `label_canon` is
    # already Part-qualified ("ITEM I.2"/"ITEM II.4") by the time it reaches this loop --
    # no parent lookup is needed, just splitting the carrying node's own canon.
    items_found: list[str] = []
    for n in r.nodes:
        if n.level_kind != "item":
            continue
        items_found.append(n.label_canon)
        multi_keys = [rid.split(" ", 1)[1] for rid in n.rule_ids if rid.startswith("multi.ITEM ")]
        if r.grammar == "form10q" and " " in n.label_canon and "." in n.label_canon.split(" ", 1)[1]:
            part = n.label_canon.split(" ", 1)[1].split(".", 1)[0]
            items_found += [f"ITEM {part}.{k}" for k in multi_keys]
        else:
            items_found += [f"ITEM {k}" for k in multi_keys]
    seen: set[str] = set()
    items_found = [x for x in items_found if not (x in seen or seen.add(x))]
    doc = dict(common, filed_year=filed_year, signals=json.dumps(r.profile.signals, sort_keys=True), n_blocks=len(r.blocks),
               n_candidates=len(r.candidates), n_nodes=len(r.nodes), toc_found=bool(r.toc),
               items_found=items_found,
               doc_raw_start=r.doc.text_start, doc_raw_end=r.doc.text_end,
               front_end=r.paths["_bounds"]["front_end"], back_start=r.paths["_bounds"]["back_start"],
               n_segments=len(seg_info.get("starts") or [0]),
               level_profile=json.dumps(r.profile_levels),
               normalized_text=r.normalized_text if keep_text else None, error=None,
               input_sha256=getattr(r, "input_sha256", None), input_bytes=getattr(r, "input_bytes", None),
               manifest_cik=getattr(r, "manifest_cik", None))
    rej = [dict(accession_number=r.accession, sequence=seq, block_idx=x.block_idx, kind=x.kind, label_canon=x.label_canon,
                score=x.score, reason=x.reason, raw_start=x.raw_start, text=x.text) for x in r.rejected]
    return nodes, doc, rej
