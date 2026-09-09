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


# --------------------------------------------------------------------------- #
# JSON  (what the web interface sends when a coordinator enters their own data)
# --------------------------------------------------------------------------- #

def from_dict(payload: dict, name: str = "custom department") -> Instance:
    """Build an Instance from the interface's JSON.

    Deliberately forgiving about types — the browser sends strings — and
    deliberately *not* forgiving about meaning: anything inconsistent is left
    for :func:`timeweave.validate.validate` to report in the user's own terms
    rather than being silently repaired here.
    """
    def as_int(v, default=0):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return default

    rooms = [Room(id=str(r.get("id", "")).strip(),
                  capacity=as_int(r.get("capacity"), 0),
                  is_lab=bool(r.get("isLab")))
             for r in payload.get("rooms", [])]

    divisions = [Division(id=str(d.get("id", "")).strip(),
                          name=str(d.get("name", "")).strip() or str(d.get("id", "")),
                          strength=as_int(d.get("strength"), 0))
                 for d in payload.get("divisions", [])]

    faculty = []
    for f in payload.get("faculty", []):
        blocks = set()
        for entry in f.get("unavailable", []):
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                blocks.add((as_int(entry[0], -1), as_int(entry[1], -1)))
        faculty.append(Faculty(id=str(f.get("id", "")).strip(),
                               name=str(f.get("name", "")).strip() or str(f.get("id", "")),
                               unavailable=frozenset(blocks)))

    courses = [Course(code=str(c.get("code", "")).strip(),
                      name=str(c.get("name", "")).strip() or str(c.get("code", "")),
                      division=str(c.get("division", "")).strip(),
                      faculty=str(c.get("faculty", "")).strip(),
                      lectures=as_int(c.get("lectures"), 0),
                      labs=as_int(c.get("labs"), 0))
               for c in payload.get("courses", [])]

    return Instance(name=str(payload.get("name") or name), rooms=rooms,
                    faculty=faculty, divisions=divisions, courses=courses)


def to_dict(inst: Instance) -> dict:
    """The inverse — used to seed the editor from a generated example."""
    return {
        "name": inst.name,
        "rooms": [{"id": r.id, "capacity": r.capacity, "isLab": r.is_lab}
                  for r in inst.rooms],
        "divisions": [{"id": d.id, "name": d.name, "strength": d.strength}
                      for d in inst.divisions],
        "faculty": [{"id": f.id, "name": f.name,
                     "unavailable": [list(x) for x in sorted(f.unavailable)]}
                    for f in inst.faculty],
        "courses": [{"code": c.code, "name": c.name, "division": c.division,
                     "faculty": c.faculty, "lectures": c.lectures, "labs": c.labs}
                    for c in inst.courses],
    }


def starter_department() -> Instance:
    """A small, obviously-editable example so the editor is never blank.

    Two classes, four subjects each, four teachers, three rooms — small enough to
    read at a glance and solvable, so a first-time user sees a timetable before
    they change anything.
    """
    divisions = [Division(id="D1", name="CE-A", strength=60),
                 Division(id="D2", name="CE-B", strength=60)]
    rooms = [Room(id="LH1", capacity=70, is_lab=False),
             Room(id="LH2", capacity=70, is_lab=False),
             Room(id="LAB1", capacity=70, is_lab=True)]
    faculty = [
        Faculty(id="F1", name="Anita Deshmukh",
                unavailable=frozenset({(0, 0), (0, 1), (4, 6), (4, 7)})),
        Faculty(id="F2", name="Rahul Iyer",
                unavailable=frozenset({(2, 5), (2, 6), (2, 7)})),
        Faculty(id="F3", name="Meera Kulkarni",
                unavailable=frozenset({(1, 0), (3, 0)})),
        Faculty(id="F4", name="Sanjay Bhatt", unavailable=frozenset({(4, 0), (4, 1)})),
    ]
    subjects = [("AI", "Artificial Intelligence", "F1", 3, 1),
                ("DBMS", "Database Management Systems", "F2", 3, 1),
                ("CN", "Computer Networks", "F3", 3, 0),
                ("OS", "Operating Systems", "F4", 3, 0)]
    courses = []
    for d in divisions:
        for code, title, fid, lec, lab in subjects:
            courses.append(Course(code=f"{code}-{d.name}", name=title, division=d.id,
                                  faculty=fid, lectures=lec, labs=lab))
    return Instance(name="my department", rooms=rooms, faculty=faculty,
                    divisions=divisions, courses=courses)
