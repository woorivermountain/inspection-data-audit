from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from metrics import group_resubstitution_accuracy, majority_accuracy, safe_float, safe_int


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def require(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.exists():
        raise FileNotFoundError(f"필수 파일이 없습니다: {path}")
    return path


def metric(value: Any, unit: str, source: str, definition: str, interpretation: str) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "source": source,
        "definition": definition,
        "interpretation": interpretation,
    }


def audit(data_root: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    sources = {
        "products": "analysis/out/01_product_log.csv",
        "roi": "analysis/out/02_roi_records.csv",
        "images": "analysis/out/04_image_index.csv",
        "warnings": "analysis/out/10_warnings.csv",
        "model_results": "experiments/results/results.csv",
        "lovo": "derive/results/stagen_lovo.csv",
        "human": "roi_perception_labels.csv",
        "case": "cases/STG-2026-01/case.yaml",
        "case_summary": "cases/summary.csv",
    }
    paths = {key: require(data_root, rel) for key, rel in sources.items()}

    products = read_csv(paths["products"])
    roi = read_csv(paths["roi"])
    images = read_csv(paths["images"])
    warnings = read_csv(paths["warnings"])
    model_results = read_csv(paths["model_results"])
    lovo = read_csv(paths["lovo"])
    human = read_csv(paths["human"])
    case_summary = read_csv(paths["case_summary"])

    metrics: dict[str, dict[str, Any]] = {}
    metrics["product_units"] = metric(len(products), "제품", sources["products"], "제품 로그 행 수", "ROI 행의 상위 독립 단위 후보")
    metrics["roi_rows"] = metric(len(roi), "ROI 행", sources["roi"], "ROI 판정 레코드 수", "독립 사건 수와 구분해야 함")
    metrics["image_count"] = metric(len(images), "이미지", sources["images"], "이미지 인덱스 행 수", "결과 화면이며 원본 여부를 별도 기록해야 함")

    recovered = 0
    for row in roi:
        cutline = safe_int(row.get("MatchCutline_i", row.get("MatchCutline")))
        predicted = int(max(safe_int(row.get("OPoint")), safe_int(row.get("RPoint"))) >= cutline)
        recovered += predicted == safe_int(row.get("PassYN"))
    recovery = recovered / len(roi) if roi else float("nan")
    metrics["machine_rule_recovery"] = metric(
        round(recovery, 6), "비율", sources["roi"],
        "max(OPoint, RPoint) >= MatchCutline 과 PassYN의 일치율",
        "독립 정답이 아니라 설비 출력과 결정적으로 연결된 라벨인지 점검",
    )

    date_resub = group_resubstitution_accuracy(images, "folder_day", "label")
    image_majority = majority_accuracy(row["label"] for row in images)
    days: dict[str, set[str]] = defaultdict(set)
    for row in images:
        days[row["folder_day"]].add(row["label"])
    mixed_days = sum(len(labels) > 1 for labels in days.values())
    metrics["date_resub_accuracy"] = metric(round(date_resub, 6), "비율", sources["images"], "날짜별 최빈 라벨을 같은 데이터에 재대입", "외삽 성능이 아니라 수집 지원 편향")
    metrics["image_majority_accuracy"] = metric(round(image_majority, 6), "비율", sources["images"], "전체 이미지 다수 라벨 정확도", "날짜 재대입 lift의 기준선")
    metrics["mixed_label_days"] = metric(mixed_days, "일", sources["images"], "정상과 불합격 이미지가 모두 존재하는 날짜 수", "날짜 교락의 데이터 지원 범위")
    metrics["total_image_days"] = metric(len(days), "일", sources["images"], "이미지가 존재하는 고유 날짜 수", "날짜 단위 평가의 분모")

    result_lookup = {(row.get("protocol"), row.get("method"), row.get("imgset")): row for row in model_results}
    selected_results = {
        "date_random_auroc": ("A-무작위", "date-only", "meta"),
        "date_lodo_auroc": ("B-LODO", "date-only", "meta"),
        "border_red_lodo_auroc": ("B-LODO", "border-red", "meta"),
        "patchcore_crop_random_auroc": ("A-무작위", "PatchCore", "img_crop"),
        "patchcore_clean_random_auroc": ("A-무작위", "PatchCore", "img_clean"),
        "patchcore_crop_lodo_auroc": ("B-LODO", "PatchCore", "img_crop"),
        "patchcore_clean_lodo_auroc": ("B-LODO", "PatchCore", "img_clean"),
        "clip_clean_lodo_auroc": ("B-LODO", "CLIP-zeroshot", "img_clean"),
    }
    for name, key in selected_results.items():
        row = result_lookup.get(key)
        if row:
            metrics[name] = metric(
                safe_float(row.get("auroc")), "AUROC", sources["model_results"],
                f"protocol={key[0]}, method={key[1]}, imgset={key[2]}",
                f"95% CI [{row.get('lo')}, {row.get('hi')}], n_pos={row.get('n_pos')}",
            )

    for name, random_name, lodo_name, asset in (
        ("date_protocol_gap", "date_random_auroc", "date_lodo_auroc", "meta"),
        ("patchcore_crop_protocol_gap", "patchcore_crop_random_auroc", "patchcore_crop_lodo_auroc", "img_crop"),
        ("patchcore_clean_protocol_gap", "patchcore_clean_random_auroc", "patchcore_clean_lodo_auroc", "img_clean"),
    ):
        if random_name in metrics and lodo_name in metrics:
            gap = safe_float(metrics[random_name]["value"]) - safe_float(metrics[lodo_name]["value"])
            metrics[name] = metric(
                round(gap, 6), "AUROC 차이", sources["model_results"],
                f"A-무작위 AUROC - B-LODO AUROC ({asset})",
                "양수이면 무작위 평가가 날짜 단위 외삽보다 낙관적",
            )

    pass_warnings = sum(safe_int(row.get("verdict")) == 1 for row in warnings)
    fail_warnings = sum(safe_int(row.get("verdict")) == 0 for row in warnings)
    metrics["warning_rows_total"] = metric(len(warnings), "행", sources["warnings"], "저장된 경고 행 전체", "통과 이탈과 불합격을 분리해 보고")
    metrics["warning_rows_pass"] = metric(pass_warnings, "행", sources["warnings"], "verdict=1 경고", "라벨 없이 추가로 찾은 통과 이탈")
    metrics["warning_rows_fail"] = metric(fail_warnings, "행", sources["warnings"], "verdict=0 경고", "이미 불합격인 두 사건")

    human_by_id = {safe_int(row.get("id")): row for row in human}
    diff_controls = [row for row in human if row.get("kind") in {"control_diff", "control_diff_repeat"}]
    same_controls = [row for row in human if row.get("kind") in {"control_same", "control_same_repeat"}]
    repeats = [row for row in human if str(row.get("rep_of", "")).strip()]
    diff_correct = sum(row.get("human") == "DIFF" for row in diff_controls)
    same_correct = sum(row.get("human") == "SAME" for row in same_controls)
    repeat_correct = 0
    repeat_evaluable = 0
    for row in repeats:
        original = human_by_id.get(safe_int(row.get("rep_of"), -1))
        if original:
            repeat_evaluable += 1
            repeat_correct += row.get("human") == original.get("human")
    metrics["human_diff_sensitivity"] = metric(round(diff_correct / len(diff_controls), 6), "비율", sources["human"], "차이를 심은 대조군에서 DIFF 응답 비율", f"{diff_correct}/{len(diff_controls)}")
    metrics["human_same_specificity"] = metric(round(same_correct / len(same_controls), 6), "비율", sources["human"], "동일 대조군에서 SAME 응답 비율", f"{same_correct}/{len(same_controls)}")
    metrics["human_repeat_agreement"] = metric(round(repeat_correct / repeat_evaluable, 6), "비율", sources["human"], "반복 문항과 최초 응답의 단순 일치율", f"{repeat_correct}/{repeat_evaluable}; 전문가 타당도와 별개")

    random_rows = [row for row in lovo if row.get("g") == "random"]
    nonrandom_rows = [row for row in lovo if row.get("g") != "random"]
    best_random = min(random_rows, key=lambda row: safe_float(row.get("q10")))
    best_method = min(nonrandom_rows, key=lambda row: safe_float(row.get("q10")))
    random_q10 = safe_float(best_random.get("q10"))
    best_q10 = safe_float(best_method.get("q10"))
    metrics["lovo_random_q10"] = metric(random_q10, "잔여 오류율", sources["lovo"], f"f={best_random.get('f')}, g=random, q=10%", "실데이터 무작위 질문 기준선")
    metrics["lovo_best_q10"] = metric(best_q10, "잔여 오류율", sources["lovo"], f"f={best_method.get('f')}, g={best_method.get('g')}, q=10%", "무작위보다 낮지 않으면 기권 방법의 실데이터 근거 없음")
    metrics["lovo_q10_improvement"] = metric(round(random_q10 - best_q10, 8), "오류율 감소", sources["lovo"], "무작위 q10 - 최고 비무작위 q10", "0 이하면 실데이터에서 개선 없음")

    case_text = paths["case"].read_text(encoding="utf-8")
    actual_error_count = len(re.findall(r"^\s+- error_id:", case_text, flags=re.MULTILINE))
    summary_error_count = safe_int(case_summary[0].get("오류기록")) if case_summary else 0
    metrics["analyst_log_actual"] = metric(actual_error_count, "건", sources["case"], "case.yaml의 error_id 항목 수", "최신 사례 기록")
    metrics["analyst_log_summary"] = metric(summary_error_count, "건", sources["case_summary"], "summary.csv 오류기록 값", "최신 사례 기록과 다르면 stale artifact")

    flags: list[dict[str, str]] = []
    if summary_error_count != actual_error_count:
        flags.append({"severity": "high", "issue": "stale_summary", "detail": f"analyst_log 실제 {actual_error_count}건, summary.csv {summary_error_count}건"})
    if metrics.get("date_lodo_auroc"):
        gap = date_resub - safe_float(metrics["date_lodo_auroc"]["value"])
        if gap > 0.2:
            flags.append({"severity": "high", "issue": "resubstitution_vs_extrapolation", "detail": f"날짜 재대입 정확도와 LODO AUROC 차이 {gap:.3f}"})
    if repeat_evaluable and repeat_correct / repeat_evaluable < 0.8:
        flags.append({"severity": "high", "issue": "human_repeatability", "detail": f"반복 일치 {repeat_correct}/{repeat_evaluable}"})
    if random_q10 - best_q10 <= 0:
        flags.append({"severity": "high", "issue": "no_lovo_improvement", "detail": f"q10 무작위={random_q10:.6f}, 최고 비무작위={best_q10:.6f}"})

    return metrics, flags


def write_outputs(output_dir: Path, metrics: dict[str, dict[str, Any]], flags: list[dict[str, str]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "current_metrics.json").open("w", encoding="utf-8") as stream:
        json.dump({"metrics": metrics, "flags": flags}, stream, ensure_ascii=False, indent=2)

    with (output_dir / "evidence_ledger.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["metric", "value", "unit", "source", "definition", "interpretation"],
            lineterminator="\n",
        )
        writer.writeheader()
        for name, item in metrics.items():
            writer.writerow({"metric": name, **item})

    def value(name: str) -> Any:
        return metrics.get(name, {}).get("value", "-")

    lines = [
        "# 현재 데이터 감사 보고서",
        "",
        "## 핵심 결과",
        "",
        f"- 제품 {value('product_units')}개, ROI {value('roi_rows')}행, 이미지 {value('image_count')}장",
        f"- 설비 판정 규칙 재현율: {value('machine_rule_recovery')}",
        f"- 날짜 재대입 정확도: {value('date_resub_accuracy')} / 날짜 LODO AUROC: {value('date_lodo_auroc')}",
        f"- date-only 무작위-LODO 격차: {value('date_protocol_gap')}",
        f"- 테두리 적색 LODO AUROC: {value('border_red_lodo_auroc')}",
        f"- PatchCore 무작위-LODO 격차: crop {value('patchcore_crop_protocol_gap')}, clean {value('patchcore_clean_protocol_gap')}",
        f"- 오버레이 제거 PatchCore LODO AUROC: {value('patchcore_clean_lodo_auroc')}",
        f"- 사람 판정: 차이 민감도 {value('human_diff_sensitivity')}, 동일 특이도 {value('human_same_specificity')}, 반복 일치 {value('human_repeat_agreement')}",
        f"- 사양 기권 q10 개선: {value('lovo_q10_improvement')}",
        f"- analyst_log: 실제 {value('analyst_log_actual')}건 / 요약 {value('analyst_log_summary')}건",
        "",
        "## 자동 플래그",
        "",
    ]
    if flags:
        for flag in flags:
            lines.append(f"- **{flag['severity']} · {flag['issue']}** — {flag['detail']}")
    else:
        lines.append("- 탐지된 불일치 없음")
    lines.extend([
        "",
        "## 판정",
        "",
        "현재 결과는 모델 성능 논문보다 모델링 전 데이터 감사 연구에 더 적합하다. 외부 검증 전에 수치 정의와 stale artifact를 먼저 고정해야 한다.",
        "",
    ])
    (output_dir / "current_audit_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="현재 산업 검사 데이터의 핵심 증거를 재검산한다.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    metrics, flags = audit(args.data_root)
    write_outputs(args.output_dir, metrics, flags)
    print(f"[완료] 지표 {len(metrics)}개, 플래그 {len(flags)}개")
    print(f"[결과] {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
