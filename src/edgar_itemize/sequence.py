"""Fault-tolerant enumeration sequencing.

Given an ordered list of enumeration candidates (each with a score and the set
of (family, value) readings its label admits, e.g. "(i)" -> alpha 9 or roman 1),
choose for each one of: continue an open level (possibly skipping missing
indicators), open a deeper level, or reject it — minimizing total cost over the
whole sequence.  This replaces greedy descent, which cannot tell "descend a
level" from "missing indicator" from "spurious label" until it is too late.

Costs (all deterministic):
  reject               : candidate score (evidence thrown away)
  continue, no gap     : 0
  continue, gap of k   : k * skip_cost
  open level at 1      : 0
  open level at 2      : skip_cost           (a missing first indicator)
  open level at v > 2  : open_high_cost, and only when the item has NO other legal move
                         and its family is not `num` (Turn 12 B.2.0, `seq.open_midrun`)
  open level in the same family as the level above : + same_family_cost
                         (an alpha list nested directly under an alpha list is rare;
                          left free, every restarted list staircases one level deeper — F3)
  restart at an open level d with value 1 (2): restart_cost (+ skip_cost)
                         (closes level d and deeper: a new sibling list, e.g. a new
                          section or defined term whose own heading was not a candidate)
  family change vs. document prior at that depth : family_cost
A Viterbi pass over stack states with beam pruning keeps this linear in the
number of candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Reading = tuple[str, int] | tuple[str, int, float]  # (family, value[, extra_cost])

# --- Turn 12 B.2.0, `seq.open_midrun` (docs/turn12_decisions/a2_clause_layer.md s8) -------
# A list whose first member INSIDE the section is (e) -- because (a)-(d) belong to the
# previous section, or were never candidates, or sit in a block the normalizer merged --
# has no legal move at any cost, and because a rejection leaves the stack untouched every
# later member of that list goes down with it.  That cascade is mechanism N3: 58,087 rows,
# 44.3% of every `clause_nonmonotone` rejection, 95.5% / 90.0% judged real on two banks.
# The relief is one branch: a level may open at a value above 2, at a flat cost.
#
# The flat cost IS the run test, applied by the Viterbi pass with no new rule: a lone (e)
# in prose pays OPEN_HIGH_COST against a rejection cost equal to its own score (0.50-0.70
# in this population) and loses; a run (e) (f) (g) pays it once and earns it back, because
# the second and third members continue at cost 0.
#
# Two scope clauses, both measured:
#   * never a bare numeral (`_NO_MIDRUN_FAMILIES`).  N3 & family != num is 33/33 in sample
#     and 45/50 out of sample; N3 & family == num is 2/6 -- the parenthetical numeral gloss
#     of a spelled-out number split across a line break ("three\n(3) Business Days").
#   * only where the item has NO other legal move under the state in front of it, which is
#     mechanism N3's own definition.  Without it a wide-gap continuation (gap 3 costs 1.05)
#     would lose to a mid-run open at 0.90 and the relief would silently re-parent accepted
#     clauses -- mechanisms N5g and N5c, neither of which is built this turn.
OPEN_HIGH_COST = 0.9
_NO_MIDRUN_FAMILIES = ("num",)


@dataclass(slots=True)
class SeqItem:
    score: float
    readings: list[Reading]


@dataclass(slots=True)
class Placement:
    depth: int  # 0 = top level within the container
    family: str
    value: int
    gap: int  # number of skipped indicators before this one
    restart: bool = False  # began a new sibling list at this depth (closed the previous one)
    open_high: bool = False  # opened this level at a value above 2 (`seq.open_midrun`)


@dataclass
class SeqResult:
    placements: list[Placement | None] = field(default_factory=list)  # None = rejected
    cost: float = 0.0


def _has_legal_move(st: tuple, readings: list[Reading], max_depth: int, max_gap: int) -> bool:
    """Would `sequence` have a move other than reject for `readings` in state `st`?

    The N3 predicate, restated as the `seq.open_midrun` guard: a continuation of an open
    level of one of the item's families within `max_gap`, or an open/restart (value 1 or 2),
    or ANY open level of one of its families (a same-family list that is open but out of
    reach is mechanism N5g, which is not built this turn).
    """
    for r in readings:
        fam, val = r[0], r[1]
        if val in (1, 2):
            return True
        for f0, v0 in st:
            if f0 == fam:
                return True
    return False


def sequence(items: list[SeqItem], *, max_depth: int = 6, skip_cost: float = 0.35, family_cost: float = 0.25,
             max_gap: int = 4, beam: int = 48, family_prior: dict[int, str] | None = None,
             restart_cost: float = 0.35, same_family_cost: float = 0.5,
             open_high: bool = True, open_high_cost: float | None = None) -> SeqResult:
    prior = family_prior or {}
    hi_cost = OPEN_HIGH_COST if open_high_cost is None else open_high_cost
    # state: tuple of (family, value) per open level
    states: dict[tuple, tuple[float, list]] = {(): (0.0, [])}  # state -> (cost, placements so far)
    for it in items:
        nxt: dict[tuple, tuple[float, list]] = {}

        def offer(st: tuple, cost: float, pl: list) -> None:
            cur = nxt.get(st)
            if cur is None or cost < cur[0] - 1e-9:
                nxt[st] = (cost, pl)

        for st, (cost, pl) in states.items():
            # reject
            offer(st, cost + it.score, pl + [None])
            midrun = open_high and not _has_legal_move(st, it.readings, max_depth, max_gap)
            for r in it.readings:
                # a reading is (family, value) or (family, value, extra_cost); the surcharge
                # is B.2.3's, and every existing call site is unchanged at the default 0.0
                fam, val = r[0], r[1]
                extra = r[2] if len(r) > 2 else 0.0
                # continue at an open level d (closing deeper ones)
                for d in range(len(st) - 1, -1, -1):
                    f0, v0 = st[d]
                    if f0 != fam:
                        continue
                    gap = val - v0 - 1
                    if gap < 0 or gap > max_gap:
                        continue
                    c = cost + extra + gap * skip_cost
                    offer(st[:d] + ((fam, val),), c, pl + [Placement(d, fam, val, gap)])
                # open a deeper level
                if len(st) < max_depth and val in (1, 2):
                    d = len(st)
                    c = cost + extra + (skip_cost if val == 2 else 0.0)
                    if prior.get(d) and prior[d] != fam:
                        c += family_cost
                    if st and st[-1][0] == fam:
                        c += same_family_cost
                    offer(st + ((fam, val),), c, pl + [Placement(d, fam, val, val - 1)])
                # open a deeper level MID-RUN, at a value above 2 (`seq.open_midrun`)
                elif len(st) < max_depth and val > 2 and midrun and fam not in _NO_MIDRUN_FAMILIES:
                    d = len(st)
                    c = cost + extra + hi_cost
                    if prior.get(d) and prior[d] != fam:
                        c += family_cost
                    if st and st[-1][0] == fam:
                        c += same_family_cost
                    # gap 0, not val-1: the earlier members of this list are not missing,
                    # they are outside the span, so no `seq.gapN` is claimed for them
                    offer(st + ((fam, val),), c, pl + [Placement(d, fam, val, 0, open_high=True)])
                # restart a sibling list at an open level (closing it and everything deeper)
                if val in (1, 2):
                    for d in range(len(st)):
                        c = cost + extra + restart_cost + (skip_cost if val == 2 else 0.0)
                        if prior.get(d) and prior[d] != fam:
                            c += family_cost
                        offer(st[:d] + ((fam, val),), c, pl + [Placement(d, fam, val, val - 1, True)])
        # beam prune
        if len(nxt) > beam:
            nxt = dict(sorted(nxt.items(), key=lambda kv: kv[1][0])[:beam])
        states = nxt
    best_state = min(states.items(), key=lambda kv: kv[1][0])
    cost, pl = best_state[1]
    return SeqResult(placements=pl, cost=cost)


def learn_family_prior(items: list[SeqItem], *, max_depth: int = 6, open_high: bool = True) -> dict[int, str]:
    """Dominant family per depth from a cheap first pass without priors."""
    from collections import Counter, defaultdict

    r = sequence(items, max_depth=max_depth, family_prior=None, open_high=open_high)
    votes: dict[int, Counter] = defaultdict(Counter)
    for p in r.placements:
        if p is not None:
            votes[p.depth][p.family] += 1
    return {d: c.most_common(1)[0][0] for d, c in votes.items()}
