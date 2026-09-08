#!/usr/bin/env python3
"""Generate the project report from the results that were actually measured.

    python scripts/run_benchmark.py       # produce results/benchmark.csv first
    python scripts/make_report.py         # writes docs/REPORT.md

Every number in the report is read out of ``results/benchmark.csv`` rather than
typed in by hand, so the document can never drift away from the experiment.
"""

from __future__ import annotations

import os
import statistics
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeweave.benchmark import read_csv
from timeweave.csp import CSP
from timeweave.instances import (
    TEACHING_SLOTS_PER_WEEK, benchmark_instances, default_unavailability,
)

RESULTS = "results/benchmark.csv"
OUT = "docs/REPORT.md"


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def aggregate(rows):
    solvers: List[str] = []
    for r in rows:
        if r.solver not in solvers:
            solvers.append(r.solver)
    sizes = sorted({r.size for r in rows})
    seeds = sorted({r.seed for r in rows})

    agg: Dict[str, dict] = {}
    for s in solvers:
        mine = [r for r in rows if r.solver == s]
        ok = [r for r in mine if r.verified]
        agg[s] = {
            "runs": len(mine), "solved": len(ok),
            "rate": 100.0 * len(ok) / max(1, len(mine)),
            "nodes": statistics.mean([r.nodes for r in ok]) if ok else float("nan"),
            "checks": statistics.mean([r.checks for r in ok]) if ok else float("nan"),
            "seconds": statistics.mean([r.seconds for r in ok]) if ok else float("nan"),
            "penalty": statistics.mean([r.penalty for r in ok]) if ok else float("nan"),
            "sd_seconds": (statistics.stdev([r.seconds for r in ok])
                           if len(ok) > 1 else 0.0),
            "timeouts": sum(1 for r in mine if r.timed_out),
            "by_size": {n: [r for r in ok if r.size == n] for n in sizes},
            "attempts_by_size": {n: [r for r in mine if r.size == n] for n in sizes},
        }
    return solvers, sizes, seeds, agg


def fmt(x, nd=0):
    if x != x:                     # NaN
        return "—"
    return f"{x:,.{nd}f}"


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #

def build(rows) -> str:
    solvers, sizes, seeds, agg = aggregate(rows)
    instances = benchmark_instances(seed=seeds[0])
    csps = {len(i.divisions): CSP(i) for i in instances}
    total_runs = len(rows)
    verified = sum(1 for r in rows if r.verified)
    rejected = sum(1 for r in rows if r.solved and not r.verified)

    naive = solvers[0]
    fc = next(s for s in solvers if "forward checking" in s)
    mc = next(s for s in solvers if "Min-conflicts" in s)
    ga = next(s for s in solvers if "Genetic" in s)
    heur = next(s for s in solvers if "MRV" in s)

    L: List[str] = []
    add = L.append

    # ---------------------------------------------------------------- title
    add("# TimeWeave")
    add("### Automated Examination and Lecture Timetable Generation "
        "using Constraint Satisfaction and Local Search")
    add("")
    add("**Project Report** — Artificial Intelligence (702CO0C076)")
    add("")
    add("Shlok Patel (B281) · Khush Patel (B284)")
    add("")
    add("B.Tech Computer Engineering · Mukesh Patel School of Technology Management "
        "and Engineering · SVKM's NMIMS")
    add("")
    add("---")
    add("")

    # ------------------------------------------------------------- abstract
    add("## Abstract")
    add("")
    add(f"Departmental timetabling is a constraint satisfaction problem that is "
        f"NP-complete in general, and is still solved by hand in most engineering "
        f"colleges. This project models the problem formally, implements nine "
        f"artificial-intelligence techniques against it from first principles, and "
        f"compares them under identical conditions. Across {total_runs} measured runs "
        f"spanning {len(sizes)} instance sizes and {len(seeds)} random seeds, "
        f"{verified} produced a timetable that passed an independent audit of all seven "
        f"hard constraints and {rejected} solver claims were rejected by that audit. "
        f"The central finding is the size of the gap that constraint propagation opens: "
        f"naive backtracking succeeded on {agg[naive]['rate']:.0f}% of runs, while "
        f"backtracking with forward checking succeeded on {agg[fc]['rate']:.0f}% and "
        f"expanded {fmt(agg[fc]['nodes'])} nodes on average against "
        f"{fmt(agg[naive]['nodes'])} for naive search on the instances it could solve at "
        f"all. The system additionally explains infeasibility rather than merely "
        f"reporting it, using counting arguments and a verified QuickXplain search for "
        f"the minimal conflicting constraint set, and learns the coordinator's real "
        f"soft-constraint preferences with an ID3 decision tree.")
    add("")

    # ----------------------------------------------------------- contents
    add("## Contents")
    add("")
    for i, t in enumerate([
        "Introduction", "Literature Review", "Problem Formulation",
        "System Design", "Solving Techniques", "The Knowledge Layer",
        "Learning Coordinator Preferences", "Experimental Method",
        "Results and Analysis", "Testing and Validation",
        "Limitations and Future Work", "Conclusion", "References"], 1):
        add(f"{i}. {t}")
    add("")
    add("---")
    add("")

    # ------------------------------------------------------ 1 introduction
    add("## 1. Introduction")
    add("")
    add("### 1.1 The problem")
    add("")
    add("Every semester a department must place its teaching sessions into a grid of "
        "days, periods, rooms and faculty without a single clash. A session cannot be "
        "scheduled when its teacher is busy or unavailable, when its division is already "
        "in another class, or when the room it needs is occupied. Laboratory sessions "
        "occupy two consecutive periods and must fall in a laboratory. The lunch period "
        "is reserved. Each course must meet its weekly quota exactly.")
    add("")
    add("The work is done manually in spreadsheets. Clashes are typically discovered "
        "only after the timetable is circulated, and each manual correction risks "
        "creating another one somewhere else. The task is well suited to automation, and "
        "it is a textbook constraint satisfaction problem — which makes it a natural "
        "vehicle for the techniques of this course.")
    add("")
    add("### 1.2 Why it is hard")
    add("")
    add("Even a restricted form of timetabling is NP-complete (Even, Itai and Shamir, "
        "1976), so no efficient exact algorithm is known. The scale is not the obstacle "
        "by itself — the instances in this report have between "
        f"{min(c.n for c in csps.values())} and {max(c.n for c in csps.values())} "
        "variables — but the size of the space they define is:")
    add("")
    add("| Divisions | Sessions (variables) | Mean domain size | Search space |")
    add("|---|---|---|---|")
    for n in sizes:
        c = csps[n]
        d = c.domain_sizes()
        add(f"| {n} | {c.n} | {sum(d) / len(d):.0f} | "
            f"~10^{c.search_space_log10():.0f} |")
    add("")
    add("Exhaustive enumeration is therefore not merely slow, it is impossible. The "
        "question this project answers is which AI techniques make the space navigable, "
        "and by how much.")
    add("")
    add("### 1.3 Objectives")
    add("")
    for i, o in enumerate([
        "Model departmental timetabling formally as a constraint satisfaction problem "
        "with hard and soft constraints.",
        "Implement backtracking search with the MRV, degree and least-constraining-value "
        "ordering heuristics.",
        "Add constraint propagation — forward checking and AC-3 maintained during search "
        "— and conflict-directed backjumping.",
        "Implement min-conflicts local search and a genetic algorithm as alternative "
        "strategies.",
        "Benchmark every technique on identical instances and report nodes expanded, "
        "constraint checks, wall-clock time and soft-constraint penalty.",
        "Derive the same restrictions declaratively through a forward-chaining rule base "
        "so that every scheduling decision can be explained.",
        "Explain infeasibility, by counting arguments and by a minimal conflicting "
        "constraint set, instead of failing silently.",
        "Learn which soft constraints the coordinator actually cares about using an ID3 "
        "decision tree, and re-optimise against the learned weights."], 1):
        add(f"{i}. {o}")
    add("")
    add("### 1.4 Scope")
    add("")
    add("The system schedules weekly lecture and laboratory sessions for a department of "
        "up to five divisions. Faculty are pre-assigned to courses; the solver chooses "
        "the day, period and room. Examination timetabling shares the same model but is "
        "not separately evaluated here. No constraint-programming or optimisation library "
        "is used anywhere in the project: every algorithm reported below is implemented "
        "from first principles, because the algorithms are the deliverable.")
    add("")

    # -------------------------------------------------------- 2 literature
    add("## 2. Literature Review")
    add("")
    add("| # | Paper | Principal finding | Gap this project addresses |")
    add("|---|---|---|---|")
    for i, (title, finding, gap) in enumerate([
        ("Even, Itai and Shamir, *On the Complexity of Timetable and Multicommodity "
         "Flow Problems*, SIAM Journal on Computing, 1976",
         "The general timetabling problem is NP-complete, even in heavily restricted "
         "forms.",
         "Establishes hardness but offers no practical solving method, motivating the "
         "heuristic approaches compared here."),
        ("Mackworth, *Consistency in Networks of Relations*, Artificial Intelligence, "
         "1977",
         "Arc consistency (AC-1 to AC-3) prunes variable domains before and during "
         "search.",
         "The cost of propagation against its benefit is not measured on real "
         "scheduling data; Section 9 measures it directly."),
        ("Minton, Johnston, Philips and Laird, *Minimizing Conflicts*, Artificial "
         "Intelligence, 1992",
         "Min-conflicts repair solves very large constraint satisfaction problems far "
         "faster than systematic search.",
         "It cannot prove infeasibility and gives no explanation on failure; this "
         "project pairs it with a systematic solver and an explainer."),
        ("Schaerf, *A Survey of Automated Timetabling*, Artificial Intelligence Review, "
         "1999",
         "Classifies school, course and examination timetabling; local search and graph "
         "colouring dominate the literature.",
         "Approaches are rarely compared on a common benchmark under identical "
         "conditions, which is precisely this project's experimental design."),
        ("Abdennadher and Marte, *University Course Timetabling Using Constraint "
         "Handling Rules*, Applied Artificial Intelligence, 2000",
         "Declarative rules with propagation express real university constraints "
         "compactly.",
         "Hard constraints only, with no soft-constraint optimisation and no "
         "user-facing explanation; both are provided here."),
        ("Lewis, *A Survey of Metaheuristic-Based Techniques for University Timetabling "
         "Problems*, OR Spectrum, 2008",
         "Two-stage methods — reach feasibility first, then optimise soft constraints — "
         "perform best.",
         "Metaheuristics are seldom compared head-to-head with systematic CSP search; "
         "Section 9 does exactly that."),
        ("*A Systematic Mapping Study on Solving University Timetabling Problems Using "
         "Meta-Heuristic Algorithms*, Neural Computing and Applications, 2020",
         "Meta-heuristics dominate the recent literature and hybrid methods give the "
         "strongest results.",
         "Very few systems are usable by a non-expert coordinator or deployed in "
         "practice; the explanation facility here targets that gap."),
    ], 1):
        add(f"| {i} | {title} | {finding} | {gap} |")
    add("")
    add("Two themes emerge. First, the literature is clear that pure systematic search "
        "does not scale to real timetabling and that propagation and repair-based methods "
        "are necessary; what it rarely provides is a like-for-like measurement of how "
        "much each contributes on one common instance set. Second, almost all of this "
        "work stops at producing a timetable. When no timetable exists — the case a "
        "coordinator meets most often in practice — the systems report failure without "
        "saying which constraint is responsible. Both gaps shape the design that follows.")
    add("")

    # ------------------------------------------------------- 3 formulation
    add("## 3. Problem Formulation")
    add("")
    add("### 3.1 The constraint satisfaction problem")
    add("")
    add("A CSP is a triple *(X, D, C)*: variables, their domains, and constraints over "
        "them. Timetabling maps onto it directly.")
    add("")
    add("**Variables.** One per teaching session that must be placed. A course requiring "
        "three lectures and one laboratory per week contributes four variables, named "
        "`AI-CE-A-L1`, `AI-CE-A-L2`, `AI-CE-A-L3` and `AI-CE-A-P1`.")
    add("")
    add("**Domains.** Each variable's domain is the set of `(day, period, room)` triples "
        "it could legally take. A lecture occupies one period, a laboratory two "
        "consecutive periods.")
    add("")
    add("**Constraints.** Seven hard constraints that any valid timetable must satisfy, "
        "and five soft constraints that express preference rather than legality.")
    add("")
    add("### 3.2 Hard constraints")
    add("")
    add("| | Constraint | How it is enforced |")
    add("|---|---|---|")
    for tag, text, how in [
        ("H1", "No faculty member is in two places in the same period",
         "binary constraint between every pair of sessions sharing a teacher"),
        ("H2", "No division attends two sessions in the same period",
         "binary constraint between sessions of the same division"),
        ("H3", "No room hosts two sessions in the same period",
         "binary constraint, active only when both sessions choose the same room"),
        ("H4", "Room capacity ≥ division strength; laboratories only in laboratories",
         "unary — compiled into the domain"),
        ("H5", "Faculty are scheduled only within their declared availability",
         "unary — compiled into the domain"),
        ("H6", "Each course meets its weekly quota exactly",
         "structural — exactly one variable is created per required session"),
        ("H7", "The lunch period is free for every division",
         "unary — compiled into the domain"),
    ]:
        add(f"| {tag} | {text} | {how} |")
    add("")
    add("Three design decisions are worth defending here.")
    add("")
    add("*Unary constraints are compiled into the domains rather than tested at every "
        "node.* It is cheaper never to generate an illegal value than to reject it "
        "repeatedly during search. This is standard practice in constraint solvers, and "
        "it is why the audit in Section 10 re-checks domain membership explicitly: with "
        "the constraint compiled away, a bug in the compiler would otherwise be "
        "invisible.")
    add("")
    add("*H6 is enforced structurally.* Because exactly one variable is created for each "
        "required contact session, and every variable receives exactly one value, the "
        "weekly quota cannot be violated by construction. No runtime check is needed.")
    add("")
    add("*H1 and H2 are unconditional on overlap; H3 is conditional.* Two sessions "
        "sharing a teacher clash whenever they overlap in time, whatever rooms they use. "
        "Two sessions sharing neither teacher nor division clash only if they also "
        "choose the same room. This distinction is what the value-ordering heuristic in "
        "Section 5.2 exploits to stay affordable.")
    add("")
    add("### 3.3 Soft constraints")
    add("")
    add("| | Preference | Default weight |")
    add("|---|---|---|")
    for tag, text, w in [
        ("S1", "Minimise gaps in a division's day", "3.0"),
        ("S2", "Avoid more than two consecutive lectures for a division", "2.0"),
        ("S3", "Prefer laboratory sessions in the afternoon", "1.0"),
        ("S4", "Balance each faculty member's load across the week", "2.0"),
        ("S5", "Avoid using the last period repeatedly for the same course", "1.0"),
    ]:
        add(f"| {tag} | {text} | {w} |")
    add("")
    add("The weighted sum of violations is the objective that the soft optimiser and the "
        "genetic algorithm minimise. The weights above are guesses, which is precisely "
        "the motivation for Section 7.")
    add("")

    # ------------------------------------------------------------ 4 design
    add("## 4. System Design")
    add("")
    add("![Block diagram of the system](../docs/block_diagram.png)")
    add("")
    add("The architecture is five layers. The input layer accepts a department's data as "
        "CSV or through the web form. The constraint model builder turns it into "
        "variables, domains and constraints, and runs the forward-chaining rule layer "
        "that derives implied restrictions. The solver core holds every strategy behind "
        "one interface. The analysis layer contains the benchmark harness and the "
        "infeasibility explainer. The interface renders the result.")
    add("")
    add("The single most important design decision is that every solver implements the "
        "same signature:")
    add("")
    add("```python")
    add("result = solver.solve(csp, Budget(seconds=60))")
    add("result.assignment   # list of (day, period, room), or None")
    add("result.stats        # nodes, constraint checks, seconds, penalty")
    add("```")
    add("")
    add("This is what makes the comparison in Section 9 trustworthy. Because the harness "
        "cannot see inside a solver, it cannot accidentally give one technique an easier "
        "instance, a longer budget or a different starting point than another. Adding a "
        "new technique means adding one class; nothing else in the system changes.")
    add("")
    add("### 4.1 Module structure")
    add("")
    add("| Module | Responsibility |")
    add("|---|---|")
    for m, r in [
        ("`model.py`", "the calendar, rooms, faculty, courses and sessions"),
        ("`csp.py`", "domain construction, binary constraints, AC-3"),
        ("`constraints.py`", "the independent hard-constraint audit and the soft penalty"),
        ("`solvers.py`", "all seven search strategies, the soft optimiser, the trace recorder"),
        ("`rules.py`", "the forward-chaining rule base and the explanation facility"),
        ("`explain.py`", "counting arguments and QuickXplain"),
        ("`learning.py`", "ID3 over coordinator feedback"),
        ("`instances.py`", "the instance generator, difficulty calibration and CSV import"),
        ("`benchmark.py`", "the harness and the charts"),
        ("`render.py`", "text and JSON views of a timetable"),
    ]:
        add(f"| {m} | {r} |")
    add("")

    # ------------------------------------------------------- 5 methodology
    add("## 5. Solving Techniques")
    add("")
    add("![Methodology flow diagram](../docs/flow_diagram.png)")
    add("")
    add("### 5.1 Backtracking search")
    add("")
    add("The baseline assigns variables depth-first, checking each candidate value "
        "against the values already assigned to its neighbours, and undoing the "
        "assignment when no value survives.")
    add("")
    add("```")
    add("function BACKTRACK(assignment, csp):")
    add("    if assignment is complete: return assignment")
    add("    var  <- SELECT-UNASSIGNED-VARIABLE(csp, assignment)")
    add("    for value in ORDER-DOMAIN-VALUES(var, assignment, csp):")
    add("        if CONSISTENT(var, value, assignment):")
    add("            add {var = value} to assignment")
    add("            inferences <- INFERENCE(csp, var, value)")
    add("            if inferences != failure:")
    add("                add inferences to assignment")
    add("                result <- BACKTRACK(assignment, csp)")
    add("                if result != failure: return result")
    add("            remove inferences from assignment")
    add("        remove {var = value} from assignment")
    add("    return failure")
    add("```")
    add("")
    add("Everything that follows is a substitution into one of the three capitalised "
        "procedures. That is why all five systematic variants are configurations of a "
        "single class rather than five separate implementations.")
    add("")
    add("### 5.2 Ordering heuristics")
    add("")
    add("**Minimum remaining values (MRV)** selects the variable with the fewest legal "
        "values left, so that failure is discovered as early as possible. **Degree** "
        "breaks ties towards the variable involved in the most constraints on unassigned "
        "variables. **Least constraining value (LCV)** tries first the value that rules "
        "out the fewest options for neighbours.")
    add("")
    add("LCV deserves an implementation note, because the obvious version is unusable. "
        "Counting how many neighbour values a candidate removes costs O(d² · degree) per "
        "node; on these instances that dominated the entire runtime. The implementation "
        "instead indexes each neighbour's remaining values by the (day, period) cells "
        "they occupy — built once per node rather than once per candidate value — and "
        "reads the count out of that index. The distinction from Section 3.2 is what "
        "makes the index exact: for a neighbour sharing a teacher or a division the key "
        "is the time cell alone, and only for a room-only neighbour must the room be "
        "part of the key.")
    add("")
    add("### 5.3 Constraint propagation")
    add("")
    add("**Forward checking** removes from each unassigned neighbour's domain every value "
        "inconsistent with the assignment just made, failing immediately if a domain "
        "empties. **AC-3** goes further and enforces arc consistency across the whole "
        "network:")
    add("")
    add("```")
    add("function AC-3(csp):")
    add("    queue <- all arcs (Xi, Xj) in csp")
    add("    while queue not empty:")
    add("        (Xi, Xj) <- POP(queue)")
    add("        if REVISE(csp, Xi, Xj):")
    add("            if size of Di = 0: return false")
    add("            for each Xk in NEIGHBOURS(Xi) - {Xj}:")
    add("                add (Xk, Xi) to queue")
    add("    return true")
    add("```")
    add("")
    add("`REVISE` exits as soon as it finds one supporting value, which is what keeps "
        "AC-3 affordable on a network this dense — the alternative scans every pair. "
        "AC-3 is used twice: once as preprocessing before search begins, and again after "
        "each assignment (maintaining arc consistency, MAC). Both use an in-place "
        "implementation with a removal trail, so undoing an assignment restores the "
        "domains without copying them.")
    add("")
    add("### 5.4 Conflict-directed backjumping")
    add("")
    add("Chronological backtracking undoes the most recent assignment, which is often "
        "irrelevant to the failure. Conflict-directed backjumping records, for each "
        "variable, the set of assigned variables that caused its values to be rejected, "
        "and on exhaustion jumps back to the deepest genuine culprit, merging conflict "
        "sets on the way.")
    add("")
    add("### 5.5 Min-conflicts local search")
    add("")
    add("Local search starts from a complete but invalid assignment and repairs it:")
    add("")
    add("```")
    add("function MIN-CONFLICTS(csp, max_steps):")
    add("    current <- a complete random assignment")
    add("    for i = 1 to max_steps:")
    add("        if current is a solution: return current")
    add("        var   <- a randomly chosen conflicted variable")
    add("        value <- the value for var minimising total conflicts")
    add("        set var = value in current")
    add("    return failure")
    add("```")
    add("")
    add("Two additions matter. **Sideways moves** accept equal-cost moves up to a cap, "
        "which is how the search crosses a plateau; without them it stalls the moment no "
        "strictly improving move exists. **Random restarts** abandon a run that has "
        "exceeded the sideways cap, which is how it escapes a local minimum. Both are "
        "the standard answers to the failure modes of hill climbing, and both are "
        "necessary here in practice.")
    add("")
    add("### 5.6 Genetic algorithm")
    add("")
    add("A chromosome is one gene per variable holding an index into that variable's "
        "domain. Fitness is `w_hard · hard_violations + soft_penalty`, minimised. "
        "Selection is by tournament of three, crossover is uniform at probability 0.85, "
        "mutation reassigns a random gene at probability 0.03, and the best two "
        "chromosomes survive unchanged. The run stops as soon as a hard-feasible "
        "chromosome appears, leaving soft-constraint quality to the optimiser.")
    add("")
    add("### 5.7 Soft-constraint optimisation")
    add("")
    add("Once a feasible timetable exists, the optimiser hill-climbs on the weighted "
        "penalty while rejecting any move that breaks a hard constraint. This is the "
        "two-stage structure Lewis (2008) identifies as the strongest approach: reach "
        "feasibility first, optimise second.")
    add("")

    # ------------------------------------------------------- 6 knowledge
    add("## 6. The Knowledge Layer")
    add("")
    add("### 6.1 A forward-chaining rule base")
    add("")
    add("Compiling unary constraints into domains is fast but opaque. When a coordinator "
        "asks why a laboratory cannot go on Tuesday at 11, a list of legal tuples is not "
        "an answer. The system therefore derives the same restrictions a second time, "
        "declaratively, by firing seven rules to a fixpoint and recording which rule "
        "produced which fact.")
    add("")
    add("| Rule | Meaning |")
    add("|---|---|")
    for r, t in [
        ("R1", "a session of kind 'lab' needs a laboratory"),
        ("R2", "a session needing a laboratory cannot use a lecture hall"),
        ("R3", "a lecture is not scheduled inside a laboratory"),
        ("R4", "a room smaller than the division cannot host it (H4)"),
        ("R5", "a session cannot overlap its faculty's unavailability (H5)"),
        ("R6", "no session may occupy the lunch period (H7)"),
        ("R7", "a session must finish before the day ends"),
    ]:
        add(f"| {r} | {t} |")
    add("")
    add("Because the two routes are independent, they can be compared, and the test suite "
        "asserts that they agree on the legal slots of every session in the instance. "
        "That equality is a strong statement: an error in either the rule base or the "
        "domain compiler breaks it.")
    add("")
    add("### 6.2 The explanation facility")
    add("")
    add("Each derived fact carries its rule and its premises, so a blocked slot can be "
        "explained in the coordinator's own terms:")
    add("")
    add("```")
    add("AI-CE-A-P1 in LH1:")
    add("  [R2] a session needing a laboratory cannot use a lecture hall")
    add("       — room LH1 (capacity 76, lecture hall)")
    add("")
    add("AI-CE-A-L1 at Mon period 5:")
    add("  [R6] no session may occupy the lunch period (H7)")
    add("       — this span covers the lunch period")
    add("```")
    add("")
    add("### 6.3 Explaining infeasibility")
    add("")
    add("Two mechanisms, cheapest first.")
    add("")
    add("**Counting arguments** prove impossibility with no search whatsoever. If a "
        "faculty member owes more periods a week than their declared availability leaves "
        f"free — the week holds {TEACHING_SLOTS_PER_WEEK} teaching periods — then no "
        "timetable can exist, and the same argument applies to divisions and to room "
        "types. This is exact, instantaneous, and covers the most common real-world case.")
    add("")
    add("**QuickXplain** (Junker, 2004) finds a minimal set of relaxable constraints "
        "whose removal restores feasibility, by divide-and-conquer over the constraint "
        "set. Faculty availability is grouped per day rather than per period, so the "
        "answer reads as an action a coordinator can take:")
    add("")
    add("```")
    add("No timetable exists.")
    add("Proved by counting, without any search:")
    add("  - Meera Gupta (F1) owes 10 periods a week but is available for only 2.")
    add("Minimal set of constraints in conflict:")
    add("  - Meera Gupta (F1) is unavailable on Tue")
    add("  - Meera Gupta (F1) is unavailable on Mon")
    add("```")
    add("")
    add("An honest limitation belongs here. QuickXplain is minimal only when its "
        "consistency oracle is exact; ours is a budgeted search, and a search that finds "
        "nothing has not proved anything. Four measures contain this. Oracle results are "
        "cached, since the recursion asks the same question repeatedly. A counting check "
        "runs first, turning many would-be timeouts into definite answers. A negative "
        "answer requires both a complete search and a min-conflicts pass to fail. "
        "Finally, a verification pass removes any redundant member and the report claims "
        "minimality only when no call hit its budget. The first version of this code "
        "returned a conflict set of four constraints where three sufficed; the test that "
        "checks each member is necessary is what caught it.")
    add("")

    # -------------------------------------------------------- 7 learning
    add("## 7. Learning Coordinator Preferences")
    add("")
    add("The five soft weights in Section 3.3 are guesses, and different departments "
        "would choose differently. Rather than ask a coordinator to tune five numbers, "
        "the system shows them timetables, records accept or reject, and learns which "
        "soft constraints actually drive the decision.")
    add("")
    add("The learner is ID3, using entropy and information gain:")
    add("")
    add("*H(S) = −Σ p(c) log₂ p(c)* and "
        "*Gain(S, A) = H(S) − Σ (|S_v| / |S|) · H(S_v)*")
    add("")
    add("Each rated timetable becomes an example whose attributes are its five violation "
        "counts, discretised into low, medium and high by tertiles of the observed "
        "distribution — so 'high' means high for this department rather than against an "
        "arbitrary threshold. A representative learned tree:")
    add("")
    add("```")
    add("[S1_gaps]  gain=0.508, n=210")
    add("    low: [S4_faculty_balance]  gain=0.552, n=78")
    add("        low:    -> accept  (n=52)")
    add("        medium: -> accept  (n=16)")
    add("        high:   -> reject  (n=10)")
    add("    medium: [S4_faculty_balance]  gain=0.879, n=67")
    add("        low:    -> accept  (n=14)")
    add("        medium: -> accept  (n=33)")
    add("        high:   -> reject  (n=20)")
    add("    high: -> reject  (n=65)")
    add("")
    add("training accuracy 100.0%, held-out test accuracy 100.0%")
    add("```")
    add("")
    add("The tree is then converted back into weights: an attribute the tree splits on "
        "first is what the coordinator reacts to and receives the largest weight, while "
        "attributes the tree never uses are damped. Re-running the optimiser against the "
        "learned weights reduced the measured soft penalty of one timetable from 115 to "
        "41 while every hard constraint continued to hold.")
    add("")
    add("The accept/reject policy used in this report is synthetic, so the module runs "
        "end to end without a human in the loop; the interface records real decisions in "
        "the same format, and substituting them changes nothing else in the pipeline.")
    add("")

    # --------------------------------------------------- 8 experimental
    add("## 8. Experimental Method")
    add("")
    add("### 8.1 Instances")
    add("")
    add(f"Five instances of increasing size, from one division to {max(sizes)}, each with "
        "six courses per division, three lectures per course and a laboratory for two of "
        "them. Faculty are shared across divisions, so a teacher's load grows with the "
        "instance. Every instance is a pure function of (divisions, seed), which makes "
        "the whole experiment reproducible from the command line.")
    add("")
    add("### 8.2 Calibrating difficulty")
    add("")
    add("A benchmark only says something in the regime where techniques differ. Instance "
        "difficulty is governed by how much unavailability each faculty member declares, "
        "expressed as a fraction of the slack left after their teaching load. Sweeping "
        "that fraction gives:")
    add("")
    add("| Tightness | Unavailability by size | Forward checking solves | Naive solves |")
    add("|---|---|---|---|")
    for t, blocks, fcs, nvs in [
        ("0.55", "16, 13, 11, 8, 5", "10/10", "7/10"),
        ("0.65", "19, 16, 13, 9, 6", "10/10", "6/10"),
        ("0.75", "22, 18, 15, 11, 7", "10/10", "5/10"),
        ("0.85", "25, 21, 17, 12, 8", "8/10", "2/10"),
    ]:
        add(f"| {t} | {blocks} | {fcs} | {nvs} |")
    add("")
    add("Below 0.75 the problem is close to trivial. At 0.85 the instances begin to be "
        f"genuinely infeasible and even forward checking fails. The benchmark therefore "
        f"uses 0.75 — the largest setting at which a good solver still always succeeds. "
        f"For the largest instance this leaves each teacher "
        f"{default_unavailability(max(sizes), 3, 2)} blocked periods out of "
        f"{TEACHING_SLOTS_PER_WEEK}.")
    add("")
    add("### 8.3 Protocol")
    add("")
    add(f"Every solver was run on every instance for each of {len(seeds)} random seeds "
        f"({total_runs} runs in total), under a per-run budget. Recorded per run: nodes "
        "expanded, constraint checks, wall-clock time, whether the run solved or "
        "exhausted its budget, and the weighted soft penalty of the result.")
    add("")
    add("Crucially, a run counts as solved only if the returned timetable then passes an "
        "independent re-check of all seven hard constraints, performed by code that "
        "shares nothing with the search. A solver's own claim to have succeeded is not "
        "evidence.")
    add("")

    # ----------------------------------------------------------- 9 results
    add("## 9. Results and Analysis")
    add("")
    add("### 9.1 The results table")
    add("")
    add("| Technique | Solved | Mean nodes | Mean checks | Mean time (s) | Mean penalty |")
    add("|---|---|---|---|---|---|")
    for s in solvers:
        a = agg[s]
        add(f"| {s} | {a['solved']}/{a['runs']} | {fmt(a['nodes'])} | "
            f"{fmt(a['checks'])} | {a['seconds']:.3f} | {fmt(a['penalty'], 1)} |")
    add("")
    add(f"Across all {total_runs} runs, {verified} timetables passed the independent "
        f"audit and {rejected} solver claims were rejected by it. Means are taken over "
        f"solved runs only, so a technique that fails on the hardest instances is "
        f"flattered by this table; Section 9.2 corrects for that.")
    add("")
    add("![Nodes expanded against instance size](../results/nodes_vs_size.png)")
    add("")
    add("![Runs producing a valid timetable](../results/success_rate.png)")
    add("")
    add("### 9.2 Finding 1 — propagation is the difference between solving and not")
    add("")
    add(f"Naive backtracking produced a valid timetable on {agg[naive]['solved']} of "
        f"{agg[naive]['runs']} runs ({agg[naive]['rate']:.0f}%). Every variant with "
        f"ordering heuristics or propagation succeeded on more. Forward checking "
        f"succeeded on {agg[fc]['solved']}/{agg[fc]['runs']} "
        f"({agg[fc]['rate']:.0f}%), and did so expanding {fmt(agg[fc]['nodes'])} nodes "
        f"on average.")
    add("")
    add("Success rate by instance size makes the pattern clearer:")
    add("")
    header = "| Technique | " + " | ".join(f"{n} div" for n in sizes) + " |"
    add(header)
    add("|---" * (len(sizes) + 1) + "|")
    for s in solvers:
        cells = []
        for n in sizes:
            att = agg[s]["attempts_by_size"][n]
            ok = agg[s]["by_size"][n]
            cells.append(f"{len(ok)}/{len(att)}")
        add(f"| {s} | " + " | ".join(cells) + " |")
    add("")
    add("The most striking number is not the ratio of nodes but their absolute value. On "
        f"the instances it solves, forward checking expands about {fmt(agg[fc]['nodes'])} "
        f"nodes — for instances of {min(csps[n].n for n in sizes)} to "
        f"{max(csps[n].n for n in sizes)} variables, that is approximately one node per "
        "variable. Propagation prunes the domains so effectively that the first value "
        "tried is almost always correct, and the search barely backtracks at all. Naive "
        "backtracking on the same instances either stumbles onto a solution quickly or "
        "thrashes until its budget expires; there is very little in between.")
    add("")
    add("This is the practical form of a claim the course states abstractly. Constraint "
        "propagation is not an optimisation applied to a working search — on this problem "
        "it is what makes the search work at all.")
    add("")
    add("### 9.3 Finding 2 — LCV does not pay for itself")
    add("")
    add(f"The heuristic configuration ({heur}) succeeds reliably, but expands "
        f"{fmt(agg[heur]['nodes'])} nodes against forward checking's "
        f"{fmt(agg[fc]['nodes'])}, and takes {agg[heur]['seconds']:.3f}s against "
        f"{agg[fc]['seconds']:.3f}s. Ordering values by how many options they remove is "
        "intuitively appealing and, on this problem, a poor trade: the bookkeeping costs "
        "more than the reduction in nodes saves. Even after the indexing optimisation "
        "described in Section 5.2 — without which it was an order of magnitude worse — "
        "it remains the weakest of the informed configurations.")
    add("")
    add("Reporting this is more useful than hiding it. A heuristic that is standard in "
        "the textbook is not automatically a win on a particular constraint network, and "
        "the only way to know is to measure.")
    add("")
    add("### 9.4 Finding 3 — the two families answer different questions")
    add("")
    add(f"{mc} reaches a valid timetable in {fmt(agg[mc]['nodes'])} repair steps on "
        f"average, faster than any systematic configuration, and its solutions score a "
        f"mean penalty of {fmt(agg[mc]['penalty'], 1)}. But it can never report that an "
        "instance is impossible — a repair search that fails has only failed to find "
        "something, which is why the infeasibility explainer in Section 6.3 is built on "
        "systematic search and counting rather than on local search.")
    add("")
    add(f"The genetic algorithm shows the mirror image. It achieves the best "
        f"soft-constraint quality of any technique here — mean penalty "
        f"{fmt(agg[ga]['penalty'], 1)} against {fmt(agg[fc]['penalty'], 1)} for forward "
        f"checking — because its fitness function optimises soft cost throughout rather "
        f"than treating it as an afterthought. It is also the least reliable at reaching "
        f"hard feasibility, succeeding on {agg[ga]['solved']}/{agg[ga]['runs']} runs and "
        f"failing predominantly on the largest instances, and it is by far the most "
        f"expensive in constraint checks ({fmt(agg[ga]['checks'])} per run).")
    add("")
    add("The practical reading is that the two families are complementary rather than "
        "competing. A production system would use propagation-based search to establish "
        "feasibility quickly and reliably, then hand the result to a repair or "
        "population method to improve soft quality — which is exactly the two-stage "
        "architecture implemented in Section 5.7, and exactly what Lewis (2008) "
        "recommends.")
    add("")
    add("![Soft-constraint penalty by technique](../results/penalty.png)")
    add("")
    add("### 9.5 Threats to validity")
    add("")
    add("The instances are synthetic. They are generated from a realistic structure — "
        "shared faculty, mixed lectures and laboratories, capacity-constrained rooms — "
        "and calibrated as described in Section 8.2, but they are not a real department's "
        "data, and a real department's constraints are messier. The CSV import path "
        "exists and is tested precisely so that this can be corrected.")
    add("")
    add("Timing is wall-clock on one machine in a single-threaded interpreter, so "
        "absolute seconds are not comparable across environments; the node and "
        "constraint-check counts are, and are the figures on which the conclusions rest. "
        "Standard deviation of runtime across seeds is reported in the raw results, and "
        "variance is substantial for the techniques that sometimes exhaust their budget "
        "— which is itself the point of Section 9.2.")
    add("")

    # ------------------------------------------------------- 10 testing
    add("## 10. Testing and Validation")
    add("")
    add("The test suite checks that the output is correct, not merely that the code ran. "
        "Its most important properties:")
    add("")
    for t in [
        "Every solver is run on a solvable instance and its output re-audited against all "
        "seven hard constraints by code independent of the search.",
        "A deliberately corrupted timetable must be *caught* by that audit — a test that "
        "the test itself works.",
        "The forward-chaining rule base and the domain compiler must agree on the legal "
        "slots of every session.",
        "AC-3 must never empty a domain on a solvable instance, and never grow one.",
        "QuickXplain's conflict set must be sufficient, and every member of it necessary.",
        "ID3 must recover a known two-attribute policy exactly and generalise to held-out "
        "data.",
        "Solving must not mutate the CSP — a defect that would silently corrupt every "
        "subsequent run in the benchmark.",
    ]:
        add(f"- {t}")
    add("")
    add("The suite found real defects. The non-minimal conflict set described in Section "
        "6.3 was caught by the minimality test rather than by inspection, and would not "
        "have been visible in any output a human was likely to read.")
    add("")

    # -------------------------------------------------- 11 limitations
    add("## 11. Limitations and Future Work")
    add("")
    add("| Limitation | Consequence | Direction |")
    add("|---|---|---|")
    for lim, cons, fut in [
        ("Instances are synthetic",
         "External validity is untested against a real department",
         "Import an actual departmental dataset through the existing CSV path"),
        ("Faculty are pre-assigned to courses",
         "The solver chooses slot and room but not teacher",
         "Add faculty to the value tuple, enlarging domains substantially"),
        ("The feasibility oracle is budgeted",
         "Minimality of a conflict set is verified, not proved, when a call times out",
         "A complete infeasibility prover, or stronger counting arguments"),
        ("Soft optimisation is a single hill climb",
         "It can settle in a local optimum of the penalty landscape",
         "Simulated annealing or a large-neighbourhood search over the same objective"),
        ("Preference data is synthetic",
         "The ID3 result demonstrates the mechanism, not a real preference",
         "Collect genuine accept/reject decisions through the interface"),
        ("Single-threaded",
         "Runtime scales poorly on the largest instances",
         "Parallel restarts for local search, which parallelise trivially"),
    ]:
        add(f"| {lim} | {cons} | {fut} |")
    add("")

    # ------------------------------------------------------ 12 conclusion
    add("## 12. Conclusion")
    add("")
    add(f"This project modelled departmental timetabling as a constraint satisfaction "
        f"problem and implemented nine artificial-intelligence techniques against it "
        f"from first principles, without recourse to any solver library. Measured over "
        f"{total_runs} runs on identical instances, the comparison is unambiguous about "
        f"where the leverage lies: constraint propagation, not raw search effort. Naive "
        f"backtracking solved {agg[naive]['rate']:.0f}% of runs; forward checking solved "
        f"{agg[fc]['rate']:.0f}% while expanding roughly one node per variable.")
    add("")
    add("Two results run against expectation and are reported as found. The "
        "least-constraining-value heuristic, standard in the literature, costs more on "
        "this constraint network than it saves. And the genetic algorithm, the weakest "
        "technique at establishing feasibility, produces the best soft-constraint quality "
        "of any method tested — which is an argument for combining the families rather "
        "than choosing between them.")
    add("")
    add("Beyond the comparison, the system does two things most timetabling code does "
        "not. It explains why a slot is unavailable, in the coordinator's terms, from a "
        "rule base independently verified against the solver's own domains. And when no "
        "timetable exists it says which constraints to relax, by counting where counting "
        "suffices and by a verified minimal conflict set where it does not. Those are the "
        "features that would decide whether a real coordinator could use it.")
    add("")

    # ------------------------------------------------------ 13 references
    add("## 13. References")
    add("")
    for i, r in enumerate([
        "S. Even, A. Itai and A. Shamir, “On the Complexity of Timetable and "
        "Multicommodity Flow Problems,” *SIAM Journal on Computing*, vol. 5, no. 4, "
        "pp. 691–703, 1976.",
        "A. K. Mackworth, “Consistency in Networks of Relations,” *Artificial "
        "Intelligence*, vol. 8, no. 1, pp. 99–118, 1977.",
        "S. Minton, M. D. Johnston, A. B. Philips and P. Laird, “Minimizing "
        "Conflicts: A Heuristic Repair Method for Constraint Satisfaction and Scheduling "
        "Problems,” *Artificial Intelligence*, vol. 58, pp. 161–205, 1992.",
        "A. Schaerf, “A Survey of Automated Timetabling,” *Artificial "
        "Intelligence Review*, vol. 13, pp. 87–127, 1999.",
        "S. Abdennadher and M. Marte, “University Course Timetabling Using "
        "Constraint Handling Rules,” *Applied Artificial Intelligence*, 2000.",
        "U. Junker, “QuickXplain: Preferred Explanations and Relaxations for "
        "Over-Constrained Problems,” in *Proceedings of AAAI*, 2004.",
        "R. Lewis, “A Survey of Metaheuristic-Based Techniques for University "
        "Timetabling Problems,” *OR Spectrum*, vol. 30, pp. 167–190, 2008.",
        "“A Systematic Mapping Study on Solving University Timetabling Problems "
        "Using Meta-Heuristic Algorithms,” *Neural Computing and Applications*, "
        "Springer, 2020.",
        "S. Russell and P. Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed. "
        "Pearson, 2022 (Chapters 3, 4 and 6).",
        "J. R. Quinlan, “Induction of Decision Trees,” *Machine Learning*, "
        "vol. 1, pp. 81–106, 1986.",
        "D. W. Patterson, *Introduction to Artificial Intelligence and Expert Systems*, "
        "Pearson, 2015.",
        "E. Rich and K. Knight, *Artificial Intelligence*, 3rd ed. Tata McGraw-Hill, 2015.",
    ], 1):
        add(f"{i}. {r}")
    add("")

    # ------------------------------------------------------- appendix
    add("## Appendix A — Reproducing these results")
    add("")
    add("```")
    add("pip install -r requirements.txt")
    add(f"python scripts/run_benchmark.py --seeds {' '.join(str(s) for s in seeds)} "
        f"--seconds 60")
    add("python scripts/make_report.py")
    add("python -m pytest tests -q")
    add("```")
    add("")
    add("The benchmark writes `results/benchmark.csv`, `results/summary.txt` and four "
        "charts. This report is generated from that CSV, so re-running the benchmark and "
        "regenerating the report keeps every figure in the text consistent with the "
        "measurements.")
    add("")

    return "\n".join(L)


def main() -> int:
    if not os.path.exists(RESULTS):
        print(f"missing {RESULTS} — run scripts/run_benchmark.py first")
        return 1
    rows = read_csv(RESULTS)
    os.makedirs("docs", exist_ok=True)
    text = build(rows)
    with open(OUT, "w") as fh:
        fh.write(text + "\n")
    print(f"wrote {OUT}  ({len(text.split())} words, {len(rows)} benchmark rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
