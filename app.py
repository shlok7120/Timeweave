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
from timeweave.instances import generate, infeasible_example
from timeweave.model import DAYS, LUNCH_PERIOD, PERIOD_LABELS
from timeweave.learning import learn_preferences
from timeweave.render import faculty_load, to_json
from timeweave.rules import Explainer
from timeweave.solvers import SOLVER_KEYS, Budget, SoftOptimiser, Trace

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

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
    seconds = float(body.get("seconds", 20))

    csp = get_csp(divisions, seed)
    solver = SOLVER_KEYS.get(key, SOLVER_KEYS["fc"])(seed)
    result = solver.solve(csp, Budget(seconds=seconds))

    payload = {"solver": solver.name, "stats": result.stats.row()}
    if result.assignment is None:
        payload["solved"] = False
        payload["explanation"] = quickxplain(csp.instance, seconds=2.0).text()
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
    result = solver.solve(csp, Budget(seconds=float(body.get("seconds", 20))), trace=trace)

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
    ex = quickxplain(inst, seconds=2.0)
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
    res = solver.solve(csp, Budget(seconds=20))
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
