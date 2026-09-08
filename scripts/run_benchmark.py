#!/usr/bin/env python3
"""Run every solver on every benchmark instance and write the results table.

    python scripts/run_benchmark.py --seeds 0 1 2 --seconds 20

Outputs, under ``results/``:
    benchmark.csv      one row per (instance, seed, solver)
    summary.txt        the table that goes in the report
    *.png              the comparison charts
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeweave.benchmark import make_charts, run_benchmark, summarise, write_csv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--nodes", type=int, default=2_000_000)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    print(f"TimeWeave benchmark — seeds {args.seeds}, {args.seconds}s cap per run\n")
    rows = run_benchmark(seeds=args.seeds, seconds=args.seconds, nodes=args.nodes)

    os.makedirs(args.out, exist_ok=True)
    write_csv(rows, os.path.join(args.out, "benchmark.csv"))
    table = summarise(rows)
    with open(os.path.join(args.out, "summary.txt"), "w") as fh:
        fh.write(table + "\n")
    charts = make_charts(rows, args.out)

    print("\n" + table)
    print("\nwrote:")
    for p in [os.path.join(args.out, "benchmark.csv"),
              os.path.join(args.out, "summary.txt")] + charts:
        print("   ", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
