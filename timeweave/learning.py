"""ID3 decision-tree learning of the coordinator's real preferences.

The five soft constraints all carry weights, and the weights are guesses.  In
practice a coordinator does not care about them equally: one department will
tolerate gaps but never an unbalanced faculty week, another the reverse.

Rather than ask them to tune five numbers, TimeWeave shows them timetables,
records accept/reject, and learns which soft constraints actually drive the
decision — with ID3, using entropy and information gain exactly as in Unit 5.
The learned tree is then turned back into weights the optimiser uses.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .constraints import DEFAULT_WEIGHTS, soft_penalty
from .csp import CSP
from .model import Value

Example = Dict[str, str]          # feature -> categorical value, plus "label"
LEVELS = ("low", "medium", "high")


# --------------------------------------------------------------------------- #
# Turning a timetable into categorical features
# --------------------------------------------------------------------------- #

def raw_features(csp: CSP, assignment: Sequence[Value]) -> Dict[str, int]:
    return dict(soft_penalty(csp, assignment).raw)


def make_thresholds(rows: Sequence[Dict[str, int]]) -> Dict[str, Tuple[float, float]]:
    """Per-feature tertile cut points, so 'high' means high *for this department*."""
    out: Dict[str, Tuple[float, float]] = {}
    for key in rows[0]:
        vals = sorted(r[key] for r in rows)
        lo = vals[len(vals) // 3]
        hi = vals[2 * len(vals) // 3]
        out[key] = (lo, hi)
    return out


def discretise(row: Dict[str, int], thresholds: Dict[str, Tuple[float, float]]) -> Example:
    ex: Example = {}
    for key, value in row.items():
        lo, hi = thresholds[key]
        ex[key] = "low" if value <= lo else ("medium" if value <= hi else "high")
    return ex


# --------------------------------------------------------------------------- #
# ID3
# --------------------------------------------------------------------------- #

@dataclass
class Node:
    feature: Optional[str] = None
    label: Optional[str] = None
    branches: Dict[str, "Node"] = field(default_factory=dict)
    support: int = 0
    gain: float = 0.0

    @property
    def is_leaf(self) -> bool:
        return self.feature is None


def entropy(examples: Sequence[Example], target: str = "label") -> float:
    if not examples:
        return 0.0
    counts = Counter(e[target] for e in examples)
    total = len(examples)
    return -sum((c / total) * math.log2(c / total) for c in counts.values() if c)


def information_gain(examples: Sequence[Example], feature: str,
                     target: str = "label") -> float:
    base = entropy(examples, target)
    total = len(examples)
    remainder = 0.0
    for value in {e[feature] for e in examples}:
        subset = [e for e in examples if e[feature] == value]
        remainder += (len(subset) / total) * entropy(subset, target)
    return base - remainder


def id3(examples: Sequence[Example], features: Sequence[str],
        target: str = "label", depth: int = 0, max_depth: int = 6) -> Node:
    labels = [e[target] for e in examples]
    if not labels:
        return Node(label="reject", support=0)
    if len(set(labels)) == 1:
        return Node(label=labels[0], support=len(examples))
    majority = Counter(labels).most_common(1)[0][0]
    if not features or depth >= max_depth:
        return Node(label=majority, support=len(examples))

    gains = [(information_gain(examples, f, target), f) for f in features]
    best_gain, best = max(gains)
    if best_gain <= 1e-9:
        return Node(label=majority, support=len(examples))

    node = Node(feature=best, support=len(examples), gain=best_gain)
    remaining = [f for f in features if f != best]
    for value in LEVELS:
        subset = [e for e in examples if e[best] == value]
        if not subset:
            node.branches[value] = Node(label=majority, support=0)
        else:
            node.branches[value] = id3(subset, remaining, target, depth + 1, max_depth)
    return node


def classify(node: Node, example: Example) -> str:
    while not node.is_leaf:
        branch = node.branches.get(example.get(node.feature, "low"))
        if branch is None:
            return "reject"
        node = branch
    return node.label or "reject"


def accuracy(node: Node, examples: Sequence[Example], target: str = "label") -> float:
    if not examples:
        return 0.0
    right = sum(1 for e in examples if classify(node, e) == e[target])
    return right / len(examples)


def render(node: Node, indent: str = "", edge: str = "") -> str:
    if node.is_leaf:
        return f"{indent}{edge}-> {node.label}  (n={node.support})\n"
    out = f"{indent}{edge}[{node.feature}]  gain={node.gain:.3f}, n={node.support}\n"
    for value, child in node.branches.items():
        out += render(child, indent + "    ", f"{value}: ")
    return out


def feature_depths(node: Node, depth: int = 0,
                   acc: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    acc = {} if acc is None else acc
    if node.is_leaf:
        return acc
    acc.setdefault(node.feature, depth)
    for child in node.branches.values():
        feature_depths(child, depth + 1, acc)
    return acc


def learned_weights(node: Node, base: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Turn a tree into soft-constraint weights.

    A feature the tree splits on first is what the coordinator actually reacts
    to, so it gets the largest weight; features the tree never uses are damped.
    """
    weights = dict(base or DEFAULT_WEIGHTS)
    depths = feature_depths(node)
    if not depths:
        return weights
    for key in weights:
        if key in depths:
            weights[key] = round(4.0 / (1 + depths[key]), 2) * 2
        else:
            weights[key] = 0.5
    return weights


# --------------------------------------------------------------------------- #
# Synthetic coordinator, so the module is runnable and testable end to end
# --------------------------------------------------------------------------- #

def coordinator_policy(ex: Example) -> str:
    """The hidden preference we ask ID3 to recover.

    This stands in for a real coordinator clicking accept/reject; swap it for
    a CSV of real decisions and nothing else changes.
    """
    if ex["S1_gaps"] == "high":
        return "reject"
    if ex["S4_faculty_balance"] == "high":
        return "reject"
    return "accept"


def sample_timetables(csp: CSP, base: Sequence[Value], n: int = 240,
                      seed: int = 0) -> List[Dict[str, int]]:
    """Perturb a valid timetable to get a spread of soft-constraint profiles."""
    rng = random.Random(seed)
    rows: List[Dict[str, int]] = []
    for _ in range(n):
        candidate = list(base)
        for _ in range(rng.randint(1, max(2, csp.n // 3))):
            i = rng.randrange(csp.n)
            if csp.domains[i]:
                candidate[i] = rng.choice(csp.domains[i])
        rows.append(raw_features(csp, candidate))
    return rows


@dataclass
class LearningReport:
    tree: Node
    train_accuracy: float
    test_accuracy: float
    thresholds: Dict[str, Tuple[float, float]]
    weights: Dict[str, float]
    n_train: int
    n_test: int

    def text(self) -> str:
        return (f"ID3 on {self.n_train} rated timetables "
                f"(held-out {self.n_test}):\n"
                f"  training accuracy {self.train_accuracy:.1%}, "
                f"test accuracy {self.test_accuracy:.1%}\n\n"
                + render(self.tree)
                + "\nLearned weights: "
                + ", ".join(f"{k}={v}" for k, v in sorted(self.weights.items())))


def learn_preferences(csp: CSP, base: Sequence[Value], n: int = 240,
                      seed: int = 0, policy=coordinator_policy) -> LearningReport:
    rows = sample_timetables(csp, base, n=n, seed=seed)
    thresholds = make_thresholds(rows)
    examples = []
    for row in rows:
        ex = discretise(row, thresholds)
        ex["label"] = policy(ex)
        examples.append(ex)

    rng = random.Random(seed + 1)
    rng.shuffle(examples)
    cut = int(len(examples) * 0.7)
    train, test = examples[:cut], examples[cut:]
    features = [k for k in examples[0] if k != "label"]
    tree = id3(train, features)
    return LearningReport(
        tree=tree,
        train_accuracy=accuracy(tree, train),
        test_accuracy=accuracy(tree, test),
        thresholds=thresholds,
        weights=learned_weights(tree),
        n_train=len(train), n_test=len(test))
