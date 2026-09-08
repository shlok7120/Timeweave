"""The benchmark harness: every solver, identical instances, one results table.

This is the part of the project that turns "we implemented seven algorithms"
into "here is which one wins, and by how much".  Every solver sees exactly the
same instances and the same budget, and every returned timetable is re-checked
against all seven hard constraints before it counts as solved.
"""

from __future__ import annotations

import csv
import os
import statistics
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .constraints import hard_violations
from .csp import CSP
from .instances import benchmark_instances
from .model import Instance
from .solvers import Budget, all_solvers

# Chart palette: slots 1-7 of the validated categorical order.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#dcdcd6"


@dataclass
class Run:
    instance: str
    size: int
    variables: int
    seed: int
    solver: str
    solved: bool
    verified: bool
    timed_out: bool
    nodes: int
    checks: int
    seconds: float
    penalty: float
    violations: int


def run_benchmark(seeds: Sequence[int] = (0, 1, 2),
                  seconds: float = 20.0,
                  nodes: int = 2_000_000,
                  instances: Optional[Sequence[Instance]] = None,
                  progress: bool = True) -> List[Run]:
    rows: List[Run] = []
    for seed in seeds:
        insts = list(instances) if instances is not None else benchmark_instances(seed=seed)
        for inst in insts:
            csp = CSP(inst)
            for solver in all_solvers(seed=seed):
                res = solver.solve(csp, Budget(seconds=seconds, nodes=nodes))
                violations = (len(hard_violations(csp, res.assignment))
                              if res.assignment is not None else -1)
                row = Run(
                    instance=inst.name, size=len(inst.divisions), variables=csp.n,
                    seed=seed, solver=solver.name, solved=res.stats.solved,
                    verified=res.assignment is not None and violations == 0,
                    timed_out=res.stats.timed_out, nodes=res.stats.nodes,
                    checks=res.stats.checks, seconds=res.stats.seconds,
                    penalty=res.stats.penalty, violations=violations)
                rows.append(row)
                if progress:
                    flag = "ok " if row.verified else ("--- " if not row.solved else "BAD")
                    print(f"  [{flag}] seed {seed} {inst.name:12s} {solver.name:40s} "
                          f"nodes={row.nodes:<9d} {row.seconds:6.2f}s "
                          f"penalty={row.penalty}")
    return rows


def read_csv(path: str) -> List[Run]:
    """Load a results table back, so charts can be re-rendered without re-running."""
    out: List[Run] = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            out.append(Run(
                instance=r["instance"], size=int(r["divisions"]),
                variables=int(r["variables"]), seed=int(r["seed"]), solver=r["solver"],
                solved=bool(int(r["solved"])), verified=bool(int(r["verified"])),
                timed_out=bool(int(r["timed_out"])), nodes=int(r["nodes"]),
                checks=int(r["checks"]), seconds=float(r["seconds"]),
                penalty=float(r["penalty"]), violations=int(r["violations"])))
    return out


def write_csv(rows: Sequence[Run], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["instance", "divisions", "variables", "seed", "solver", "solved",
                    "verified", "timed_out", "nodes", "checks", "seconds",
                    "penalty", "violations"])
        for r in rows:
            w.writerow([r.instance, r.size, r.variables, r.seed, r.solver, int(r.solved),
                        int(r.verified), int(r.timed_out), r.nodes, r.checks,
                        round(r.seconds, 4), r.penalty, r.violations])


def summarise(rows: Sequence[Run]) -> str:
    """The results table as it appears in the report."""
    solvers: List[str] = []
    for r in rows:
        if r.solver not in solvers:
            solvers.append(r.solver)
    out = [
        f"{'solver':42s} {'solved':>8s} {'nodes':>12s} {'checks':>13s} "
        f"{'time (s)':>10s} {'penalty':>9s}",
        "-" * 100,
    ]
    for s in solvers:
        mine = [r for r in rows if r.solver == s]
        ok = [r for r in mine if r.verified]
        nodes = statistics.mean([r.nodes for r in ok]) if ok else float("nan")
        checks = statistics.mean([r.checks for r in ok]) if ok else float("nan")
        secs = statistics.mean([r.seconds for r in ok]) if ok else float("nan")
        pen = statistics.mean([r.penalty for r in ok]) if ok else float("nan")
        out.append(f"{s:42s} {len(ok):>4d}/{len(mine):<3d} {nodes:>12,.0f} "
                   f"{checks:>13,.0f} {secs:>10.3f} {pen:>9.1f}")
    bad = [r for r in rows if r.solved and not r.verified]
    out.append("")
    out.append(f"independently re-checked: {sum(1 for r in rows if r.verified)} valid "
               f"timetables, {len(bad)} solver claims rejected by the audit")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #

def _style(ax, title: str, xlabel: str, ylabel: str):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=12, pad=12, loc="left")
    ax.set_xlabel(xlabel, color=INK_SOFT, fontsize=9)
    ax.set_ylabel(ylabel, color=INK_SOFT, fontsize=9)
    ax.tick_params(colors=INK_SOFT, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def make_charts(rows: Sequence[Run], folder: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(folder, exist_ok=True)
    plt.rcParams["font.family"] = "DejaVu Sans"
    solvers: List[str] = []
    for r in rows:
        if r.solver not in solvers:
            solvers.append(r.solver)
    sizes = sorted({r.size for r in rows})
    written: List[str] = []

    # 1. nodes expanded vs instance size (log scale) --------------------- #
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=200)
    for k, s in enumerate(solvers):
        ys = []
        for n in sizes:
            vals = [r.nodes for r in rows if r.solver == s and r.size == n and r.verified]
            ys.append(statistics.mean(vals) if vals else float("nan"))
        ax.plot(sizes, ys, marker="o", markersize=5, linewidth=2,
                color=PALETTE[k % len(PALETTE)], label=s)
    ax.set_yscale("log")
    ax.set_xticks(sizes)
    _style(ax, "Nodes expanded to find a valid timetable",
           "divisions in the instance", "nodes (log scale, mean of solved runs)")
    ax.legend(fontsize=7.5, frameon=False, labelcolor=INK_SOFT, loc="upper left")
    p = os.path.join(folder, "nodes_vs_size.png")
    fig.tight_layout(); fig.savefig(p); plt.close(fig); written.append(p)

    # 2. how often each solver actually produced a verified timetable ---- #
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=200)
    rates, labels = [], []
    for s in solvers:
        mine = [r for r in rows if r.solver == s]
        rates.append(100.0 * sum(1 for r in mine if r.verified) / max(1, len(mine)))
        labels.append(s)
    bars = ax.barh(range(len(labels)), rates, color=PALETTE[0], height=0.62)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    for i, (b, v) in enumerate(zip(bars, rates)):
        ax.text(v + 2, b.get_y() + b.get_height() / 2, f"{v:.0f}%",
                va="center", fontsize=8.5, color=INK)
    _style(ax, "Runs that produced a timetable passing every hard constraint",
           "percent of runs", "")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    p = os.path.join(folder, "success_rate.png")
    fig.tight_layout(); fig.savefig(p); plt.close(fig); written.append(p)

    # 3. runtime vs instance size ---------------------------------------- #
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=200)
    for k, s in enumerate(solvers):
        ys = []
        for n in sizes:
            vals = [r.seconds for r in rows if r.solver == s and r.size == n and r.verified]
            ys.append(statistics.mean(vals) if vals else float("nan"))
        ax.plot(sizes, ys, marker="o", markersize=5, linewidth=2,
                color=PALETTE[k % len(PALETTE)], label=s)
    ax.set_yscale("log")
    ax.set_xticks(sizes)
    _style(ax, "Wall-clock time to a valid timetable",
           "divisions in the instance", "seconds (log scale)")
    ax.legend(fontsize=7.5, frameon=False, labelcolor=INK_SOFT, loc="upper left")
    p = os.path.join(folder, "time_vs_size.png")
    fig.tight_layout(); fig.savefig(p); plt.close(fig); written.append(p)

    # 4. soft-constraint penalty ----------------------------------------- #
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=200)
    pens, labels = [], []
    for s in solvers:
        vals = [r.penalty for r in rows if r.solver == s and r.verified]
        if vals:
            pens.append(statistics.mean(vals))
            labels.append(s)
    bars = ax.barh(range(len(labels)), pens, color=PALETTE[2], height=0.62)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()
    for b, v in zip(bars, pens):
        ax.text(v + max(pens) * 0.02, b.get_y() + b.get_height() / 2, f"{v:.0f}",
                va="center", fontsize=8.5, color=INK)
    _style(ax, "Soft-constraint penalty of the timetable found (lower is better)",
           "weighted penalty, mean of solved runs", "")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    p = os.path.join(folder, "penalty.png")
    fig.tight_layout(); fig.savefig(p); plt.close(fig); written.append(p)

    return written
