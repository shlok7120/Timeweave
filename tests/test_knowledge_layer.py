"""Rule base, explanation facility, ID3 learner and the infeasibility explainer."""

import pytest

from timeweave.csp import CSP
from timeweave.explain import (
    necessary_conditions, quickxplain, relaxable_constraints,
)
from timeweave.instances import generate, infeasible_example
from timeweave.learning import (
    LEVELS, accuracy, classify, coordinator_policy, entropy, id3,
    information_gain, learn_preferences, learned_weights,
)
from timeweave.rules import Explainer, forward_chain
from timeweave.solvers import BacktrackingSolver, Budget

INST = generate(divisions=2, seed=13)


# --------------------------------------------------------------------------- #
# Forward chaining
# --------------------------------------------------------------------------- #

def test_rule_base_reaches_a_fixpoint():
    facts, passes = forward_chain(INST)
    assert passes >= 2                       # one pass to derive, one to confirm
    again, _ = forward_chain(INST)
    assert facts.facts == again.facts        # deterministic


def test_rule_base_agrees_with_the_domain_compiler():
    """Two independent routes to the same legal slots must not disagree."""
    csp = CSP(INST)
    ex = Explainer(INST)
    for i, s in enumerate(csp.variables):
        assert set(csp.domains[i]) == set(ex.legal_values(s.id, s.length)), s.id


def test_explanation_facility_names_a_rule_and_a_reason():
    csp = CSP(INST)
    ex = Explainer(INST)
    s = csp.variables[0]
    reasons = ex.explain(s.id, (0, 4, 0))          # the lunch period
    assert reasons and reasons[0].startswith("[R6]")
    assert "lunch" in reasons[0]
    legal = csp.domains[0][0]
    assert ex.explain(s.id, legal) == []


def test_explanation_covers_room_type():
    csp = CSP(INST)
    ex = Explainer(INST)
    lab = next(s for s in csp.variables if s.is_lab)
    hall = next(i for i, r in enumerate(INST.rooms) if not r.is_lab)
    reasons = ex.explain(lab.id, (0, 0, hall))
    assert any("laboratory" in r for r in reasons)


# --------------------------------------------------------------------------- #
# Infeasibility
# --------------------------------------------------------------------------- #

def test_counting_argument_proves_the_obvious_case():
    bad = infeasible_example(seed=2)
    diags = necessary_conditions(bad)
    assert diags
    assert any(d.kind == "faculty" and d.required > d.available for d in diags)


def test_feasible_instance_has_no_counting_violation():
    assert necessary_conditions(INST) == []


def test_quickxplain_returns_a_minimal_conflict():
    bad = infeasible_example(seed=2)
    ex = quickxplain(bad, seconds=2.0)
    assert not ex.feasible
    assert ex.conflict, "an over-constrained instance must yield a conflict set"
    tags = set(relaxable_constraints(bad))
    assert set(ex.conflict) <= tags

    # Minimality: relaxing the whole conflict set must make it solvable...
    csp = CSP(bad, dropped=set(ex.conflict))
    ok, _ = csp.ac3()
    assert ok
    solved = BacktrackingSolver(ordering="mrv", inference="fc").solve(
        csp, Budget(seconds=5))
    assert solved.stats.solved

    # ...while putting any single member back must break it again.
    for tag in ex.conflict:
        partial = CSP(bad, dropped=set(ex.conflict) - {tag})
        res = BacktrackingSolver(ordering="mrv", inference="fc").solve(
            partial, Budget(seconds=3, nodes=60_000))
        assert not res.stats.solved, f"{tag} was not needed — the set is not minimal"


def test_quickxplain_says_nothing_is_wrong_with_a_good_instance():
    ex = quickxplain(INST, seconds=3.0)
    assert ex.feasible
    assert ex.conflict == []


# --------------------------------------------------------------------------- #
# ID3
# --------------------------------------------------------------------------- #

def test_entropy_and_information_gain_on_a_known_case():
    data = [{"f": "low", "label": "accept"}, {"f": "low", "label": "accept"},
            {"f": "high", "label": "reject"}, {"f": "high", "label": "reject"}]
    assert entropy(data) == pytest.approx(1.0)
    assert information_gain(data, "f") == pytest.approx(1.0)
    pure = [{"f": "low", "label": "accept"}] * 4
    assert entropy(pure) == pytest.approx(0.0)


def test_id3_recovers_a_known_two_feature_policy():
    examples = []
    for a in LEVELS:
        for b in LEVELS:
            for c in LEVELS:
                ex = {"S1_gaps": a, "S4_faculty_balance": b, "S3_lab_afternoon": c}
                ex["label"] = coordinator_policy({**ex, "S1_gaps": a,
                                                  "S4_faculty_balance": b})
                examples.append(ex)
    tree = id3(examples, ["S1_gaps", "S4_faculty_balance", "S3_lab_afternoon"])
    assert accuracy(tree, examples) == pytest.approx(1.0)
    assert classify(tree, {"S1_gaps": "high", "S4_faculty_balance": "low",
                           "S3_lab_afternoon": "low"}) == "reject"
    assert classify(tree, {"S1_gaps": "low", "S4_faculty_balance": "low",
                           "S3_lab_afternoon": "high"}) == "accept"


def test_learning_pipeline_generalises_and_reweights():
    csp = CSP(INST)
    base = BacktrackingSolver(ordering="mrv", inference="fc", seed=3).solve(
        csp, Budget(seconds=20))
    report = learn_preferences(csp, base.assignment, n=240, seed=3)
    assert report.test_accuracy >= 0.9
    # the features the hidden policy uses must outrank the ones it ignores
    w = report.weights
    assert w["S1_gaps"] > w["S2_consecutive"]
    assert w["S4_faculty_balance"] > w["S5_last_period"]


def test_weights_fall_back_to_defaults_for_a_stump():
    from timeweave.learning import Node
    leaf = Node(label="accept", support=10)
    w = learned_weights(leaf)
    assert set(w) == {"S1_gaps", "S2_consecutive", "S3_lab_afternoon",
                      "S4_faculty_balance", "S5_last_period"}


# --------------------------------------------------------------------------- #
# Validation of user-entered departments
# --------------------------------------------------------------------------- #

def test_starter_department_is_clean_and_solvable():
    from timeweave.instances import starter_department
    from timeweave.validate import validate as validate_inst
    inst = starter_department()
    assert validate_inst(inst) == []
    csp = CSP(inst)
    res = BacktrackingSolver(ordering="mrv", inference="fc").solve(csp, Budget(seconds=20))
    assert res.stats.solved


def test_json_round_trip_preserves_the_instance():
    from timeweave.instances import from_dict, starter_department, to_dict
    inst = starter_department()
    back = from_dict(to_dict(inst))
    assert [c.code for c in back.courses] == [c.code for c in inst.courses]
    assert {f.id: f.unavailable for f in back.faculty} == \
           {f.id: f.unavailable for f in inst.faculty}
    assert len(CSP(back).variables) == len(CSP(inst).variables)


def test_validation_catches_what_a_user_typing_data_gets_wrong():
    from timeweave.instances import from_dict, starter_department, to_dict
    from timeweave.validate import validate as validate_inst

    dep = to_dict(starter_department())

    dangling = {**dep, "courses": [{**dep["courses"][0], "faculty": "F99"}]}
    msgs = [p.message for p in validate_inst(from_dict(dangling))]
    assert any("F99" in m for m in msgs)

    dupes = {**dep, "divisions": dep["divisions"] + [dep["divisions"][0]]}
    assert any("appears 2 times" in p.message for p in validate_inst(from_dict(dupes)))

    no_lab = {**dep, "rooms": [r for r in dep["rooms"] if not r["isLab"]]}
    problems = validate_inst(from_dict(no_lab))
    assert any("laboratory" in p.message and p.severity == "error" for p in problems)

    tiny = {**dep, "rooms": [{**r, "capacity": 5} for r in dep["rooms"]]}
    assert any(p.severity == "error" for p in validate_inst(from_dict(tiny)))


def test_validation_separates_impossible_from_inconsistent():
    """A department can be perfectly well-formed and still have no timetable."""
    from timeweave.instances import infeasible_example
    from timeweave.validate import validate as validate_inst
    problems = validate_inst(infeasible_example(seed=2))
    assert problems
    assert all(p.severity == "warning" for p in problems), \
        "over-constrained data is impossible, not malformed"
