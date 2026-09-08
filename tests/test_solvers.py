import pytest

from timeweave.constraints import hard_violations, penalty_value, soft_penalty
from timeweave.csp import CSP
from timeweave.instances import generate
from timeweave.solvers import (
    BacktrackingSolver, Budget, GeneticSolver, MinConflictsSolver, SoftOptimiser,
)

SMALL = generate(divisions=2, seed=21)


def solvers():
    return [
        BacktrackingSolver(ordering="mrv", value_order="lcv", seed=1),
        BacktrackingSolver(ordering="mrv", inference="fc", seed=1),
        BacktrackingSolver(ordering="mrv", inference="mac", seed=1),
        BacktrackingSolver(ordering="mrv", backjump=True, seed=1),
        MinConflictsSolver(seed=1),
        GeneticSolver(seed=1, generations=200),
    ]


@pytest.mark.parametrize("solver", solvers(), ids=lambda s: s.name)
def test_every_solver_returns_a_valid_timetable(solver):
    """The property that matters: whatever the search, the output is legal."""
    csp = CSP(SMALL)
    result = solver.solve(csp, Budget(seconds=30))
    assert result.stats.solved, f"{solver.name} failed on a solvable instance"
    assert result.assignment is not None
    assert hard_violations(csp, result.assignment) == []


def test_solvers_do_not_mutate_the_csp():
    csp = CSP(SMALL)
    before = [list(d) for d in csp.domains]
    BacktrackingSolver(ordering="mrv", inference="mac").solve(csp, Budget(seconds=20))
    assert [sorted(d) for d in csp.domains] == [sorted(d) for d in before]


def test_backjumping_and_inference_are_mutually_exclusive():
    with pytest.raises(ValueError):
        BacktrackingSolver(backjump=True, inference="fc")


def test_forward_checking_expands_no_more_nodes_than_naive():
    csp = CSP(generate(divisions=2, seed=33))
    naive = BacktrackingSolver(seed=2).solve(csp, Budget(seconds=10, nodes=200_000))
    fc = BacktrackingSolver(ordering="mrv", inference="fc", seed=2).solve(
        csp, Budget(seconds=10, nodes=200_000))
    assert fc.stats.solved
    if naive.stats.solved:
        assert fc.stats.nodes <= naive.stats.nodes


def test_unsolvable_budget_is_reported_not_faked():
    csp = CSP(generate(divisions=5, seed=3))
    res = BacktrackingSolver(seed=1).solve(csp, Budget(seconds=1.0, nodes=500))
    assert res.assignment is None
    assert not res.stats.solved
    assert res.stats.timed_out


def test_soft_optimiser_improves_without_breaking_feasibility():
    csp = CSP(SMALL)
    base = BacktrackingSolver(ordering="mrv", inference="fc", seed=5).solve(
        csp, Budget(seconds=20))
    assert base.assignment is not None
    before = penalty_value(csp, base.assignment)
    better, stats = SoftOptimiser(steps=1200, seed=5).improve(csp, base.assignment)
    assert hard_violations(csp, better) == []
    assert penalty_value(csp, better) <= before
    assert stats.extra["improvement"] >= 0


def test_penalty_report_components_are_non_negative():
    csp = CSP(SMALL)
    res = BacktrackingSolver(ordering="mrv", inference="fc", seed=7).solve(
        csp, Budget(seconds=20))
    report = soft_penalty(csp, res.assignment)
    assert set(report.raw) == {"S1_gaps", "S2_consecutive", "S3_lab_afternoon",
                               "S4_faculty_balance", "S5_last_period"}
    assert all(v >= 0 for v in report.raw.values())
    assert report.total == pytest.approx(sum(report.parts.values()))


def test_audit_catches_a_deliberately_broken_timetable():
    csp = CSP(SMALL)
    res = BacktrackingSolver(ordering="mrv", inference="fc", seed=9).solve(
        csp, Budget(seconds=20))
    broken = list(res.assignment)
    # force two sessions of the same division into the same slot
    i, j = next((a, b) for a in range(csp.n) for b in range(a + 1, csp.n)
                if csp.variables[a].division == csp.variables[b].division
                and csp.variables[a].length == csp.variables[b].length)
    broken[j] = broken[i]
    problems = hard_violations(csp, broken)
    assert problems and any(p.startswith("H2") for p in problems)
