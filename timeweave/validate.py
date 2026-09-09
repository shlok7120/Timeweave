"""Validating a real department's data before the solver ever sees it.

Importing real data is where a project like this usually falls over: a typo in a
faculty id, a room that is too small for every division, a course whose weekly
quota cannot physically fit.  The solver would report "no timetable" for all of
these and leave the coordinator no wiser.

This module checks the input itself and reports problems in the coordinator's
own terms, separating what is *wrong* (an error — the data is inconsistent) from
what is merely *impossible* (a warning — the data is consistent but no timetable
can exist), because the two need different fixes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import List

from .explain import necessary_conditions
from .model import DAYS, LAB_LENGTH, LUNCH_PERIOD, PERIODS, Instance


@dataclass
class Problem:
    severity: str          # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        mark = "ERROR  " if self.severity == "error" else "WARNING"
        return f"{mark}  [{self.where}] {self.message}"


def validate(inst: Instance) -> List[Problem]:
    """Every problem found, worst first. An empty list means the data is usable."""
    out: List[Problem] = []
    faculty_ids = {f.id for f in inst.faculty}
    division_ids = {d.id for d in inst.divisions}

    # ---- structural errors: the data contradicts itself ------------------ #
    for label, ids in [("rooms.csv", [r.id for r in inst.rooms]),
                       ("faculty.csv", [f.id for f in inst.faculty]),
                       ("divisions.csv", [d.id for d in inst.divisions]),
                       ("courses.csv", [c.code for c in inst.courses])]:
        for dup, n in Counter(ids).items():
            if n > 1:
                out.append(Problem("error", label, f"id '{dup}' appears {n} times"))

    if not inst.rooms:
        out.append(Problem("error", "rooms.csv", "no rooms defined"))
    if not inst.divisions:
        out.append(Problem("error", "divisions.csv", "no divisions defined"))
    if not inst.courses:
        out.append(Problem("error", "courses.csv", "no courses defined"))

    for c in inst.courses:
        if c.faculty not in faculty_ids:
            out.append(Problem("error", "courses.csv",
                               f"{c.code} names faculty '{c.faculty}', which is not "
                               f"in faculty.csv"))
        if c.division not in division_ids:
            out.append(Problem("error", "courses.csv",
                               f"{c.code} names division '{c.division}', which is not "
                               f"in divisions.csv"))
        if c.lectures < 0 or c.labs < 0:
            out.append(Problem("error", "courses.csv",
                               f"{c.code} has a negative weekly quota"))
        if c.lectures == 0 and c.labs == 0:
            out.append(Problem("warning", "courses.csv",
                               f"{c.code} needs no sessions and will be ignored"))

    for d in inst.divisions:
        if d.strength <= 0:
            out.append(Problem("error", "divisions.csv",
                               f"division {d.name} has strength {d.strength}"))

    for r in inst.rooms:
        if r.capacity <= 0:
            out.append(Problem("error", "rooms.csv",
                               f"room {r.id} has capacity {r.capacity}"))

    for f in inst.faculty:
        for (d, p) in f.unavailable:
            if not (0 <= d < len(DAYS)) or not (0 <= p < PERIODS):
                out.append(Problem("error", "faculty.csv",
                                   f"{f.id} has an unavailability outside the "
                                   f"timetable grid ({d}, {p})"))

    if any(p.severity == "error" for p in out):
        return out            # the checks below assume the references resolve

    # ---- a variable with an empty domain can never be placed ------------- #
    strengths = {d.id: d.strength for d in inst.divisions}
    for c in inst.courses:
        need = strengths[c.division]
        halls = [r for r in inst.rooms if not r.is_lab and r.capacity >= need]
        labs = [r for r in inst.rooms if r.is_lab and r.capacity >= need]
        div = next(d for d in inst.divisions if d.id == c.division)
        if c.lectures and not halls:
            out.append(Problem("error", "rooms.csv",
                               f"no lecture hall can hold {div.name} "
                               f"({need} students), so {c.code} cannot be scheduled"))
        if c.labs and not labs:
            out.append(Problem("error", "rooms.csv",
                               f"no laboratory can hold {div.name} ({need} students), "
                               f"so the {c.code} lab cannot be scheduled"))

    lab_starts = [p for p in range(PERIODS - LAB_LENGTH + 1)
                  if LUNCH_PERIOD not in range(p, p + LAB_LENGTH)]
    for f in inst.faculty:
        free_days = {d for d in range(len(DAYS))
                     if any((d, p) not in f.unavailable
                            for p in range(PERIODS) if p != LUNCH_PERIOD)}
        if not free_days and any(c.faculty == f.id for c in inst.courses):
            out.append(Problem("error", "faculty.csv",
                               f"{f.name} ({f.id}) is unavailable all week but is "
                               f"assigned courses"))
        teaches_lab = any(c.faculty == f.id and c.labs for c in inst.courses)
        if teaches_lab:
            ok = any(all((d, q) not in f.unavailable for q in range(p, p + LAB_LENGTH))
                     for d in range(len(DAYS)) for p in lab_starts)
            if not ok:
                out.append(Problem("error", "faculty.csv",
                                   f"{f.name} ({f.id}) teaches a laboratory but has no "
                                   f"{LAB_LENGTH} consecutive free periods anywhere in "
                                   f"the week"))

    # ---- counting arguments: consistent data, impossible timetable ------- #
    for diag in necessary_conditions(inst):
        out.append(Problem("warning", "capacity", str(diag)))

    out.sort(key=lambda p: 0 if p.severity == "error" else 1)
    return out


def report(inst: Instance) -> str:
    problems = validate(inst)
    lines = [inst.summary(), ""]
    if not problems:
        lines.append("No problems found — this data can be scheduled.")
        return "\n".join(lines)
    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]
    lines.append(f"{len(errors)} error(s), {len(warnings)} warning(s):")
    lines.append("")
    lines += [f"  {p}" for p in problems]
    lines.append("")
    if errors:
        lines.append("Errors mean the data is inconsistent — fix the CSV files.")
    if warnings and not errors:
        lines.append("The data is consistent, but no timetable can exist as it stands. "
                     "Relax one of the constraints above, or run the explainer for a "
                     "minimal set to change.")
    return "\n".join(lines)
