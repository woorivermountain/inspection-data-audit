from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

from metrics import binary_auc, group_resubstitution_accuracy, majority_accuracy, mean


STRENGTHS = (0.0, 0.25, 0.5, 0.75, 1.0)
PATHOLOGIES = ("label_circularity", "verdict_overlay", "date_confound", "event_duplication")


def make_base(seed: int, n_events: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows = []
    for event_id in range(n_events):
        truth = int(rng.random() < 0.12)
        latent = (1.4 if truth else -0.4) + rng.gauss(0, 1.0)
        machine_score = latent + rng.gauss(0, 0.7)
        machine_verdict = int(machine_score >= 0.5)
        rows.append({
            "event_id": f"E{event_id:04d}",
            "day": f"D{rng.randrange(10):02d}",
            "truth": truth,
            "machine_score": machine_score,
            "machine_verdict": machine_verdict,
            "observed_label": truth,
            "overlay_feature": rng.gauss(0, 1.0),
        })
    return rows


def inject(rows: list[dict[str, object]], pathology: str, strength: float, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed + 100_000)
    injected: list[dict[str, object]] = []
    for source in rows:
        row = dict(source)
        if pathology == "label_circularity" and rng.random() < strength:
            row["observed_label"] = row["machine_verdict"]
        elif pathology == "verdict_overlay":
            row["overlay_feature"] = rng.gauss(3.0 * strength * int(row["observed_label"]), 1.0)
        elif pathology == "date_confound" and rng.random() < strength:
            if int(row["observed_label"]) == 1:
                row["day"] = f"D{rng.randrange(0, 5):02d}"
            else:
                row["day"] = f"D{rng.randrange(5, 10):02d}"

        copies = 1
        if pathology == "event_duplication" and int(row["observed_label"]) == 1:
            copies = 1 + round(9 * strength)
        for copy_id in range(copies):
            out = dict(row)
            out["row_id"] = f"{row['event_id']}_{copy_id}"
            injected.append(out)
    return injected


def measure(rows: list[dict[str, object]]) -> dict[str, float]:
    labels = [int(row["observed_label"]) for row in rows]
    machine = [int(row["machine_verdict"]) for row in rows]
    overlay = [float(row["overlay_feature"]) for row in rows]
    label_recovery = sum(a == b for a, b in zip(labels, machine)) / len(rows)
    overlay_auc = binary_auc(labels, overlay)
    date_rows = [{"day": str(row["day"]), "label": str(row["observed_label"])} for row in rows]
    date_accuracy = group_resubstitution_accuracy(date_rows, "day", "label")
    baseline = majority_accuracy(str(value) for value in labels)
    pos_rows = sum(labels)
    pos_events = len({str(row["event_id"]) for row in rows if int(row["observed_label"]) == 1})
    return {
        "label_rule_recovery": label_recovery,
        "overlay_auc": overlay_auc,
        "date_accuracy": date_accuracy,
        "date_lift": date_accuracy - baseline,
        "positive_row_event_ratio": pos_rows / pos_events if pos_events else float("nan"),
    }


def run(seeds: int, events: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for pathology in PATHOLOGIES:
        for strength in STRENGTHS:
            for seed in range(seeds):
                base = make_base(seed, events)
                rows = inject(base, pathology, strength, seed)
                results.append({
                    "pathology": pathology,
                    "strength": strength,
                    "seed": seed,
                    "n_events": events,
                    "n_rows": len(rows),
                    **measure(rows),
                })
    return results


def summarize(results: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, float], list[dict[str, object]]] = defaultdict(list)
    for row in results:
        groups[(str(row["pathology"]), float(row["strength"]))].append(row)
    metrics = ("label_rule_recovery", "overlay_auc", "date_lift", "positive_row_event_ratio")
    summary = []
    for (pathology, strength), rows in sorted(groups.items()):
        out: dict[str, object] = {"pathology": pathology, "strength": strength, "runs": len(rows)}
        for metric_name in metrics:
            values = [float(row[metric_name]) for row in rows]
            out[f"{metric_name}_mean"] = mean(values)
            out[f"{metric_name}_min"] = min(values)
            out[f"{metric_name}_max"] = max(values)
        summary.append(out)
    return summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def monotonicity(summary: list[dict[str, object]], pathology: str, metric_name: str) -> tuple[bool, list[float]]:
    rows = sorted((row for row in summary if row["pathology"] == pathology), key=lambda row: float(row["strength"]))
    values = [float(row[f"{metric_name}_mean"]) for row in rows]
    return all(b >= a - 1e-12 for a, b in zip(values, values[1:])), values


def write_report(path: Path, summary: list[dict[str, object]], seeds: int, events: int) -> None:
    checks = [
        ("label_circularity", "label_rule_recovery"),
        ("verdict_overlay", "overlay_auc"),
        ("date_confound", "date_lift"),
        ("event_duplication", "positive_row_event_ratio"),
    ]
    lines = [
        "# 합성 병리 주입 실험 보고서",
        "",
        f"- 시드: 병리·강도별 {seeds}개",
        f"- 독립 사건: 시드별 {events}개",
        "- 병리 강도: 0, 0.25, 0.50, 0.75, 1.00",
        "",
        "## 단조성 점검",
        "",
        "| 병리 | 대응 지표 | 강도별 평균 | 판정 |",
        "|---|---|---|---|",
    ]
    passed = True
    for pathology, metric_name in checks:
        ok, values = monotonicity(summary, pathology, metric_name)
        passed &= ok
        formatted = " → ".join(f"{value:.3f}" for value in values)
        lines.append(f"| {pathology} | {metric_name} | {formatted} | {'통과' if ok else '실패'} |")
    lines.extend([
        "",
        "## 판정",
        "",
        "이 실험은 진단 지표의 내부 작동 여부만 확인한다. 실제 데이터 일반화나 성능 붕괴 예측을 증명하지 않는다.",
        "",
        f"전체 내부 단조성 판정: **{'통과' if passed else '실패'}**",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="산업 검사 데이터 병리를 합성 주입하고 감사 지표를 점검한다.")
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--events", type=int, default=300)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = run(args.seeds, args.events)
    summary = summarize(results)
    write_csv(args.output_dir / "synthetic_benchmark.csv", results)
    write_csv(args.output_dir / "synthetic_summary.csv", summary)
    write_report(args.output_dir / "synthetic_report.md", summary, args.seeds, args.events)
    print(f"[완료] 합성 실행 {len(results)}회")
    print(f"[결과] {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
