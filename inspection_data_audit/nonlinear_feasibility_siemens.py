from __future__ import annotations

import argparse
import csv
import json
import time
from bisect import bisect_right
from dataclasses import asdict
from pathlib import Path

from external_validate_siemens import load_mapping, validate_files
from temporal_followup_siemens import (
    TARGET_SLIP_RATE,
    TARGET_VOLUME_REDUCTION,
    business_metrics,
    select_threshold,
)


# 결과를 보기 전에 고정한 단일 비선형 기준 모델이다. 실행 후 튜닝하지 않는다.
TRAIN_FRACTION = 0.50
CALIBRATION_FRACTION = 0.20
FUTURE_FRACTION = 0.30
MODEL_CONFIG = {
    "learning_rate": 0.08,
    "max_iter": 200,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 50,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": 20260815,
}


def split_boundaries(timestamps: list[str]) -> tuple[str, str, list[str]]:
    unique = sorted(set(timestamps))
    if len(unique) < 10:
        raise ValueError("시간 분할에는 최소 10개의 고유 timestamp가 필요합니다")
    train_index = max(0, min(len(unique) - 3, round(len(unique) * TRAIN_FRACTION) - 1))
    calibration_index = max(train_index + 1, min(len(unique) - 2, round(len(unique) * (TRAIN_FRACTION + CALIBRATION_FRACTION)) - 1))
    future = unique[calibration_index + 1 :]
    slice_ends = [future[min(len(future) - 1, round(len(future) * index / 5) - 1)] for index in range(1, 5)]
    return unique[train_index], unique[calibration_index], slice_ends


def require_ml_packages():
    try:
        import numpy as np
        import pandas as pd
        import sklearn
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import roc_auc_score
    except ImportError as error:
        raise SystemExit(
            "pandas와 scikit-learn이 필요합니다. 다음 Python으로 실행하세요:\n"
            ".venv/bin/python nonlinear_feasibility_siemens.py --skip-hash"
        ) from error
    return np, pd, sklearn, HistGradientBoostingClassifier, roc_auc_score


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float) -> str:
    return f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="고정 HistGradientBoosting 모델이 Siemens 학습 feasibility gate를 통과하는지 평가한다."
    )
    parser.add_argument("--dataset", type=Path, default=Path("external_data/siemens/dataset.csv"))
    parser.add_argument("--mapping", type=Path, default=Path("external_data/siemens/mapping.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--skip-hash", action="store_true", help="이미 검증한 CSV의 SHA-256 재계산 생략")
    parser.add_argument("--check-only", action="store_true", help="패키지·파일·고정 설정만 확인하고 학습하지 않음")
    args = parser.parse_args()

    np, pd, sklearn, HistGradientBoostingClassifier, roc_auc_score = require_ml_packages()
    validate_files(args.dataset, args.mapping, verify_hash=not args.skip_hash)
    mapping = load_mapping(args.mapping)
    feature_names = sorted({feature for features in mapping.values() for feature in features})
    model_features = ["inspection_type", *feature_names]

    if args.check_only:
        print(f"[확인] pandas {pd.__version__}, scikit-learn {sklearn.__version__}")
        print(f"[확인] 모델 특징 {len(model_features)}개, 설정 {json.dumps(MODEL_CONFIG, sort_keys=True)}")
        print("[확인] 학습 50% / calibration 20% / 미래 30%, timestamp 단위 분리")
        return

    print(f"[1/5] CSV 적재: 물리 특징 {len(feature_names)}개 + inspection_type", flush=True)
    dtype = {"class": "int8", "inspection_type": "int8", **{name: "float32" for name in feature_names}}
    frame = pd.read_csv(
        args.dataset,
        usecols=["timestamp", "class", *model_features],
        dtype=dtype,
        low_memory=False,
    )
    frame[feature_names] = frame[feature_names].fillna(0.0)
    timestamp_values = frame["timestamp"].astype(str)
    train_end, calibration_end, slice_ends = split_boundaries(timestamp_values.tolist())
    train_mask = timestamp_values <= train_end
    calibration_mask = (timestamp_values > train_end) & (timestamp_values <= calibration_end)
    future_mask = timestamp_values > calibration_end

    X = frame[model_features].to_numpy(dtype=np.float32, copy=True)
    y = frame["class"].to_numpy(dtype=np.int8, copy=True)
    del frame

    train_indices = np.flatnonzero(train_mask.to_numpy())
    calibration_indices = np.flatnonzero(calibration_mask.to_numpy())
    future_indices = np.flatnonzero(future_mask.to_numpy())
    for name, indices in (("train", train_indices), ("calibration", calibration_indices), ("future", future_indices)):
        classes = np.unique(y[indices])
        if len(classes) != 2:
            raise ValueError(f"{name} 구간에 두 클래스가 모두 필요합니다: {classes.tolist()}")

    train_y = y[train_indices]
    counts = np.bincount(train_y, minlength=2)
    class_weight = len(train_y) / (2.0 * counts)
    sample_weight = class_weight[train_y]

    print(
        f"[2/5] 학습: {len(train_indices):,}행, calibration {len(calibration_indices):,}행, 미래 {len(future_indices):,}행",
        flush=True,
    )
    started = time.perf_counter()
    model = HistGradientBoostingClassifier(
        categorical_features=[0],
        **MODEL_CONFIG,
    )
    model.fit(X[train_indices], train_y, sample_weight=sample_weight)
    elapsed = time.perf_counter() - started
    print(f"[3/5] 학습 완료: {elapsed:.1f}초", flush=True)

    train_scores = model.predict_proba(X[train_indices])[:, 1]
    calibration_scores = model.predict_proba(X[calibration_indices])[:, 1]
    future_scores = model.predict_proba(X[future_indices])[:, 1]
    calibration_y = y[calibration_indices]
    future_y = y[future_indices]
    threshold = select_threshold(calibration_y.tolist(), calibration_scores.tolist(), TARGET_SLIP_RATE)

    train_business = business_metrics(train_y.tolist(), train_scores.tolist(), threshold)
    calibration_business = business_metrics(calibration_y.tolist(), calibration_scores.tolist(), threshold)
    future_business = business_metrics(future_y.tolist(), future_scores.tolist(), threshold)
    train_auc = float(roc_auc_score(train_y, train_scores))
    calibration_auc = float(roc_auc_score(calibration_y, calibration_scores))
    future_auc = float(roc_auc_score(future_y, future_scores))

    print(
        f"[4/5] calibration gate: slip={calibration_business.slip_rate:.3%}, "
        f"volume={calibration_business.volume_reduction:.3%}, "
        f"{'통과' if calibration_business.target_met else '미달'}",
        flush=True,
    )
    future_timestamps = timestamp_values.to_numpy()[future_indices]
    slice_rows: list[dict[str, object]] = []
    slice_business = []
    for slice_index in range(5):
        positions = np.array([bisect_right(slice_ends, value) == slice_index for value in future_timestamps])
        labels = future_y[positions]
        scores = future_scores[positions]
        metrics = business_metrics(labels.tolist(), scores.tolist(), threshold)
        slice_business.append(metrics)
        slice_rows.append({
            "period": "future_slice",
            "slice": slice_index + 1,
            "auroc": float(roc_auc_score(labels, scores)),
            "threshold": round(float(threshold), 12),
            **asdict(metrics),
        })

    rows = [
        {"period": "train", "slice": 0, "auroc": train_auc, "threshold": round(float(threshold), 12), **asdict(train_business)},
        {"period": "calibration", "slice": 0, "auroc": calibration_auc, "threshold": round(float(threshold), 12), **asdict(calibration_business)},
        {"period": "future_all", "slice": 0, "auroc": future_auc, "threshold": round(float(threshold), 12), **asdict(future_business)},
        *slice_rows,
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "nonlinear_feasibility_business.csv", rows)

    failed_slices = sum(not metrics.target_met for metrics in slice_business)
    gate = calibration_business.target_met
    lines = [
        "# 5단계 — 비선형 모델 feasibility gate",
        "",
        "## 사전 고정 프로토콜",
        "",
        "- 모델: `HistGradientBoostingClassifier` 단일 설정",
        f"- 설정: `{json.dumps(MODEL_CONFIG, sort_keys=True)}`",
        f"- 입력: inspection type + mapping.json의 물리 측정 특징 {len(feature_names)}개",
        "- timestamp와 meta_feat 열은 모델 입력에서 제외",
        "- 분할: 앞 50% 학습 / 다음 20% calibration / 마지막 30% 미래 평가",
        "- 동일 timestamp는 한 구간에만 포함",
        f"- calibration gate: slip rate ≤ {TARGET_SLIP_RATE:.0%} 및 volume reduction ≥ {TARGET_VOLUME_REDUCTION:.0%}",
        "- 임계값은 calibration에서만 정하고 미래 구간에서 재조정하지 않음",
        "",
        "## 결과",
        "",
        "| 구간 | 행 | true defect | AUROC | slip rate | volume reduction | manual review | 목표 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        f"| 학습 | {train_business.rows:,} | {train_business.positives:,} | {fmt(train_auc)} | {fmt(train_business.slip_rate)} | {fmt(train_business.volume_reduction)} | {fmt(train_business.manual_review_rate)} | 참고 |",
        f"| calibration | {calibration_business.rows:,} | {calibration_business.positives:,} | {fmt(calibration_auc)} | {fmt(calibration_business.slip_rate)} | {fmt(calibration_business.volume_reduction)} | {fmt(calibration_business.manual_review_rate)} | {'통과' if gate else '미달'} |",
        f"| 미래 전체 | {future_business.rows:,} | {future_business.positives:,} | {fmt(future_auc)} | {fmt(future_business.slip_rate)} | {fmt(future_business.volume_reduction)} | {fmt(future_business.manual_review_rate)} | {'달성' if future_business.target_met else '미달'} |",
    ]
    for index, (metrics, row) in enumerate(zip(slice_business, slice_rows), 1):
        lines.append(
            f"| 미래 {index} | {metrics.rows:,} | {metrics.positives:,} | {fmt(float(row['auroc']))} | "
            f"{fmt(metrics.slip_rate)} | {fmt(metrics.volume_reduction)} | {fmt(metrics.manual_review_rate)} | "
            f"{'달성' if metrics.target_met else '미달'} |"
        )
    lines.extend([
        "",
        "## 판정",
        "",
        f"Calibration feasibility gate: **{'통과' if gate else '미달'}**.",
        "",
        (
            f"Gate를 통과했으므로 미래 {failed_slices}/5개 구간의 실패를 시간 강건성 결과로 해석할 수 있다."
            if gate
            else "Gate를 통과하지 못했으므로 미래 결과를 시간 드리프트의 효과로 해석하지 않는다. 모델 변경은 새 실험으로 사전 고정해야 한다."
        ),
        "",
        f"실행 환경: pandas {pd.__version__}, scikit-learn {sklearn.__version__}, 학습 {elapsed:.1f}초.",
        "",
    ])
    (args.output_dir / "nonlinear_feasibility_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[5/5] 결과 저장: {args.output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
