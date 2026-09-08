#!/usr/bin/env python3
"""Calibrate instance difficulty — how the ``tightness`` constant was chosen.

A benchmark is only interesting in the regime where the techniques actually
differ.  Too loose and every solver wins in *n* nodes; too tight and the
instances are infeasible and nothing wins.  This script sweeps the tightness
parameter of :func:`timeweave.instances.default_unavailability` and reports,
for each setting, how often forward checking succeeds and how often naive
backtracking does.

    python scripts/calibrate.py

The useful band is where forward checking is at 100% and naive backtracking is
not — which is how 0.75 became the default.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeweave.csp import CSP
from timeweave.instances import TEACHING_SLOTS_PER_WEEK, generate
from timeweave.solvers import BacktrackingSolver, Budget


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tightness", type=float, nargs="+",
                    default=[0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--seconds", type=float, default=6.0)
    args = ap.parse_args()

    print(f"{'tightness':>10s}  {'unavailability by size':>26s}  "
          f"{'FC solves':>10s}  {'naive solves':>13s}")
    print("-" * 68)

    for t in args.tightness:
        blocks, fc_ok, naive_ok, total = [], 0, 0, 0
        for n in args.sizes:
            worst = n * 5                                   # lectures + one 2-period lab
            unavailable = max(2, int((TEACHING_SLOTS_PER_WEEK - worst) * t))
            blocks.append(unavailable)
            for seed in args.seeds:
                inst = generate(divisions=n, courses_per_division=6,
                                unavailability=unavailable, seed=seed)
                csp = CSP(inst)
                total += 1
                fc = BacktrackingSolver(ordering="mrv", inference="fc", seed=seed)
                if fc.solve(csp, Budget(seconds=args.seconds)).stats.solved:
                    fc_ok += 1
                naive = BacktrackingSolver(seed=seed)
                if naive.solve(csp, Budget(seconds=args.seconds,
                                           nodes=300_000)).stats.solved:
                    naive_ok += 1
        sizes = " ".join(f"{b:>4d}" for b in blocks)
        print(f"{t:>10.2f}  {sizes:>26s}  {fc_ok:>4d}/{total:<5d}  {naive_ok:>6d}/{total:<6d}")

    print("\nRead it as: pick the largest tightness where forward checking still "
          "solves every instance.\nBelow that band the problem is trivial; above it "
          "the instances stop being solvable at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
