"""Core data model for the TimeWeave timetabling problem.

A timetable is modelled as a Constraint Satisfaction Problem:

    variables  -  one per teaching session that must be placed
    domains    -  every (day, period, room) triple the session could legally take
    constraints-  the clash rules H1..H7 (hard) and the preferences S1..S5 (soft)

Nothing in this module knows how to *search*; it only describes the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #

DAYS: Tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri")
PERIODS: int = 8                 # period indices 0 .. 7
LUNCH_PERIOD: int = 4            # H7: this period is never taught in
LAB_LENGTH: int = 2              # a laboratory session occupies two periods

PERIOD_LABELS: Tuple[str, ...] = (
    "09:00", "10:00", "11:00", "12:00", "13:00 (lunch)", "14:00", "15:00", "16:00",
)

# A value assigned to a variable.
Value = Tuple[int, int, int]     # (day index, start period, room index)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Room:
    id: str
    capacity: int
    is_lab: bool


@dataclass(frozen=True)
class Faculty:
    id: str
    name: str
    # (day index, period index) pairs on which this member cannot teach
    unavailable: FrozenSet[Tuple[int, int]] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Division:
    id: str
    name: str
    strength: int


@dataclass(frozen=True)
class Course:
    code: str
    name: str
    division: str
    faculty: str
    lectures: int              # weekly lecture hours
    labs: int                  # weekly laboratory *sessions* (each LAB_LENGTH long)


@dataclass(frozen=True)
class Session:
    """One indivisible teaching event — this is a CSP variable."""
    id: str
    course: str
    division: str
    faculty: str
    kind: str                  # "lecture" | "lab"
    length: int                # number of consecutive periods

    @property
    def is_lab(self) -> bool:
        return self.kind == "lab"


# --------------------------------------------------------------------------- #
# Instance
# --------------------------------------------------------------------------- #

@dataclass
class Instance:
    """A complete timetabling problem for one department."""

    name: str
    rooms: List[Room]
    faculty: List[Faculty]
    divisions: List[Division]
    courses: List[Course]

    # ---- derived lookups -------------------------------------------------- #
    def room_index(self) -> Dict[str, int]:
        return {r.id: i for i, r in enumerate(self.rooms)}

    def faculty_by_id(self) -> Dict[str, Faculty]:
        return {f.id: f for f in self.faculty}

    def division_by_id(self) -> Dict[str, Division]:
        return {d.id: d for d in self.divisions}

    def course_by_code(self) -> Dict[str, Course]:
        return {c.code: c for c in self.courses}

    # ---- H6: the weekly quota is enforced structurally -------------------- #
    def sessions(self) -> List[Session]:
        """One variable per required contact session.

        H6 ("each course meets its weekly quota exactly") needs no runtime check:
        we create exactly as many variables as the quota demands, and every
        variable receives exactly one value.
        """
        out: List[Session] = []
        for c in self.courses:
            for i in range(c.lectures):
                out.append(Session(
                    id=f"{c.code}-L{i + 1}", course=c.code, division=c.division,
                    faculty=c.faculty, kind="lecture", length=1))
            for i in range(c.labs):
                out.append(Session(
                    id=f"{c.code}-P{i + 1}", course=c.code, division=c.division,
                    faculty=c.faculty, kind="lab", length=LAB_LENGTH))
        return out

    def total_contact_periods(self) -> int:
        return sum(c.lectures + c.labs * LAB_LENGTH for c in self.courses)

    def summary(self) -> str:
        return (f"{self.name}: {len(self.divisions)} divisions, "
                f"{len(self.courses)} courses, {len(self.sessions())} sessions, "
                f"{len(self.rooms)} rooms, {len(self.faculty)} faculty")


# --------------------------------------------------------------------------- #
# Small helpers shared by the constraint and solver layers
# --------------------------------------------------------------------------- #

def periods_of(value: Value, length: int) -> range:
    """The period indices a session occupies, given its start value."""
    _, start, _ = value
    return range(start, start + length)


def overlaps(a: Value, la: int, b: Value, lb: int) -> bool:
    """True when two placed sessions collide in time."""
    if a[0] != b[0]:                       # different days
        return False
    return a[1] < b[1] + lb and b[1] < a[1] + la
