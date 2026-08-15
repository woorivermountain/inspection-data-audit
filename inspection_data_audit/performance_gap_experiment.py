from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from math import isnan, sqrt
from pathlib import Path
from typing import Iterable, Sequence

from metrics import binary_auc, mean


STRENGTHS = (0.0, 0.25, 0.5, 0.75, 1.0)
PATHOLOGIES = ("label_circularity", "verdict_overlay", "date_confound", "event_duplication")
FEATURES = ("stable_feature", "machine_score", "overlay_feature", "date_feature")
AUDIT_METRIC = {
    "label_circularity": "label_rule_recovery",
    "verdict_overlay": "overlay_auc",
    "date_confound": "date_auc",
    "event_duplication": "positive_row_event_ratio",
}


def generate_period(seed: int, n_events: int, pathology: str, strength: float, deployment: bool) -> list[dict[str, object]]:
    """Generate development or deployment rows.

    Development data receives the selected pathology. Deployment data contains
    new events, independent truth labels, and raw/no-shortcut features.
    """
    rng = random.Random(seed + (1_000_000 if deployment else 0))
    rows: list[dict[str, object]] = []
    period = "deploy" if deployment else "develop"
    for index in range(n_events):
        truth = int(rng.random() < 0.20)
        stable_feature = rng.gauss(1.25 if truth else -0.25, 1.15)
        machine_score = rng.gauss(0.65 if truth else -0.10, 1.20)
        machine_verdict = int(machine_score >= 0.45)
        observed_label = truth
        if not deployment and pathology == "label_circularity" and rng.random() < strength:
            observed_label = machine_verdict

        overlay_feature = rng.gauss(0.0, 1.0)
        if not deployment and pathology == "verdict_overlay":
            overlay_feature += 3.0 * strength * (2 * observed_label - 1)

        date_feature = rng.gauss(0.0, 1.0)
        if not deployment and pathology == "date_confound":
            date_feature += 2.5 * strength * (2 * observed_label - 1)

        event_id = f"{period}-E{index:05d}"
        base = {
            "event_id": event_id,
            "truth": truth,
            "observed_label": observed_label,
            "machine_verdict": machine_verdict,
            "stable_feature": stable_feature,
            "machine_score": machine_score,
            "overlay_feature": overlay_feature,
            "date_feature": date_feature,
        }
        copies = 1
        if not deployment and pathology == "event_duplication" and observed_label == 1:
            copies = 1 + round(9 * strength)
        for copy_index in range(copies):
            row = dict(base)
            row["row_id"] = f"{event_id}-{copy_index}"
            rows.append(row)
    return rows


def split_random_rows(rows: Sequence[dict[str, object]], seed: int, train_fraction: float = 0.70):
    order = list(range(len(rows)))
    random.Random(seed + 20_000).shuffle(order)
    cut = max(1, min(len(order) - 1, round(len(order) * train_fraction)))
    return [rows[i] for i in order[:cut]], [rows[i] for i in order[cut:]]


def split_grouped_events(rows: Sequence[dict[str, object]], seed: int, train_fraction: float = 0.70):
    events = sorted({str(row["event_id"]) for row in rows})
    random.Random(seed + 30_000).shuffle(events)
    cut = max(1, min(len(events) - 1, round(len(events) * train_fraction)))
    train_events = set(events[:cut])
    train = [row for row in rows if str(row["event_id"]) in train_events]
    test = [row for row in rows if str(row["event_id"]) not in train_events]
    return train, test


class DiagonalDiscriminant:
    """Small auditable classifier with optional event memorization."""

    def __init__(self) -> None:
        self.centers: dict[str, float] = {}
        self.scales: dict[str, float] = {}
        self.weights: dict[str, float] = {}
        self.event_labels: dict[str, float] = {}

    def fit(self, rows: Sequence[dict[str, object]], label_key: str = "observed_label") -> "DiagonalDiscriminant":
        positives = [row for row in rows if int(row[label_key]) == 1]
        negatives = [row for row in rows if int(row[label_key]) == 0]
        if not positives or not negatives:
            raise ValueError("학습 데이터에 두 클래스가 모두 필요합니다")
        for feature in FEATURES:
            values = [float(row[feature]) for row in rows]
            center = sum(values) / len(values)
            variance = sum((value - center) ** 2 for value in values) / max(1, len(values) - 1)
            scale = sqrt(max(variance, 1e-9))
            pos_mean = mean((float(row[feature]) - center) / scale for row in positives)
            neg_mean = mean((float(row[feature]) - center) / scale for row in negatives)
            self.centers[feature] = center
            self.scales[feature] = scale
            self.weights[feature] = pos_mean - neg_mean

        event_values: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            event_values[str(row["event_id"])].append(int(row[label_key]))
        self.event_labels = {event: sum(values) / len(values) for event, values in event_values.items()}
        return self

    def score(self, row: dict[str, object], memorize_events: bool = True) -> float:
        event_id = str(row["event_id"])
        if memorize_events and event_id in self.event_labels:
            return 100.0 if self.event_labels[event_id] >= 0.5 else -100.0
        total = 0.0
        for feature in FEATURES:
            standardized = (float(row[feature]) - self.centers[feature]) / self.scales[feature]
            total += self.weights[feature] * standardized
        return total


def evaluate(train: Sequence[dict[str, object]], test: Sequence[dict[str, object]], target: str) -> float:
    model = DiagonalDiscriminant().fit(train, label_key="observed_label")
    labels = [int(row[target]) for row in test]
    scores = [model.score(row, memorize_events=True) for row in test]
    return binary_auc(labels, scores)


def audit_metrics(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    labels = [int(row["observed_label"]) for row in rows]
    machine = [int(row["machine_verdict"]) for row in rows]
    overlay = [float(row["overlay_feature"]) for row in rows]
    date_feature = [float(row["date_feature"]) for row in rows]
    label_rule_recovery = sum(a == b for a, b in zip(labels, machine)) / len(rows)
    overlay_auc = binary_auc(labels, overlay)
    date_auc_raw = binary_auc(labels, date_feature)
    date_auc = max(date_auc_raw, 1.0 - date_auc_raw)
    positive_rows = sum(labels)
    positive_events = len({str(row["event_id"]) for row in rows if int(row["observed_label"]) == 1})
    return {
        "label_rule_recovery": label_rule_recovery,
        "overlay_auc": overlay_auc,
        "date_auc": date_auc,
        "positive_row_event_ratio": positive_rows / positive_events if positive_events else float("nan"),
    }


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = average
        start = end
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    mx, my = mean(x), mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denominator = sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return numerator / denominator if denominator else float("nan")


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return pearson(average_ranks(x), average_ranks(y))


def run(seeds: int, events: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for pathology in PATHOLOGIES:
        for strength in STRENGTHS:
            for seed in range(seeds):
                development = generate_period(seed, events, pathology, strength, deployment=False)
                deployment = generate_period(seed, events, pathology, strength, deployment=True)
                random_train, random_test = split_random_rows(development, seed)
                group_train, group_test = split_grouped_events(development, seed)
                random_auroc = evaluate(random_train, random_test, target="observed_label")
                group_auroc = evaluate(group_train, group_test, target="observed_label")
                deployment_auroc = evaluate(development, deployment, target="truth")
                audits = audit_metrics(development)
                results.append({
                    "pathology": pathology,
                    "strength": strength,
                    "seed": seed,
                    "development_events": events,
                    "development_rows": len(development),
                    **audits,
                    "random_auroc": random_auroc,
                    "group_auroc": group_auroc,
                    "deployment_auroc": deployment_auroc,
                    "random_group_gap": random_auroc - group_auroc,
                    "random_deployment_gap": random_auroc - deployment_auroc,
                })
    return results


def summarize(results: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, float], list[dict[str, object]]] = defaultdict(list)
    for row in results:
        groups[(str(row["pathology"]), float(row["strength"]))].append(row)
    columns = (
        "label_rule_recovery", "overlay_auc", "date_auc", "positive_row_event_ratio",
        "random_auroc", "group_auroc", "deployment_auroc", "random_group_gap", "random_deployment_gap",
    )
    output: list[dict[str, object]] = []
    for (pathology, strength), rows in sorted(groups.items()):
        item: dict[str, object] = {"pathology": pathology, "strength": strength, "runs": len(rows)}
        for column in columns:
            item[f"{column}_mean"] = mean(float(row[column]) for row in rows)
        output.append(item)
    return output


def correlations(results: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    for pathology in PATHOLOGIES:
        rows = [row for row in results if row["pathology"] == pathology]
        metric_name = AUDIT_METRIC[pathology]
        audit_values = [float(row[metric_name]) for row in rows]
        strengths = [float(row["strength"]) for row in rows]
        primary_gaps = [float(row["random_deployment_gap"]) for row in rows]
        group_gaps = [float(row["random_group_gap"]) for row in rows]
        clean = mean(float(row["random_deployment_gap"]) for row in rows if float(row["strength"]) == 0.0)
        maximum = mean(float(row["random_deployment_gap"]) for row in rows if float(row["strength"]) == 1.0)
        rho = spearman(audit_values, primary_gaps)
        delta = maximum - clean
        passed = rho >= 0.50 and delta >= 0.05
        output.append({
            "pathology": pathology,
            "audit_metric": metric_name,
            "rho_audit_primary_gap": rho,
            "rho_strength_primary_gap": spearman(strengths, primary_gaps),
            "rho_audit_group_gap": spearman(audit_values, group_gaps),
            "clean_primary_gap": clean,
            "max_primary_gap": maximum,
            "max_minus_clean": delta,
            "pilot_pass": passed,
        })
    return output


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, correlation_rows: Sequence[dict[str, object]], seeds: int, events: int) -> None:
    lines = [
        "# 2단계 성능 격차 예측 실험",
        "",
        f"- 병리·강도별 시드: {seeds}개",
        f"- 개발 사건 및 배치 사건: 각각 {events}개",
        "- 기준 모델: 대각선 선형 판별기 + 학습 사건 암기",
        "- 주 결과: 무작위 행 분할 AUROC - 새 사건·새 시점 배치 AUROC",
        "",
        "## 사전 고정한 내부 파일럿 기준",
        "",
        "- 감사 지표–주 성능 격차 Spearman ρ ≥ 0.50",
        "- 최대 강도 평균 격차 - 강도 0 평균 격차 ≥ 0.05",
        "",
        "## 결과",
        "",
        "| 병리 | 감사 지표 | ρ(감사, 격차) | clean 격차 | max 격차 | 증가 | 판정 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in correlation_rows:
        lines.append(
            f"| {row['pathology']} | {row['audit_metric']} | "
            f"{float(row['rho_audit_primary_gap']):.3f} | {float(row['clean_primary_gap']):.3f} | "
            f"{float(row['max_primary_gap']):.3f} | {float(row['max_minus_clean']):.3f} | "
            f"{'통과' if row['pilot_pass'] else '실패'} |"
        )
    passed = sum(bool(row["pilot_pass"]) for row in correlation_rows)
    lines.extend([
        "",
        "## 해석 제한",
        "",
        "이 결과는 미리 정의한 생성 과정과 기준 분류기 안에서의 내부 구성 타당도만 보여준다. 외부 산업 데이터에서 동일한 상관이 재현되기 전에는 일반화된 예측 도구라고 주장하지 않는다.",
        "",
        f"내부 파일럿 통과: **{passed}/{len(correlation_rows)}개 병리**",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="감사 지표가 무작위-배치 성능 격차를 예측하는지 합성 검증한다.")
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--events", type=int, default=300)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = run(args.seeds, args.events)
    summary = summarize(results)
    correlation_rows = correlations(results)
    write_csv(args.output_dir / "performance_gap_runs.csv", results)
    write_csv(args.output_dir / "performance_gap_summary.csv", summary)
    write_csv(args.output_dir / "performance_gap_correlations.csv", correlation_rows)
    write_report(args.output_dir / "performance_gap_report.md", correlation_rows, args.seeds, args.events)
    print(f"[완료] 성능 격차 실험 {len(results)}회")
    print(f"[내부 통과] {sum(bool(row['pilot_pass']) for row in correlation_rows)}/{len(correlation_rows)}개 병리")
    print(f"[결과] {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
