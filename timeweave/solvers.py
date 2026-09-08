"""Every solving strategy TimeWeave compares.

All solvers implement the same interface::

    solver.solve(csp, budget) -> Result

so the benchmark harness can run them on identical instances without knowing
anything about how they work.  That is the whole point of the comparison.

Systematic search
    * naive backtracking
    * backtracking + MRV / degree / LCV ordering heuristics
    * backtracking + forward checking
    * backtracking + AC-3 maintained during search (MAC)
    * conflict-directed backjumping

Local and population search
    * min-conflicts with sideways moves and random restarts
    * a genetic algorithm

Post-processing
    * a soft-constraint optimiser that keeps hard feasibility
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .constraints import penalty_value
from .csp import CSP
from .model import Value


# --------------------------------------------------------------------------- #
# Budget / results
# --------------------------------------------------------------------------- #

@dataclass
class Budget:
    seconds: float = 60.0
    nodes: int = 5_000_000

    def start(self) -> "Deadline":
        return Deadline(time.perf_counter() + self.seconds, self.nodes)


@dataclass
class Deadline:
    at: float
    nodes_left: int

    def expired(self) -> bool:
        return self.nodes_left <= 0 or time.perf_counter() > self.at

    def tick(self) -> None:
        self.nodes_left -= 1


@dataclass
class Trace:
    """A bounded recording of what the search did, for the interface to replay.

    Search is the part of this project that is hardest to explain in words: MRV
    picking the most constrained session, forward checking collapsing a
    neighbour's options, a dead end unwinding.  Recording it costs nothing when
    switched off and makes the demo self-explanatory when switched on.
    """
    max_events: int = 4000
    events: List[dict] = field(default_factory=list)
    truncated: bool = False

    def record(self, kind: str, **fields) -> None:
        if len(self.events) >= self.max_events:
            self.truncated = True
            return
        self.events.append({"t": kind, **fields})

    def __len__(self) -> int:
        return len(self.events)


@dataclass
class Stats:
    solver: str
    solved: bool = False
    timed_out: bool = False
    nodes: int = 0
    checks: int = 0
    seconds: float = 0.0
    penalty: float = float("nan")
    extra: Dict[str, float] = field(default_factory=dict)

    def row(self) -> Dict[str, object]:
        d = {"solver": self.solver, "solved": self.solved, "timed_out": self.timed_out,
             "nodes": self.nodes, "checks": self.checks,
             "seconds": round(self.seconds, 4), "penalty": self.penalty}
        d.update(self.extra)
        return d


@dataclass
class Result:
    assignment: Optional[List[Value]]
    stats: Stats


class Solver:
    name = "solver"

    def solve(self, csp: CSP, budget: Budget,
              trace: Optional["Trace"] = None) -> Result:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Systematic search
# --------------------------------------------------------------------------- #

class _Failure:
    """Carries a conflict set back up the recursion (used by CBJ)."""
    __slots__ = ("conflict_set",)

    def __init__(self, conflict_set: Set[int]):
        self.conflict_set = conflict_set


class BacktrackingSolver(Solver):
    """One configurable depth-first solver covering five textbook variants."""

    def __init__(self, ordering: str = "static", value_order: str = "static",
                 inference: Optional[str] = None, backjump: bool = False,
                 seed: int = 0, label: Optional[str] = None):
        assert ordering in ("static", "mrv")
        assert value_order in ("static", "lcv")
        assert inference in (None, "fc", "mac")
        if backjump and inference:
            raise ValueError("backjumping is implemented for plain checking only")
        self.ordering = ordering
        self.value_order = value_order
        self.inference = inference
        self.backjump = backjump
        self.seed = seed
        self.name = label or self._auto_label()

    def _auto_label(self) -> str:
        bits = ["Backtracking"]
        if self.ordering == "mrv":
            bits.append("MRV/Deg")
        if self.value_order == "lcv":
            bits.append("LCV")
        if self.inference == "fc":
            bits.append("FC")
        if self.inference == "mac":
            bits.append("MAC (AC-3)")
        if self.backjump:
            bits.append("CBJ")
        return " + ".join(bits) if len(bits) > 1 else "Naive backtracking"

    # -- helpers -------------------------------------------------------- #
    def _select_var(self, csp: CSP, curr: List[List[Value]],
                    assignment: List[Optional[Value]]) -> int:
        unassigned = [i for i in range(csp.n) if assignment[i] is None]
        if self.ordering == "static":
            return unassigned[0]
        # MRV, tie-broken by degree on unassigned neighbours
        best, best_key = unassigned[0], None
        for i in unassigned:
            degree = sum(1 for j in csp.neighbours[i] if assignment[j] is None)
            key = (len(curr[i]), -degree)
            if best_key is None or key < best_key:
                best, best_key = i, key
        return best

    def _order_values(self, csp: CSP, i: int, curr: List[List[Value]],
                      assignment: List[Optional[Value]]) -> List[Value]:
        """Least-constraining-value ordering.

        The naive form costs O(d^2 * degree) per node, which dominates runtime on
        an instance this size.  Instead we index each neighbour's remaining
        values by the (day, period) cells they occupy — built once per node —
        and then read the number of values a candidate would rule out straight
        out of that index.
        """
        values = list(curr[i])
        if self.value_order != "lcv" or len(values) < 2:
            return values

        me = csp.variables[i]
        index = []                       # (always_conflicts, cell -> set of value ids)
        for j in csp.neighbours[i]:
            if assignment[j] is not None:
                continue
            other = csp.variables[j]
            always = (me.faculty == other.faculty) or (me.division == other.division)
            cells: Dict[Tuple[int, int, int], Set[int]] = {}
            for k, (d, p, r) in enumerate(curr[j]):
                for q in range(p, p + other.length):
                    key = (d, q, -1) if always else (d, q, r)
                    cells.setdefault(key, set()).add(k)
            index.append((always, cells))

        def cost(v: Value) -> int:
            d, p, r = v
            removed = 0
            for always, cells in index:
                hit: Set[int] = set()
                for q in range(p, p + me.length):
                    got = cells.get((d, q, -1) if always else (d, q, r))
                    if got:
                        hit |= got
                removed += len(hit)
            return removed

        return sorted(values, key=cost)

    def _forward_check(self, csp: CSP, i: int, v: Value, curr: List[List[Value]],
                       assignment: List[Optional[Value]],
                       trail: List[Tuple[int, Value]]) -> bool:
        for j in csp.neighbours[i]:
            if assignment[j] is not None:
                continue
            keep, gone = [], []
            for vj in curr[j]:
                (keep if csp.consistent(i, v, j, vj) else gone).append(vj)
            if gone:
                curr[j] = keep
                trail.extend((j, x) for x in gone)
                if not keep:
                    return False
        return True

    def _mac(self, csp: CSP, i: int, curr: List[List[Value]],
             assignment: List[Optional[Value]],
             trail: List[Tuple[int, Value]]) -> bool:
        """Maintain arc consistency after assigning *i* (AC-3, in place)."""
        from collections import deque
        q = deque((j, i) for j in csp.neighbours[i] if assignment[j] is None)
        in_q = set(q)
        while q:
            a, b = q.popleft()
            in_q.discard((a, b))
            keep, gone = [], []
            for va in curr[a]:
                if any(csp.consistent(a, va, b, vb) for vb in curr[b]):
                    keep.append(va)
                else:
                    gone.append(va)
            if not gone:
                continue
            curr[a] = keep
            trail.extend((a, x) for x in gone)
            if not keep:
                return False
            for k in csp.neighbours[a]:
                if k != b and assignment[k] is None and (k, a) not in in_q:
                    q.append((k, a))
                    in_q.add((k, a))
        return True

    @staticmethod
    def _undo(curr: List[List[Value]], trail: List[Tuple[int, Value]], mark: int) -> None:
        while len(trail) > mark:
            j, v = trail.pop()
            curr[j].append(v)

    # -- search --------------------------------------------------------- #
    def solve(self, csp: CSP, budget: Budget,
              trace: Optional[Trace] = None) -> Result:
        self._trace = trace
        rng = random.Random(self.seed)
        deadline = budget.start()
        csp.checks = 0
        t0 = time.perf_counter()
        assignment: List[Optional[Value]] = [None] * csp.n
        curr = [list(d) for d in csp.domains]
        trail: List[Tuple[int, Value]] = []
        self._nodes = 0
        self._timed_out = False
        if trace is not None:
            trace.record("start", solver=self.name,
                         domains=[len(d) for d in curr])

        for d in curr:
            rng.shuffle(d)

        if self.inference == "mac":
            before = [len(d) for d in curr]
            ok, pruned = csp.ac3()
            if not ok:
                return Result(None, Stats(self.name, False, False, 0, csp.checks,
                                          time.perf_counter() - t0))
            curr = [list(x) for x in pruned]
            for d in curr:
                rng.shuffle(d)
            if trace is not None:
                trace.record("preprocess",
                             removed=sum(before) - sum(len(d) for d in curr),
                             domains=[len(d) for d in curr])

        outcome = self._search(csp, assignment, curr, trail, deadline)
        elapsed = time.perf_counter() - t0
        solved = outcome is True
        stats = Stats(self.name, solved, self._timed_out, self._nodes, csp.checks, elapsed)
        if solved:
            stats.penalty = penalty_value(csp, assignment)  # type: ignore[arg-type]
            return Result(list(assignment), stats)  # type: ignore[arg-type]
        return Result(None, stats)

    def _search(self, csp: CSP, assignment: List[Optional[Value]],
                curr: List[List[Value]], trail: List[Tuple[int, Value]],
                deadline: Deadline):
        if all(v is not None for v in assignment):
            return True
        if deadline.expired():
            self._timed_out = True
            return _Failure(set(range(csp.n)))

        i = self._select_var(csp, curr, assignment)
        conflict_set: Set[int] = set()

        for v in self._order_values(csp, i, curr, assignment):
            deadline.tick()
            self._nodes += 1
            culprit = self._first_conflict(csp, i, v, assignment)
            if culprit is not None:
                conflict_set.add(culprit)
                continue

            assignment[i] = v
            mark = len(trail)
            if self._trace is not None:
                self._trace.record("assign", var=i, value=list(v),
                                   depth=sum(1 for x in assignment if x is not None),
                                   remaining=len(curr[i]))
            ok = True
            if self.inference == "fc":
                ok = self._forward_check(csp, i, v, curr, assignment, trail)
            elif self.inference == "mac":
                ok = self._mac(csp, i, curr, assignment, trail)
            if self._trace is not None and self.inference and len(trail) > mark:
                pruned_vars = sorted({j for j, _ in trail[mark:]})
                self._trace.record("prune", var=i, count=len(trail) - mark,
                                   vars=pruned_vars[:12], wipeout=not ok)

            if ok:
                out = self._search(csp, assignment, curr, trail, deadline)
                if out is True:
                    return True
                if self.backjump and isinstance(out, _Failure):
                    if i not in out.conflict_set:
                        self._undo(curr, trail, mark)
                        assignment[i] = None
                        return out                     # jump straight past i
                    conflict_set |= out.conflict_set - {i}
                if self._timed_out:
                    self._undo(curr, trail, mark)
                    assignment[i] = None
                    return _Failure(conflict_set)

            self._undo(curr, trail, mark)
            assignment[i] = None
            if self._trace is not None:
                self._trace.record("undo", var=i, value=list(v))

        if self._trace is not None:
            self._trace.record("deadend", var=i)
        return _Failure(conflict_set or {j for j in range(i)})

    @staticmethod
    def _first_conflict(csp: CSP, i: int, v: Value,
                        assignment: Sequence[Optional[Value]]) -> Optional[int]:
        for j in csp.neighbours[i]:
            vj = assignment[j]
            if vj is not None and not csp.consistent(i, v, j, vj):
                return j
        return None


# --------------------------------------------------------------------------- #
# Local search
# --------------------------------------------------------------------------- #

class MinConflictsSolver(Solver):
    """Min-conflicts repair (Minton et al., 1992) with plateau handling."""

    name = "Min-conflicts local search"

    def __init__(self, max_steps: int = 40_000, sideways_cap: int = 60,
                 restarts: int = 40, seed: int = 0):
        self.max_steps = max_steps
        self.sideways_cap = sideways_cap
        self.restarts = restarts
        self.seed = seed

    def solve(self, csp: CSP, budget: Budget,
              trace: Optional[Trace] = None) -> Result:
        rng = random.Random(self.seed)
        deadline = budget.start()
        csp.checks = 0
        t0 = time.perf_counter()
        steps = 0
        restarts_used = 0
        plateau_trace: List[int] = []

        for attempt in range(self.restarts + 1):
            assignment: List[Optional[Value]] = [rng.choice(d) if d else None
                                                 for d in csp.domains]
            if any(v is None for v in assignment):
                break
            sideways = 0
            for _ in range(self.max_steps):
                if deadline.expired():
                    return self._result(csp, None, t0, steps, restarts_used,
                                        plateau_trace, timed_out=True)
                conflicted = [i for i in range(csp.n)
                              if csp.conflicts(i, assignment[i], assignment) > 0]
                plateau_trace.append(len(conflicted))
                if not conflicted:
                    return self._result(csp, assignment, t0, steps, restarts_used,
                                        plateau_trace)
                i = rng.choice(conflicted)
                before = csp.conflicts(i, assignment[i], assignment)
                best, best_c = [], None
                for v in csp.domains[i]:
                    c = csp.conflicts(i, v, assignment)
                    if best_c is None or c < best_c:
                        best, best_c = [v], c
                    elif c == best_c:
                        best.append(v)
                chosen = rng.choice(best)
                steps += 1
                deadline.tick()
                if trace is not None:
                    trace.record("repair", var=i, value=list(chosen),
                                 conflicts=len(conflicted), was=before, now=best_c)
                if best_c is not None and best_c >= before:
                    sideways += 1
                    if sideways > self.sideways_cap:
                        break                          # plateau: restart
                else:
                    sideways = 0
                assignment[i] = chosen
            restarts_used += 1

        return self._result(csp, None, t0, steps, restarts_used, plateau_trace)

    def _result(self, csp, assignment, t0, steps, restarts, trace, timed_out=False):
        elapsed = time.perf_counter() - t0
        st = Stats(self.name, assignment is not None, timed_out, steps, csp.checks, elapsed)
        st.extra["restarts"] = restarts
        st.extra["min_conflicts_seen"] = min(trace) if trace else -1
        if assignment is not None:
            st.penalty = penalty_value(csp, assignment)
        return Result(assignment, st)


# --------------------------------------------------------------------------- #
# Genetic algorithm
# --------------------------------------------------------------------------- #

class GeneticSolver(Solver):
    """Population search over complete assignments."""

    name = "Genetic algorithm"

    def __init__(self, population: int = 40, generations: int = 300,
                 crossover: float = 0.85, mutation: float = 0.03,
                 elite: int = 2, tournament: int = 3, hard_weight: float = 100.0,
                 seed: int = 0):
        self.population = population
        self.generations = generations
        self.crossover = crossover
        self.mutation = mutation
        self.elite = elite
        self.tournament = tournament
        self.hard_weight = hard_weight
        self.seed = seed

    # chromosome = list of value indices, one gene per variable
    def _decode(self, csp: CSP, chrom: Sequence[int]) -> List[Value]:
        return [csp.domains[i][g] for i, g in enumerate(chrom)]

    def _hard_count(self, csp: CSP, values: Sequence[Value]) -> int:
        bad = 0
        for i in range(csp.n):
            for j in csp.neighbours[i]:
                if j > i and not csp.consistent(i, values[i], j, values[j]):
                    bad += 1
        return bad

    def _fitness(self, csp: CSP, chrom: Sequence[int]) -> Tuple[float, int, List[Value]]:
        values = self._decode(csp, chrom)
        hard = self._hard_count(csp, values)
        soft = penalty_value(csp, values)
        return self.hard_weight * hard + soft, hard, values

    def solve(self, csp: CSP, budget: Budget,
              trace: Optional[Trace] = None) -> Result:
        rng = random.Random(self.seed)
        deadline = budget.start()
        csp.checks = 0
        t0 = time.perf_counter()
        if any(not d for d in csp.domains):
            return Result(None, Stats(self.name, False, False, 0, 0, 0.0))

        sizes = [len(d) for d in csp.domains]
        pop = [[rng.randrange(s) for s in sizes] for _ in range(self.population)]
        scored = [(self._fitness(csp, c), c) for c in pop]
        best = min(scored, key=lambda x: x[0][0])
        generations = 0
        timed_out = False

        for gen in range(self.generations):
            if deadline.expired():
                timed_out = True
                break
            generations = gen + 1
            deadline.tick()
            scored.sort(key=lambda x: x[0][0])
            if scored[0][0][0] < best[0][0]:
                best = scored[0]
            if best[0][1] == 0:
                break                       # a hard-feasible timetable exists
            nxt = [c for _, c in scored[:self.elite]]
            while len(nxt) < self.population:
                p1 = self._tournament(rng, scored)
                p2 = self._tournament(rng, scored)
                child = list(p1)
                if rng.random() < self.crossover:
                    child = [a if rng.random() < 0.5 else b for a, b in zip(p1, p2)]
                for k in range(len(child)):
                    if rng.random() < self.mutation:
                        child[k] = rng.randrange(sizes[k])
                nxt.append(child)
            scored = [(self._fitness(csp, c), c) for c in nxt]
            if trace is not None:
                trace.record("generation", gen=generations,
                             best=round(scored[0][0][0], 1) if scored else None,
                             hard=scored[0][0][1] if scored else None)

        scored.sort(key=lambda x: x[0][0])
        if scored[0][0][0] < best[0][0]:
            best = scored[0]

        (score, hard, values), _chrom = best
        elapsed = time.perf_counter() - t0
        solved = hard == 0
        st = Stats(self.name, solved, timed_out, generations, csp.checks, elapsed)
        st.extra["generations"] = generations
        st.extra["hard_violations"] = hard
        if solved:
            st.penalty = penalty_value(csp, values)
            return Result(values, st)
        return Result(None, st)

    def _tournament(self, rng: random.Random, scored) -> List[int]:
        pick = min(rng.sample(scored, min(self.tournament, len(scored))),
                   key=lambda x: x[0][0])
        return pick[1]


# --------------------------------------------------------------------------- #
# Soft-constraint optimiser
# --------------------------------------------------------------------------- #

class SoftOptimiser:
    """Hill-climb on the soft penalty while never breaking a hard constraint."""

    name = "Soft-constraint optimiser"

    def __init__(self, steps: int = 3000, seed: int = 0):
        self.steps = steps
        self.seed = seed

    def improve(self, csp: CSP, assignment: List[Value],
                weights: Optional[Dict[str, float]] = None,
                budget: Optional[Budget] = None) -> Tuple[List[Value], Stats]:
        rng = random.Random(self.seed)
        deadline = (budget or Budget(seconds=20.0)).start()
        t0 = time.perf_counter()
        current = list(assignment)
        best_cost = penalty_value(csp, current, weights)
        start_cost = best_cost
        moves = 0

        for _ in range(self.steps):
            if deadline.expired():
                break
            i = rng.randrange(csp.n)
            old = current[i]
            candidates = [v for v in csp.domains[i] if v != old]
            if not candidates:
                continue
            rng.shuffle(candidates)
            for v in candidates[:12]:
                current[i] = v
                if csp.conflicts(i, v, current) > 0:
                    continue
                cost = penalty_value(csp, current, weights)
                if cost < best_cost:
                    best_cost = cost
                    old = v
                    moves += 1
                    break
            current[i] = old

        st = Stats(self.name, True, False, moves, csp.checks, time.perf_counter() - t0)
        st.penalty = best_cost
        st.extra["penalty_before"] = start_cost
        st.extra["improvement"] = start_cost - best_cost
        return current, st


# --------------------------------------------------------------------------- #
# The registry the benchmark and the web API both use
# --------------------------------------------------------------------------- #

def all_solvers(seed: int = 0) -> List[Solver]:
    return [
        BacktrackingSolver(seed=seed, label="1. Naive backtracking"),
        BacktrackingSolver(ordering="mrv", value_order="lcv", seed=seed,
                           label="2. BT + MRV / Degree / LCV"),
        BacktrackingSolver(ordering="mrv", inference="fc", seed=seed,
                           label="3. BT + forward checking"),
        BacktrackingSolver(ordering="mrv", inference="mac", seed=seed,
                           label="4. BT + AC-3 (MAC)"),
        BacktrackingSolver(ordering="mrv", backjump=True, seed=seed,
                           label="5. BT + conflict-directed backjumping"),
        MinConflictsSolver(seed=seed),
        GeneticSolver(seed=seed),
    ]


SOLVER_KEYS = {
    "naive": lambda s: BacktrackingSolver(seed=s, label="Naive backtracking"),
    "heuristics": lambda s: BacktrackingSolver(ordering="mrv", value_order="lcv", seed=s,
                                               label="BT + MRV / Degree / LCV"),
    "fc": lambda s: BacktrackingSolver(ordering="mrv", inference="fc", seed=s,
                                       label="BT + forward checking"),
    "mac": lambda s: BacktrackingSolver(ordering="mrv", inference="mac", seed=s,
                                        label="BT + AC-3 (MAC)"),
    "cbj": lambda s: BacktrackingSolver(ordering="mrv", backjump=True, seed=s,
                                        label="BT + conflict-directed backjumping"),
    "minconflicts": lambda s: MinConflictsSolver(seed=s),
    "genetic": lambda s: GeneticSolver(seed=s),
}
