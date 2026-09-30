"""Compound-exhibit segmentation and per-restart chains.

Turn 7 decision (d) put the segment boundary on the finished tree
(`agenda.compound_restart`); Turn 8 Phase B.1 moved the decision to the candidate
list because the article and section chains now run per restart (`seq.per_segment`)
and so cannot be what defines one.  Turn 8 B.2b then split the one decision B.1 took
into the two facts it was answering with a single test:

  `chain.restart`               the article numbering restarts here, so the chains do
  `seg.instrument_boundary`     ... and a different instrument begins: a new `segment`
  `chain.restart_unsegmented`   ... but nothing says a new instrument begins, so the
                                restart chains separately INSIDE the enclosing segment

These tests therefore drive the real path — `build_contract_tree` then `assign_paths` —
rather than hand-built nodes.
"""

from edgar_itemize.agenda import MAX_SEGMENTS, assign_paths, chain_restarts, compound_restarts
from edgar_itemize.blocks import Block
from edgar_itemize.candidates import Candidate
from edgar_itemize.grammar.contract import ContractGrammar

G = ContractGrammar()

IWW = ("IN WITNESS WHEREOF, the parties hereto have caused this Agreement to be duly executed "
       "by their respective authorized officers as of the day and year first above written.")
RECITAL = ("NOW, THEREFORE, in consideration of the premises and for other good and valuable "
           "consideration, the parties hereto hereby agree as follows:")
PROSE = ("The Borrower shall from time to time deliver to the Administrative Agent such "
         "certificates and other documents as the Lenders may reasonably request, in each case "
         "in form and substance reasonably satisfactory to the Administrative Agent and the "
         "Required Lenders, together with such supporting information as they may require.")

DEFAULT_RULES = ("lbl.article", "sty.caps", "sty.center", "pos.short_block")


def build(rows):
    """rows: (kind, label, pos, score, rules, text[, title]).  kind None = a block with no
    candidate; the optional seventh field overrides the candidate's title (a bare table
    cell has none)."""
    blocks, cands = [], []
    for i, row in enumerate(rows):
        kind, label, pos, score, rules, text = row[:6]
        title = row[6] if len(row) > 6 else "T"
        blocks.append(Block(idx=i, text=text, raw_start=pos, raw_end=pos + len(text),
                            norm_start=pos, norm_end=pos + len(text), lines=tuple(text.split("\n"))))
        if kind is not None:
            cands.append(Candidate(block_idx=i, kind=kind, label_raw=label, label_canon=label, title=title,
                                   score=score, rule_ids=list(rules), order_key=G.order_key(kind, label),
                                   head_raw_start=pos, head_raw_end=pos + len(label), norm_start=pos))
    return blocks, cands


def instrument(base, name, *, articles=3, cover=True, step=2000, close=True):
    """One glued instrument: cover line, title, recitals, ARTICLE k / SECTION k.01, execution clause."""
    rows = []
    if cover:
        rows.append((None, None, base - 800, 0, (), "EXHIBIT C"))
        rows.append((None, None, base - 600, 0, (), name))
        rows.append((None, None, base - 400, 0, (), RECITAL))
    for k in range(1, articles + 1):
        pos = base + (k - 1) * step
        rows.append(("article", f"ARTICLE {k}", pos, 0.85, DEFAULT_RULES, f"ARTICLE {k}"))
        rows.append(("section", f"SECTION {k}.01", pos + 200, 0.55, ("lbl.section",),
                     f"SECTION {k}.01 Title."))
        rows.append((None, None, pos + 400, 0, (), PROSE))  # body prose: seg.prior_body
        rows.append(("section", f"SECTION {k}.02", pos + 900, 0.55, ("lbl.section",),
                     f"SECTION {k}.02 Title."))
        rows.append((None, None, pos + 1100, 0, (), PROSE))
    if close:
        rows.append((None, None, base + articles * step, 0, (), IWW))
        rows.append((None, None, base + articles * step + 200, 0, (), "/s/ A. Borrower"))
    return rows


def parse(rows, doc_end):
    from edgar_itemize.tree_contract import build_contract_tree

    blocks, cands = build(sorted(rows, key=lambda r: r[2]))
    nodes, rejected = build_contract_tree(blocks, cands, [], G, doc_raw_start=0, doc_raw_end=doc_end,
                                          norm_len=doc_end)
    paths = assign_paths(nodes, blocks, doc_start=0, doc_end=doc_end)
    return blocks, cands, nodes, rejected, paths


def restarts(cands):
    """Head offsets carrying `chain.restart`: where the numbering restarts and a new
    pair of chains begins, whether or not a new instrument does."""
    return sorted(c.head_raw_start for c in cands if "chain.restart" in c.rule_ids)


def unsegmented(cands):
    return sorted(c.head_raw_start for c in cands if "chain.restart_unsegmented" in c.rule_ids)


def section_restarts(cands):
    """Head offsets carrying `chain.section_restart` (Turn 9 B.1): the SECTION numbering
    goes back below its running maximum and a run of ascending sections follows, with no
    ARTICLE heading to say so."""
    return sorted(c.head_raw_start for c in cands if "chain.section_restart" in c.rule_ids)


def labels(nodes, kind, seg_of=None, seg=None):
    out = [n.label_canon for n in sorted(nodes, key=lambda n: n.raw_start)
           if n.level_kind == kind and (seg is None or seg_of[n.node_id] == seg)]
    return out


# ---------------------------------------------------------------------------
# the candidate-level decision
# ---------------------------------------------------------------------------

def test_three_instrument_exhibit_gets_three_segments_and_three_article_chains():
    rows = instrument(2000, "CREDIT AGREEMENT") + instrument(12000, "SECURITY AGREEMENT") \
        + instrument(22000, "GUARANTY AGREEMENT", close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 34000)
    seg = paths["_segments"]
    assert seg["starts"] == [0, 12000, 22000]
    of = seg["of_node"]
    # three ARTICLE 1 nodes, one per instrument, each opening its own segment
    arts = [n for n in nodes if n.level_kind == "article" and n.order_key == 1]
    assert len(arts) == 3
    assert sorted(n.head_raw_start for n in arts) == [2000, 12000, 22000]
    assert [n.head_raw_start for n in compound_restarts(nodes, blocks)] == [12000, 22000]
    # every boundary is a numbering restart AND an instrument boundary: each instrument
    # closes with its execution clause and the next names itself
    assert restarts(cands) == [12000, 22000]
    assert unsegmented(cands) == []
    for c in cands:
        if c.head_raw_start in (12000, 22000):
            assert "seg.prior_closed" in c.rule_ids and "seg.new_title" in c.rule_ids
            assert "seg.instrument_boundary" in c.rule_ids
    # every instrument keeps its full article and section chain
    for s in (0, 1, 2):
        assert labels(nodes, "article", of, s) == ["ARTICLE 1", "ARTICLE 2", "ARTICLE 3"]
        assert labels(nodes, "section", of, s) == ["SECTION 1.01", "SECTION 1.02", "SECTION 2.01",
                                                   "SECTION 2.02", "SECTION 3.01", "SECTION 3.02"]
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    # the chains that ran inside a segment say so
    assert all("seq.per_segment" in n.rule_ids for n in nodes if n.node_id and of[n.node_id] > 0)
    assert not any("seq.per_segment" in n.rule_ids for n in nodes if n.node_id and of[n.node_id] == 0)
    # sections nest under an article of their OWN instrument
    by_id = {n.node_id: n for n in nodes}
    for n in nodes:
        if n.level_kind == "section":
            assert of[by_id[n.parent_id].node_id] == of[n.node_id]


def test_one_global_chain_would_have_dropped_the_later_instruments():
    """The regression this rule exists for: without segments the second and third
    ARTICLE 1 lose to the first instrument's monotone chain (Turn 7 C2's 664
    chain casualties).  Strip the fresh-instrument evidence and it happens again.

    Turn 9 B.1 narrows what "it" is.  With no instrument evidence no ARTICLE opens a
    chain, so the later ARTICLE 1 headings are still written out `nonmonotone` — that is
    still the article rule's job.  But the section numbering restarts visibly at each
    instrument, so `chain.section_restart` now opens the chain there and the later
    instruments' section bodies survive under a synthesized parent instead of being
    written out with them (docs/turn9_decisions/b1_section_restart.md)."""
    rows = [r for r in (instrument(2000, "CREDIT AGREEMENT") + instrument(12000, "SECURITY AGREEMENT")
                        + instrument(22000, "GUARANTY AGREEMENT", close=False))
            if r[0] is not None or r[5] not in ("EXHIBIT C", RECITAL)
            and "AGREEMENT" not in r[5]]
    blocks, cands, nodes, rejected, paths = parse(rows, 34000)
    assert paths["_segments"]["starts"] == [0]          # no instrument evidence: no segment
    assert restarts(cands) == []                        # and no article-level chain restart
    real = [n for n in nodes if n.level_kind == "article" and "gram.synth_article" not in n.rule_ids]
    assert len([n for n in real if n.order_key == 1]) == 1
    # the two later ARTICLE 1 headings are the labels the article chain still drops
    assert sorted(r.raw_start for r in rejected if r.reason == "nonmonotone") == [12000, 22000]
    # the section restart carries the unsegmented id and never the instrument boundary
    assert section_restarts(cands) == [12200, 22200]
    assert unsegmented(cands) == [12200, 22200]
    assert not [c for c in cands if "seg.instrument_boundary" in c.rule_ids]
    # every instrument's sections survive, in their own chain
    assert labels(nodes, "section") == [f"SECTION {k}.0{j}" for _ in range(3)
                                        for k in (1, 2, 3) for j in (1, 2)]


def test_single_instrument_agreement_is_byte_identical_to_the_unsegmented_build():
    """One agreement: nothing this turn touches may show up anywhere in the output.

    Byte-identical to main means no new rule id on any node or candidate, one chain,
    one segment, and the ordinals the unsegmented build would have produced.
    """
    rows = instrument(2000, "CREDIT AGREEMENT", articles=6)
    blocks, cands, nodes, rejected, paths = parse(rows, 20000)
    assert paths["_segments"]["starts"] == [0]
    assert all(v == 0 for v in paths["_segments"]["of_node"].values())
    assert labels(nodes, "article") == [f"ARTICLE {k}" for k in range(1, 7)]
    assert labels(nodes, "section") == [f"SECTION {k}.0{j}" for k in range(1, 7) for j in (1, 2)]
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    # top-level ordinals 1..6, the six articles, exactly as one chain would number them
    arts = sorted((n for n in nodes if n.level_kind == "article"), key=lambda n: n.raw_start)
    assert [paths[n.node_id][1] for n in arts] == [1, 2, 3, 4, 5, 6]
    new_ids = {"seq.per_segment", "chain.restart", "chain.restart_unsegmented",
               "chain.section_restart", "seg.instrument_boundary", "seg.prior_closed",
               "seg.new_title", "seg.new_parties", "agenda.compound_restart",
               "agenda.segment_back_matter"}
    assert not any(new_ids.intersection(c.rule_ids) for c in cands)
    assert not any(new_ids.intersection(n.rule_ids) for n in nodes)


def test_a_cross_reference_to_article_1_opens_no_segment():
    xref = ("article", "ARTICLE 1", 12000, 0.40, ("lbl.article", "rej.lowercase_title"),
            "Article 1 of the Credit Agreement, as that term is defined in the Uniform Commercial Code, "
            "shall govern the perfection of the security interest granted hereunder.")
    rows = instrument(2000, "CREDIT AGREEMENT") + [
        (None, None, 11600, 0, (), "SECURITY AGREEMENT"),  # fresh-instrument evidence IS present
        (None, None, 11800, 0, (), RECITAL),
        xref,
    ]
    blocks, cands, nodes, rejected, paths = parse(rows, 20000)
    assert paths["_segments"]["starts"] == [0]
    assert restarts(cands) == []
    # the same line as a real heading (no cross-reference rule id, normal score) does open one
    rows2 = [r for r in rows if r is not xref] + [("article", "ARTICLE 1", 12000, 0.85, DEFAULT_RULES, "ARTICLE 1")]
    assert parse(rows2, 20000)[4]["_segments"]["starts"] == [0, 12000]


def test_an_uncondemned_table_of_contents_does_not_make_the_body_a_second_instrument():
    """seg.prior_body.  A credit agreement whose own index escaped condemnation has
    article and section candidates before its own first ARTICLE 1, and the preamble
    between them reads as a fresh instrument -- but index rows are not an agreement
    body, so nothing precedes this article to close."""
    index = []
    for k in range(1, 4):
        index.append(("article", f"ARTICLE {k}", 1000 + (k - 1) * 120, 0.6, DEFAULT_RULES, f"ARTICLE {k}"))
        index.append(("section", f"SECTION {k}.01", 1040 + (k - 1) * 120, 0.6, ("lbl.section",),
                      f"SECTION {k}.01 Title"))
    rows = index + [(None, None, 1600, 0, (), "CREDIT AGREEMENT"),
                    (None, None, 1700, 0, (), RECITAL)] + instrument(2000, "CREDIT AGREEMENT", cover=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 20000)
    assert paths["_segments"]["starts"] == [0]
    assert restarts(cands) == []
    # the same index followed by a whole agreement and THEN a second instrument does segment
    rows2 = rows + instrument(12000, "SECURITY AGREEMENT", close=False)
    assert parse(rows2, 24000)[4]["_segments"]["starts"] == [0, 12000]


def test_a_restart_cannot_count_itself_as_its_own_segments_body():
    """seg.prior_body is scoped to the segment a restart would close, and the offset at
    which a body is established is the one AFTER the prose.  So a second restart that
    follows the first with no body of its own between them opens nothing."""
    rows = instrument(2000, "CREDIT AGREEMENT") + instrument(12000, "SECURITY AGREEMENT", close=False) + [
        (None, None, 19000, 0, (), "EXHIBIT D"),
        (None, None, 19100, 0, (), "PLEDGE AGREEMENT"),
        (None, None, 19150, 0, (), RECITAL),
        ("article", "ARTICLE 1", 19200, 0.85, DEFAULT_RULES, "ARTICLE 1"),
        (None, None, 19300, 0, (), "EXHIBIT E"),
        (None, None, 19350, 0, (), "GUARANTY AGREEMENT"),
        (None, None, 19400, 0, (), RECITAL),
        ("article", "ARTICLE 1", 19500, 0.85, DEFAULT_RULES, "ARTICLE 1"),
    ]
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    assert restarts(cands) == [12000, 19200]
    # 19200 restarts the numbering but nothing closed before it (the second instrument
    # has no execution clause), so it chains separately inside segment 1
    assert paths["_segments"]["starts"] == [0, 12000]
    assert unsegmented(cands) == [19200]


def test_the_documents_first_article_never_opens_a_segment():
    # an instrument preceded by nothing but a cover page and recitals: one segment
    rows = instrument(2000, "CREDIT AGREEMENT")
    blocks, cands = build(sorted(rows, key=lambda r: r[2]))
    assert chain_restarts(blocks, cands) == []


def test_restart_gate_rejects_synthetic_low_score_and_non_value_1():
    base = instrument(2000, "CREDIT AGREEMENT") + [(None, None, 11600, 0, (), "SECURITY AGREEMENT"),
                                                   (None, None, 11800, 0, (), RECITAL)]
    good = ("article", "ARTICLE 1", 12000, 0.85, DEFAULT_RULES, "ARTICLE 1")
    assert parse(base + [good], 20000)[4]["_segments"]["starts"] == [0, 12000]
    for bad in (
        ("article", "ARTICLE 1", 12000, 0.45, DEFAULT_RULES, "ARTICLE 1"),               # below the 0.5 floor
        ("article", "ARTICLE 2", 12000, 0.85, DEFAULT_RULES, "ARTICLE 2"),               # not a value-1 restart
        ("article", "ARTICLE 1", 12000, 0.85, DEFAULT_RULES + ("rej.prose",), "ARTICLE 1"),
        ("article", "ARTICLE 1", 12000, 0.85, DEFAULT_RULES + ("toc.leader_or_pageno",), "ARTICLE 1"),
        ("article", "ARTICLE 1", 12000, 0.85, DEFAULT_RULES + ("gram.synth_article",), "ARTICLE 1"),
    ):
        blocks, cands, _, _, paths = parse(base + [bad], 20000)
        assert paths["_segments"]["starts"] == [0], bad
        assert restarts(cands) == [], bad


def test_fresh_instrument_evidence_is_required():
    # an execution clause alone is not enough: no title, no recitals, no cover line
    rows = instrument(2000, "CREDIT AGREEMENT") + \
        instrument(12000, "SECURITY AGREEMENT", cover=False, close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    assert paths["_segments"]["starts"] == [0]
    assert restarts(cands) == []  # not even a chain restart: the numbering is one chain


# ---------------------------------------------------------------------------
# instrument boundary versus numbering restart (Turn 8 B.2b)
# ---------------------------------------------------------------------------

def test_two_instruments_with_a_closed_prior_and_a_new_title_are_two_segments():
    """The instrument-boundary case: agreement A signs off, agreement B names itself.
    Two chains AND two segments, and the evidence is on the candidate one term each."""
    rows = instrument(2000, "CREDIT AGREEMENT") + instrument(12000, "SECURITY AGREEMENT", close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    seg = paths["_segments"]
    assert restarts(cands) == [12000]          # two chains
    assert seg["starts"] == [0, 12000]         # two segments
    assert unsegmented(cands) == []
    c = next(c for c in cands if c.head_raw_start == 12000)
    assert "chain.restart" in c.rule_ids
    assert "seg.prior_closed" in c.rule_ids and "seg.new_title" in c.rule_ids
    assert "seg.instrument_boundary" in c.rule_ids
    of = seg["of_node"]
    for s in (0, 1):
        assert labels(nodes, "article", of, s) == ["ARTICLE 1", "ARTICLE 2", "ARTICLE 3"]
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    # each instrument is addressed as its own document: ordinals restart
    arts = sorted((n for n in nodes if n.level_kind == "article"), key=lambda n: n.raw_start)
    assert [paths[n.node_id][1] for n in arts] == [1, 2, 3, 1, 2, 3]
    assert [n.head_raw_start for n in compound_restarts(nodes, blocks)] == [12000]


def test_a_numbering_restart_with_no_instrument_evidence_chains_but_does_not_segment():
    """The decoupled case B.1 and B.2 could not both serve: the numbering restarts, so
    the chains must, but nothing closed before it, so `segment` must not claim a second
    instrument.  One segment, two chains, and the ordinals keep counting."""
    rows = instrument(2000, "CREDIT AGREEMENT", close=False) \
        + instrument(12000, "SECURITY AGREEMENT", close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    seg = paths["_segments"]
    assert restarts(cands) == [12000]      # two chains ...
    assert unsegmented(cands) == [12000]   # ... one segment
    assert seg["starts"] == [0]
    assert all(v == 0 for v in seg["of_node"].values())
    assert not compound_restarts(nodes, blocks)
    c = next(c for c in cands if c.head_raw_start == 12000)
    assert "seg.prior_closed" not in c.rule_ids and "seg.instrument_boundary" not in c.rule_ids
    # the chains ran per restart, so both ARTICLE 1s and every section survive
    arts = sorted((n for n in nodes if n.level_kind == "article"), key=lambda n: n.raw_start)
    assert [n.label_canon for n in arts] == ["ARTICLE 1", "ARTICLE 2", "ARTICLE 3"] * 2
    assert len(labels(nodes, "section")) == 12
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    # the second chain says so, and its sections nest under ITS articles, not the first
    # chain's -- two ARTICLE 1 nodes in one segment must not collide in the article key
    second = [n for n in arts if n.raw_start >= 12000]
    assert all("seq.per_segment" in n.rule_ids for n in second)
    assert not any("seq.per_segment" in n.rule_ids for n in arts if n.raw_start < 12000)
    by_id = {n.node_id: n for n in nodes}
    for n in nodes:
        if n.level_kind == "section":
            assert (by_id[n.parent_id].raw_start >= 12000) == (n.raw_start >= 12000)
    # ordinals CONTINUE the enclosing segment's count instead of restarting at 1
    assert [paths[n.node_id][1] for n in arts] == [1, 2, 3, 4, 5, 6]
    assert [paths[n.node_id][0] for n in arts] == [2] * 6  # one main body, no segment back matter


# ---------------------------------------------------------------------------
# paths, meta and back matter
# ---------------------------------------------------------------------------

def test_segments_are_orthogonal_to_meta_and_restart_the_ordinals():
    rows = instrument(2000, "CREDIT AGREEMENT") + instrument(12000, "SECURITY AGREEMENT", close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    seg = paths["_segments"]
    assert seg["starts"] == [0, 12000]
    arts = sorted((n for n in nodes if n.level_kind == "article"), key=lambda n: n.raw_start)
    # top-level ordinals restart inside the new segment
    assert [paths[n.node_id][1] for n in arts] == [1, 2, 3, 1, 2, 3]
    # segment 0 gets its own back matter: its execution clause closes agreement A
    assert seg["back"][0] == 2000 + 3 * 2000
    assert paths[arts[3].node_id][0] == 2  # the second agreement opens in ITS main body
    assert all("agenda.segment_back_matter" not in n.rule_ids for n in arts)


def test_segment_back_matter_marks_a_closed_instruments_tail():
    # signature-page enumeration after the first agreement's execution clause: back
    # matter of segment 0, reached only through segment 0's own scan.  Without the
    # segment the document-wide scan starts after the LAST structural head — inside
    # the second agreement — and leaves this in the main body (Turn 6's IWW class).
    tail_rows = [("clause", "(a)", 8600, 0.5, ("lbl.clause",), "(a) Borrower")]
    rows = instrument(2000, "CREDIT AGREEMENT") + tail_rows \
        + instrument(12000, "SECURITY AGREEMENT", close=False)
    blocks, cands, nodes, rejected, paths = parse(rows, 24000)
    tail = next(n for n in nodes if n.label_canon == "(a)")
    assert paths[tail.node_id][0] == 3  # META_BACK
    assert "agenda.segment_back_matter" in tail.rule_ids
    # the rule id names the mechanism, so it is only carried where the *segment's* own
    # scan is what placed the node: in the one-agreement document the document-wide
    # scan reaches the same clause and no segment rule id is added
    plain = parse(instrument(2000, "CREDIT AGREEMENT") + tail_rows, 24000)
    tail2 = next(n for n in plain[2] if n.label_canon == "(a)")
    assert plain[4][tail2.node_id][0] == 3
    assert "agenda.segment_back_matter" not in tail2.rule_ids


def test_segment_count_is_capped_for_int8():
    # each instrument needs a name the document has not used yet, or `seg.new_title`
    # is false from the second one on and they are unsegmented restarts instead
    rows = []
    for k in range(60):
        name = f"SECURITY AGREEMENT {chr(65 + k // 26)}{chr(65 + k % 26)}"
        rows += instrument(2000 + k * 6000, name, articles=1, close=(k < 59))
    blocks, cands, nodes, rejected, paths = parse(rows, 2000 + 60 * 6000)
    assert len(restarts(cands)) == MAX_SEGMENTS - 1  # the cap is on the restarts
    assert len(paths["_segments"]["starts"]) == MAX_SEGMENTS
    assert max(paths["_segments"]["of_node"].values()) < MAX_SEGMENTS


# ---------------------------------------------------------------------------
# Turn 9 B.1: `chain.section_restart` -- the numbering restarts and no ARTICLE says so
# (docs/turn9_decisions/a3_chain_flips.md section 7, b1_section_restart.md).
# ---------------------------------------------------------------------------

def sec_run(base, labs, *, score=0.55, rules=("lbl.section",), step=600, title="T"):
    """A run of section headings with body prose between them and no ARTICLE anywhere."""
    rows = []
    for k, lab in enumerate(labs):
        pos = base + k * step
        rows.append(("section", lab, pos, score, rules, f"{lab} Title.", title))
        rows.append((None, None, pos + 200, 0, (), PROSE))
    return rows


def restart_heads(rows):
    """`chain.section_restart` offsets the candidate-level decision writes, without
    building a tree: `chain_restarts` is where both restart rules are decided."""
    blocks, cands = build(sorted(rows, key=lambda r: r[2]))
    chain_restarts(blocks, cands)
    return section_restarts(cands)


def test_a_second_section_run_with_no_article_opens_its_own_chain():
    """The A.3 population: a guaranty glued after a credit agreement numbers its own
    paragraphs 1..6 and carries no ARTICLE heading at all, so `chain.restart` -- which is
    decided on article candidates -- can never be asked about it.  Under one chain per
    stretch the shorter run loses and every one of its labels is written out
    `nonmonotone`; the section restart gives it a chain of its own."""
    first = [f"SECTION {k}" for k in range(1, 7)]
    rows = sec_run(2000, first) + sec_run(12000, first)
    blocks, cands, nodes, rejected, paths = parse(rows, 20000)
    assert section_restarts(cands) == [12000]
    assert restarts(cands) == []                       # no article-level restart fired
    assert unsegmented(cands) == [12000]               # ... so it is never a segment
    assert not [c for c in cands if "seg.instrument_boundary" in c.rule_ids]
    assert paths["_segments"]["starts"] == [0]
    # both runs survive, in that order, and nothing is written out
    assert labels(nodes, "section") == first + first
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    # the second run chains separately and says so
    assert all("seq.per_segment" in n.rule_ids for n in nodes
               if n.level_kind == "section" and n.raw_start >= 12000)
    assert all("seq.per_segment" not in n.rule_ids for n in nodes
               if n.level_kind == "section" and n.raw_start < 12000)


def test_a_run_shorter_than_three_is_not_a_restart():
    """Test (b), min_run = 3: one stray label out of order is noise and a pair is not an
    instrument.  A.3's sensitivity table measures 3 against 5 and 8; below 3 a single
    mis-numbered heading would fork the chain."""
    first = [f"SECTION {k}" for k in range(1, 7)]
    for tail, restart in ((["SECTION 2", "SECTION 8"], False),                # a lone label
                          (["SECTION 2", "SECTION 3", "SECTION 8"], False),  # a pair
                          (["SECTION 2", "SECTION 3", "SECTION 4"], True)):  # a run of three
        rows = sec_run(2000, first) + sec_run(12000, tail)
        blocks, cands, nodes, rejected, paths = parse(rows, 20000)
        assert section_restarts(cands) == ([12000] if restart else []), tail
        if not restart:
            # the out-of-order labels are exactly what the monotone chain writes out
            assert [r.label_canon for r in rejected if r.reason == "nonmonotone"] == tail[:-1]


def test_an_index_row_or_a_table_cell_never_opens_a_chain():
    """Tests (d) and (e).  An index run that opens a chain re-accepts the whole table of
    contents as a second instrument's body, and a bare numeric table cell (a commitment
    schedule amount, a covenant ratio) is not a heading at all."""
    first = [f"SECTION {k}" for k in range(1, 7)]
    run = [f"SECTION {k}" for k in (1, 2, 3)]
    for rules, title in ((("lbl.section", "toc.leader_or_pageno"), "T"),
                         (("lbl.section", "toc.section_run"), "T"),
                         (("lbl.section", "rej.xref_phrase"), "T"),
                         (("lbl.section", "pos.in_table"), ""),
                         (("lbl.section", "pos.table_row"), None)):
        rows = sec_run(2000, first) + sec_run(12000, run, rules=rules, title=title)
        assert restart_heads(rows) == [], rules
    # the same run as an ordinary heading, and as a table row that carries style
    # evidence and a title, does open one
    assert restart_heads(sec_run(2000, first) + sec_run(12000, run)) == [12000]
    styled = sec_run(12000, run, rules=("lbl.section", "pos.table_row", "sty.caps"))
    assert restart_heads(sec_run(2000, first) + styled) == [12000]


def test_a_low_scoring_restart_candidate_is_refused():
    """Test (c): `chain.restart`'s own 0.5 bar, asked of the opening candidate."""
    first = [f"SECTION {k}" for k in range(1, 7)]
    rows = sec_run(2000, first) + sec_run(12000, ["SECTION 1"], score=0.45) \
        + sec_run(12600, ["SECTION 2", "SECTION 3"])
    assert restart_heads(rows) == []


def test_section_restarts_split_both_chains_and_keep_the_article_addressing():
    """The restart splits the ARTICLE chain too, so a second instrument that numbers
    `SECTION 1.01` under its own `ARTICLE 2` heading does not borrow the first
    instrument's article, and two `SECTION 1.01` nodes in one segment cannot collide."""
    rows = (sec_run(2000, [f"SECTION 1.0{k}" for k in (1, 2)])
            + [("article", "ARTICLE 2", 4000, 0.85, DEFAULT_RULES, "ARTICLE 2")]
            + sec_run(4200, [f"SECTION 2.0{k}" for k in (1, 2)])
            + sec_run(12000, [f"SECTION 1.0{k}" for k in (1, 2, 3)])
            + [("article", "ARTICLE 2", 14000, 0.85, DEFAULT_RULES, "ARTICLE 2")]
            + sec_run(14200, [f"SECTION 2.0{k}" for k in (1, 2)]))
    blocks, cands, nodes, rejected, paths = parse(rows, 20000)
    assert section_restarts(cands) == [12000]
    assert not [r for r in rejected if r.reason == "nonmonotone"]
    by_id = {n.node_id: n for n in nodes}
    # every section nests under an article on its own side of the restart
    for n in nodes:
        if n.level_kind == "section":
            assert (by_id[n.parent_id].raw_start < 12000) == (n.raw_start < 12000)
    # one segment throughout: a section restart is a numbering fact, not an instrument
    assert paths["_segments"]["starts"] == [0]
    assert set(paths["_segments"]["of_node"].values()) == {0}
