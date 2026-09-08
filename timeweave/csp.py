"""Turning an :class:`~timeweave.model.Instance` into a CSP, plus arc consistency.

Hard constraints
----------------
H1  no faculty member is in two places at once          (binary)
H2  no division attends two sessions at once            (binary)
H3  no room hosts two sessions at once                  (binary)
H4  room capacity >= division strength, labs in labs    (unary -> domain filter)
H5  faculty are only scheduled when available           (unary -> domain filter)
H6  each course meets its weekly quota exactly          (structural, see Instance.sessions)
H7  the lunch period is free for every division         (unary -> domain filter)

Unary constraints are compiled away into the domains, which is what a real CSP
solver does: it is cheaper to never generate an illegal value than to test for
it at every node.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import (
    DAYS, LUNCH_PERIOD, PERIODS, Instance, Session, Value, overlaps,
)

# A relaxable constraint is a hashable tag; QuickXplain searches over these.
Relaxable = Tuple


class CSP:
    """A constraint network over teaching sessions."""

    def __init__(self, instance: Instance, dropped: Optional[Set[Relaxable]] = None):
        self.instance = instance
        self.dropped: Set[Relaxable] = set(dropped or ())
        self.variables: List[Session] = instance.sessions()
        self.n = len(self.variables)
        self.index: Dict[str, int] = {s.id: i for i, s in enumerate(self.variables)}

        self._rooms = instance.rooms
        self._faculty = instance.faculty_by_id()
        self._divisions = instance.division_by_id()

        self.domains: List[List[Value]] = [self._build_domain(s) for s in self.variables]
        self.neighbours: List[Set[int]] = self._build_neighbours()
        self.checks = 0                     # constraint checks, for the benchmark

    # ------------------------------------------------------------------ #
    # Domain construction  (H4, H5, H7 and the day-boundary rule)
    # ------------------------------------------------------------------ #
    def _eligible_rooms(self, s: Session) -> List[int]:
        strength = self._divisions[s.division].strength
        out = []
        for i, room in enumerate(self._rooms):
            if ("labroom",) not in self.dropped:
                if room.is_lab != s.is_lab:
                    continue
            if ("capacity",) not in self.dropped:
                if room.capacity < strength:
                    continue
            out.append(i)
        return out

    def _build_domain(self, s: Session) -> List[Value]:
        fac = self._faculty[s.faculty]
        rooms = self._eligible_rooms(s)
        values: List[Value] = []
        for d in range(len(DAYS)):
            for p in range(PERIODS - s.length + 1):
                span = range(p, p + s.length)
                # H7 - never teach across the lunch period
                if ("lunch",) not in self.dropped and LUNCH_PERIOD in span:
                    continue
                # H5 - faculty availability
                blocked = False
                if ("avail", s.faculty, d) not in self.dropped:
                    for q in span:
                        if (d, q) in fac.unavailable:
                            blocked = True
                            break
                if blocked:
                    continue
                for r in rooms:
                    values.append((d, p, r))
        return values

    # ------------------------------------------------------------------ #
    # Binary constraints  (H1, H2, H3)
    # ------------------------------------------------------------------ #
    def _shares_resource(self, i: int, j: int) -> bool:
        a, b = self.variables[i], self.variables[j]
        if a.faculty == b.faculty or a.division == b.division:
            return True
        # they can still clash on a room if their room sets intersect
        return bool({v[2] for v in self.domains[i]} & {v[2] for v in self.domains[j]})

    def _build_neighbours(self) -> List[Set[int]]:
        nb: List[Set[int]] = [set() for _ in range(self.n)]
        for i in range(self.n):
            for j in range(i + 1, self.n):
                if self._shares_resource(i, j):
                    nb[i].add(j)
                    nb[j].add(i)
        return nb

    def consistent(self, i: int, vi: Value, j: int, vj: Value) -> bool:
        """True when placing variable *i* at *vi* and *j* at *vj* is legal."""
        self.checks += 1
        a, b = self.variables[i], self.variables[j]
        if not overlaps(vi, a.length, vj, b.length):
            return True
        # They overlap in time, so any shared resource is a clash.
        if a.faculty == b.faculty:          # H1
            return False
        if a.division == b.division:        # H2
            return False
        if vi[2] == vj[2]:                  # H3
            return False
        return True

    def assignment_consistent(self, i: int, vi: Value, assignment: Dict[int, Value]) -> bool:
        for j in self.neighbours[i]:
            vj = assignment.get(j)
            if vj is not None and not self.consistent(i, vi, j, vj):
                return False
        return True

    def conflicts(self, i: int, vi: Value, assignment: Sequence[Optional[Value]]) -> int:
        """How many neighbours the value *vi* would clash with (local search)."""
        n = 0
        for j in self.neighbours[i]:
            vj = assignment[j]
            if vj is not None and not self.consistent(i, vi, j, vj):
                n += 1
        return n

    # ------------------------------------------------------------------ #
    # AC-3  (Mackworth, 1977)
    # ------------------------------------------------------------------ #
    def ac3(self, domains: Optional[List[List[Value]]] = None,
            queue: Optional[Iterable[Tuple[int, int]]] = None) -> Tuple[bool, List[List[Value]]]:
        """Enforce arc consistency.

        Returns ``(ok, domains)``.  ``ok`` is False when some domain was
        emptied, which proves the instance has no solution under these
        constraints.  ``domains`` is the pruned copy.
        """
        doms = [list(d) for d in (domains if domains is not None else self.domains)]
        q = deque(queue if queue is not None
                  else ((i, j) for i in range(self.n) for j in self.neighbours[i]))
        in_q = set(q)
        while q:
            i, j = q.popleft()
            in_q.discard((i, j))
            if self._revise(doms, i, j):
                if not doms[i]:
                    return False, doms
                for k in self.neighbours[i]:
                    if k != j and (k, i) not in in_q:
                        q.append((k, i))
                        in_q.add((k, i))
        return True, doms

    def _revise(self, doms: List[List[Value]], i: int, j: int) -> bool:
        """Remove values of Xi that have no support in Xj."""
        revised = False
        keep: List[Value] = []
        for vi in doms[i]:
            supported = False
            for vj in doms[j]:
                if self.consistent(i, vi, j, vj):
                    supported = True
                    break                    # early exit keeps AC-3 affordable
            if supported:
                keep.append(vi)
            else:
                revised = True
        if revised:
            doms[i] = keep
        return revised

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def domain_sizes(self) -> List[int]:
        return [len(d) for d in self.domains]

    def search_space_log10(self) -> float:
        import math
        total = 0.0
        for d in self.domains:
            if not d:
                return float("-inf")
            total += math.log10(len(d))
        return total

    def describe(self) -> str:
        sizes = self.domain_sizes()
        arcs = sum(len(n) for n in self.neighbours)
        return (f"{self.n} variables, domains {min(sizes)}-{max(sizes)} "
                f"(mean {sum(sizes) / len(sizes):.0f}), {arcs} directed arcs, "
                f"search space ~1e{self.search_space_log10():.0f}")
