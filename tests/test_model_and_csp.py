import pytest

from timeweave.csp import CSP
from timeweave.instances import generate, load_csv, save_csv
from timeweave.model import LAB_LENGTH, LUNCH_PERIOD, PERIODS


@pytest.fixture(scope="module")
def csp():
    return CSP(generate(divisions=2, seed=11))


def test_h6_quota_is_structural(csp):
    """One variable per required contact session — nothing more, nothing less."""
    expected = sum(c.lectures + c.labs for c in csp.instance.courses)
    assert csp.n == expected
    for course in csp.instance.courses:
        mine = [s for s in csp.variables if s.course == course.code]
        assert sum(1 for s in mine if s.kind == "lecture") == course.lectures
        assert sum(1 for s in mine if s.kind == "lab") == course.labs


def test_domains_respect_unary_constraints(csp):
    faculty = csp.instance.faculty_by_id()
    strengths = csp.instance.division_by_id()
    for i, s in enumerate(csp.variables):
        assert csp.domains[i], f"{s.id} has an empty domain"
        for (d, p, r) in csp.domains[i]:
            span = range(p, p + s.length)
            assert LUNCH_PERIOD not in span                    # H7
            assert p + s.length <= PERIODS                     # day boundary
            for q in span:                                     # H5
                assert (d, q) not in faculty[s.faculty].unavailable
            room = csp.instance.rooms[r]
            assert room.is_lab == s.is_lab                     # H4
            assert room.capacity >= strengths[s.division].strength


def test_labs_are_two_consecutive_periods(csp):
    for s in csp.variables:
        assert s.length == (LAB_LENGTH if s.is_lab else 1)


def test_consistent_detects_each_hard_constraint(csp):
    same_faculty = None
    for i in range(csp.n):
        for j in range(i + 1, csp.n):
            a, b = csp.variables[i], csp.variables[j]
            if a.faculty == b.faculty and a.division != b.division:
                same_faculty = (i, j)
                break
        if same_faculty:
            break
    i, j = same_faculty
    v = csp.domains[i][0]
    assert not csp.consistent(i, v, j, v)              # H1: same slot, same teacher
    other_day = next((w for w in csp.domains[j] if w[0] != v[0]), None)
    assert csp.consistent(i, v, j, other_day)          # different days never clash


def test_ac3_is_sound_and_shrinks_nothing_it_should_not(csp):
    ok, pruned = csp.ac3()
    assert ok
    for before, after in zip(csp.domains, pruned):
        assert set(after) <= set(before)
        assert after, "AC-3 must not empty a domain on a solvable instance"


def test_csv_round_trip(tmp_path):
    inst = generate(divisions=2, seed=4)
    save_csv(inst, str(tmp_path))
    back = load_csv(str(tmp_path))
    assert [r.id for r in back.rooms] == [r.id for r in inst.rooms]
    assert [c.code for c in back.courses] == [c.code for c in inst.courses]
    assert {f.id: f.unavailable for f in back.faculty} == \
           {f.id: f.unavailable for f in inst.faculty}
    assert len(CSP(back).variables) == len(CSP(inst).variables)
