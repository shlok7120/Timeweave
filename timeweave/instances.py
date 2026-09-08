"""Building problem instances: a synthetic generator and CSV import/export.

The generator is what makes the benchmark reproducible — every instance is a
pure function of ``(divisions, seed)``, so a result table can be regenerated
exactly.  The CSV loader is what lets a real department feed in its own data.
"""

from __future__ import annotations

import csv
import os
import random
from typing import List, Optional, Tuple

from .model import DAYS, LUNCH_PERIOD, PERIODS, Course, Division, Faculty, Instance, Room

SUBJECTS = [
    ("AI", "Artificial Intelligence"),
    ("DBMS", "Database Management Systems"),
    ("CN", "Computer Networks"),
    ("OS", "Operating Systems"),
    ("SE", "Software Engineering"),
    ("TOC", "Theory of Computation"),
    ("ML", "Machine Learning"),
    ("CG", "Computer Graphics"),
]

FIRST_NAMES = ["Anita", "Rahul", "Meera", "Sanjay", "Priya", "Vikram", "Neha",
               "Arjun", "Kavita", "Deepak", "Shalini", "Rohit"]
LAST_NAMES = ["Deshmukh", "Iyer", "Kulkarni", "Bhatt", "Nair", "Chauhan",
              "Rane", "Joshi", "Menon", "Patil", "Sharma", "Gupta"]


TEACHING_SLOTS_PER_WEEK = len(DAYS) * (PERIODS - 1)     # the lunch period is never used


def default_unavailability(divisions: int, lectures: int, labs_for: int,
                           tightness: float = 0.75) -> int:
    """How much unavailability to give each faculty member.

    A faculty member teaching one course in every division owes
    ``divisions * (lectures + 2)`` periods a week in the worst case, leaving
    ``35 - that`` periods of slack.  We consume ``tightness`` of the slack, which
    calibrated (see ``scripts/calibrate.py``) to the useful regime: forward
    checking always succeeds, while naive backtracking frequently does not.

    Pushing ``tightness`` past ~0.85 tips instances into genuine infeasibility,
    which :func:`timeweave.explain.necessary_conditions` then detects by counting.
    """
    worst = divisions * (lectures + 2 if labs_for > 0 else lectures)
    slack = TEACHING_SLOTS_PER_WEEK - worst
    return max(2, int(slack * tightness))


def generate(divisions: int = 2, courses_per_division: int = 6,
             lectures: int = 3, labs_for: int = 2, seed: int = 0,
             unavailability: Optional[int] = None, name: Optional[str] = None) -> Instance:
    """A department with *divisions* divisions, each taking the same subjects.

    ``labs_for`` is how many of each division's courses also carry a laboratory
    session.  ``unavailability`` is how many (day, period) blocks each faculty
    member declares unavailable — the main source of hard difficulty.  Left as
    ``None`` it is set as high as the counting argument allows, which keeps the
    instance both solvable and genuinely tight.
    """
    rng = random.Random(seed)
    courses_per_division = min(courses_per_division, len(SUBJECTS))
    if unavailability is None:
        unavailability = default_unavailability(divisions, lectures, labs_for)

    divs = [Division(id=f"D{i + 1}", name=f"CE-{chr(ord('A') + i)}",
                     strength=rng.choice([58, 62, 66, 70]))
            for i in range(divisions)]
    max_strength = max(d.strength for d in divs)

    # Rooms: enough lecture halls to run every division at once, plus labs.
    rooms: List[Room] = []
    for i in range(divisions):
        rooms.append(Room(id=f"LH{i + 1}", capacity=max_strength + rng.choice([0, 6, 12]),
                          is_lab=False))
    for i in range(max(1, divisions // 2)):
        rooms.append(Room(id=f"LAB{i + 1}", capacity=max_strength + 6, is_lab=True))

    # One faculty member per subject, shared across every division.
    faculty: List[Faculty] = []
    used_names = set()
    for k in range(courses_per_division):
        while True:
            nm = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if nm not in used_names:
                used_names.add(nm)
                break
        blocks = set()
        while len(blocks) < unavailability:
            d = rng.randrange(len(DAYS))
            p = rng.randrange(PERIODS)
            if p != LUNCH_PERIOD:
                blocks.add((d, p))
        faculty.append(Faculty(id=f"F{k + 1}", name=nm, unavailable=frozenset(blocks)))

    courses: List[Course] = []
    for d in divs:
        for k in range(courses_per_division):
            code, title = SUBJECTS[k]
            courses.append(Course(
                code=f"{code}-{d.name}", name=title, division=d.id,
                faculty=f"F{k + 1}", lectures=lectures,
                labs=1 if k < labs_for else 0))

    return Instance(
        name=name or f"dept-{divisions}div-seed{seed}",
        rooms=rooms, faculty=faculty, divisions=divs, courses=courses)


def infeasible_example(seed: int = 0) -> Instance:
    """A deliberately over-constrained instance, for the explainer demo.

    One faculty member is unavailable on almost the whole week while still
    owing a full teaching load, so no timetable can exist.
    """
    inst = generate(divisions=2, courses_per_division=3, lectures=3, labs_for=1, seed=seed)
    blocked = {(d, p) for d in range(len(DAYS)) for p in range(PERIODS)}
    keep = {(0, 0), (0, 1)}                      # only two free periods all week
    blocked -= keep
    faculty = []
    for f in inst.faculty:
        if f.id == "F1":
            faculty.append(Faculty(id=f.id, name=f.name, unavailable=frozenset(blocked)))
        else:
            faculty.append(f)
    inst.faculty = faculty
    inst.name = "over-constrained-demo"
    return inst


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

def save_csv(inst: Instance, folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "rooms.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "capacity", "is_lab"])
        for r in inst.rooms:
            w.writerow([r.id, r.capacity, int(r.is_lab)])
    with open(os.path.join(folder, "divisions.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name", "strength"])
        for d in inst.divisions:
            w.writerow([d.id, d.name, d.strength])
    with open(os.path.join(folder, "faculty.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name", "unavailable"])
        for f in inst.faculty:
            blocks = " ".join(f"{DAYS[d]}:{p}" for d, p in sorted(f.unavailable))
            w.writerow([f.id, f.name, blocks])
    with open(os.path.join(folder, "courses.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "division", "faculty", "lectures", "labs"])
        for c in inst.courses:
            w.writerow([c.code, c.name, c.division, c.faculty, c.lectures, c.labs])


def load_csv(folder: str, name: Optional[str] = None) -> Instance:
    day_index = {d: i for i, d in enumerate(DAYS)}

    with open(os.path.join(folder, "rooms.csv")) as fh:
        rooms = [Room(id=r["id"], capacity=int(r["capacity"]),
                      is_lab=bool(int(r["is_lab"]))) for r in csv.DictReader(fh)]
    with open(os.path.join(folder, "divisions.csv")) as fh:
        divisions = [Division(id=r["id"], name=r["name"], strength=int(r["strength"]))
                     for r in csv.DictReader(fh)]
    with open(os.path.join(folder, "faculty.csv")) as fh:
        faculty = []
        for r in csv.DictReader(fh):
            blocks = set()
            for token in (r["unavailable"] or "").split():
                d, p = token.split(":")
                blocks.add((day_index[d], int(p)))
            faculty.append(Faculty(id=r["id"], name=r["name"],
                                   unavailable=frozenset(blocks)))
    with open(os.path.join(folder, "courses.csv")) as fh:
        courses = [Course(code=r["code"], name=r["name"], division=r["division"],
                          faculty=r["faculty"], lectures=int(r["lectures"]),
                          labs=int(r["labs"])) for r in csv.DictReader(fh)]

    return Instance(name=name or os.path.basename(folder.rstrip("/")),
                    rooms=rooms, faculty=faculty, divisions=divisions, courses=courses)


BENCHMARK_SIZES: Tuple[int, ...] = (1, 2, 3, 4, 5)


def benchmark_instances(seed: int = 0) -> List[Instance]:
    """The five instances of increasing size used in the results table."""
    return [generate(divisions=n, courses_per_division=6, lectures=3, labs_for=2,
                     seed=seed, name=f"{n}-division")
            for n in BENCHMARK_SIZES]
