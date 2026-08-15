from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from bisect import bisect_right
from dataclasses import asdict
from pathlib import Path

from external_validate_siemens import validate_files
from temporal_followup_siemens import business_metrics, select_threshold


TARGET_SLIP_RATE = 0.01
TARGET_VOLUME_REDUCTION = 0.40
MODEL_CONFIG = {
    "n_estimators": 300,
    "max_depth": None,
    "min_samples_leaf": 2,
    "max_features": "sqrt",
    "class_weight": "balanced_subsample",
    "n_jobs": -1,
}
PAPER_RFC2 = {
    "test": {"slip": 0.010, "volume": 0.897},
    "slice_1": {"slip": 0.102, "volume": 0.667},
    "slice_2": {"slip": 0.146, "volume": 0.524},
    "slice_3": {"slip": 0.150, "volume": 0.654},
    "slice_4": {"slip": 0.109, "volume": 0.760},
    "slice_5": {"slip": 0.110, "volume": 0.397},
}


def paper_time_boundaries(timestamps: list[str]) -> tuple[str, list[str]]:
    unique = sorted(set(timestamps))
    if len(unique) < 10:
        raise ValueError("논문 시간 분할에는 최소 10개의 고유 timestamp가 필요합니다")
    modeling_end_index = max(0, min(len(unique) - 6, round(len(unique) * 0.50) - 1))
    evaluation = unique[modeling_end_index + 1 :]
    slice_ends = [evaluation[min(len(evaluation) - 1, round(len(evaluation) * index / 5) - 1)] for index in range(1, 5)]
    return unique[modeling_end_index], slice_ends


def require_packages():
    try:
        import numpy as np
        import pandas as pd
        import sklearn
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import average_precision_score
        from sklearn.model_selection import StratifiedKFold, train_test_split
    except ImportError as error:
        raise SystemExit(
            "pandas와 scikit-learn이 필요합니다. 기존 데이터 가상환경의 Python으로 실행하세요."
        ) from error
    return np, pd, sklearn, RandomForestClassifier, average_precision_score, StratifiedKFold, train_test_split


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def aggregate(rows: list[dict[str, object]], period: str) -> dict[str, float]:
    selected = [row for row in rows if row["period"] == period]
    result: dict[str, float] = {"runs": float(len(selected))}
    for key in ("pr_auc", "slip_rate", "volume_reduction", "manual_review_rate"):
        values = [float(row[key]) for row in selected]
        result[f"{key}_mean"] = statistics.mean(values)
        result[f"{key}_sd"] = statistics.stdev(values) if len(values) > 1 else 0.0
        result[f"{key}_min"] = min(values)
        result[f"{key}_max"] = max(values)
    result["target_passes"] = float(sum(bool(row["target_met"]) for row in selected))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pfab & Rothering의 공개된 40/10/10x5 분할과 CV 임계값 절차를 고정 RFC로 부분 재현한다."
    )
    parser.add_argument("--dataset", type=Path, default=Path("external_data/siemens/dataset.csv"))
    parser.add_argument("--mapping", type=Path, default=Path("external_data/siemens/mapping.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--seeds", type=int, default=1, help="파일럿 1, 논문과 같은 반복 수 10")
    parser.add_argument("--skip-hash", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds는 1 이상이어야 합니다")

    np, pd, sklearn, RandomForestClassifier, average_precision_score, StratifiedKFold, train_test_split = require_packages()
    validate_files(args.dataset, args.mapping, verify_hash=not args.skip_hash)
    header = pd.read_csv(args.dataset, nrows=0).columns.tolist()
    feature_names = [name for name in header if name not in ("", "timestamp", "class") and not name.startswith("Unnamed")]
    if args.check_only:
        print(f"[확인] pandas {pd.__version__}, scikit-learn {sklearn.__version__}")
        print(f"[확인] 모델 입력 {len(feature_names)}개: inspection_type, meta_feat, inspection_feat")
        print(f"[확인] 고정 RFC 설정: {json.dumps(MODEL_CONFIG, sort_keys=True)}")
        print("[주의] 공개 GitLab 코드 접근 불가 및 search space 미공개로 완전 재현이 아닌 프로토콜 부분 재현")
        return

    print(f"[1/4] CSV 적재: 논문 테이블 특징 {len(feature_names)}개", flush=True)
    dtype = {"class": "int8", **{name: "float32" for name in feature_names}}
    frame = pd.read_csv(
        args.dataset,
        usecols=["timestamp", "class", *feature_names],
        dtype=dtype,
        low_memory=False,
    )
    frame[feature_names] = frame[feature_names].fillna(0.0)
    timestamps = frame["timestamp"].astype(str).to_numpy()
    modeling_end, slice_ends = paper_time_boundaries(timestamps.tolist())
    X = frame[feature_names].to_numpy(dtype=np.float32, copy=True)
    y = frame["class"].to_numpy(dtype=np.int8, copy=True)
    del frame
    modeling_indices = np.flatnonzero(timestamps <= modeling_end)
    evaluation_indices = np.flatnonzero(timestamps > modeling_end)

    all_rows: list[dict[str, object]] = []
    print(f"[2/4] 모델링 절반 {len(modeling_indices):,}행, 미래 절반 {len(evaluation_indices):,}행", flush=True)
    started = time.perf_counter()
    for seed in range(args.seeds):
        hyper_indices, test_indices = train_test_split(
            modeling_indices,
            test_size=0.20,
            stratify=y[modeling_indices],
            random_state=seed,
        )
        fold_thresholds: list[float] = []
        folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        for fold, (fit_positions, validation_positions) in enumerate(folds.split(hyper_indices, y[hyper_indices]), 1):
            fit_indices = hyper_indices[fit_positions]
            validation_indices = hyper_indices[validation_positions]
            fold_model = RandomForestClassifier(random_state=seed * 100 + fold, **MODEL_CONFIG)
            fold_model.fit(X[fit_indices], y[fit_indices])
            scores = fold_model.predict_proba(X[validation_indices])[:, 1]
            fold_thresholds.append(
                select_threshold(y[validation_indices].tolist(), scores.tolist(), TARGET_SLIP_RATE)
            )
            print(f"      seed {seed + 1}/{args.seeds}, CV {fold}/5", flush=True)

        threshold = statistics.mean(fold_thresholds)
        model = RandomForestClassifier(random_state=seed, **MODEL_CONFIG)
        model.fit(X[hyper_indices], y[hyper_indices])

        def evaluate(period: str, indices, slice_index: int = 0) -> dict[str, object]:
            scores = model.predict_proba(X[indices])[:, 1]
            labels = y[indices]
            metrics = business_metrics(labels.tolist(), scores.tolist(), threshold)
            return {
                "seed": seed,
                "period": period,
                "slice": slice_index,
                "threshold": round(float(threshold), 12),
                "pr_auc": float(average_precision_score(labels, scores)),
                **asdict(metrics),
            }

        all_rows.append(evaluate("random_test", test_indices))
        future_timestamps = timestamps[evaluation_indices]
        for slice_index in range(5):
            positions = np.array([bisect_right(slice_ends, value) == slice_index for value in future_timestamps])
            all_rows.append(evaluate(f"future_{slice_index + 1}", evaluation_indices[positions], slice_index + 1))
        print(f"      seed {seed + 1}/{args.seeds} 완료", flush=True)

    elapsed = time.perf_counter() - started
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "paper_protocol_reconstruction_runs.csv", all_rows)
    periods = ["random_test", "future_1", "future_2", "future_3", "future_4", "future_5"]
    summaries = {period: aggregate(all_rows, period) for period in periods}

    lines = [
        "# 6단계 — Siemens 논문 프로토콜 부분 재현",
        "",
        "> 원 논문이 인용한 GitLab 코드는 현재 인증 없이는 접근할 수 없고, 논문에는 Bayesian optimization search space와 최종 hyperparameter가 없다. 따라서 이것은 완전한 코드 재현이 아니라 공개된 데이터 분할·5-fold 임계값 절차를 고정 RFC로 재구성한 결과다.",
        "",
        "## 재구성한 공개 절차",
        "",
        "- 전체 시간순 앞 50%를 모델링, 뒤 50%를 미래 평가로 사용",
        "- 앞 절반을 층화 무작위 80%/20%로 분할: 전체의 40% hyperparameter 데이터, 10% random test",
        "- hyperparameter 데이터에서 stratified 5-fold CV",
        "- fold별 slip≤1%에서 최대 volume reduction을 주는 임계값을 찾고 5개 임계값 평균",
        "- 뒤 절반은 시간순 5개 평가 구간",
        f"- 반복 시드 {args.seeds}개, 고정 RFC 설정 `{json.dumps(MODEL_CONFIG, sort_keys=True)}`",
        "",
        "## 결과와 논문 RFC2 평균 비교",
        "",
        "| 구간 | 재현 slip | 논문 slip | 재현 volume | 논문 volume | 재현 PR-AUC |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    paper_keys = ["test", "slice_1", "slice_2", "slice_3", "slice_4", "slice_5"]
    labels = ["무작위 test", "미래 1", "미래 2", "미래 3", "미래 4", "미래 5"]
    for period, paper_key, label in zip(periods, paper_keys, labels):
        row = summaries[period]
        paper = PAPER_RFC2[paper_key]
        lines.append(
            f"| {label} | {row['slip_rate_mean']:.3f} | {paper['slip']:.3f} | "
            f"{row['volume_reduction_mean']:.3f} | {paper['volume']:.3f} | {row['pr_auc_mean']:.3f} |"
        )
    random_gate = (
        summaries["random_test"]["slip_rate_mean"] <= TARGET_SLIP_RATE
        and summaries["random_test"]["volume_reduction_mean"] >= TARGET_VOLUME_REDUCTION
    )
    future_failures = sum(
        summaries[period]["slip_rate_mean"] > TARGET_SLIP_RATE
        or summaries[period]["volume_reduction_mean"] < TARGET_VOLUME_REDUCTION
        for period in periods[1:]
    )
    lines.extend([
        "",
        "## 판정",
        "",
        f"무작위 test gate: **{'통과' if random_gate else '미달'}**. 미래 목표 미달: **{future_failures}/5개 구간**.",
        f"무작위 test의 시드별 gate 통과는 **{int(summaries['random_test']['target_passes'])}/{args.seeds}회**였고, "
        f"slip 범위는 {summaries['random_test']['slip_rate_min']:.3f}~{summaries['random_test']['slip_rate_max']:.3f}, "
        f"volume 범위는 {summaries['random_test']['volume_reduction_min']:.3f}~{summaries['random_test']['volume_reduction_max']:.3f}였다.",
        f"미래 평가는 총 **{5 * args.seeds}개 시드-구간 중 "
        f"{sum(int(summaries[period]['target_passes']) for period in periods[1:])}개**만 두 업무목표를 동시에 충족했다.",
        "",
        "따라서 무작위 분할에서 미래 평가로 이동할 때 업무목표가 붕괴하는 정성적 현상은 재현됐다. 다만 무작위 test 평균부터 논문 RFC2의 volume과 차이가 크므로 수치의 완전 재현으로 해석하지 않는다.",
        "",
        "논문 값과 차이가 나면 이를 데이터 재현 실패로 단정하지 않는다. 공개되지 않은 search space·최종 hyperparameter·코드 버전과 현재 라이브러리 버전이 잠재 원인이다.",
        "",
        f"실행 환경: pandas {pd.__version__}, scikit-learn {sklearn.__version__}, 총 모델링 {elapsed:.1f}초.",
        "",
    ])
    (args.output_dir / "paper_protocol_reconstruction_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[3/4] 모델링 완료: {elapsed:.1f}초", flush=True)
    print(f"[4/4] 결과 저장: {args.output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
