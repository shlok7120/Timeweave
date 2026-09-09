#!/usr/bin/env python3
"""Load a real department's CSV data, validate it, and schedule it.

    python scripts/import_csv.py data/sample-department
    python scripts/import_csv.py data/my-department --solve --out timetable.txt

This is the path from a coordinator's spreadsheets to a timetable. It refuses to
run the solver on data that has errors, because a solver failure is a far worse
error message than "courses.csv line 4 names a faculty id that does not exist".
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeweave.constraints import hard_violations, soft_penalty
from timeweave.csp import CSP
from timeweave.explain import quickxplain
from timeweave.instances import load_csv
from timeweave.render import text_timetable
from timeweave.solvers import Budget, SoftOptimiser, SOLVER_KEYS
from timeweave.validate import report, validate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder holding rooms/faculty/divisions/courses .csv")
    ap.add_argument("--solve", action="store_true", help="also produce a timetable")
    ap.add_argument("--solver", default="fc", choices=sorted(SOLVER_KEYS))
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--no-optimise", action="store_true")
    ap.add_argument("--out", help="write the timetable to this file as well")
    args = ap.parse_args()

    try:
        inst = load_csv(args.folder)
    except FileNotFoundError as e:
        print(f"Could not read the data: {e}")
        print("The folder needs rooms.csv, faculty.csv, divisions.csv and courses.csv "
              "— see data/template/ for the expected columns.")
        return 2

    print(report(inst))
    problems = validate(inst)
    if any(p.severity == "error" for p in problems):
        return 1
    if not args.solve:
        print("\n(pass --solve to generate a timetable)")
        return 0

    csp = CSP(inst)
    print(f"\n{csp.describe()}\n")
    solver = SOLVER_KEYS[args.solver](0)
    result = solver.solve(csp, Budget(seconds=args.seconds))

    if result.assignment is None:
        print(f"{solver.name} found no timetable within {args.seconds:.0f}s.\n")
        print(quickxplain(inst, seconds=5.0).text())
        return 1

    assignment = result.assignment
    print(f"{solver.name}: solved in {result.stats.nodes} nodes, "
          f"{result.stats.seconds:.2f}s")
    if not args.no_optimise:
        assignment, opt = SoftOptimiser(steps=4000, seed=0).improve(
            csp, assignment, budget=Budget(seconds=20))
        print(f"soft optimiser: penalty {opt.extra['penalty_before']:.0f} -> "
              f"{opt.penalty:.0f}")

    problems = hard_violations(csp, assignment)
    print(f"hard-constraint audit: {'passed' if not problems else problems}")
    print(soft_penalty(csp, assignment))

    text = "\n\n".join(text_timetable(csp, assignment, d.id) for d in inst.divisions)
    print("\n" + text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
