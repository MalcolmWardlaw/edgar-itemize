from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.sequence import SeqItem, learn_family_prior, sequence


def readings(label):
    out = []
    for fam in ("alpha", "roman", "upper", "upper_roman", "num"):
        v = ContractGrammar.clause_value(label, fam)
        if v:
            out.append((fam, v))
    return out


def run(labels, scores=None):
    items = [SeqItem(scores[i] if scores else 0.6, readings(l)) for i, l in enumerate(labels)]
    prior = learn_family_prior(items)
    r = sequence(items, family_prior=prior)
    return [(p.depth, p.family, p.value, p.gap) if p else None for p in r.placements]


def test_simple_nesting_and_i_ambiguity():
    out = run(["a", "b", "i", "ii", "c"])
    assert out[2] == (1, "roman", 1, 0) and out[3] == (1, "roman", 2, 0) and out[4] == (0, "alpha", 3, 0)


def test_alpha_i_after_h_is_sibling():
    out = run(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"])
    assert out[8] == (0, "alpha", 9, 0) and out[9] == (0, "alpha", 10, 0)


def test_missing_indicator_is_a_gap_not_a_descent():
    out = run(["a", "b", "d", "e"])
    assert out[2] == (0, "alpha", 4, 1) and out[3] == (0, "alpha", 5, 0)


def test_spurious_low_score_candidate_is_rejected():
    out = run(["a", "b", "x", "c"], scores=[0.7, 0.7, 0.1, 0.7])
    assert out[2] is None and out[3] == (0, "alpha", 3, 0)


def test_return_to_outer_level_closes_inner():
    out = run(["a", "i", "ii", "b", "i"])
    assert out[3] == (0, "alpha", 2, 0) and out[4] == (1, "roman", 1, 0)


def test_missing_first_indicator_opens_level_with_cost():
    out = run(["a", "ii", "iii", "b"])
    assert out[1] == (1, "roman", 2, 1) and out[2] == (1, "roman", 3, 0) and out[3] == (0, "alpha", 2, 0)


def test_same_family_value1_restarts_instead_of_staircasing():
    # a new (a) after (c) in an alpha list is a restarted sibling list, not a child list
    out = run(["a", "b", "c", "a", "b"])
    assert out[3] == (0, "alpha", 1, 0) and out[4] == (0, "alpha", 2, 0)
    # ... and the restart is flagged
    items = [SeqItem(0.6, readings(l)) for l in ["a", "b", "c", "a", "b"]]
    r = sequence(items, family_prior=learn_family_prior(items))
    assert [p.restart for p in r.placements] == [False, False, False, True, False]


def test_restart_does_not_steal_genuine_child_lists():
    # roman under alpha still descends at zero cost; a fresh (a) after the outer list
    # has advanced restarts the outer list at the outer depth
    out = run(["a", "i", "ii", "b", "a", "b"])
    assert out[1] == (1, "roman", 1, 0) and out[3] == (0, "alpha", 2, 0)
    assert out[4] == (0, "alpha", 1, 0) and out[5] == (0, "alpha", 2, 0)


def test_definition_list_does_not_staircase():
    # each defined term restarts its own (a)(b) list; depth stays flat
    out = run(["a", "b", "a", "b", "c", "a", "b", "a"])
    assert {p[0] for p in out} == {0}
