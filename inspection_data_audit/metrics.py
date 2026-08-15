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
    pairs = sorted((float(score), int(label)) for label, score in zip(labels, scores))
    positives = sum(label == 1 for _, label in pairs)
    negatives = len(pairs) - positives
    if not positives or not negatives:
        return float("nan")

    # Sum the average ranks of positives without materialising every rank.
    positive_rank_sum = 0.0
    start = 0
    while start < len(pairs):
        end = start + 1
        while end < len(pairs) and pairs[end][0] == pairs[start][0]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        positive_rank_sum += average_rank * sum(label == 1 for _, label in pairs[start:end])
        start = end

    mann_whitney = positive_rank_sum - positives * (positives + 1) / 2.0
    return mann_whitney / (positives * negatives)


def grouped_binary_auc(group_counts: Mapping[str, Mapping[int, int]]) -> float:
    """Exact AUROC when every row in a sorted group has the same score."""
    positives = sum(int(counts.get(1, 0)) for counts in group_counts.values())
    negatives = sum(int(counts.get(0, 0)) for counts in group_counts.values())
    if not positives or not negatives:
        return float("nan")
    negative_before = 0
    wins = 0.0
    for group in sorted(group_counts):
        counts = group_counts[group]
        positive = int(counts.get(1, 0))
        negative = int(counts.get(0, 0))
        wins += positive * negative_before + 0.5 * positive * negative
        negative_before += negative
    return wins / (positives * negatives)


def mean(values: Iterable[float]) -> float:
    vals = [v for v in values if not isnan(v)]
    return sum(vals) / len(vals) if vals else float("nan")
