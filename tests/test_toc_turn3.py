"""Turn 3 TOC-package behavior: hint corroboration, back-index guard, cross-kind
restart, blip tolerance, furniture bridging, chain-completion veto, LIS leader
penalty, stack-neutral TOC nodes, and 10-Q grammar routing."""

from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.grammar.form10q import Form10QGrammar
from edgar_itemize.pipeline import grammar_for
from edgar_itemize.sgml import DocumentBlock
from edgar_itemize.toc import detect_toc, veto_chain_completing
from edgar_itemize.tree import Node, build_tree
from edgar_itemize.headings import merge_and_renumber

G = Form10KGrammar()


def blk(idx, pos, text="Item", **kw):
    return Block(idx=idx, text=text, raw_start=pos, raw_end=pos + 50, norm_start=pos, norm_end=pos + 50, **kw)


def cand(block_idx, kind, label, pos, score=0.8, rules=(), toc_hint=False):
    return Candidate(
        block_idx=block_idx, kind=kind, label_raw=label, label_canon=label, title="T", score=score,
        rule_ids=list(rules), order_key=G.order_key(kind, label), toc_hint=toc_hint,
        head_raw_start=pos, head_raw_end=pos + 10,
    )


def make(pairs, gap=100):
    """pairs: list of (kind, label, score, rules). Returns blocks, cands at even spacing."""
    blocks, cands = [], []
    for i, (kind, label, score, rules) in enumerate(pairs):
        pos = i * gap
        blocks.append(blk(i, pos))
        hint = any(r.startswith("toc.") for r in rules)
        cands.append(cand(i, kind, label, pos, score=score, rules=rules, toc_hint=hint))
    return blocks, cands


def test_href_only_hints_need_corroboration():
    # four real body items whose anchors carry bare-# hrefs: no leaders, no toc
    # word, no duplication -> must NOT become a region (D2 defense-in-depth)
    pairs = [("item", f"ITEM {k}", 0.8, ["toc.href"]) for k in ("1", "2", "3", "5")]
    blocks, cands = make(pairs)
    assert detect_toc(blocks, cands, norm_len=400) == []


def test_leader_hints_still_condemn():
    pairs = [("item", f"ITEM {k}", 0.5, ["toc.leader_or_pageno"]) for k in ("1", "2", "3", "5")]
    pairs += [("item", f"ITEM {k}", 0.9, []) for k in ("1", "2", "3", "5")]
    blocks, cands = make(pairs)
    regions = detect_toc(blocks, cands, norm_len=800)
    assert len(regions) == 1 and regions[0].reason == "toc.hints"
    assert regions[0].last_cand == 3


def test_back_index_does_not_condemn_body():
    # body run early, leader-hinted duplicate index in the back 30% of the doc:
    # duplicated_later must not fire on the body run (E2 back-index inversion)
    body = [("item", f"ITEM {k}", 0.9, []) for k in ("1", "2", "3", "5", "7", "8", "9", "10")]
    blocks, cands = [], []
    for i, (kind, label, score, rules) in enumerate(body):
        pos = i * 100
        blocks.append(blk(i, pos))
        cands.append(cand(i, kind, label, pos, score=score, rules=rules))
    n = len(body)
    for j, (kind, label, score, rules) in enumerate(body):
        i = n + j
        pos = 9000 + j * 30
        blocks.append(blk(i, pos))
        cands.append(cand(i, kind, label, pos, score=0.5, rules=["toc.leader_or_pageno"], toc_hint=True))
    regions = detect_toc(blocks, cands, norm_len=10000)
    reasons = {(r.first_cand, r.reason) for r in regions}
    assert (0, "toc.duplicated_later") not in reasons  # body run survives
    assert any(r.first_cand == n and r.reason == "toc.hints" for r in regions)  # back index condemned


def test_cross_kind_restart_saves_body_part():
    # items-only TOC run, then body PART I right after: PART I must start a new
    # run, not be swallowed (B2 part-swallow)
    pairs = [("item", f"ITEM {k}", 0.5, ["toc.leader_or_pageno"]) for k in ("1", "2", "3", "5", "7")]
    pairs += [("part", "PART I", 1.0, [])]
    pairs += [("item", f"ITEM {k}", 0.9, []) for k in ("1", "2", "3")]
    blocks, cands = make(pairs, gap=50)
    regions = detect_toc(blocks, cands, norm_len=450)
    assert len(regions) == 1
    assert regions[0].last_cand == 4  # PART I (index 5) not included


def test_midrun_blip_absorbed():
    # a mislabeled row (ITEM 10 misread) inside an ascending TOC run must not
    # split the run when the next row restores order (B2 underreach)
    labels = ["1", "2", "3", "5", "10", "7", "8", "9A"]
    pairs = [("item", f"ITEM {k}", 0.5, ["toc.leader_or_pageno"]) for k in labels]
    pairs += [("item", f"ITEM {k}", 0.9, []) for k in ("1", "2", "3", "5", "7", "8")]
    blocks, cands = make(pairs, gap=50)
    regions = detect_toc(blocks, cands, norm_len=700)
    assert len(regions) == 1
    assert regions[0].first_cand == 0 and regions[0].last_cand == len(labels) - 1


def test_furniture_bridging_text_toc():
    # PART candidates separated by dot-leader item lines that are not candidates:
    # the run must bridge the furniture and qualify via parts_only (B1)
    blocks, cands = [], []
    i = 0
    for pnum, plabel in enumerate(["PART I", "PART II", "PART III", "PART IV"]):
        pos = i * 80
        blocks.append(blk(i, pos, text=plabel))
        cands.append(cand(i, "part", plabel, pos, score=0.8, rules=[]))
        i += 1
        for j in range(6):  # dot-leader furniture lines between part rows
            pos = i * 80
            blocks.append(blk(i, pos, text=f"{j + 1}. Business ....... {j + 3}"))
            i += 1
    for plabel in ["PART I", "PART II", "PART III", "PART IV"]:  # body copies later
        pos = i * 80
        blocks.append(blk(i, pos, text=plabel))
        cands.append(cand(i, "part", plabel, pos, score=0.9, rules=[]))
        i += 1
    regions = detect_toc(blocks, cands, norm_len=i * 80)
    assert len(regions) == 1 and regions[0].reason == "toc.parts_only"
    assert regions[0].last_cand == 3


def test_chain_completion_veto():
    # a "region" of strong, un-hinted stub headings that are the only copies of
    # their labels must be vetoed back into the tree (E2)
    pairs = [("item", f"ITEM {k}", 0.8, ["toc.href"]) for k in ("9", "10", "11", "12")]
    blocks, cands = make(pairs)
    from edgar_itemize.toc import TocRegion

    regions = [TocRegion(first_cand=0, last_cand=3, block_start=0, block_end=3, reason="toc.hints")]
    kept = veto_chain_completing(cands, regions)
    assert kept == []
    assert all("toc.vetoed_region" in c.rule_ids for c in cands)


def test_veto_spares_real_toc():
    # leader-hinted TOC rows duplicated by body copies: veto must not fire
    pairs = [("item", f"ITEM {k}", 0.5, ["toc.leader_or_pageno"]) for k in ("1", "2", "3", "5")]
    pairs += [("item", f"ITEM {k}", 0.9, []) for k in ("1", "2", "3", "5")]
    blocks, cands = make(pairs)
    from edgar_itemize.toc import TocRegion

    regions = [TocRegion(first_cand=0, last_cand=3, block_start=0, block_end=3, reason="toc.hints")]
    assert veto_chain_completing(cands, regions) == regions


def test_lis_prefers_unhinted_copy():
    # same label twice at equal score, later copy leader-hinted: body (earlier,
    # un-hinted) copy must win the chain slot (A3c)
    blocks = [blk(0, 100), blk(1, 900)]
    cands = [
        cand(0, "item", "ITEM 1", 100, score=0.8, rules=[]),
        cand(1, "item", "ITEM 1", 900, score=0.8, rules=["toc.leader_or_pageno"], toc_hint=True),
    ]
    nodes, rejected = build_tree(blocks, cands, [], G, doc_raw_start=0, doc_raw_end=1000, norm_len=1000)
    items = [n for n in nodes if n.level_kind == "item"]
    assert len(items) == 1 and items[0].head_raw_start == 100


def test_toc_node_stack_neutral_and_end_capped():
    # a mid-document toc node must not orphan following headings, and its end
    # must stay capped rather than extend to the next node
    part = Node(1, 0, 1, "part", "PART I", "PART I", None, 100, 5000, 100, 110, 100, 5000, 1, 1.0, [])
    item = Node(2, 0, 2, "item", "ITEM 1", "ITEM 1", None, 200, 5000, 200, 210, 200, 5000, 1, 1.0, [])
    toc = Node(3, 0, 1, "toc", "TOC", None, None, 300, 400, 300, 310, 300, 400, 0, 0.8, ["toc.hints"])
    head = Node(4, 0, 3, "heading", None, None, "H", 500, 5000, 500, 510, 500, 5000, 0, 0.6, ["hdg"])
    root = Node(0, -1, 0, "document", None, None, None, 0, 6000, 0, 0, 0, 6000, 0, 1.0, ["doc"])
    ordered = merge_and_renumber([root, part, item, toc], [head])
    toc_out = next(n for n in ordered if n.level_kind == "toc")
    head_out = next(n for n in ordered if n.level_kind == "heading")
    item_out = next(n for n in ordered if n.level_kind == "item")
    assert head_out.parent_id == item_out.node_id  # not orphaned to root by the toc
    assert toc_out.raw_end == 400  # end stays capped at the region


def test_10q_grammar_routing():
    for t in ("10-Q", "10-Q/A", "10QSB", "10-QSB/A"):
        d = DocumentBlock(sequence=1, type=t, filename="f.txt", description=None, is_html=False,
                          doc_start=0, doc_end=1, text_start=0, text_end=1)
        assert isinstance(grammar_for(d), Form10QGrammar), t
    d = DocumentBlock(sequence=1, type="10-K", filename="f.txt", description=None, is_html=False,
                      doc_start=0, doc_end=1, text_start=0, text_end=1)
    assert isinstance(grammar_for(d), Form10KGrammar)


def test_member_rescue_sole_strong_copy():
    # a strong, un-hinted heading inside a condemned region, with no strong copy
    # of its label outside, must be rescued into the tree
    # mid-body condemned region containing the document's ONLY strong ITEM 7
    # (the real MD&A heading) plus weak rows whose strong copies come later
    pairs = [("item", "ITEM 1", 0.9, []),
             ("item", "ITEM 2", 0.9, []),
             ("item", "ITEM 3", 0.9, []),
             ("item", "ITEM 7", 0.9, []),      # in region: sole strong copy -> rescue
             ("item", "ITEM 7A", 0.5, ["toc.leader_or_pageno"]),  # in region
             ("item", "ITEM 8", 0.5, ["toc.leader_or_pageno"]),   # in region
             ("item", "ITEM 7A", 0.9, []),
             ("item", "ITEM 8", 0.9, [])]
    blocks, cands = make(pairs)
    from edgar_itemize.toc import TocRegion

    regions = [TocRegion(first_cand=3, last_cand=5, block_start=3, block_end=5, reason="toc.duplicated_later")]
    nodes, rejected = build_tree(blocks, cands, regions, G, doc_raw_start=0, doc_raw_end=800, norm_len=800)
    starts = {n.label_canon: n.head_raw_start for n in nodes if n.level_kind == "item"}
    assert starts.get("ITEM 7") == 300  # rescued from the region
    assert starts.get("ITEM 7A") == 600 and starts.get("ITEM 8") == 700  # later strong copies win
    assert any(r.reason == "toc" and r.label_canon == "ITEM 7A" for r in rejected)


def test_incorporation_index_does_not_condemn_body():
    # real body run (0.80) duplicated by a back incorporation index whose rows
    # score lower but above the strong threshold and carry NO leader hints
    # ("Item 6 ... Annual Report page 52" two-column layout)
    body = [("item", f"ITEM {k}", 0.8, []) for k in ("5", "6", "7", "7A", "8", "9", "10", "11")]
    blocks, cands = [], []
    for i, (kind, label, score, rules) in enumerate(body):
        blocks.append(blk(i, i * 100))
        cands.append(cand(i, kind, label, i * 100, score=score, rules=rules))
    n = len(body)
    for j, (kind, label, _s, _r) in enumerate(body):
        i = n + j
        pos = 8000 + j * 40
        blocks.append(blk(i, pos))
        cands.append(cand(i, kind, label, pos, score=0.62, rules=[]))
    regions = detect_toc(blocks, cands, norm_len=9000)
    assert not any(r.first_cand == 0 and r.reason == "toc.duplicated_later" for r in regions)


def test_page_header_copies_do_not_veto_member_rescue():
    # the real ITEM 9 sits in a condemned region; the only copies outside are a
    # "(continued)" page header and an exact page-start repeat: neither is an
    # independent copy, so the region member is still rescued (Turn 5)
    pairs = [("item", "ITEM 1", 0.9, []),
             ("item", "ITEM 2", 0.9, []),
             ("item", "ITEM 9", 0.9, []),                      # in region: the real heading
             ("item", "ITEM 9A", 0.5, ["toc.leader_or_pageno"]),  # in region
             ("item", "ITEM 9B", 0.5, ["toc.leader_or_pageno"]),  # in region
             ("item", "ITEM 9", 0.7, ["rej.continued", "rej.continued_first"]),
             ("item", "ITEM 9", 0.8, ["rej.page_repeat"]),
             ("item", "ITEM 9A", 0.9, []),
             ("item", "ITEM 9B", 0.9, [])]
    blocks, cands = make(pairs)
    from edgar_itemize.toc import TocRegion

    regions = [TocRegion(first_cand=2, last_cand=4, block_start=2, block_end=4, reason="toc.duplicated_later")]
    nodes, rejected = build_tree(blocks, cands, regions, G, doc_raw_start=0, doc_raw_end=900, norm_len=900)
    n9 = next(n for n in nodes if n.label_canon == "ITEM 9")
    assert n9.head_raw_start == 200 and "toc.member_rescued" in n9.rule_ids
