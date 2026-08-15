# Inspection Data Audit — 로컬 실행 패키지

이 폴더는 산업 검사 데이터의 **모델링 전 감사(pre-model audit)** 연구를 단계적으로 실행하기 위한 최소 패키지다.

현재 단계의 목표는 두 가지다.

1. 기존 슈타겐 데이터에서 논문 후보 수치를 원자료 산출물로부터 다시 계산한다.
2. 라벨 순환, 판정 렌더링 누수, 날짜 교락, 사건 복제가 진단 지표에 어떤 변화를 만드는지 합성 데이터로 확인한다.

새로운 이상탐지 모델을 학습하는 것은 이번 단계에 포함하지 않는다. 먼저 데이터 감사 지표가 알려진 병리에 반응하고 깨끗한 데이터에서 오탐하지 않는지 검증한다.

## 폴더 구성

- `notion_research_plan.md`: 노션에 바로 붙여넣을 연구 배경·문제정의·실행계획
- `audit_current_data.py`: 현재 데이터의 핵심 수치와 불일치를 재검산
- `simulate_pathologies.py`: 합성 병리 주입 벤치마크
- `run.sh`: 두 실험을 순서대로 실행
- `tests/`: 지표 계산 단위 테스트
- `outputs/`: 실행 시 생성되는 CSV, JSON, Markdown 결과

## 요구 환경

- Python 3.10 이상
- 외부 Python 패키지 없음

macOS 기본 `python3` 또는 기존 데이터 폴더의 가상환경을 사용할 수 있다.

## 가장 간단한 실행

```bash
cd "/Users/woorivermountain/Documents/연구주제/inspection_data_audit"
./run.sh "/Users/woorivermountain/Desktop/data"
```

다른 Python을 사용하려면 `PYTHON_BIN`을 지정한다.

```bash
PYTHON_BIN="/Users/woorivermountain/Desktop/data/.venv/bin/python" \
  ./run.sh "/Users/woorivermountain/Desktop/data"
```

## 개별 실행

현재 데이터 감사:

```bash
python3 audit_current_data.py \
  --data-root "/Users/woorivermountain/Desktop/data" \
  --output-dir outputs
```

합성 병리 주입 실험:

```bash
python3 simulate_pathologies.py \
  --seeds 100 \
  --events 300 \
  --output-dir outputs
```

테스트:

```bash
python3 -m unittest discover -s tests -v
```

## 생성되는 결과

- `outputs/current_metrics.json`: 재계산된 핵심 수치
- `outputs/evidence_ledger.csv`: 수치–정의–출처–해석 연결표
- `outputs/current_audit_report.md`: 현재 데이터 감사 결과
- `outputs/synthetic_benchmark.csv`: 시드별 병리 주입 결과
- `outputs/synthetic_summary.csv`: 병리·강도별 요약
- `outputs/synthetic_report.md`: 합성 실험 판정 보고서

## 이번 단계의 완료 조건

- 저장된 수치가 동일 입력에서 재현된다.
- 서로 다른 정의의 수치가 같은 이름으로 섞이지 않는다.
- 각 합성 병리의 전용 지표가 강도 증가에 따라 대체로 단조 증가한다.
- 깨끗한 대조군과 병리 데이터가 구분되지 않으면 해당 지표는 다음 단계로 넘기지 않는다.
