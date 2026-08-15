from __future__ import annotations

from collections import Counter, defaultdict
from math import isnan
from typing import Iterable, Mapping, Sequence


def safe_float(value, default=float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def accuracy(expected: Sequence[str], observed: Sequence[str]) -> float:
    if not expected:
        return float("nan")
    return sum(a == b for a, b in zip(expected, observed)) / len(expected)


def majority_accuracy(values: Iterable[str]) -> float:
    values = list(values)
    if not values:
        return float("nan")
    return Counter(values).most_common(1)[0][1] / len(values)


def group_resubstitution_accuracy(rows: Iterable[Mapping[str, str]], group: str, label: str) -> float:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[str(row[group])].append(str(row[label]))
    total = sum(len(v) for v in groups.values())
    if not total:
        return float("nan")
    correct = sum(Counter(v).most_common(1)[0][1] for v in groups.values())
    return correct / total


def binary_auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    """Tie-aware AUROC using the Mann-Whitney interpretation."""
    pos = [s for y, s in zip(labels, scores) if y == 1]
    neg = [s for y, s in zip(labels, scores) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def mean(values: Iterable[float]) -> float:
    vals = [v for v in values if not isnan(v)]
    return sum(vals) / len(vals) if vals else float("nan")
