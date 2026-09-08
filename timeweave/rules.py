"""A forward-chaining rule base, and the explanation facility built on it.

`csp.py` compiles the unary constraints straight into the domains, which is fast
but opaque: when a coordinator asks *"why can't I put the AI lab on Tuesday at
11?"* a list of legal tuples is no answer.

This module derives the same restrictions declaratively, by firing rules to a
fixpoint and recording which rule produced which fact.  The explanation facility
then replays that provenance in English.  ``tests/test_knowledge_layer.py`` asserts that
the rule base and the domain compiler agree on every session — two independent
routes to the same set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Sequence, Set, Tuple

from .model import DAYS, LUNCH_PERIOD, PERIODS, Instance, Value

Fact = Tuple


@dataclass
class Derivation:
    fact: Fact
    rule: str
    premises: Tuple[Fact, ...]


@dataclass
class Rule:
    id: str
    description: str
    fire: Callable[["FactBase"], Iterable[Tuple[Fact, Tuple[Fact, ...]]]]


class FactBase:
    def __init__(self, facts: Iterable[Fact] = ()):
        self.facts: Set[Fact] = set(facts)
        self.by_head: Dict[str, List[Fact]] = {}
        self.why: Dict[Fact, Derivation] = {}
        for f in self.facts:
            self.by_head.setdefault(f[0], []).append(f)

    def add(self, fact: Fact, rule: str, premises: Tuple[Fact, ...]) -> bool:
        if fact in self.facts:
            return False
        self.facts.add(fact)
        self.by_head.setdefault(fact[0], []).append(fact)
        self.why[fact] = Derivation(fact, rule, premises)
        return True

    def of(self, head: str) -> List[Fact]:
        return self.by_head.get(head, [])

    def has(self, fact: Fact) -> bool:
        return fact in self.facts


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #

def _r1_needs_lab(fb: FactBase):
    for f in fb.of("session"):
        _, sid, _course, _div, _fid, kind, _len = f
        if kind == "lab":
            yield ("needs_lab", sid), (f,)


def _r2_lab_room_only(fb: FactBase):
    for f in fb.of("needs_lab"):
        _, sid = f
        for r in fb.of("room"):
            _, ridx, _rid, _cap, is_lab = r
            if not is_lab:
                yield ("forbidden_room", sid, ridx), (f, r)


def _r3_lecture_not_in_lab(fb: FactBase):
    for f in fb.of("session"):
        _, sid, _c, _d, _fid, kind, _l = f
        if kind == "lecture":
            for r in fb.of("room"):
                _, ridx, _rid, _cap, is_lab = r
                if is_lab:
                    yield ("forbidden_room", sid, ridx), (f, r)


def _r4_capacity(fb: FactBase):
    strength = {d[1]: d[2] for d in fb.of("division")}
    for f in fb.of("session"):
        _, sid, _c, div, _fid, _k, _l = f
        need = strength[div]
        for r in fb.of("room"):
            _, ridx, _rid, cap, _is_lab = r
            if cap < need:
                yield ("forbidden_room", sid, ridx), (f, r)


def _r5_faculty_unavailable(fb: FactBase):
    blocks: Dict[str, Set[Tuple[int, int]]] = {}
    src: Dict[Tuple[str, int, int], Fact] = {}
    for u in fb.of("faculty_unavailable"):
        _, fid, d, p = u
        blocks.setdefault(fid, set()).add((d, p))
        src[(fid, d, p)] = u
    for f in fb.of("session"):
        _, sid, _c, _div, fid, _k, length = f
        for (d, p) in blocks.get(fid, ()):
            for start in range(max(0, p - length + 1), p + 1):
                if start + length <= PERIODS:
                    yield ("blocked_start", sid, d, start), (f, src[(fid, d, p)])


def _r6_lunch(fb: FactBase):
    for f in fb.of("session"):
        _, sid, _c, _div, _fid, _k, length = f
        for d in range(len(DAYS)):
            for start in range(PERIODS - length + 1):
                if LUNCH_PERIOD in range(start, start + length):
                    yield ("blocked_start", sid, d, start), (f, ("policy", "lunch"))


def _r7_day_boundary(fb: FactBase):
    for f in fb.of("session"):
        _, sid, _c, _div, _fid, _k, length = f
        for d in range(len(DAYS)):
            for start in range(PERIODS - length + 1, PERIODS):
                yield ("blocked_start", sid, d, start), (f, ("policy", "day_end"))


RULES: List[Rule] = [
    Rule("R1", "a session of kind 'lab' needs a laboratory", _r1_needs_lab),
    Rule("R2", "a session needing a laboratory cannot use a lecture hall", _r2_lab_room_only),
    Rule("R3", "a lecture is not scheduled inside a laboratory", _r3_lecture_not_in_lab),
    Rule("R4", "a room smaller than the division cannot host it (H4)", _r4_capacity),
    Rule("R5", "a session cannot overlap its faculty's unavailability (H5)", _r5_faculty_unavailable),
    Rule("R6", "no session may occupy the lunch period (H7)", _r6_lunch),
    Rule("R7", "a session must finish before the day ends", _r7_day_boundary),
]


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

def initial_facts(inst: Instance) -> List[Fact]:
    facts: List[Fact] = []
    for d in inst.divisions:
        facts.append(("division", d.id, d.strength))
    for i, r in enumerate(inst.rooms):
        facts.append(("room", i, r.id, r.capacity, r.is_lab))
    for f in inst.faculty:
        for (d, p) in sorted(f.unavailable):
            facts.append(("faculty_unavailable", f.id, d, p))
    for s in inst.sessions():
        facts.append(("session", s.id, s.course, s.division, s.faculty, s.kind, s.length))
    return facts


def forward_chain(inst: Instance, rules: Sequence[Rule] = RULES) -> Tuple[FactBase, int]:
    """Fire every rule until no new fact appears. Returns the facts and the pass count."""
    fb = FactBase(initial_facts(inst))
    passes = 0
    changed = True
    while changed:
        changed = False
        passes += 1
        for rule in rules:
            for fact, premises in rule.fire(fb):
                if fb.add(fact, rule.id, tuple(premises)):
                    changed = True
    return fb, passes


class Explainer:
    """Answers 'why is this slot unavailable?' from the derived facts."""

    def __init__(self, inst: Instance):
        self.inst = inst
        self.facts, self.passes = forward_chain(inst)
        self._rule_text = {r.id: r.description for r in RULES}

    # -- the derived legal domain -------------------------------------- #
    def legal_values(self, session_id: str, length: int) -> List[Value]:
        out: List[Value] = []
        nrooms = len(self.inst.rooms)
        for d in range(len(DAYS)):
            for p in range(PERIODS):
                if self.facts.has(("blocked_start", session_id, d, p)):
                    continue
                if p + length > PERIODS:
                    continue
                for r in range(nrooms):
                    if self.facts.has(("forbidden_room", session_id, r)):
                        continue
                    out.append((d, p, r))
        return out

    # -- explanation ---------------------------------------------------- #
    def explain(self, session_id: str, value: Value) -> List[str]:
        """Reasons this slot is not allowed. Empty list means it is allowed."""
        d, p, r = value
        reasons: List[str] = []
        blocked = ("blocked_start", session_id, d, p)
        if self.facts.has(blocked):
            reasons.append(self._render(blocked))
        forbidden = ("forbidden_room", session_id, r)
        if self.facts.has(forbidden):
            reasons.append(self._render(forbidden))
        return reasons

    def _render(self, fact: Fact) -> str:
        why = self.facts.why.get(fact)
        if why is None:
            return "given in the input data"
        rule = self._rule_text.get(why.rule, why.rule)
        detail = ""
        for prem in why.premises:
            if prem and prem[0] == "faculty_unavailable":
                _, fid, d, p = prem
                name = next((f.name for f in self.inst.faculty if f.id == fid), fid)
                detail = f" — {name} has declared {DAYS[d]} period {p + 1} unavailable"
            elif prem and prem[0] == "room":
                detail = f" — room {prem[2]} (capacity {prem[3]}," \
                         f" {'laboratory' if prem[4] else 'lecture hall'})"
            elif prem and prem[0] == "policy" and prem[1] == "lunch":
                detail = " — this span covers the lunch period"
            elif prem and prem[0] == "policy" and prem[1] == "day_end":
                detail = " — the session would run past the end of the day"
        return f"[{why.rule}] {rule}{detail}"

    def summary(self) -> str:
        derived = len(self.facts.facts) - len(initial_facts(self.inst))
        return (f"{len(RULES)} rules reached a fixpoint in {self.passes} passes, "
                f"deriving {derived} facts")
