"""Hard-constraint auditing and the weighted soft-constraint penalty.

The solvers guarantee hard feasibility by construction; :func:`hard_violations`
exists so the test suite and the benchmark can *independently* re-check every
solution rather than trusting the search that produced it.

Soft constraints
----------------
S1  minimise gaps in a division's day
S2  avoid more than two consecutive lectures for a division
S3  prefer laboratory sessions in the afternoon
S4  balance each faculty member's load across the week
S5  avoid using the last period of the day for the same course repeatedly
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .csp import CSP
from .model import DAYS, PERIODS, Value, overlaps

# Weights are deliberately exposed: the ID3 preference learner rewrites them.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "S1_gaps": 3.0,
    "S2_consecutive": 2.0,
    "S3_lab_afternoon": 1.0,
    "S4_faculty_balance": 2.0,
    "S5_last_period": 1.0,
}


@dataclass
class PenaltyReport:
    total: float
    parts: Dict[str, float]
    raw: Dict[str, int]

    def __str__(self) -> str:
        bits = ", ".join(f"{k}={v}" for k, v in sorted(self.raw.items()))
        return f"penalty {self.total:.1f}  ({bits})"


# --------------------------------------------------------------------------- #
# Hard constraints
# --------------------------------------------------------------------------- #

def hard_violations(csp: CSP, assignment: Sequence[Value]) -> List[str]:
    """Re-check every hard constraint from scratch. Empty list == valid."""
    problems: List[str] = []
    vars_ = csp.variables

    if any(v is None for v in assignment):
        problems.append("H6: not every session was placed")
        return problems

    for i in range(csp.n):
        a, va = vars_[i], assignment[i]
        # H4/H5/H7 are compiled into the domains, so verify membership directly.
        if va not in csp.domains[i]:
            problems.append(f"H4/H5/H7: {a.id} placed at an illegal slot {va}")
        for j in range(i + 1, csp.n):
            b, vb = vars_[j], assignment[j]
            if not overlaps(va, a.length, vb, b.length):
                continue
            if a.faculty == b.faculty:
                problems.append(f"H1: {a.id} and {b.id} both need faculty {a.faculty}")
            if a.division == b.division:
                problems.append(f"H2: {a.id} and {b.id} both need division {a.division}")
            if va[2] == vb[2]:
                room = csp.instance.rooms[va[2]].id
                problems.append(f"H3: {a.id} and {b.id} both need room {room}")
    return problems


def is_feasible(csp: CSP, assignment: Sequence[Value]) -> bool:
    return not hard_violations(csp, assignment)


# --------------------------------------------------------------------------- #
# Soft constraints
# --------------------------------------------------------------------------- #

def _occupancy(csp: CSP, assignment: Sequence[Value]):
    """day -> division -> sorted list of (period, session index)."""
    by_div = defaultdict(lambda: defaultdict(list))
    by_fac = defaultdict(lambda: defaultdict(int))
    for i, v in enumerate(assignment):
        if v is None:
            continue
        s = csp.variables[i]
        d, p, _ = v
        for q in range(p, p + s.length):
            by_div[d][s.division].append((q, i))
            by_fac[s.faculty][d] += 1
    for d in by_div:
        for div in by_div[d]:
            by_div[d][div].sort()
    return by_div, by_fac


def soft_penalty(csp: CSP, assignment: Sequence[Value],
                 weights: Dict[str, float] | None = None) -> PenaltyReport:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    raw = {k: 0 for k in DEFAULT_WEIGHTS}
    by_div, by_fac = _occupancy(csp, assignment)

    # S1 gaps, S2 long runs
    for d, divs in by_div.items():
        for div, entries in divs.items():
            periods = sorted({p for p, _ in entries})
            if not periods:
                continue
            for a, b in zip(periods, periods[1:]):
                gap = b - a - 1
                if gap > 0:
                    # a free lunch period is not a gap
                    span = set(range(a + 1, b))
                    raw["S1_gaps"] += len(span - {4})
            run = 1
            for a, b in zip(periods, periods[1:]):
                run = run + 1 if b == a + 1 else 1
                if run > 3:
                    raw["S2_consecutive"] += 1

    # S3 labs in the afternoon (period >= 5)
    for i, v in enumerate(assignment):
        if v is None:
            continue
        if csp.variables[i].is_lab and v[1] < 5:
            raw["S3_lab_afternoon"] += 1

    # S4 faculty load spread across the week
    for fac, per_day in by_fac.items():
        loads = [per_day.get(d, 0) for d in range(len(DAYS))]
        if loads:
            raw["S4_faculty_balance"] += max(loads) - min(loads)

    # S5 the same course landing in the last period repeatedly
    last_period = PERIODS - 1
    per_course = defaultdict(int)
    for i, v in enumerate(assignment):
        if v is None:
            continue
        s = csp.variables[i]
        if v[1] + s.length - 1 == last_period:
            per_course[s.course] += 1
    raw["S5_last_period"] += sum(max(0, c - 1) for c in per_course.values())

    parts = {k: raw[k] * w[k] for k in raw}
    return PenaltyReport(total=sum(parts.values()), parts=parts, raw=raw)


def penalty_value(csp: CSP, assignment: Sequence[Value],
                  weights: Dict[str, float] | None = None) -> float:
    return soft_penalty(csp, assignment, weights).total
