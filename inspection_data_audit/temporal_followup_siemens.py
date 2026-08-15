from __future__ import annotations

import argparse
import csv
from bisect import bisect_right
from dataclasses import dataclass, asdict
from math import floor, isnan
from pathlib import Path

from external_validate_siemens import (
    evaluate_models,
    fit_models,
    inventory,
    is_train,
    load_mapping,
    rows_from,
    validate_files,
)


# Siemens 결과를 본 뒤 만든 개발 후보다. 같은 데이터의 확증 기준으로 사용하지 않는다.
TARGET_SLIP_RATE = 0.01
TARGET_VOLUME_REDUCTION = 0.40
CANDIDATE_TIME_AUC = 0.60
CANDIDATE_WORST_SLICE_DROP = 0.05
MIN_SLICE_POSITIVES = 100


@dataclass
class BusinessMetrics:
    rows: int
    positives: int
    negatives: int
    tp: int
    fp: int
    tn: int
    fn: int
    slip_rate: float
    volume_reduction: float
    manual_review_rate: float
    target_met: bool


def select_threshold(labels: list[int], scores: list[float], target_slip: float = TARGET_SLIP_RATE) -> float:
    """Highest observed positive-score cutoff with training slip no greater than target."""
    positive_scores = sorted(score for label, score in zip(labels, scores) if label == 1)
    if not positive_scores:
        raise ValueError("임계값을 정할 양성 학습 표본이 없습니다")
    allowed_false_negatives = floor(len(positive_scores) * target_slip)
    return positive_scores[min(allowed_false_negatives, len(positive_scores) - 1)]


def business_metrics(labels: list[int], scores: list[float], threshold: float) -> BusinessMetrics:
    tp = fp = tn = fn = 0
    for label, score in zip(labels, scores):
        predicted_defect = score >= threshold
        if label == 1 and predicted_defect:
            tp += 1
        elif label == 1:
            fn += 1
        elif predicted_defect:
            fp += 1
        else:
            tn += 1
    positives = tp + fn
    negatives = tn + fp
    slip = fn / positives if positives else float("nan")
    volume = tn / negatives if negatives else float("nan")
    manual = (tp + fp) / max(1, positives + negatives)
    return BusinessMetrics(
        rows=positives + negatives,
        positives=positives,
        negatives=negatives,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        slip_rate=slip,
        volume_reduction=volume,
        manual_review_rate=manual,
        target_met=slip <= TARGET_SLIP_RATE and volume >= TARGET_VOLUME_REDUCTION,
    )


def collect_future_scores(
    dataset: Path,
    models,
    cutoff: str,
    slice_ends: list[str],
    seed: int,
) -> tuple[list[int], list[float], list[list[int]], list[list[float]]]:
    train_labels: list[int] = []
    train_scores: list[float] = []
    slice_labels: list[list[int]] = [[] for _ in range(5)]
    slice_scores: list[list[float]] = [[] for _ in range(5)]
    for row_index, row in rows_from(dataset):
        timestamp = row["timestamp"]
        label = int(row["class"])
        score = models[("future_time", row["inspection_type"])].score(row)
        if is_train("future_time", row_index, timestamp, cutoff, seed):
            train_labels.append(label)
            train_scores.append(score)
        else:
            index = bisect_right(slice_ends, timestamp)
            slice_labels[index].append(label)
            slice_scores[index].append(score)
    return train_labels, train_scores, slice_labels, slice_scores


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float) -> str:
    return "NA" if isnan(value) else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Siemens 결과에서 시간·업무지표 후속 프로토콜을 탐색 개발한다.")
    parser.add_argument("--dataset", type=Path, default=Path("external_data/siemens/dataset.csv"))
    parser.add_argument("--mapping", type=Path, default=Path("external_data/siemens/mapping.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--skip-hash", action="store_true")
    args = parser.parse_args()

    validate_files(args.dataset, args.mapping, verify_hash=not args.skip_hash)
    mapping = load_mapping(args.mapping)
    print("[1/4] 시간축·독립 단위 재구성")
    audit = inventory(args.dataset, mapping)
    cutoff = str(audit["cutoff"])
    slice_ends = list(audit["slice_ends"])
    print("[2/4] 고정 선형 판별기 학습")
    models, _ = fit_models(args.dataset, mapping, cutoff, args.seed)
    print("[3/4] 무작위 기준과 미래 구간 AUROC 계산")
    evaluations, slices = evaluate_models(args.dataset, models, cutoff, slice_ends, args.seed)
    print("[4/4] 학습 임계값을 고정하고 미래 업무지표 계산")
    train_y, train_s, slice_y, slice_s = collect_future_scores(
        args.dataset, models, cutoff, slice_ends, args.seed
    )
    threshold = select_threshold(train_y, train_s)
    training = business_metrics(train_y, train_s, threshold)
    future_y = [label for labels in slice_y for label in labels]
    future_s = [score for scores in slice_s for score in scores]
    future = business_metrics(future_y, future_s, threshold)
    slice_business = [business_metrics(labels, scores, threshold) for labels, scores in zip(slice_y, slice_s)]

    random_auc = float(evaluations["random_row"]["auroc"])
    valid_slices = [row for row in slices if int(row["positives"]) >= MIN_SLICE_POSITIVES]
    worst_slice_auc = min(float(row["auroc"]) for row in valid_slices)
    worst_slice_drop = random_auc - worst_slice_auc
    timestamp_auc = float(audit["timestamp_auc"])
    candidate_time_flag = timestamp_auc >= CANDIDATE_TIME_AUC
    candidate_slice_flag = worst_slice_drop >= CANDIDATE_WORST_SLICE_DROP

    rows: list[dict[str, object]] = [
        {"period": "training", "slice": 0, "threshold": threshold, **asdict(training)},
        {"period": "future_all", "slice": 0, "threshold": threshold, **asdict(future)},
    ]
    rows.extend(
        {"period": "future_slice", "slice": index, "threshold": threshold, **asdict(metrics)}
        for index, metrics in enumerate(slice_business, 1)
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "temporal_followup_business.csv", rows)

    failures = sum(not item.target_met for item in slice_business)
    lines = [
        "# 4단계 개발 실험 — 시간 강건성과 업무지표",
        "",
        "> 이 실험은 Siemens 외부 결과를 본 뒤 설계한 사후 개발 분석이다. 동일 데이터에서 확증된 지표로 주장하지 않으며, 다음 외부 데이터에 적용하기 위한 후보 프로토콜을 만든다.",
        "",
        "## 후보 프로토콜",
        "",
        f"- 학습 목표: defect slip rate ≤ {TARGET_SLIP_RATE:.0%}",
        f"- 동시에 필요한 false-call volume reduction: ≥ {TARGET_VOLUME_REDUCTION:.0%}",
        f"- 클래스 불균형 대응 시간 신호 후보: timestamp-only 방향무관 AUROC ≥ {CANDIDATE_TIME_AUC:.2f}",
        f"- 국소 붕괴 후보: 무작위 AUROC - 최악 미래 구간 AUROC ≥ {CANDIDATE_WORST_SLICE_DROP:.2f}",
        f"- 유효 미래 구간: true defect ≥ {MIN_SLICE_POSITIVES}건",
        "",
        "임계값은 앞 70% 학습 기간에서 slip rate 1% 이하를 만족하는 가장 높은 관측 양성 점수로 정하고, 이후 변경하지 않았다.",
        "",
        "## 시간 강건성 결과",
        "",
        "| 항목 | 값 | 후보 판정 |",
        "|---|---:|---|",
        f"| timestamp-only 방향무관 AUROC | {timestamp_auc:.3f} | {'발동' if candidate_time_flag else '미발동'} |",
        f"| 행 무작위 AUROC | {random_auc:.3f} | 기준 |",
        f"| 최악 유효 미래 구간 AUROC | {worst_slice_auc:.3f} | true defect ≥ {MIN_SLICE_POSITIVES} |",
        f"| 무작위-최악 구간 격차 | {worst_slice_drop:.3f} | {'발동' if candidate_slice_flag else '미발동'} |",
        "",
        "## 업무지표 결과",
        "",
        "| 구간 | 행 | true defect | slip rate | volume reduction | manual review | 목표 |",
        "|---|---:|---:|---:|---:|---:|---|",
        f"| 학습 기간 | {training.rows:,} | {training.positives:,} | {fmt(training.slip_rate)} | {fmt(training.volume_reduction)} | {fmt(training.manual_review_rate)} | {'달성' if training.target_met else '미달'} |",
        f"| 미래 전체 | {future.rows:,} | {future.positives:,} | {fmt(future.slip_rate)} | {fmt(future.volume_reduction)} | {fmt(future.manual_review_rate)} | {'달성' if future.target_met else '미달'} |",
    ]
    for index, metrics in enumerate(slice_business, 1):
        lines.append(
            f"| 미래 {index} | {metrics.rows:,} | {metrics.positives:,} | {fmt(metrics.slip_rate)} | "
            f"{fmt(metrics.volume_reduction)} | {fmt(metrics.manual_review_rate)} | "
            f"{'달성' if metrics.target_met else '미달'} |"
        )
    lines.extend([
        "",
        "## 해석",
        "",
        f"고정 임계값은 미래 5구간 중 **{failures}/5개**에서 두 업무 목표를 동시에 만족하지 못했다. 그러나 학습 기간도 volume reduction이 {training.volume_reduction:.1%}에 불과해 40% 목표를 달성하지 못했다. 따라서 미래 실패 전체를 시간 드리프트의 결과로 해석할 수 없고, 이 선형 기준 모델 자체의 업무 적합성이 부족하다.",
        "",
        f"그럼에도 행 무작위 AUROC {random_auc:.3f}만 보면 이 실패가 드러나지 않는다. 이 실험이 직접 지지하는 결론은 시간 드리프트의 인과적 재현이 아니라, AUROC만으로 운영 가능성을 판단할 수 없으며 학습 구간의 slip rate·volume reduction feasibility를 먼저 확인해야 한다는 점이다.",
        "",
        "## 다음 데이터에 고정할 후보",
        "",
        "1. timestamp-only AUROC와 최악 유효 시간 구간 하락폭을 사전 감사 지표로 계산한다.",
        "2. 학습 기간에서만 운영 임계값을 정하고 미래 데이터에서는 재조정하지 않는다.",
        "3. AUROC보다 slip rate와 volume reduction의 동시 달성 여부를 우선 보고한다.",
        "4. 기준 모델이 학습 기간의 두 업무 목표부터 충족한 경우에만 미래 실패를 시간 강건성 문제로 해석한다.",
        "5. 위 수치 기준은 다음 외부 데이터 실행 전에 별도 태그로 고정하며, Siemens 결과는 개발 세트로만 취급한다.",
        "",
    ])
    (args.output_dir / "temporal_followup_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[완료] 시간·업무지표 후속 실험: 미래 목표 미달 {failures}/5개 구간")


if __name__ == "__main__":
    main()
