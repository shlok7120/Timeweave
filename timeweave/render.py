"""Turning a solved assignment into something a human reads."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Sequence

from .csp import CSP
from .model import DAYS, LUNCH_PERIOD, PERIOD_LABELS, PERIODS, Value


def _cells(csp: CSP, assignment: Sequence[Value]):
    """(day, period) -> list of (session, room id) for every placed session."""
    grid: Dict[tuple, List[tuple]] = defaultdict(list)
    for i, v in enumerate(assignment):
        if v is None:
            continue
        s = csp.variables[i]
        d, p, r = v
        room = csp.instance.rooms[r].id
        for q in range(p, p + s.length):
            grid[(d, q)].append((s, room, q == p))
    return grid


def division_grid(csp: CSP, assignment: Sequence[Value], division_id: str) -> List[List[str]]:
    """A period x day table of strings for one division."""
    grid = _cells(csp, assignment)
    table: List[List[str]] = []
    for p in range(PERIODS):
        row = []
        for d in range(len(DAYS)):
            if p == LUNCH_PERIOD:
                row.append("— lunch —")
                continue
            entries = [(s, room, first) for (s, room, first) in grid.get((d, p), [])
                       if s.division == division_id]
            if not entries:
                row.append("")
            else:
                s, room, first = entries[0]
                tag = s.course.split("-")[0]
                kind = "LAB" if s.is_lab else ""
                cont = "" if first else " (cont.)"
                row.append(f"{tag} {kind}".strip() + f"\n{room}{cont}")
        table.append(row)
    return table


def text_timetable(csp: CSP, assignment: Sequence[Value], division_id: str) -> str:
    div = csp.instance.division_by_id()[division_id]
    table = division_grid(csp, assignment, division_id)
    width = 16
    out = [f"Timetable — {div.name} (strength {div.strength})", ""]
    header = "period".ljust(14) + "".join(d.ljust(width) for d in DAYS)
    out.append(header)
    out.append("-" * len(header))
    for p, row in enumerate(table):
        label = PERIOD_LABELS[p].ljust(14)
        first = label + "".join(c.split("\n")[0].ljust(width) for c in row)
        second = " " * 14 + "".join(
            (c.split("\n")[1] if "\n" in c else "").ljust(width) for c in row)
        out.append(first)
        if second.strip():
            out.append(second)
    return "\n".join(out)


def faculty_load(csp: CSP, assignment: Sequence[Value]) -> Dict[str, List[int]]:
    """faculty id -> periods taught on each day."""
    load = {f.id: [0] * len(DAYS) for f in csp.instance.faculty}
    for i, v in enumerate(assignment):
        if v is None:
            continue
        s = csp.variables[i]
        load[s.faculty][v[0]] += s.length
    return load


def to_json(csp: CSP, assignment: Sequence[Value]) -> dict:
    """Everything the web interface needs to draw the timetable."""
    sessions = []
    for i, v in enumerate(assignment):
        if v is None:
            continue
        s = csp.variables[i]
        d, p, r = v
        sessions.append({
            "id": s.id, "course": s.course, "division": s.division,
            "faculty": s.faculty,
            "facultyName": next((f.name for f in csp.instance.faculty
                                 if f.id == s.faculty), s.faculty),
            "kind": s.kind, "day": d, "period": p, "length": s.length,
            "room": csp.instance.rooms[r].id,
        })
    return {
        "days": list(DAYS),
        "periods": list(PERIOD_LABELS),
        "lunch": LUNCH_PERIOD,
        "divisions": [{"id": d.id, "name": d.name, "strength": d.strength}
                      for d in csp.instance.divisions],
        "faculty": [{"id": f.id, "name": f.name} for f in csp.instance.faculty],
        "rooms": [{"id": r.id, "capacity": r.capacity, "isLab": r.is_lab}
                  for r in csp.instance.rooms],
        "sessions": sessions,
    }
