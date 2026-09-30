"""Turn 12 B.2 — the clause-layer reliefs.

`docs/turn12_decisions/a2_clause_layer.md` section 8.  The A.2 census classified all
229,552 rejected clause candidates by mechanism with zero rows unclassified, and two
banks (400 + 150 windows, plus a 50-window scope re-bank) decided which mechanisms are
real.  Four reliefs ship, each scoped to the strata that cleared 0.90:

  B.2.0  seq.open_midrun              N3, 53,211 rows (non-numeral families), 45/50 o.o.s.
  B.2.1  gram.clause_under_article /  O1/O2/O5/O7 unconditionally, O3/O4 behind
         gram.synth_section           `prose_after`; behind the region test
  B.2.2  gram.clause_alpha_bijective  N1b unconditionally, N1a behind `lower_double`
  B.2.3  seq.alt_readings             N5x, behind the alternatives-pair test
  B.2.4  the bookkeeping fix          the 91,487 silently dropped candidates get a row

One test per clause of the spec, plus the disjointness and no-regression tests the build
brief names: the control class (a candidate the reliefs must NOT touch), Turn 11's two
acceptance sets, and B.1's C1 behaviour.
"""

import os
from pathlib import Path

import pytest

from edgar_itemize import tree_contract as TC
from edgar_itemize.candidates import ctoc_row_pass, find_candidates
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.normalize_text import text_to_blocks
from edgar_itemize.sequence import SeqItem, learn_family_prior, sequence
from edgar_itemize.toc import detect_toc
from edgar_itemize.tree_contract import _families, build_contract_tree

G = ContractGrammar()
# The private contracts-text corpus (`--kind text`; not published): files at
# <cik6>/<cik>/<accession>_<seq>.txt under EDGAR_ITEMIZE_TEXT_CORPUS.
_CORPUS_ENV = os.environ.get("EDGAR_ITEMIZE_TEXT_CORPUS")
CORPUS = Path(_CORPUS_ENV) if _CORPUS_ENV else None
needs_corpus = pytest.mark.skipif(CORPUS is None or not CORPUS.exists(),
                                  reason="EDGAR_ITEMIZE_TEXT_CORPUS not set or not mounted "
                                         "(private contracts-text corpus)")


def parse(body: str, *, era: str = "text"):
    blocks, norm, _ = text_to_blocks(body, 0)
    cands = find_candidates(blocks, G, era=era)
    toc = detect_toc(blocks, cands, norm_len=len(norm), grammar="contract")
    ctoc_row_pass(cands, blocks, toc, norm, era=era)
    nodes, rejected = build_contract_tree(blocks, cands, toc, G, doc_raw_start=0,
                                          doc_raw_end=len(body), norm_len=len(norm))
    return dict(blocks=blocks, cands=cands, nodes=nodes, rejected=rejected)


def clauses(r) -> dict[str, tuple[int, str]]:
    """label -> (depth, level_kind) for every accepted clause node."""
    return {n.label_canon: (n.depth, n.level_kind) for n in r["nodes"]
            if n.level_kind.startswith("clause")}


def rejected_clauses(r) -> dict[str, str]:
    return {x.label_canon: x.reason for x in r["rejected"] if x.kind == "clause"}


def rules_of(r, label: str) -> list[str]:
    return [x for n in r["nodes"] if n.label_canon == label for x in n.rule_ids]


# --- the sequencer's readings, as test_sequence.py's helper but through _families ------
def run(labels, scores=None, **kw):
    items = [SeqItem(scores[i] if scores else 0.6, _families(l, **kw)) for i, l in enumerate(labels)]
    prior = learn_family_prior(items)
    r = sequence(items, family_prior=prior)
    return [(p.depth, p.family, p.value, p.gap, p.open_high) if p else None for p in r.placements]


# =====================================================================================
# B.2.0  seq.open_midrun
# =====================================================================================

def test_late_lettered_marker_opens_a_run():
    """A list whose first member inside the span is (e) -- mechanism N3, 44.3% of every
    `clause_nonmonotone` rejection.  Today every member goes down together; the relief
    opens the level at a flat cost the second member earns back."""
    out = run(["e", "f", "g"])
    assert out[0] == (0, "alpha", 5, 0, True), "the run opens mid-alphabet"
    assert out[1] == (0, "alpha", 6, 0, False) and out[2] == (0, "alpha", 7, 0, False)
    assert [p for p in out if p is None] == []


def test_late_numeral_does_not_open_a_run():
    """The `num` exclusion (s7c): N3 & family == num is 2/6 real on the banks -- the
    parenthetical gloss of a spelled-out number split across a line break, `three\\n(3)
    Business Days`.  R6 never opens a level at a bare numeral."""
    assert run(["3", "4", "5"]) == [None, None, None]
    # ... while the same three markers as letters are relieved
    assert [p is not None for p in run(["c", "d", "e"])] == [True, True, True]


def test_lone_late_marker_in_prose_is_still_rejected():
    """The flat cost IS the run test: one orphan pays `open_high_cost` against a rejection
    cost equal to its own score and loses; two earn it back."""
    assert run(["a", "b", "q"]) == run(["a", "b", "q"])  # deterministic
    out = run(["a", "b", "q"])
    assert out[2] is None, "a lone mid-alphabet marker never opens a level"


def test_open_midrun_never_outbids_a_legal_continuation():
    """The guard is mechanism N3's own definition -- no other legal move.  A wide gap
    (N5g) and a cost rejection (N5c) are not built this turn, so a continuation, however
    expensive, must still win over a mid-run open."""
    items = [SeqItem(0.6, _families(l)) for l in ("a", "b", "f")]
    on = sequence(items, family_prior=learn_family_prior(items))
    off = sequence(items, family_prior=learn_family_prior(items), open_high=False)
    # gap 3 costs 1.05 and the rejection costs the score, so the DP rejects it either way:
    # what matters is that the relief does not step in where a continuation was legal
    assert [p is None for p in on.placements] == [p is None for p in off.placements] == [False, False, True]
    out = run(["a", "b", "z"])          # same-family open level, gap far beyond max_gap
    assert out[2] is None, "N5g stays rejected: the family is open but out of reach"
    out = run(["a", "b", "c", "d", "e", "f", "g"])   # and an ordinary run is untouched
    assert [p[4] for p in out] == [False] * 7


def test_open_midrun_rule_id_reaches_the_node():
    r = parse(
        "ARTICLE I\n\nDEFINITIONS\n\nSection 1.01. Certain Terms. As used herein:\n\n"
        "(e) Conditions to Effectiveness of Increase and the fees payable in connection.\n\n"
        "(f) Disbursement Procedures for each Borrowing requested by the Borrower.\n\n"
        "(g) Reimbursement obligations of the Borrower in respect of each drawing.\n\n")
    assert set(clauses(r)) == {"(e)", "(f)", "(g)"}
    assert "seq.open_midrun" in rules_of(r, "(e)")
    assert "seq.open_midrun" not in rules_of(r, "(f)")


def test_open_high_cost_is_a_parameter_and_off_is_the_old_tree():
    items = [SeqItem(0.6, _families(l)) for l in ("e", "f", "g")]
    assert [p is None for p in sequence(items, open_high=False).placements] == [True] * 3
    assert [p is None for p in sequence(items, open_high_cost=10.0).placements] == [True] * 3
    assert [p is None for p in sequence(items).placements] == [False] * 3


# =====================================================================================
# B.2.1  gram.clause_under_article / gram.synth_section
# =====================================================================================

DEFN_REGION = (
    "ARTICLE I\n\nDEFINITIONS\n\n"
    '"Applicable Margin" means, for any day, the rate per annum set forth below:\n\n'
    "(a) the aggregate principal amount of all Revolving Loans outstanding on such day;\n\n"
    "(b) the aggregate face amount of all Letters of Credit issued and outstanding;\n\n"
    "(c) the aggregate principal amount of all Swingline Loans outstanding on such day.\n\n"
    "ARTICLE II\n\nTHE CREDITS\n\n"
    "Section 2.01. Commitments. Each Lender severally agrees to make Loans.\n\n"
    "(i) each Borrowing shall be in an aggregate amount that is a multiple thereof;\n\n"
    "(ii) each Borrowing shall be comprised entirely of Loans of the same Type.\n\n")


def test_definition_components_under_a_definitions_article():
    """O1, 31,004 rows, 17/17 on the first bank: an enumerated component of a definition
    inside `ARTICLE I DEFINITIONS`, where no Section heading exists by design.  The clauses
    are parented on the ARTICLE -- no node is created."""
    r = parse(DEFN_REGION)
    assert set(clauses(r)) >= {"(a)", "(b)", "(c)"}
    assert "gram.clause_under_article" in rules_of(r, "(a)")
    assert not [n for n in r["nodes"] if "gram.synth_section" in n.rule_ids]
    art = next(n for n in r["nodes"] if n.label_canon == "ARTICLE 1")
    assert next(n for n in r["nodes"] if n.label_canon == "(a)").parent_id == art.node_id
    assert "(a)" not in rejected_clauses(r)


def test_schedule_row_without_prose_after_is_not_relieved():
    """O4 behind `prose_after` (s8 B.2.1): the schedule/annex stratum failed its first bank
    at 87.5% and cleared 18/20 out of sample only with the clause.  A pricing-grid row whose
    marker carries no prose is exactly what the clause removes."""
    body = ("SCHEDULE 1.01\n\nPRICING GRID\n\n"
            "(a) Tranche A\n\n(b) Tranche B\n\n(c) Tranche C\n\n"
            "ARTICLE I\n\nDEFINITIONS\n\n"
            "Section 1.01. Terms. As used herein in this Agreement and otherwise.\n\n")
    r = parse(body)
    assert {k: v for k, v in rejected_clauses(r).items()} == {
        "(a)": "clause_outside_section", "(b)": "clause_outside_section",
        "(c)": "clause_outside_section"}
    assert not clauses(r)


def test_region_test_refuses_a_lone_run_in_enumeration():
    """The discriminating clause: >= 3 block-initial members in distinct blocks AND a
    monotone run of >= 3.  A run-in enumeration inside one sentence is one block."""
    body = ("ARTICLE I\n\nDEFINITIONS\n\n"
            '"Change of Control" means the occurrence of (a) any person becoming the '
            "beneficial owner of more than 35% of the voting stock, (b) the sale of all or "
            "substantially all of the assets, or (c) the liquidation of the Borrower.\n\n"
            "ARTICLE II\n\nTHE CREDITS\n\nSection 2.01. Commitments. Each Lender agrees.\n\n")
    r = parse(body)
    assert not clauses(r), "one block is not a run"


def test_synth_section_where_no_article_encloses_the_region():
    """Where there is no article either, a Section is synthesized at the region start, the
    mirror of `gram.synth_article`, so the depth convention article/section/clause holds."""
    body = ("AMENDMENT NO. 2 TO CREDIT AGREEMENT\n\n"
            "WITNESSETH:\n\n"
            "WHEREAS, the Borrower has requested certain amendments to the Credit Agreement;\n\n"
            "(a) the definition of Applicable Margin is amended in its entirety to read;\n\n"
            "(b) the definition of Borrowing Base is amended by adding a new clause thereto;\n\n"
            "(c) Schedule 2.01 is amended and restated in the form attached hereto.\n\n"
            "Section 4.01. Effectiveness. This Amendment shall become effective when.\n\n")
    r = parse(body)
    synth = [n for n in r["nodes"] if "gram.synth_section" in n.rule_ids and n.level_kind == "section"]
    assert len(synth) == 1 and synth[0].depth == 2
    kids = [n for n in r["nodes"] if n.parent_id == synth[0].node_id]
    assert {n.label_canon for n in kids} == {"(a)", "(b)", "(c)"}
    assert synth[0].raw_start == kids[0].raw_start


def test_relief_creates_no_node_where_an_article_exists():
    """`gram.clause_under_article` is the no-node variant: the count of article and section
    nodes is the same with the relief on and off."""
    off = {}
    for flag in (False, True):
        TC._R3_ON = flag
        r = parse(DEFN_REGION)
        off[flag] = sorted(n.label_canon for n in r["nodes"]
                           if n.level_kind in ("article", "section"))
    TC._R3_ON = True
    assert off[False] == off[True]


# =====================================================================================
# B.2.2  gram.clause_alpha_bijective
# =====================================================================================

def test_double_letter_continuation_after_z():
    """(z) then the doubled-letter convention: already readable, and still readable."""
    assert ContractGrammar.clause_value("dd", "alpha") == 30
    out = run(["x", "y", "z", "aa", "bb", "cc", "dd"])
    assert [p is None for p in out] == [False] * 7
    assert out[-1][:3] == (0, "alpha", 30)


def test_bijective_double_is_readable_in_lower_case():
    """N1a, the markers with no reading in any family: `(dk)` continues `(dj)` under the
    bijective convention (26*4+11 = 115) and nowhere else."""
    assert ContractGrammar.clause_value("dk", "alpha") is None
    assert ContractGrammar.clause_value_bijective("dk", "alpha") == 115
    out = run(["di", "dj", "dk"])
    assert [p[:3] for p in out] == [(0, "alpha", 113), (0, "alpha", 114), (0, "alpha", 115)]


def test_bijective_double_is_not_read_in_upper_case():
    """`lower_double` (s8 B.2.2): the clause that keeps `(NY)` / `(CA)` docket stamps and
    subsidiary-schedule state codes unreadable.  An upper-case pair reads only where it
    already did -- a repeat, (DD)."""
    assert ContractGrammar.clause_value_bijective("DK", "upper") is None
    assert ContractGrammar.clause_value_bijective("NY", "upper") is None
    assert ContractGrammar.clause_value_bijective("DD", "upper") == 108
    assert run(["DI", "DJ", "DK"]) == [None, None, None]


def test_bijective_reading_can_never_open_a_list():
    """Every bijective value is >= 27 and `sequence` opens or restarts only at 1 or 2, so a
    stray `(dk)` in prose still has no legal move -- and `num` is not its family, so
    `seq.open_midrun` does not rescue it either... unless a run of them stands together,
    which is the C1 shape itself."""
    assert run(["a", "b", "dk"])[2] is None


def test_bijective_off_is_the_old_reading():
    TC._R2_ON = False
    try:
        assert _families("dk") == []
        assert _families("dd") == [("alpha", 30)]
    finally:
        TC._R2_ON = True
    assert _families("dk") == [("alpha", 115)]


# =====================================================================================
# B.2.3  seq.alt_readings
# =====================================================================================

def test_alternatives_pair_reads_as_its_own_family():
    """(x)/(y)/(z) as alternatives, not the 24th-26th member of a lettered list: N5x,
    15,693 rows, 18/18 on the bank."""
    out = run(["x", "y", "z"], alt=True)
    assert [p[:3] for p in out] == [(0, "alt", 1), (0, "alt", 2), (0, "alt", 3)]


def test_lone_alternative_marker_never_opens_a_list():
    """The pair test, applied by the caller: a span with one x/y/z marker is offered no
    `alt` reading at all, so `as provided in clause (x) above` cannot open a list."""
    assert _families("x", alt=False) == [("alpha", 24), ("roman", 10)]
    assert ("alt", 1, TC._ALT_SURCHARGE) in _families("x", alt=True)
    r = parse("ARTICLE I\n\nDEFINITIONS\n\nSection 1.01. Terms. As used herein.\n\n"
              "(a) The Borrower shall deliver to the Administrative Agent a certificate.\n\n"
              "(x) as provided in clause (x) above, the Borrower may elect otherwise.\n\n")
    assert "(x)" not in clauses(r)


def test_alt_surcharge_loses_to_a_legal_lettered_continuation():
    """A genuine mid-alphabet (x) continuing a lettered run: the continuation is free and
    the alt reading carries `restart_cost`, so the lettered reading always wins."""
    out = run(["u", "v", "w", "x", "y"], alt=True)
    assert [p[1] for p in out] == ["alpha"] * 5


def test_alt_rule_id_reaches_the_node():
    body = ("ARTICLE I\n\nDEFINITIONS\n\nSection 1.01. Terms. As used herein.\n\n"
            "(x) the Borrower shall have delivered all documents required hereunder;\n\n"
            "(y) no Default shall have occurred and be continuing on such date; or\n\n"
            "(z) the Administrative Agent shall have received the fees then payable.\n\n")
    r = parse(body)
    assert set(clauses(r)) == {"(x)", "(y)", "(z)"}
    assert "seq.alt_readings" in rules_of(r, "(x)")
    assert clauses(r)["(x)"][1] == "clause_alpha", "no new level_kind is published"


# =====================================================================================
# B.2.4  the bookkeeping fix
# =====================================================================================

def test_zero_section_document_writes_a_rejected_row_for_every_clause():
    body = ("ARTICLE I\n\nDEFINITIONS\n\n"
            "(a) The Borrower shall deliver to the Agent a certificate of a Responsible.\n\n"
            "(b) The Borrower shall deliver the financial statements described therein.\n\n")
    r = parse(body)
    assert not [n for n in r["nodes"] if n.level_kind == "section"]
    assert rejected_clauses(r) == {"(a)": "clause_outside_section", "(b)": "clause_outside_section"}


def test_bookkeeping_changes_no_node():
    body = ("ARTICLE I\n\nDEFINITIONS\n\n"
            "(a) The Borrower shall deliver to the Agent a certificate of a Responsible.\n\n"
            "(b) The Borrower shall deliver the financial statements described therein.\n\n")
    seen = {}
    for flag in (False, True):
        TC._BOOKKEEPING_ON = flag
        r = parse(body)
        seen[flag] = ([(n.node_id, n.parent_id, n.depth, n.level_kind, n.label_canon,
                        n.raw_start, n.raw_end, sorted(n.rule_ids)) for n in r["nodes"]],
                      len([x for x in r["rejected"] if x.kind == "clause"]))
    TC._BOOKKEEPING_ON = True
    assert seen[False][0] == seen[True][0], "nodes are bit-identical"
    assert seen[False][1] == 0 and seen[True][1] == 2, "only the rejected table grows"


@needs_corpus
def test_named_zero_section_document_nodes_are_bit_identical():
    """`0000891618-00-004561` seq 2 (contracts text) is one of the 1,191 zero-section documents of
    `runs/judge/turn12-a2-docs.parquet`: 855 clause candidates, 20 accepted articles, no
    section, and before B.2.4 not one of the 855 reached either `mk()` or the rejected
    table.  The fix writes all 855 and changes no node."""
    from edgar_itemize.pipeline import parse_document
    from edgar_itemize.sgml import load_text_submission

    rel = CORPUS / "000078/786110/0000891618-00-004561_2.txt"
    out = {}
    for flag in (False, True):
        TC._BOOKKEEPING_ON = flag
        sub = load_text_submission(rel, "0000891618-00-004561", 2)
        pr = parse_document(sub, sub.documents[0], "786110")
        out[flag] = ([(n.node_id, n.parent_id, n.depth, n.level_kind, n.label_canon, n.title,
                       n.raw_start, n.raw_end, n.head_raw_start, n.head_raw_end,
                       sorted(n.rule_ids)) for n in pr.nodes],
                     sorted((x.raw_start, x.label_canon, x.reason) for x in pr.rejected
                            if x.kind == "clause"))
    TC._BOOKKEEPING_ON = True
    assert out[False][0] == out[True][0], "every node bit-identical"
    assert out[False][1] == [] and len(out[True][1]) == 855
    assert {r[2] for r in out[True][1]} == {"clause_outside_section"}


# =====================================================================================
# disjointness and no regression
# =====================================================================================

def test_ordinary_section_span_is_untouched():
    """The control class: an ordinary (a)/(b)/(c) list under an accepted Section is placed
    exactly as it was, with no relief rule id anywhere on it."""
    body = ("ARTICLE V\n\nNEGATIVE COVENANTS\n\n"
            "Section 5.01. Liens. The Borrower will not create any Lien upon any property.\n\n"
            "(a) Liens created under the Loan Documents and securing the Obligations;\n\n"
            "(b) Liens for Taxes not yet due or which are being contested in good faith;\n\n"
            "(c) carriers', warehousemen's and mechanics' Liens arising in the ordinary.\n\n")
    seen = {}
    for flags in (False, True):
        for f in ("_R6_ON", "_R3_ON", "_R2_ON", "_R4_ON"):
            setattr(TC, f, flags)
        r = parse(body)
        seen[flags] = [(n.label_canon, n.depth, n.level_kind, n.parent_id, sorted(n.rule_ids))
                       for n in r["nodes"]]
    for f in ("_R6_ON", "_R3_ON", "_R2_ON", "_R4_ON"):
        setattr(TC, f, True)
    assert seen[False] == seen[True]


def test_reliefs_are_disjoint_one_rule_id_per_node():
    """No node may carry two relief ids: the four reliefs fire on disjoint mechanisms."""
    for body in (DEFN_REGION,
                 "ARTICLE I\n\nDEFINITIONS\n\nSection 1.01. Terms. As used.\n\n"
                 "(e) Conditions to Effectiveness of Increase and the fees payable.\n\n"
                 "(f) Disbursement Procedures for each Borrowing requested hereunder.\n\n"
                 "(g) Reimbursement obligations in respect of each drawing made thereunder.\n\n"):
        r = parse(body)
        for n in r["nodes"]:
            ids = {x for x in n.rule_ids
                   if x in ("seq.open_midrun", "seq.alt_readings", "gram.clause_under_article",
                            "gram.synth_section")}
            assert len(ids) <= 1 or ids == {"seq.open_midrun", "gram.clause_under_article"}, ids


def test_turn11_ctoc_rules_still_behave():
    """B.1's C1 (`rej.ctoc_row`) and Turn 11's region relief are upstream of every clause
    rule: the same fixtures must condemn and relieve the same rows."""
    import test_turn11_ctoc as T11  # noqa: PLC0415

    r = T11.parse(T11.GAP_INDEX)
    assert T11.condemned(r) == {"SECTION 1.01", "SECTION 1.02", "SECTION 1.03", "SECTION 2.01"}
    assert T11.sections(r) == {}
    # every Turn 11 / B.1 fixture, with the four reliefs on and off: the condemned set, the
    # relieved set and the accepted article/section layer are identical either way
    for doc, era in ((T11.GAP_INDEX, "text"), (T11.CELL_INDEX, "html_generic"),
                     (T11.WRAP_INDEX, "text"), (T11.LEADER_INDEX, "text"),
                     (T11.LEADER_BODY, "text"), (T11.LEADER_MIXED, "text"),
                     (T11.REGION_DOC, "text")):
        seen = {}
        for flags in (False, True):
            for f in ("_R6_ON", "_R3_ON", "_R2_ON", "_R4_ON"):
                setattr(TC, f, flags)
            x = T11.parse(doc, era=era)
            seen[flags] = (T11.condemned(x), T11.relieved(x), T11.sections(x), T11.rejected_toc(x))
        for f in ("_R6_ON", "_R3_ON", "_R2_ON", "_R4_ON"):
            setattr(TC, f, True)
        assert seen[False] == seen[True]
