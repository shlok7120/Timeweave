#!/usr/bin/env python3
"""A one-command tour of everything TimeWeave does — good for the viva.

    python scripts/demo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeweave.constraints import hard_violations, penalty_value, soft_penalty
from timeweave.csp import CSP
from timeweave.explain import quickxplain
from timeweave.instances import generate, infeasible_example, save_csv
from timeweave.learning import learn_preferences
from timeweave.model import DAYS
from timeweave.render import text_timetable
from timeweave.rules import Explainer
from timeweave.solvers import Budget, SoftOptimiser, all_solvers


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    inst = generate(divisions=3, seed=0)
    csp = CSP(inst)

    rule("1. The instance and the constraint network")
    print(inst.summary())
    print(csp.describe())

    rule("2. Every solving strategy on the same instance")
    best = None
    for solver in all_solvers(seed=0):
        res = solver.solve(csp, Budget(seconds=20))
        audit = hard_violations(csp, res.assignment) if res.assignment else None
        state = "solved" if res.stats.solved else ("TIMED OUT" if res.stats.timed_out
                                                   else "failed")
        extra = f", audit {'clean' if audit == [] else audit}" if audit is not None else ""
        print(f"  {solver.name:42s} {state:10s} nodes={res.stats.nodes:>9,} "
              f"checks={res.stats.checks:>11,} {res.stats.seconds:6.2f}s{extra}")
        if res.stats.solved and best is None:
            best = res.assignment

    rule("3. Soft-constraint optimisation")
    print("before:", soft_penalty(csp, best))
    improved, stats = SoftOptimiser(steps=2500, seed=0).improve(csp, best)
    print("after :", soft_penalty(csp, improved))
    print(f"still satisfies every hard constraint: {hard_violations(csp, improved) == []}")

    rule("4. The generated timetable")
    print(text_timetable(csp, improved, inst.divisions[0].id))

    rule("5. The rule base and its explanation facility")
    ex = Explainer(inst)
    print(ex.summary())
    session = csp.variables[0]
    for value in [(0, 4, 0), (1, 7, 0)]:
        reasons = ex.explain(session.id, value)
        print(f"  {session.id} at {DAYS[value[0]]} period {value[1] + 1}:")
        for r in (reasons or ["allowed"]):
            print(f"      {r}")
    lab = next(s for s in csp.variables if s.is_lab)
    hall = next(i for i, r in enumerate(inst.rooms) if not r.is_lab)
    slot = next(v for v in csp.domains[csp.index[lab.id]])
    print(f"  {lab.id} moved into {inst.rooms[hall].id} at "
          f"{DAYS[slot[0]]} period {slot[1] + 1}:")
    for r in ex.explain(lab.id, (slot[0], slot[1], hall)):
        print(f"      {r}")

    rule("6. Explaining an impossible instance")
    bad = infeasible_example(seed=2)
    print(bad.summary())
    print(quickxplain(bad, seconds=5.0).text())

    rule("7. Learning the coordinator's preferences with ID3")
    report = learn_preferences(csp, improved, n=300, seed=0)
    print(report.text())

    rule("8. Re-optimising against the learned weights")
    tuned, tuned_stats = SoftOptimiser(steps=2500, seed=1).improve(
        csp, best, weights=report.weights)
    print(f"learned-weight cost {tuned_stats.extra['penalty_before']:.1f} -> "
          f"{tuned_stats.penalty:.1f}")
    print(f"the same timetable scored with the default weights: "
          f"{penalty_value(csp, best):.0f} -> {penalty_value(csp, tuned):.0f}")
    print("(the optimiser now chases what the coordinator actually rejects, "
          "not the five guessed weights)")
    print(f"hard constraints still satisfied: {hard_violations(csp, tuned) == []}")

    os.makedirs("data/sample-department", exist_ok=True)
    save_csv(inst, "data/sample-department")
    print("\nWrote the instance to data/sample-department/*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
