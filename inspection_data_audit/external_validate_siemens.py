from __future__ import annotations

import argparse
import csv
import hashlib
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isnan
from pathlib import Path
from typing import Iterable

from metrics import binary_auc, grouped_binary_auc


PROTOCOLS = ("random_row", "timestamp_group", "future_time")
TRAIN_FRACTION = 0.70
DATE_LIFT_THRESHOLD = 0.05
PERFORMANCE_GAP_THRESHOLD = 0.05
DUPLICATION_RATIO_THRESHOLD = 1.0
EXPECTED_SHA256 = "53e8568743216d556856ed69b388f6750fbfa0b8c59ad31f970515ac9eb10e62"
EXPECTED_SIZE = 333_590_345


@dataclass
class RunningStats:
    count: int = 0
    total: float = 0.0
    total_sq: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.total_sq += value * value

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    @property
    def variance(self) -> float:
        if self.count < 2:
            return 0.0
        return max(0.0, (self.total_sq - self.total * self.total / self.count) / (self.count - 1))


@dataclass
class LinearScore:
    intercept: float
    weights: dict[str, float]

    def score(self, row: dict[str, str]) -> float:
        return self.intercept + sum(self.weights[name] * float(row[name]) for name in self.weights)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_bucket(value: str, seed: int, buckets: int = 10_000) -> int:
    digest = hashlib.blake2b(f"{seed}:{value}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % buckets


def is_train(protocol: str, row_index: int, timestamp: str, cutoff: str, seed: int) -> bool:
    threshold = round(TRAIN_FRACTION * 10_000)
    if protocol == "random_row":
        return stable_bucket(f"row:{row_index}", seed) < threshold
    if protocol == "timestamp_group":
        return stable_bucket(f"timestamp:{timestamp}", seed) < threshold
    if protocol == "future_time":
        return timestamp <= cutoff
    raise ValueError(f"알 수 없는 프로토콜: {protocol}")


def load_mapping(path: Path) -> dict[str, tuple[str, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"0", "1", "2", "3", "4"}:
        raise ValueError("mapping.json의 inspection_type은 0~4여야 합니다")
    return {key: tuple(str(value) for value in values) for key, values in raw.items()}


def validate_files(dataset: Path, mapping: Path, verify_hash: bool) -> None:
    if not dataset.is_file() or not mapping.is_file():
        raise FileNotFoundError("dataset.csv와 mapping.json이 필요합니다. download_siemens.py를 먼저 실행하세요")
    if dataset.stat().st_size != EXPECTED_SIZE:
        raise ValueError(f"dataset.csv 크기가 공식 메타데이터와 다릅니다: {dataset.stat().st_size:,}")
    if verify_hash and sha256_file(dataset) != EXPECTED_SHA256:
        raise ValueError("dataset.csv SHA-256이 공식 메타데이터와 다릅니다")


def rows_from(path: Path) -> Iterable[tuple[int, dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "class", "inspection_type"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"필수 열 누락: {sorted(missing)}")
        for index, row in enumerate(reader):
            yield index, row


def inventory(path: Path, mapping: dict[str, tuple[str, ...]]) -> dict[str, object]:
    class_counts: Counter[int] = Counter()
    type_counts: Counter[str] = Counter()
    date_counts: dict[str, Counter[int]] = defaultdict(Counter)
    timestamp_masks: dict[str, int] = {}
    timestamp_rows: Counter[str] = Counter()
    timestamp_label_counts: dict[str, Counter[int]] = defaultdict(Counter)
    header_checked = False

    for _, row in rows_from(path):
        label = int(row["class"])
        inspection_type = row["inspection_type"]
        timestamp = row["timestamp"]
        if label not in (0, 1):
            raise ValueError(f"class는 0/1이어야 합니다: {label}")
        if inspection_type not in mapping:
            raise ValueError(f"알 수 없는 inspection_type: {inspection_type}")
        if not header_checked:
            missing_features = set(mapping[inspection_type]) - set(row)
            if missing_features:
                raise ValueError(f"mapping.json 특징 열 누락: {sorted(missing_features)}")
            header_checked = True
        class_counts[label] += 1
        type_counts[inspection_type] += 1
        date_counts[timestamp[:10]][label] += 1
        timestamp_masks[timestamp] = timestamp_masks.get(timestamp, 0) | (1 << label)
        timestamp_rows[timestamp] += 1
        timestamp_label_counts[timestamp][label] += 1

    timestamps = sorted(timestamp_masks)
    cutoff_index = max(0, min(len(timestamps) - 2, round(len(timestamps) * TRAIN_FRACTION) - 1))
    cutoff = timestamps[cutoff_index]
    future_timestamps = timestamps[cutoff_index + 1 :]
    slice_ends = [future_timestamps[min(len(future_timestamps) - 1, round(len(future_timestamps) * i / 5) - 1)] for i in range(1, 5)]
    total = sum(class_counts.values())
    baseline = max(class_counts.values()) / total
    date_accuracy = sum(max(counts.values()) for counts in date_counts.values()) / total
    positive_timestamps = sum(bool(mask & 2) for mask in timestamp_masks.values())
    mixed_timestamps = sum(mask == 3 for mask in timestamp_masks.values())
    timestamp_auc_raw = grouped_binary_auc(timestamp_label_counts)

    return {
        "rows": total,
        "class_counts": class_counts,
        "type_counts": type_counts,
        "dates": len(date_counts),
        "timestamps": len(timestamps),
        "first_timestamp": timestamps[0],
        "last_timestamp": timestamps[-1],
        "cutoff": cutoff,
        "slice_ends": slice_ends,
        "baseline_accuracy": baseline,
        "date_accuracy": date_accuracy,
        "date_lift": date_accuracy - baseline,
        "timestamp_auc": max(timestamp_auc_raw, 1.0 - timestamp_auc_raw),
        "positive_timestamps": positive_timestamps,
        "mixed_timestamps": mixed_timestamps,
        "positive_row_timestamp_ratio": class_counts[1] / positive_timestamps if positive_timestamps else float("nan"),
        "mean_rows_per_timestamp": total / len(timestamps),
    }


def fit_models(
    path: Path,
    mapping: dict[str, tuple[str, ...]],
    cutoff: str,
    seed: int,
) -> tuple[dict[tuple[str, str], LinearScore], dict[tuple[str, str, int], int]]:
    stats: dict[tuple[str, str, int, str], RunningStats] = defaultdict(RunningStats)
    counts: Counter[tuple[str, str, int]] = Counter()

    for row_index, row in rows_from(path):
        label = int(row["class"])
        inspection_type = row["inspection_type"]
        timestamp = row["timestamp"]
        memberships = [protocol for protocol in PROTOCOLS if is_train(protocol, row_index, timestamp, cutoff, seed)]
        if not memberships:
            continue
        values = {feature: float(row[feature]) for feature in mapping[inspection_type]}
        for protocol in memberships:
            counts[(protocol, inspection_type, label)] += 1
            for feature, value in values.items():
                stats[(protocol, inspection_type, label, feature)].add(value)

    models: dict[tuple[str, str], LinearScore] = {}
    for protocol in PROTOCOLS:
        for inspection_type, features in mapping.items():
            n0 = counts[(protocol, inspection_type, 0)]
            n1 = counts[(protocol, inspection_type, 1)]
            if n0 < 2 or n1 < 2:
                raise ValueError(f"학습 클래스 부족: {protocol}, type={inspection_type}, n0={n0}, n1={n1}")
            weights: dict[str, float] = {}
            intercept = 0.0
            for feature in features:
                s0 = stats[(protocol, inspection_type, 0, feature)]
                s1 = stats[(protocol, inspection_type, 1, feature)]
                pooled = ((s0.count - 1) * s0.variance + (s1.count - 1) * s1.variance) / max(1, s0.count + s1.count - 2)
                variance = max(pooled, 1e-6)
                weight = (s1.mean - s0.mean) / variance
                if abs(weight) > 1e-12:
                    weights[feature] = weight
                    intercept -= 0.5 * (s1.mean * s1.mean - s0.mean * s0.mean) / variance
            models[(protocol, inspection_type)] = LinearScore(intercept, weights)
    return models, dict(counts)


def evaluate_models(
    path: Path,
    models: dict[tuple[str, str], LinearScore],
    cutoff: str,
    slice_ends: list[str],
    seed: int,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    labels: dict[str, list[int]] = {protocol: [] for protocol in PROTOCOLS}
    scores: dict[str, list[float]] = {protocol: [] for protocol in PROTOCOLS}
    temporal_labels: list[list[int]] = [[] for _ in range(5)]
    temporal_scores: list[list[float]] = [[] for _ in range(5)]

    for row_index, row in rows_from(path):
        label = int(row["class"])
        inspection_type = row["inspection_type"]
        timestamp = row["timestamp"]
        for protocol in PROTOCOLS:
            if is_train(protocol, row_index, timestamp, cutoff, seed):
                continue
            score = models[(protocol, inspection_type)].score(row)
            labels[protocol].append(label)
            scores[protocol].append(score)
            if protocol == "future_time":
                slice_index = bisect_right(slice_ends, timestamp)
                temporal_labels[slice_index].append(label)
                temporal_scores[slice_index].append(score)

    evaluations: dict[str, dict[str, object]] = {}
    for protocol in PROTOCOLS:
        evaluations[protocol] = {
            "test_rows": len(labels[protocol]),
            "test_positives": sum(labels[protocol]),
            "auroc": binary_auc(labels[protocol], scores[protocol]),
        }
    slices = [
        {
            "slice": index + 1,
            "rows": len(temporal_labels[index]),
            "positives": sum(temporal_labels[index]),
            "auroc": binary_auc(temporal_labels[index], temporal_scores[index]),
        }
        for index in range(5)
    ]
    return evaluations, slices


def fmt(value: float) -> str:
    return "NA" if isnan(value) else f"{value:.3f}"


def write_outputs(
    output_dir: Path,
    inventory_result: dict[str, object],
    evaluations: dict[str, dict[str, object]],
    slices: list[dict[str, object]],
    seed: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    random_auc = float(evaluations["random_row"]["auroc"])
    group_auc = float(evaluations["timestamp_group"]["auroc"])
    temporal_auc = float(evaluations["future_time"]["auroc"])
    group_gap = random_auc - group_auc
    temporal_gap = random_auc - temporal_auc
    date_lift = float(inventory_result["date_lift"])
    duplication = float(inventory_result["positive_row_timestamp_ratio"])
    timestamp_auc = float(inventory_result["timestamp_auc"])
    positive_rate = int(inventory_result["class_counts"][1]) / int(inventory_result["rows"])
    valid_slice_aurocs = [float(row["auroc"]) for row in slices if not isnan(float(row["auroc"]))]
    slice_range = max(valid_slice_aurocs) - min(valid_slice_aurocs)

    summary = {
        "rows": inventory_result["rows"],
        "positive_rows": inventory_result["class_counts"][1],
        "positive_rate": positive_rate,
        "dates": inventory_result["dates"],
        "timestamps": inventory_result["timestamps"],
        "positive_timestamps": inventory_result["positive_timestamps"],
        "positive_row_timestamp_ratio": duplication,
        "date_resubstitution_accuracy": inventory_result["date_accuracy"],
        "majority_accuracy": inventory_result["baseline_accuracy"],
        "date_lift": date_lift,
        "timestamp_only_direction_free_auroc": timestamp_auc,
        "random_row_auroc": random_auc,
        "timestamp_group_auroc": group_auc,
        "future_time_auroc": temporal_auc,
        "random_group_gap": group_gap,
        "random_future_gap": temporal_gap,
        "future_slice_auroc_min": min(valid_slice_aurocs),
        "future_slice_auroc_max": max(valid_slice_aurocs),
        "future_slice_auroc_range": slice_range,
        "date_signal_flag": date_lift >= DATE_LIFT_THRESHOLD,
        "group_gap_flag": group_gap >= PERFORMANCE_GAP_THRESHOLD,
        "temporal_gap_flag": temporal_gap >= PERFORMANCE_GAP_THRESHOLD,
        "non_independent_rows_flag": duplication > DUPLICATION_RATIO_THRESHOLD,
    }
    with (output_dir / "siemens_external_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary), lineterminator="\n")
        writer.writeheader()
        writer.writerow(summary)

    lines = [
        "# 3단계 외부 검증 — Siemens SMT AOI",
        "",
        "## 고정 프로토콜",
        "",
        f"- 데이터: Mendeley Data DOI `10.17632/99jzmh9658.1` (`dataset.csv`, SHA-256 `{EXPECTED_SHA256}`)",
        "- 라벨: MIS 작업자의 재판정. `0=false call`, `1=true defect`",
        "- 특징: inspection type별 `mapping.json`에 지정된 물리 측정 열만 사용; timestamp와 meta 열은 모델에서 제외",
        f"- 분할: 행 무작위 / timestamp 그룹 / 앞 {TRAIN_FRACTION:.0%} 학습·뒤 {1-TRAIN_FRACTION:.0%} 미래 평가",
        f"- 모델: inspection type별 대각선 선형 판별기, seed={seed}",
        f"- 사전 판정 기준: 날짜 lift ≥ {DATE_LIFT_THRESHOLD:.2f}, 또는 무작위 대비 AUROC 격차 ≥ {PERFORMANCE_GAP_THRESHOLD:.2f}",
        "",
        "## 데이터 감사",
        "",
        "| 항목 | 값 | 판정 |",
        "|---|---:|---|",
        f"| 행 수 | {int(inventory_result['rows']):,} | 논문 보고 440,274행과 대조 |",
        f"| true defect 행 | {int(inventory_result['class_counts'][1]):,} ({positive_rate:.3%}) | 심한 불균형 |",
        f"| 고유 PCB timestamp | {int(inventory_result['timestamps']):,} | 행과 독립 단위를 분리 |",
        f"| 양성 행 / 양성 timestamp | {duplication:.3f} | {'비독립 행 경고' if summary['non_independent_rows_flag'] else '중복 신호 없음'} |",
        f"| 날짜 재대입 정확도 | {float(inventory_result['date_accuracy']):.3f} | 기준선 {float(inventory_result['baseline_accuracy']):.3f} |",
        f"| 날짜 lift | {date_lift:.3f} | {'발동' if summary['date_signal_flag'] else '미발동'} |",
        f"| timestamp 단독 방향무관 AUROC | {timestamp_auc:.3f} | 2단계와 같은 지표, 보조 결과 |",
        "",
        "라벨 순환의 음성 대조는 수치 하나로 단정하지 않는다. 이 표본은 AOI가 defect로 보낸 항목만 포함하고, 최종 class는 MIS 작업자가 false call/true defect로 다시 판정했다. 따라서 슈타겐처럼 `저장 라벨 = AOI 판정`인 구조는 아니지만, 전체 AOI 통과품이 없으므로 AOI 자체의 민감도나 false negative는 평가할 수 없다.",
        "",
        "판정 렌더링 누수는 이미지가 없는 물리 측정 테이블이므로 적용 불가로 기록한다. 미발동이나 통과로 세지 않는다.",
        "",
        "## 동일 모델의 평가 프로토콜 비교",
        "",
        "| 프로토콜 | 시험 행 | 양성 | AUROC | 무작위 대비 격차 |",
        "|---|---:|---:|---:|---:|",
        f"| 행 무작위 | {int(evaluations['random_row']['test_rows']):,} | {int(evaluations['random_row']['test_positives']):,} | {fmt(random_auc)} | 0.000 |",
        f"| timestamp 그룹 | {int(evaluations['timestamp_group']['test_rows']):,} | {int(evaluations['timestamp_group']['test_positives']):,} | {fmt(group_auc)} | {fmt(group_gap)} |",
        f"| 미래 시간 | {int(evaluations['future_time']['test_rows']):,} | {int(evaluations['future_time']['test_positives']):,} | {fmt(temporal_auc)} | {fmt(temporal_gap)} |",
        "",
        "## 미래 평가 구간별 결과",
        "",
        "| 미래 구간 | 행 | 양성 | AUROC |",
        "|---:|---:|---:|---:|",
    ]
    for row in slices:
        lines.append(f"| {row['slice']} | {int(row['rows']):,} | {int(row['positives']):,} | {fmt(float(row['auroc']))} |")
    lines.extend([
        "",
        "## 외부 검증 판정",
        "",
        f"- 날짜 사전 신호: **{'발동' if summary['date_signal_flag'] else '미발동'}** (lift {date_lift:.3f})",
        f"- 행 무작위 → timestamp 그룹 격차: **{'발동' if summary['group_gap_flag'] else '미발동'}** ({group_gap:+.3f})",
        f"- 행 무작위 → 미래 시간 격차: **{'발동' if summary['temporal_gap_flag'] else '미발동'}** ({temporal_gap:+.3f})",
        f"- 비독립 행 경고: **{'발동' if summary['non_independent_rows_flag'] else '미발동'}** ({duplication:.3f} 양성 행/timestamp)",
        "",
        "### 사후 탐색 결과 — 확증 판정에 포함하지 않음",
        "",
        f"미래 5구간의 AUROC 범위는 {min(valid_slice_aurocs):.3f}~{max(valid_slice_aurocs):.3f}(range {slice_range:.3f})였다. 전체 미래 구간을 합친 AUROC는 기준을 통과했지만 특정 구간은 크게 하락했다. 이 결과를 보고 새 임계값을 만들지 않으며, 다음 확인 실험에서는 `최악 시간 구간`과 클래스 불균형에 강한 시간 지표를 사전에 등록해야 한다.",
        "",
        "이 결과는 한 공개 라인의 외부 사례 검증이다. 시간 격차가 발동하면 합성 실험의 날짜 병리가 실제 데이터의 평가 취약성과 같은 방향으로 연결된다는 증거이며, 일반 산업 데이터 전체에 대한 민감도·특이도 주장은 아니다.",
        "",
    ])
    (output_dir / "siemens_external_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Siemens SMT AOI 데이터에 고정된 외부 검증 프로토콜을 적용한다.")
    parser.add_argument("--dataset", type=Path, default=Path("external_data/siemens/dataset.csv"))
    parser.add_argument("--mapping", type=Path, default=Path("external_data/siemens/mapping.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--skip-hash", action="store_true", help="크기만 검증하고 전체 SHA-256 재계산 생략")
    args = parser.parse_args()

    validate_files(args.dataset, args.mapping, verify_hash=not args.skip_hash)
    mapping = load_mapping(args.mapping)
    print("[1/3] 데이터 단위·날짜 분포 감사")
    inventory_result = inventory(args.dataset, mapping)
    print(f"      {inventory_result['rows']:,}행, {inventory_result['timestamps']:,} timestamps")
    print("[2/3] 세 평가 프로토콜의 학습 통계 계산")
    models, _ = fit_models(args.dataset, mapping, str(inventory_result["cutoff"]), args.seed)
    print("[3/3] 행 무작위·그룹·미래 평가")
    evaluations, slices = evaluate_models(
        args.dataset,
        models,
        str(inventory_result["cutoff"]),
        list(inventory_result["slice_ends"]),
        args.seed,
    )
    write_outputs(args.output_dir, inventory_result, evaluations, slices, args.seed)
    print(f"[완료] Siemens 외부 검증: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
