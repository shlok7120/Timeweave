"""Explaining infeasibility instead of failing silently.

Two mechanisms, cheapest first:

1. :func:`necessary_conditions` — counting arguments that prove no timetable
   can exist without running any search at all ("Prof. Iyer owes 25 periods a
   week but has declared only 23 free").
2. :func:`quickxplain` — Junker's (2004) divide-and-conquer search for a
   *minimal* set of relaxable constraints whose removal restores feasibility.
   This is what the coordinator actually needs: not "no solution", but "drop
   any one of these and it works".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Set, Tuple

from .csp import CSP, Relaxable
from .model import DAYS, LAB_LENGTH, LUNCH_PERIOD, PERIODS, Instance
from .solvers import BacktrackingSolver, Budget, MinConflictsSolver

TEACHING_PERIODS_PER_WEEK = len(DAYS) * (PERIODS - 1)


# --------------------------------------------------------------------------- #
# 1. Counting arguments
# --------------------------------------------------------------------------- #

@dataclass
class Diagnosis:
    kind: str
    subject: str
    required: int
    available: int
    message: str

    def __str__(self) -> str:
        return self.message


def necessary_conditions(inst: Instance) -> List[Diagnosis]:
    """Cheap proofs of infeasibility. An empty list means "not obviously impossible"."""
    out: List[Diagnosis] = []

    # (a) every faculty member must have room in the week for their own load
    load: Dict[str, int] = {}
    for c in inst.courses:
        load[c.faculty] = load.get(c.faculty, 0) + c.lectures + c.labs * LAB_LENGTH
    for f in inst.faculty:
        needed = load.get(f.id, 0)
        blocked = len({(d, p) for (d, p) in f.unavailable if p != LUNCH_PERIOD})
        free = TEACHING_PERIODS_PER_WEEK - blocked
        if needed > free:
            out.append(Diagnosis(
                "faculty", f.id, needed, free,
                f"{f.name} ({f.id}) owes {needed} periods a week but is available "
                f"for only {free} — no timetable can exist."))

    # (b) every division must fit its own load into the week
    per_div: Dict[str, int] = {}
    for c in inst.courses:
        per_div[c.division] = per_div.get(c.division, 0) + c.lectures + c.labs * LAB_LENGTH
    for d in inst.divisions:
        needed = per_div.get(d.id, 0)
        if needed > TEACHING_PERIODS_PER_WEEK:
            out.append(Diagnosis(
                "division", d.id, needed, TEACHING_PERIODS_PER_WEEK,
                f"Division {d.name} needs {needed} periods a week but the week "
                f"only has {TEACHING_PERIODS_PER_WEEK} teaching periods."))

    # (c) rooms of each type must have enough total capacity
    lab_periods = sum(c.labs * LAB_LENGTH for c in inst.courses)
    lecture_periods = sum(c.lectures for c in inst.courses)
    labs = [r for r in inst.rooms if r.is_lab]
    halls = [r for r in inst.rooms if not r.is_lab]
    # a lab must start at a period whose whole span avoids lunch and the day end
    lab_starts = len([p for p in range(PERIODS - LAB_LENGTH + 1)
                      if LUNCH_PERIOD not in range(p, p + LAB_LENGTH)])
    lab_capacity = len(labs) * len(DAYS) * lab_starts * LAB_LENGTH
    if lab_periods > lab_capacity:
        out.append(Diagnosis(
            "rooms", "laboratories", lab_periods, lab_capacity,
            f"Laboratory sessions need {lab_periods} room-periods but the "
            f"{len(labs)} lab room(s) offer only {lab_capacity}."))
    hall_capacity = len(halls) * TEACHING_PERIODS_PER_WEEK
    if lecture_periods > hall_capacity:
        out.append(Diagnosis(
            "rooms", "lecture halls", lecture_periods, hall_capacity,
            f"Lectures need {lecture_periods} room-periods but the {len(halls)} "
            f"lecture hall(s) offer only {hall_capacity}."))
    return out


# --------------------------------------------------------------------------- #
# 2. QuickXplain
# --------------------------------------------------------------------------- #

def relaxable_constraints(inst: Instance) -> List[Relaxable]:
    """Everything a coordinator could plausibly be asked to give up.

    Faculty availability is grouped *per day* rather than per period, so the
    answer reads like "free up Prof. Iyer on Wednesday" instead of listing
    thirty individual slots.
    """
    tags: List[Relaxable] = []
    for f in inst.faculty:
        days = sorted({d for d, _ in f.unavailable})
        tags.extend(("avail", f.id, d) for d in days)
    tags.append(("lunch",))
    tags.append(("labroom",))
    tags.append(("capacity",))
    return tags


def describe(tag: Relaxable, inst: Instance) -> str:
    if tag[0] == "avail":
        name = next((f.name for f in inst.faculty if f.id == tag[1]), tag[1])
        return f"{name} ({tag[1]}) is unavailable on {DAYS[tag[2]]}"
    return {
        "lunch": "the lunch period is kept free",
        "labroom": "laboratory sessions must use a laboratory",
        "capacity": "rooms must be large enough for the division",
    }[tag[0]]


class FeasibilityOracle:
    """Answers 'is this relaxation solvable?' under a fixed budget.

    The oracle is *incomplete*: a budgeted search that finds nothing has not
    proved anything.  Two things keep that from corrupting the answer — results
    are cached (QuickXplain asks the same question repeatedly), and every
    negative answer is reached only after both a complete search with forward
    checking and a min-conflicts repair pass have failed.  Timeouts are counted
    and reported, and :func:`quickxplain` verifies minimality afterwards rather
    than trusting the recursion.
    """

    def __init__(self, inst: Instance, seconds: float = 3.0, nodes: int = 250_000):
        self.inst = inst
        self.budget = Budget(seconds=seconds, nodes=nodes)
        self.calls = 0
        self.timeouts = 0
        self._cache: Dict[frozenset, bool] = {}

    def solvable(self, enforced: Sequence[Relaxable], all_tags: Sequence[Relaxable]):
        """True when the instance is solvable with only *enforced* still in force."""
        key = frozenset(enforced)
        if key in self._cache:
            return self._cache[key], None
        self.calls += 1

        dropped = set(all_tags) - key
        # A counting argument is exact and costs nothing, so try it before any
        # search: it turns many would-be timeouts into a definite "impossible".
        if self._counting_says_impossible(dropped):
            self._cache[key] = False
            return False, None
        csp = CSP(self.inst, dropped=dropped)
        if any(not d for d in csp.domains):
            self._cache[key] = False
            return False, None
        ok, _ = csp.ac3()
        if not ok:
            self._cache[key] = False
            return False, None

        res = BacktrackingSolver(ordering="mrv", inference="fc").solve(csp, self.budget)
        if not res.stats.solved:
            # second opinion: local search is often faster at *finding* a solution
            res = MinConflictsSolver(max_steps=6000, restarts=6).solve(csp, self.budget)
            if not res.stats.solved:
                self.timeouts += 1
        self._cache[key] = res.stats.solved
        return res.stats.solved, res.assignment


    def _counting_says_impossible(self, dropped: Set[Relaxable]) -> bool:
        """Faculty load versus availability, with the dropped relaxations applied."""
        load: Dict[str, int] = {}
        for c in self.inst.courses:
            load[c.faculty] = load.get(c.faculty, 0) + c.lectures + c.labs * LAB_LENGTH
        for f in self.inst.faculty:
            needed = load.get(f.id, 0)
            blocked = sum(1 for (d, p) in f.unavailable
                          if p != LUNCH_PERIOD and ("avail", f.id, d) not in dropped)
            if needed > TEACHING_PERIODS_PER_WEEK - blocked:
                return True
        return False


@dataclass
class Explanation:
    feasible: bool
    counting: List[Diagnosis]
    conflict: List[Relaxable]
    readable: List[str]
    oracle_calls: int
    oracle_timeouts: int
    proven_minimal: bool = True

    def text(self) -> str:
        if self.feasible:
            return "A timetable exists for these constraints."
        lines = ["No timetable exists."]
        if self.counting:
            lines.append("Proved by counting, without any search:")
            lines += [f"  - {d}" for d in self.counting]
        if self.readable:
            qualifier = "Minimal" if self.proven_minimal else "Smallest found"
            lines.append(f"{qualifier} set of constraints in conflict "
                         "(relax all of these and a timetable exists again):")
            lines += [f"  - {r}" for r in self.readable]
        if not self.proven_minimal:
            lines.append("  (a feasibility check hit its budget, so minimality "
                         "is not proved for this set)")
        return "\n".join(lines)


def quickxplain(inst: Instance, seconds: float = 3.0) -> Explanation:
    """Junker's QuickXplain over the relaxable constraints of an instance."""
    counting = necessary_conditions(inst)
    tags = relaxable_constraints(inst)
    oracle = FeasibilityOracle(inst, seconds=seconds)

    ok, _ = oracle.solvable(tags, tags)
    if ok:
        return Explanation(True, counting, [], [], oracle.calls, oracle.timeouts)

    # Is it infeasible even with everything relaxed?  Then no relaxation helps.
    ok_empty, _ = oracle.solvable([], tags)
    if not ok_empty:
        readable = [str(d) for d in counting] or \
            ["the structural load alone cannot fit into the week"]
        return Explanation(False, counting, [], readable,
                           oracle.calls, oracle.timeouts)

    conflict = _qx(oracle, tags, background=[], delta=[], candidates=list(tags))
    conflict, verified = _shrink(oracle, tags, conflict)
    return Explanation(False, counting, conflict,
                       [describe(t, inst) for t in conflict],
                       oracle.calls, oracle.timeouts,
                       proven_minimal=verified and oracle.timeouts == 0)


def _shrink(oracle: FeasibilityOracle, all_tags: Sequence[Relaxable],
            conflict: List[Relaxable]) -> Tuple[List[Relaxable], bool]:
    """Drop any member the conflict does not actually need.

    QuickXplain is minimal only when its consistency oracle is exact.  Ours is a
    budgeted search, so a single timeout can leave a redundant constraint in the
    set.  This pass tests each member directly and reports whether the result is
    now genuinely minimal.
    """
    result = list(conflict)
    for tag in list(result):
        candidate = [t for t in result if t != tag]
        enforced = [t for t in all_tags if t not in candidate]
        ok, _ = oracle.solvable(enforced, all_tags)
        if ok:
            result = candidate            # tag was not needed
    # verify: the surviving set must be sufficient, and every member necessary
    enforced_all = [t for t in all_tags if t not in result]
    sufficient, _ = oracle.solvable(enforced_all, all_tags)
    necessary = True
    for tag in result:
        enforced = [t for t in all_tags if t not in [x for x in result if x != tag]]
        ok, _ = oracle.solvable(enforced, all_tags)
        if ok:
            necessary = False
            break
    return result, bool(sufficient and necessary)


def _qx(oracle: FeasibilityOracle, all_tags: Sequence[Relaxable],
        background: List[Relaxable], delta: List[Relaxable],
        candidates: List[Relaxable]) -> List[Relaxable]:
    if delta:
        ok, _ = oracle.solvable(background, all_tags)
        if not ok:
            return []
    if len(candidates) == 1:
        return list(candidates)
    half = len(candidates) // 2
    c1, c2 = candidates[:half], candidates[half:]
    d1 = _qx(oracle, all_tags, background + c1, c1, c2)
    d2 = _qx(oracle, all_tags, background + d1, d1, c1)
    return d1 + d2
