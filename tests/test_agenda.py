from edgar_itemize.agenda import PATH_LEN, assign_paths, level_profile, path_str
from edgar_itemize.blocks import Block
from edgar_itemize.tree import Node


def n(i, parent, depth, kind, start, end, label=None):
    return Node(i, parent, depth, kind, label, label, None, start, end, start, start + 5, 0, 0, 0, 1.0, [])


def test_ordinal_paths_and_meta():
    nodes = [
        n(0, -1, 0, "document", 0, 1000),
        n(1, 0, 1, "toc", 10, 100, "TOC"),
        n(2, 0, 1, "part", 100, 600, "PART I"),
        n(3, 2, 2, "item", 110, 400, "ITEM 1"),
        n(4, 3, 3, "heading", 200, 300),
        n(5, 2, 2, "item", 400, 600, "ITEM 2"),
        n(6, 0, 1, "part", 600, 900, "PART II"),
        n(7, 6, 2, "item", 610, 900, "ITEM 5"),
    ]
    blocks = [Block(idx=0, text="SIGNATURES", raw_start=900, raw_end=910, norm_start=0, norm_end=10)]
    paths = assign_paths(nodes, blocks, doc_start=0, doc_end=1000)
    assert paths[1] == [1, 1, 0, 0, 0, 0, 0, 0]
    assert paths[2] == [2, 1, 0, 0, 0, 0, 0, 0]
    assert paths[3] == [2, 1, 1, 0, 0, 0, 0, 0]
    assert paths[4] == [2, 1, 1, 1, 0, 0, 0, 0]
    assert paths[5] == [2, 1, 2, 0, 0, 0, 0, 0]
    assert paths[7] == [2, 2, 1, 0, 0, 0, 0, 0]
    assert all(len(p) == PATH_LEN for k, p in paths.items() if isinstance(k, int))
    assert paths["_bounds"]["back_start"] == 900 and paths["_bounds"]["front_end"] == 10
    assert path_str(paths[4]) == "2.1.1.1.0.0.0.0"
    prof = level_profile(nodes, paths)
    assert [(p["position"], p["dominant"]) for p in prof] == [(1, "part"), (2, "item"), (3, "heading")]


def test_back_matter_execution_clause_and_glued_index(): 
    from edgar_itemize.agenda import _back_matter_start

    nodes = [n(0, -1, 0, "document", 0, 10000), n(1, 0, 1, "article", 100, 5000, "ARTICLE I"), n(2, 1, 2, "section", 200, 5000, "SECTION 1.01")]
    iww = "IN WITNESS WHEREOF, the parties hereto have caused this Agreement to be duly executed by their respective authorized officers as of the day and year first above written."
    # the execution clause is longer than 60 chars and more than 30% of the document follows it: still the boundary
    blocks = [Block(idx=0, text=iww, raw_start=3000, raw_end=3000 + len(iww), norm_start=0, norm_end=1)]
    assert _back_matter_start(blocks, nodes, 10000) == 3000
    # text era: a terminal INDEX TO EXHIBITS glued to its table qualifies on its first line
    glued = "INDEX TO EXHIBITS\n3.1  Articles of Incorporation ........ 12\n4.1  Indenture .......................... 40"
    blocks = [Block(idx=0, text=glued, raw_start=9000, raw_end=9000 + len(glued), norm_start=0, norm_end=1, lines=tuple(glued.split("\n")))]
    assert _back_matter_start(blocks, nodes, 10000) == 9000
    # a SIGNATURES heading with most of the document after it (F-pages) is not the boundary
    blocks = [Block(idx=0, text="SIGNATURES", raw_start=4000, raw_end=4010, norm_start=0, norm_end=1)]
    assert _back_matter_start(blocks, nodes, 10000) == 10000


# --- Turn 7 (a) -> Phase B3: back matter in the financial-appendix population ---


def blk(text, raw_start, *, norm_start=None, lines=None, kind="para", size=None):
    size = size if size is not None else max(1, len(text))
    ns = raw_start if norm_start is None else norm_start
    return Block(idx=0, text=text, raw_start=raw_start, raw_end=raw_start + size, norm_start=ns, norm_end=ns + size,
                 kind=kind, lines=tuple(lines) if lines is not None else ())


def _doc_nodes(end=100000):
    return [n(0, -1, 0, "document", 0, end), n(1, 0, 1, "part", 100, end, "PART IV"), n(2, 1, 2, "item", 200, end, "ITEM 15")]


APPENDIX = [
    blk("Report of Independent Registered Public Accounting Firm", 30000),
    blk("Consolidated Balance Sheets as of December 31, 2020 and 2019", 40000),
]


def test_back_after_sigs_fires_when_the_appendix_follows():
    """agenda.back_after_sigs: a bare SIGNATURES page with 70% of the document after it
    is the boundary when that material is the financial appendix (v10 returned doc_end)."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    blocks = [blk("SIGNATURES", 25000)] + APPENDIX
    assert _back_matter_boundary(blocks, nodes, 100000) == (25000, 25000, "agenda.back_after_sigs")
    # without appendix evidence the v10 answer stands: F-page-free prose is not an appendix
    plain = [blk("SIGNATURES", 25000), blk("The Company continues to operate three segments.", 30000)]
    assert _back_matter_boundary(plain, nodes, 100000)[0] == 100000


def test_back_after_sigs_f_page_and_exhibit_row_evidence():
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    fpages = [blk("SIGNATURES", 25000)] + [blk(f"Some prose ending on page F-{i}", 30000 + 100 * i) for i in (1, 2, 3)]
    assert _back_matter_boundary(fpages, nodes, 100000)[2] == "agenda.back_after_sigs"
    exrows = [blk("EXHIBIT INDEX", 25000)] + [blk(f"{i}.1 Something Incorporated by reference to Exhibit {i}.1", 30000 + 100 * i) for i in (3, 4, 10)]
    assert _back_matter_boundary(exrows, nodes, 100000) == (25000, 25000, "agenda.back_after_sigs")


def test_back_head_strict_rejects_company_names_and_exhibit_rows():
    """agenda.back_head_strict: the two back_start false positives of
    docs/turn7_decisions/a_appendix.md section 5D."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    # 0001193125-12-110105: a note paragraph whose first words are a company name
    name = "Signature Assisted Living of Texas, LLC agreed to acquire 12 senior housing communities."
    blocks = [blk(name, 25000), blk("SIGNATURES", 50000)] + APPENDIX
    assert _back_matter_boundary(blocks, nodes, 100000)[0] == 50000
    # the same name inside the last 30% is not the boundary either
    assert _back_matter_boundary([blk(name, 95000)], nodes, 100000)[0] == 100000
    # 0001683168-18-000868: an exhibit-table row, in the last 30%
    poa = blk("Power of Attorney (included on Signature Page)", 95000)
    assert _back_matter_boundary([poa], nodes, 100000)[0] == 100000
    # a bare POWER OF ATTORNEY heading in the last 30% still is (the v10 rule)
    assert _back_matter_boundary([blk("POWER OF ATTORNEY", 95000)], nodes, 100000)[0] == 95000
    # but with the appendix still to come it is not: that line is exhibit 24's description
    # cell as often as it is a heading (13 of 2,000 sampled population filings)
    poa_row = [blk("Power of Attorney", 25000)] + APPENDIX + [blk("SIGNATURES", 50000), blk("Notes to Financial Statements", 60000)]
    assert _back_matter_boundary(poa_row, nodes, 100000)[0] == 50000


def test_back_head_strict_keeps_the_glued_signature_paragraph():
    """A SIGNATURES heading typeset into one paragraph with its attestation still counts."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    glued = ("SIGNATURES Pursuant to the requirements of Section 13 or 15(d) of the Securities Exchange Act of 1934, "
             "the registrant has duly caused this report to be signed on its behalf by the undersigned.")
    assert _back_matter_boundary([blk(glued, 25000)] + APPENDIX, nodes, 100000)[0] == 25000


def test_exhibit_index_before_signatures_wins():
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    blocks = [blk("INDEX TO EXHIBITS", 25000)] + APPENDIX + [blk("SIGNATURES", 60000)] + [blk("Notes to Financial Statements", 70000)]
    assert _back_matter_boundary(blocks, nodes, 100000)[0] == 25000


def test_back_attestation_without_a_signatures_heading():
    """agenda.back_attestation: filers who print no SIGNATURES heading open the signature
    block with the Exchange Act sentence (e.g. 0000070502-10-000084)."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    att = ("Pursuant to the requirements of Section 13 or 15(d) of the Securities Act of 1934, the Registrant has "
           "duly caused this report to be signed on its behalf by the undersigned.")
    assert _back_matter_boundary([blk(att, 25000)] + APPENDIX, nodes, 100000) == (25000, 25000, "agenda.back_attestation")
    # and with a short tail, no appendix evidence needed
    assert _back_matter_boundary([blk(att, 95000)], nodes, 100000)[2] == "agenda.back_attestation"


def test_back_iww_before_tail_fires_when_only_a_schedule_follows():
    """agenda.back_iww_before_tail (Turn 8 B3): 53 Turn 6 IWW windows sit before the
    segment's last structural head because an attached schedule with its own numbered
    sections follows the execution clause — the scan anchored on `last_head` never
    reaches the clause. Fire when nothing after it is a real (non-synthetic) article."""
    from edgar_itemize.agenda import _back_matter_boundary

    iww = "IN WITNESS WHEREOF, the parties hereto have caused this Agreement to be duly executed."
    nodes = [
        n(0, -1, 0, "document", 0, 10000),
        n(1, 0, 1, "article", 100, 10000, "ARTICLE 1"),
        n(2, 1, 2, "section", 200, 3000, "SECTION 1.01"),
        # the attached schedule's own numbering: sections, no article of its own
        n(3, 0, 1, "section", 5000, 10000, "SECTION 1"),
        n(4, 0, 1, "section", 7000, 10000, "SECTION 2"),
    ]
    blocks = [
        blk("SCHEDULE A", 4000),
        blk(iww, 3000, size=len(iww)),
        blk("Section 1. Definitions.", 5000),
        blk("Section 2. Representations.", 7000),
    ]
    # blocks need not be pre-sorted by the caller in this unit test, but assign_paths'
    # real callers hand them in document order; sort here to match that contract
    blocks = sorted(blocks, key=lambda b: b.raw_start)
    assert _back_matter_boundary(blocks, nodes, 10000) == (3000, 3000, "agenda.back_iww_before_tail")


def test_back_iww_before_tail_does_not_fire_before_a_fresh_article():
    """The same shape, but a real ARTICLE follows the clause: still the same instrument's
    body (or a fresh-instrument restart, `seg.candidate_restart` / B2's territory) —
    either way not this rule's case, and the window stays main body as before."""
    from edgar_itemize.agenda import _back_matter_boundary

    iww = "IN WITNESS WHEREOF, the parties hereto have caused this Agreement to be duly executed."
    nodes = [
        n(0, -1, 0, "document", 0, 10000),
        n(1, 0, 1, "article", 100, 6000, "ARTICLE 1"),
        n(2, 1, 2, "section", 200, 3000, "SECTION 1.01"),
        n(3, 0, 1, "article", 6000, 10000, "ARTICLE 1"),  # a fresh restart after the clause
    ]
    blocks = sorted([blk(iww, 3000, size=len(iww)), blk("ARTICLE 1", 6000)], key=lambda b: b.raw_start)
    assert _back_matter_boundary(blocks, nodes, 10000)[0] == 10000


# --- Turn 12 B.5 (docs/turn12_decisions/a5_ex13.md, "Refined design"): the ex13-scoped
# back-matter boundary. `structural` is empty for the whole EX-13 corpus bar 12
# documents (pipeline.py's `synth_root` anchor never reaches `nodes`), so `ex13=True`
# reproduces that same in-memory anchor here (`doc_start`) instead of returning
# `doc_end` immediately; and the signature-detection guard is narrowed on that path
# only, per the 59-document bank's two false-ruling mechanisms.


def test_ex13_boundary_falls_back_to_the_block_scan_when_structural_is_empty():
    """With no part/item/article/section node at all (the EX-13 shape:
    `find_headings` never sees an item, so `structural` is empty), the unscoped
    function still gives up immediately (v8's actual bug -- no boundary can ever be
    found). Passing `ex13=True` with the document's own start reproduces
    pipeline.py's synthesised anchor (`head_raw_start = doc_start`) so the scan below
    runs exactly as it would with a real structural node there."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = [n(0, -1, 0, "document", 0, 100000)]  # no part/item/article/section
    blocks = [blk("INDEX TO EXHIBITS", 25000)] + APPENDIX
    assert _back_matter_boundary(blocks, nodes, 100000)[0] == 100000  # unscoped: unchanged
    assert _back_matter_boundary(blocks, nodes, 100000, ex13=True) == (100000, -1, None)  # no doc_start: still a no-op
    assert _back_matter_boundary(blocks, nodes, 100000, doc_start=0, ex13=True)[0] == 25000


def test_ex13_boundary_refuses_a_front_loaded_chairmans_letter_signature():
    """agenda.back_after_sigs's 6-of-8 false-ruling mechanism (the 59-document bank):
    a bare SIGNATURES heading on the ARS's own front-loaded, signed chairman/CEO
    letter, with the audited financial statements (not an appendix) genuinely
    following. The unscoped rule reads that as "signed -> everything after is the
    appendix"; the ex13-scoped rule drops the whole signature-shape arm from
    `back_after_sigs`, keeping only the exhibit-index shape, so it must refuse this
    even though the heading is the unaffected PLURAL form (this is `strong`'s own
    narrowing, not the singular-only guard covered by the DRIP-card test below)."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    # 5% into the document, immediately followed by genuine financial-statement content
    blocks = [blk("SIGNATURES", 5000)] + APPENDIX
    assert _back_matter_boundary(blocks, nodes, 100000)[0] == 5000  # unscoped: back_after_sigs (wrong here)
    assert _back_matter_boundary(blocks, nodes, 100000, doc_start=0, ex13=True)[0] == 100000  # ex13: refused


def test_ex13_boundary_refuses_a_drip_card_bare_signature_but_keeps_the_plural_form():
    """agenda.back_short_tail's 4-of-46 false-ruling mechanism: a lone SINGULAR
    "Signature" heading is also the blank form-field label on a dividend-reinvestment
    / address-change / proxy card, with no attestation sentence or /s/ execution line
    nearby -- `_BARE_SIG_SINGULAR_RE` isolates exactly that shape. The PLURAL
    "SIGNATURES" form is unaffected (it is never wrong in the bank) and keeps firing
    under `ex13=True` in the same shape."""
    from edgar_itemize.agenda import _back_matter_boundary

    nodes = _doc_nodes()
    drip = [blk("Signature", 95000), blk("Please sign this card if you are changing your address.", 95020)]
    assert _back_matter_boundary(drip, nodes, 100000)[0] == 95000  # unscoped: back_short_tail (wrong here)
    assert _back_matter_boundary(drip, nodes, 100000, doc_start=0, ex13=True)[0] == 100000  # ex13: refused
    # the same shape, PLURAL heading, still fires under ex13=True
    plural = [blk("SIGNATURES", 95000), blk("Please sign this card if you are changing your address.", 95020)]
    assert _back_matter_boundary(plural, nodes, 100000, doc_start=0, ex13=True)[0] == 95000
    # and a genuine SINGULAR "Signature" WITH an /s/ execution line still fires under ex13=True
    signed = [blk("Signature\n/s/ Jane Doe, Chief Executive Officer", 95000, lines=("Signature", "/s/ Jane Doe, Chief Executive Officer"))]
    assert _back_matter_boundary(signed, nodes, 100000, doc_start=0, ex13=True)[0] == 95000


def test_back_truncate_shortens_the_last_item_only_to_the_boundary():
    """tree.back_truncate: the last item stops at the signature page instead of running
    to the end of the document over the appendix. tree.back_truncate_descend (Turn 12
    B.0, docs/turn12_decisions/a3_subheadings.md section (a)): the depth-3 child that
    sits entirely in back matter (its own raw_start is at the boundary) is RE-PARENTED
    to the document root rather than having its span clamped -- clamping would drive its
    raw_end at or below its own raw_start, which the spec rejects outright (99.95% of
    the real population inverts under that reading)."""
    from edgar_itemize.tree import truncate_at_back_start

    nodes = [n(0, -1, 0, "document", 0, 100000), n(1, 0, 1, "part", 100, 100000, "PART IV"),
             n(2, 1, 2, "item", 200, 100000, "ITEM 15"), n(3, 2, 3, "heading", 40000, 100000),
             n(4, 0, 1, "toc", 10, 90, "TOC")]
    for x in nodes:
        x.norm_start, x.norm_end = x.raw_start, x.raw_end
    assert truncate_at_back_start(nodes, 25000, 25000) == 3  # part + item clamped, heading re-parented
    assert (nodes[1].raw_end, nodes[2].raw_end) == (25000, 25000)
    assert nodes[2].norm_end == 25000 and "tree.back_truncate" in nodes[2].rule_ids
    # the descendant begins in back matter (40000 >= 25000, its parent's new end): it is
    # detached from ITEM 15 and re-parented to the document root. Its span is untouched.
    assert (nodes[3].raw_start, nodes[3].raw_end) == (40000, 100000)  # NOT clamped
    assert nodes[3].parent_id == 0 and nodes[3].depth == 1  # walked up to the root
    assert "tree.back_truncate_descend" in nodes[3].rule_ids
    assert "tree.back_truncate" not in nodes[3].rule_ids
    assert nodes[0].raw_end == 100000 and nodes[4].raw_end == 90  # document root and TOC untouched
    assert nodes[0].depth == 0 and nodes[4].depth == 1            # TOC's own depth/parent untouched
    # no boundary: nothing moves
    assert truncate_at_back_start(nodes, 100000, -1) == 0


def test_back_truncate_descend_two_levels_deep():
    """A grandchild that stays correctly nested under an already re-parented heading is
    NOT independently re-parented -- only its depth shifts, by the same delta its parent
    moved, so `depth == parent.depth + 1` keeps holding everywhere."""
    from edgar_itemize.tree import truncate_at_back_start

    nodes = [n(0, -1, 0, "document", 0, 100000), n(1, 0, 1, "part", 100, 100000, "PART IV"),
             n(2, 1, 2, "item", 200, 100000, "ITEM 15"),
             n(3, 2, 3, "heading", 30000, 90000),      # begins after the boundary
             n(4, 3, 4, "heading", 50000, 95000)]      # nested under 3, also after the boundary
    for x in nodes:
        x.norm_start, x.norm_end = x.raw_start, x.raw_end
    n_changed = truncate_at_back_start(nodes, 25000, 25000)
    # part(1) and item(2) both straddle and are clamped directly; heading(3) begins after
    # the boundary (30000 >= 25000, item's new end) and is re-parented to the root;
    # heading(4) stays correctly nested under heading(3) (30000 <= 50000 < 90000, heading
    # 3's UNCHANGED span) and only has its depth shifted through -- that is the 4th change.
    assert n_changed == 4
    assert nodes[1].raw_end == 25000 and "tree.back_truncate" in nodes[1].rule_ids
    assert nodes[2].raw_end == 25000 and "tree.back_truncate" in nodes[2].rule_ids
    assert (nodes[3].raw_start, nodes[3].raw_end) == (30000, 90000)  # span untouched
    assert nodes[3].parent_id == 0 and nodes[3].depth == 1
    assert "tree.back_truncate_descend" in nodes[3].rule_ids
    assert (nodes[4].raw_start, nodes[4].raw_end) == (50000, 95000)  # span untouched
    assert nodes[4].parent_id == 3 and nodes[4].depth == 2  # unchanged parent, shifted depth (4 -> 2)
    assert "tree.back_truncate_descend" not in nodes[4].rule_ids  # it moved by inheritance, not directly
    # depth == parent.depth + 1 holds throughout the re-parented subtree
    assert nodes[3].depth == nodes[0].depth + 1
    assert nodes[4].depth == nodes[3].depth + 1


def test_back_truncate_no_back_matter_leaves_the_tree_untouched():
    """A document with no back-matter boundary (back_start at doc_end) changes nothing,
    at any depth: no span, no parent_id, no depth, no rule id."""
    from edgar_itemize.tree import truncate_at_back_start

    nodes = [n(0, -1, 0, "document", 0, 100000), n(1, 0, 1, "part", 100, 100000, "PART IV"),
             n(2, 1, 2, "item", 200, 100000, "ITEM 15"), n(3, 2, 3, "heading", 40000, 90000),
             n(4, 3, 4, "heading", 50000, 80000)]
    for x in nodes:
        x.norm_start, x.norm_end = x.raw_start, x.raw_end
    before = [(x.raw_start, x.raw_end, x.norm_start, x.norm_end, x.parent_id, x.depth, list(x.rule_ids))
              for x in nodes]
    assert truncate_at_back_start(nodes, 100000, 100000) == 0
    after = [(x.raw_start, x.raw_end, x.norm_start, x.norm_end, x.parent_id, x.depth, list(x.rule_ids))
             for x in nodes]
    assert before == after


def test_back_truncate_descend_ex13_heading_only_tree():
    """EX-13 documents share this pass but carry no Part/Item labels -- the synthesised
    root's direct children are plain headings, several of which can independently
    straddle the boundary (each carries its own span, inherited from build_tree, all
    ending at the same doc_end before truncation). A deeper heading that is left
    entirely in back matter by that chain is re-parented to the root, exactly as the
    10-K item/part shape, and a heading that stays correctly nested under an
    already-clamped ancestor is left alone."""
    from edgar_itemize.tree import truncate_at_back_start

    nodes = [n(0, -1, 0, "document", 0, 50000),
             n(1, 0, 1, "heading", 500, 50000, "FINANCIAL STATEMENTS"),
             n(2, 1, 2, "heading", 1000, 45000, "NOTES TO FINANCIAL STATEMENTS"),
             n(3, 2, 3, "heading", 20000, 40000)]
    for x in nodes:
        x.norm_start, x.norm_end = x.raw_start, x.raw_end
    n_changed = truncate_at_back_start(nodes, 12000, 12000)
    assert n_changed == 3  # heading(1) clamped, heading(2) clamped, heading(3) re-parented
    assert nodes[1].raw_end == 12000 and "tree.back_truncate" in nodes[1].rule_ids
    assert nodes[1].parent_id == 0 and nodes[1].depth == 1  # already a top-level child; untouched otherwise
    assert nodes[2].raw_end == 12000 and "tree.back_truncate" in nodes[2].rule_ids
    assert nodes[2].parent_id == 1 and nodes[2].depth == 2  # stays under heading(1): 1000 < 12000 (its new end)
    assert (nodes[3].raw_start, nodes[3].raw_end) == (20000, 40000)  # span untouched
    assert nodes[3].parent_id == 0 and nodes[3].depth == 1  # walked past heading(2) and heading(1) to the root
    assert "tree.back_truncate_descend" in nodes[3].rule_ids


def test_assign_paths_reports_the_back_rule_and_marks_new_back_nodes():
    nodes = _doc_nodes() + [n(3, 2, 3, "heading", 30000, 40000)]
    blocks = [blk("SIGNATURES", 25000)] + APPENDIX
    paths = assign_paths(nodes, blocks, doc_start=0, doc_end=100000)
    assert paths["_bounds"]["back_start"] == 25000
    assert paths["_bounds"]["back_rule"] == "agenda.back_after_sigs"
    assert paths[3][0] == 3 and "agenda.back_after_sigs" in nodes[3].rule_ids
    assert paths[2][0] == 2 and "agenda.back_after_sigs" not in nodes[2].rule_ids
