"""Flask API and web interface for TimeWeave.

Run with::

    python app.py            # then open http://127.0.0.1:5000

The solver package has no idea this file exists — everything here is a thin
wrapper, which is what keeps the algorithms testable on their own.
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

from flask import Flask, jsonify, request, send_from_directory

from timeweave.constraints import DEFAULT_WEIGHTS, hard_violations, soft_penalty
from timeweave.csp import CSP
from timeweave.explain import quickxplain
from timeweave.instances import (
    from_dict, generate, infeasible_example, starter_department, to_dict,
)
from timeweave.model import DAYS, LUNCH_PERIOD, PERIOD_LABELS
from timeweave.learning import learn_preferences
from timeweave.render import faculty_load, to_json
from timeweave.rules import Explainer
from timeweave.validate import validate
from timeweave.solvers import SOLVER_KEYS, Budget, SoftOptimiser, Trace

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

# Vercel (and most serverless hosts) kill a request after a few seconds, so the
# solver budgets have to be shorter there than they are on a desktop. Forward
# checking solves a department of this size in well under a second, so the cap
# only bites on the deliberately slow strategies — which is worth knowing, and
# is reported back to the interface rather than hidden.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
MAX_SOLVE_SECONDS = 8.0 if SERVERLESS else 25.0
MAX_EXPLAIN_SECONDS = 3.0 if SERVERLESS else 5.0


def budget_seconds(requested) -> float:
    try:
        asked = float(requested)
    except (TypeError, ValueError):
        asked = MAX_SOLVE_SECONDS
    return max(1.0, min(asked, MAX_SOLVE_SECONDS))

_csp_cache: Dict[Tuple[int, int], CSP] = {}
_explainer_cache: Dict[Tuple[int, int], Explainer] = {}


def get_csp(divisions: int, seed: int) -> CSP:
    key = (divisions, seed)
    if key not in _csp_cache:
        _csp_cache[key] = CSP(generate(divisions=divisions, seed=seed))
    return _csp_cache[key]


def get_explainer(divisions: int, seed: int) -> Explainer:
    key = (divisions, seed)
    if key not in _explainer_cache:
        _explainer_cache[key] = Explainer(generate(divisions=divisions, seed=seed))
    return _explainer_cache[key]


@app.route("/")
def index():
    return send_from_directory(os.path.join(HERE, "web"), "index.html")


@app.route("/healthz")
def healthz():
    """A cheap endpoint that proves the deployment imported the solver."""
    from timeweave.instances import starter_department
    inst = starter_department()
    return jsonify({"ok": True, "serverless": SERVERLESS,
                    "maxSolveSeconds": MAX_SOLVE_SECONDS,
                    "instance": inst.summary()})


@app.route("/api/instance")
def api_instance():
    divisions = int(request.args.get("divisions", 3))
    seed = int(request.args.get("seed", 0))
    csp = get_csp(divisions, seed)
    return jsonify({
        "name": csp.instance.name,
        "summary": csp.instance.summary(),
        "csp": csp.describe(),
        "solvers": list(SOLVER_KEYS),
        "divisions": [{"id": d.id, "name": d.name} for d in csp.instance.divisions],
    })


@app.route("/api/solve", methods=["POST"])
def api_solve():
    body = request.get_json(force=True) or {}
    divisions = int(body.get("divisions", 3))
    seed = int(body.get("seed", 0))
    key = body.get("solver", "fc")
    optimise = bool(body.get("optimise", True))
    seconds = budget_seconds(body.get("seconds", 20))

    csp = get_csp(divisions, seed)
    solver = SOLVER_KEYS.get(key, SOLVER_KEYS["fc"])(seed)
    result = solver.solve(csp, Budget(seconds=seconds))

    payload = {"solver": solver.name, "stats": result.stats.row()}
    if result.assignment is None:
        payload["solved"] = False
        payload["explanation"] = quickxplain(csp.instance, seconds=MAX_EXPLAIN_SECONDS).text()
        return jsonify(payload)

    assignment = result.assignment
    payload["penaltyBefore"] = soft_penalty(csp, assignment).total
    if optimise:
        assignment, opt_stats = SoftOptimiser(steps=2500, seed=seed).improve(
            csp, assignment, budget=Budget(seconds=8))
        payload["optimiser"] = opt_stats.row()

    payload.update({
        "solved": True,
        "violations": hard_violations(csp, assignment),
        "penalty": soft_penalty(csp, assignment).raw,
        "penaltyTotal": soft_penalty(csp, assignment).total,
        "timetable": to_json(csp, assignment),
        "facultyLoad": faculty_load(csp, assignment),
    })
    return jsonify(payload)


@app.route("/api/trace", methods=["POST"])
def api_trace():
    """Replay the search itself, so the interface can animate it."""
    body = request.get_json(force=True) or {}
    divisions = int(body.get("divisions", 3))
    seed = int(body.get("seed", 0))
    key = body.get("solver", "fc")
    limit = min(int(body.get("limit", 4000)), 20000)

    csp = get_csp(divisions, seed)
    solver = SOLVER_KEYS.get(key, SOLVER_KEYS["fc"])(seed)
    trace = Trace(max_events=limit)
    result = solver.solve(csp, Budget(seconds=budget_seconds(body.get("seconds", 20))),
                          trace=trace)

    return jsonify({
        "solver": solver.name,
        "stats": result.stats.row(),
        "solved": result.stats.solved,
        "truncated": trace.truncated,
        "events": trace.events,
        "variables": [{"id": v.id, "course": v.course, "division": v.division,
                       "faculty": v.faculty, "kind": v.kind, "length": v.length,
                       "domain": len(csp.domains[i])}
                      for i, v in enumerate(csp.variables)],
        "rooms": [r.id for r in csp.instance.rooms],
        "days": list(DAYS),
        "periods": list(PERIOD_LABELS),
        "lunch": LUNCH_PERIOD,
        "divisions": [{"id": d.id, "name": d.name} for d in csp.instance.divisions],
    })


# --------------------------------------------------------------------------- #
# A department the user typed in themselves
# --------------------------------------------------------------------------- #

_custom_cache: Dict[str, CSP] = {}


def custom_csp(payload: dict) -> CSP:
    """Cache by content, so editing one field does not rebuild everything twice."""
    import json
    key = json.dumps(payload, sort_keys=True)
    if key not in _custom_cache:
        if len(_custom_cache) > 8:
            _custom_cache.clear()
        _custom_cache[key] = CSP(from_dict(payload))
    return _custom_cache[key]


def problems_payload(inst) -> list:
    return [{"severity": p.severity, "where": p.where, "message": p.message}
            for p in validate(inst)]


@app.route("/api/starter")
def api_starter():
    """A small, editable department so the editor is never blank."""
    return jsonify(to_dict(starter_department()))


@app.route("/api/example")
def api_example():
    """A larger generated department, for users who want something to poke at."""
    divisions = int(request.args.get("divisions", 3))
    seed = int(request.args.get("seed", 0))
    return jsonify(to_dict(generate(divisions=divisions, seed=seed)))


@app.route("/api/custom/validate", methods=["POST"])
def api_custom_validate():
    payload = request.get_json(force=True) or {}
    inst = from_dict(payload)
    problems = problems_payload(inst)
    body = {"summary": inst.summary(), "problems": problems,
            "errors": sum(1 for p in problems if p["severity"] == "error"),
            "warnings": sum(1 for p in problems if p["severity"] == "warning")}
    if not body["errors"]:
        try:
            body["csp"] = custom_csp(payload).describe()
        except Exception as exc:                       # a shape we did not anticipate
            body["problems"].append({"severity": "error", "where": "model",
                                     "message": str(exc)})
            body["errors"] += 1
    return jsonify(body)


@app.route("/api/custom/solve", methods=["POST"])
def api_custom_solve():
    body = request.get_json(force=True) or {}
    payload = body.get("department") or {}
    inst = from_dict(payload)
    problems = problems_payload(inst)
    if any(p["severity"] == "error" for p in problems):
        return jsonify({"solved": False, "problems": problems,
                        "message": "Fix the errors in the department data first."})

    csp = custom_csp(payload)
    key = body.get("solver", "fc")
    solver = SOLVER_KEYS.get(key, SOLVER_KEYS["fc"])(0)
    trace = Trace(max_events=int(body.get("traceLimit", 0))) if body.get("trace") else None
    result = solver.solve(csp, Budget(seconds=budget_seconds(body.get("seconds", 25))),
                          trace=trace)

    out = {"solver": solver.name, "stats": result.stats.row(), "problems": problems}
    if result.assignment is None:
        out["solved"] = False
        out["explanation"] = quickxplain(inst, seconds=MAX_EXPLAIN_SECONDS).text()
        return jsonify(out)

    assignment = result.assignment
    out["penaltyBefore"] = soft_penalty(csp, assignment).total
    if body.get("optimise", True):
        assignment, opt = SoftOptimiser(steps=3000, seed=0).improve(
            csp, assignment, budget=Budget(seconds=8))
        out["optimiser"] = opt.row()

    out.update({
        "solved": True,
        "violations": hard_violations(csp, assignment),
        "penalty": soft_penalty(csp, assignment).raw,
        "penaltyTotal": soft_penalty(csp, assignment).total,
        "timetable": to_json(csp, assignment),
        "facultyLoad": faculty_load(csp, assignment),
    })
    if trace is not None:
        out["trace"] = {
            "events": trace.events, "truncated": trace.truncated,
            "variables": [{"id": v.id, "course": v.course, "division": v.division,
                           "faculty": v.faculty, "kind": v.kind, "length": v.length}
                          for v in csp.variables],
            "rooms": [r.id for r in csp.instance.rooms],
            "days": list(DAYS), "periods": list(PERIOD_LABELS), "lunch": LUNCH_PERIOD,
            "divisions": [{"id": d.id, "name": d.name} for d in csp.instance.divisions],
            "solver": solver.name, "solved": result.stats.solved,
            "stats": result.stats.row(),
        }
    return jsonify(out)


@app.route("/api/custom/why", methods=["POST"])
def api_custom_why():
    body = request.get_json(force=True) or {}
    inst = from_dict(body.get("department") or {})
    ex = Explainer(inst)
    reasons = ex.explain(body["session"],
                         (int(body["day"]), int(body["period"]), int(body["room"])))
    return jsonify({"allowed": not reasons, "reasons": reasons, "rules": ex.summary()})


@app.route("/api/custom/learn", methods=["POST"])
def api_custom_learn():
    body = request.get_json(force=True) or {}
    payload = body.get("department") or {}
    inst = from_dict(payload)
    if any(p["severity"] == "error" for p in problems_payload(inst)):
        return jsonify({"error": "fix the department data first"}), 400
    csp = custom_csp(payload)
    res = SOLVER_KEYS["fc"](0).solve(csp, Budget(seconds=MAX_SOLVE_SECONDS))
    if res.assignment is None:
        return jsonify({"error": "no timetable to learn from — solve first"}), 400
    report = learn_preferences(csp, res.assignment, n=240, seed=0)
    return jsonify({"text": report.text(), "weights": report.weights,
                    "defaultWeights": DEFAULT_WEIGHTS,
                    "testAccuracy": report.test_accuracy})


@app.route("/api/custom/csv", methods=["POST"])
def api_custom_csv():
    """Hand the department back as the four CSV files, for the record."""
    import csv as _csv
    import io
    payload = request.get_json(force=True) or {}
    inst = from_dict(payload)
    files = {}

    def dump(rows):
        buf = io.StringIO()
        w = _csv.writer(buf)
        for r in rows:
            w.writerow(r)
        return buf.getvalue()

    files["rooms.csv"] = dump([["id", "capacity", "is_lab"]] +
                              [[r.id, r.capacity, int(r.is_lab)] for r in inst.rooms])
    files["divisions.csv"] = dump([["id", "name", "strength"]] +
                                  [[d.id, d.name, d.strength] for d in inst.divisions])
    files["faculty.csv"] = dump(
        [["id", "name", "unavailable"]] +
        [[f.id, f.name, " ".join(f"{DAYS[d]}:{p}" for d, p in sorted(f.unavailable))]
         for f in inst.faculty])
    files["courses.csv"] = dump(
        [["code", "name", "division", "faculty", "lectures", "labs"]] +
        [[c.code, c.name, c.division, c.faculty, c.lectures, c.labs]
         for c in inst.courses])
    return jsonify(files)


@app.route("/api/why", methods=["POST"])
def api_why():
    """The explanation facility: why can this session not go here?"""
    body = request.get_json(force=True) or {}
    divisions = int(body.get("divisions", 3))
    seed = int(body.get("seed", 0))
    ex = get_explainer(divisions, seed)
    reasons = ex.explain(body["session"],
                         (int(body["day"]), int(body["period"]), int(body["room"])))
    return jsonify({"allowed": not reasons, "reasons": reasons, "rules": ex.summary()})


@app.route("/api/infeasible")
def api_infeasible():
    inst = infeasible_example(seed=int(request.args.get("seed", 2)))
    ex = quickxplain(inst, seconds=MAX_EXPLAIN_SECONDS)
    return jsonify({
        "instance": inst.summary(),
        "text": ex.text(),
        "counting": [str(d) for d in ex.counting],
        "conflict": ex.readable,
        "oracleCalls": ex.oracle_calls,
    })


@app.route("/api/learn", methods=["POST"])
def api_learn():
    body = request.get_json(force=True) or {}
    divisions = int(body.get("divisions", 3))
    seed = int(body.get("seed", 0))
    csp = get_csp(divisions, seed)
    solver = SOLVER_KEYS["fc"](seed)
    res = solver.solve(csp, Budget(seconds=MAX_SOLVE_SECONDS))
    if res.assignment is None:
        return jsonify({"error": "no base timetable to learn from"}), 400
    report = learn_preferences(csp, res.assignment, n=240, seed=seed)
    return jsonify({
        "text": report.text(),
        "weights": report.weights,
        "defaultWeights": DEFAULT_WEIGHTS,
        "trainAccuracy": report.train_accuracy,
        "testAccuracy": report.test_accuracy,
    })


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
