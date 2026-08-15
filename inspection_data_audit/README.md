# Inspection Data Audit — 로컬 실행 패키지

이 폴더는 산업 검사 데이터의 **모델링 전 감사(pre-model audit)** 연구를 단계적으로 실행하기 위한 최소 패키지다.

현재까지 네 가지를 재현 가능하게 구현했다.

1. 기존 슈타겐 데이터에서 논문 후보 수치를 원자료 산출물로부터 다시 계산한다.
2. 라벨 순환, 판정 렌더링 누수, 날짜 교락, 사건 복제가 진단 지표에 어떤 변화를 만드는지 합성 데이터로 확인한다.
3. 감사 지표가 무작위 평가와 배치 평가의 성능 격차를 예측하는지 합성 데이터로 확인한다.
4. 고정된 프로토콜을 Siemens SMT AOI 440,274행에 적용해 외부 검증의 성공과 실패를 함께 기록한다.

새로운 이상탐지 모델을 학습하는 것은 이번 단계에 포함하지 않는다. 먼저 데이터 감사 지표가 알려진 병리에 반응하고 깨끗한 데이터에서 오탐하지 않는지 검증한다.

## 폴더 구성

- `notion_research_plan.md`: 노션에 바로 붙여넣을 연구 배경·문제정의·실행계획
- `audit_current_data.py`: 현재 데이터의 핵심 수치와 불일치를 재검산
- `simulate_pathologies.py`: 합성 병리 주입 벤치마크
- `performance_gap_experiment.py`: 무작위 평가와 배치 조건 평가의 성능 격차 실험
- `download_siemens.py`: 공식 Siemens 파일 다운로드·이어받기·SHA-256 검증
- `external_validate_siemens.py`: 행 무작위·timestamp 그룹·미래 시간 외부 검증
- `temporal_followup_siemens.py`: 시간 구간·defect slip·volume reduction 사후 개발 실험
- `nonlinear_feasibility_siemens.py`: 고정 비선형 모델의 calibration feasibility gate와 미래 평가
- `paper_protocol_reconstruction_siemens.py`: 논문의 40/10/10×5 분할과 CV 임계값 절차 부분 재현
- `run.sh`: 내부 실험과, 데이터가 있을 경우 외부 검증을 순서대로 실행
- `tests/`: 지표 계산 단위 테스트
- `outputs/`: 실행 시 생성되는 CSV, JSON, Markdown 결과

## 요구 환경

- Python 3.10 이상
- 0~4단계는 외부 Python 패키지 없음
- 5단계만 `pandas`, `scikit-learn` 필요

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

성능 격차 예측 실험:

```bash
python3 performance_gap_experiment.py \
  --seeds 100 \
  --events 300 \
  --output-dir outputs
```

Siemens 외부 데이터 다운로드(공식 조건 확인 후):

```bash
python3 download_siemens.py --accept-license
```

원자료는 약 333MB이며 `external_data/` 아래에 저장되고 Git에는 포함되지 않는다. 저장소가 공개한 파일 크기와 SHA-256이 모두 일치해야 최종 파일로 확정된다. 사용 범위는 [공식 데이터 페이지](https://data.mendeley.com/datasets/99jzmh9658/1)의 CC BY-NC 3.0 표시와 Siemens Legal Notice를 직접 확인해야 한다.

Siemens 외부 검증:

```bash
python3 external_validate_siemens.py
```

Siemens 시간·업무지표 후속 개발 실험:

```bash
python3 temporal_followup_siemens.py
```

비선형 feasibility 실험은 의도하지 않은 대용량 실행을 막기 위해 `run.sh`에 포함하지 않았다. 먼저 환경만 확인한다.

```bash
/Users/woorivermountain/Desktop/data/.venv/bin/python \
  nonlinear_feasibility_siemens.py --skip-hash --check-only
```

확인 후 전체 실행:

```bash
/Users/woorivermountain/Desktop/data/.venv/bin/python \
  nonlinear_feasibility_siemens.py --skip-hash
```

논문 프로토콜 부분 재현은 먼저 1시드 파일럿으로 실행한다.

```bash
/Users/woorivermountain/Desktop/data/.venv/bin/python \
  paper_protocol_reconstruction_siemens.py --skip-hash --seeds 1
```

파일럿이 정상 완료된 후 논문과 같은 10시드 실행:

```bash
/Users/woorivermountain/Desktop/data/.venv/bin/python \
  paper_protocol_reconstruction_siemens.py --skip-hash --seeds 10
```

원 논문이 인용한 GitLab 코드는 현재 인증 없이는 접근할 수 없고 Bayesian search space도 논문에 기재되지 않았다. 따라서 이 스크립트는 완전 동일 코드 재현이 아니라 공개된 데이터 분할과 임계값 선택 절차의 부분 재현이다.

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
- `outputs/performance_gap_runs.csv`: 시드별 무작위·그룹·배치 성능
- `outputs/performance_gap_summary.csv`: 병리·강도별 성능 격차 요약
- `outputs/performance_gap_report.md`: 감사 지표–성능 격차 상관 보고서
- `outputs/siemens_external_summary.csv`: 외부 검증 핵심 결과 한 행
- `outputs/siemens_external_report.md`: 고정 판정과 사후 탐색을 구분한 외부 검증 보고서
- `outputs/temporal_followup_business.csv`: 학습·미래 구간별 confusion matrix와 업무지표
- `outputs/temporal_followup_report.md`: 다음 외부 데이터용 시간 강건성 후보 프로토콜
- `outputs/nonlinear_feasibility_business.csv`: 비선형 모델의 구간별 AUROC·업무지표
- `outputs/nonlinear_feasibility_report.md`: calibration gate와 미래 시간 평가 보고서
- `outputs/paper_protocol_reconstruction_runs.csv`: 시드·시간 구간별 부분 재현 결과
- `outputs/paper_protocol_reconstruction_report.md`: 논문 RFC2 보고값과의 비교

## 3단계 외부 검증 요약

- 공식 파일 440,274행과 SHA-256을 확인했다.
- true defect 4,622행은 875개 양성 timestamp에 속해 양성 행/양성 PCB timestamp 비율이 5.282였다.
- 행 무작위 AUROC 0.741, timestamp 그룹 0.698, 미래 시간 0.727이었다. 사전 기준 0.05에 대한 격차는 각각 0.043, 0.014로 미발동이었다.
- 날짜 재대입 정확도 lift는 0이었다. 1.05% 양성률에서는 모든 날짜의 최빈값이 음성이어서 정확도 기반 날짜 지표가 무력해졌다.
- 미래 5구간 AUROC는 0.545~0.874로 불안정했다. 이 범위는 사후 탐색이며 확증 판정을 바꾸는 데 사용하지 않는다.

따라서 현재 프로토콜은 비독립 행을 외부 데이터에서 검출했지만, 알려진 시간 동역학을 단일 집계 격차로 검출하지 못했다. 이는 외부 검증 실패이자 다음 지표 설계의 근거다.

## 4단계 후속 개발 요약

- timestamp-only AUROC는 0.642, 무작위 AUROC와 최악 미래 구간 AUROC의 차이는 0.196이었다.
- 학습 기간에서 defect slip rate 1% 이하가 되도록 임계값을 고정했다.
- 단순 선형 모델의 학습 volume reduction은 2.7%로 목표 40%에 크게 미달했다.
- 미래 전체 slip rate는 1.6%, volume reduction은 2.7%였고 미래 5구간 모두 두 목표를 동시에 달성하지 못했다.

이 결과로 시간 드리프트의 운영 실패를 확증할 수는 없다. 기준 모델이 학습 기간부터 업무목표를 달성하지 못했기 때문이다. 대신 AUROC 0.741이 운영 가능성을 의미하지 않는다는 사실과, 시간 평가 전에 학습 feasibility를 먼저 검사해야 한다는 순서를 확인했다.

## 5단계 비선형 feasibility 결과

- 고정 HistGradientBoosting 모델의 학습 AUROC는 0.997, calibration AUROC는 0.870, 미래 AUROC는 0.878이었다.
- calibration에서 slip rate 0.62%를 만족했지만 volume reduction은 1.65%로 목표 40%에 미달했다.
- 미래 전체도 slip rate 0.85%, volume reduction 3.16%로 두 목표를 동시에 충족하지 못했다.
- calibration gate가 실패했으므로 미래 구간 결과를 시간 드리프트의 효과로 해석하지 않는다.

선형 모델보다 AUROC는 크게 높아졌지만 엄격한 defect 보호 조건에서 자동 통과시킬 수 있는 false call은 매우 적었다. 다음 단계에서는 임의의 모델을 계속 추가하지 않고, Siemens 데이터에 대해 발표된 기준 방법의 전처리·특징·분할·업무지표를 그대로 재현해 구현 차이와 데이터 한계를 구분한다.

## 이번 단계의 완료 조건

- 저장된 수치가 동일 입력에서 재현된다.
- 서로 다른 정의의 수치가 같은 이름으로 섞이지 않는다.
- 각 합성 병리의 전용 지표가 강도 증가에 따라 대체로 단조 증가한다.
- 깨끗한 대조군과 병리 데이터가 구분되지 않으면 해당 지표는 다음 단계로 넘기지 않는다.
- 감사 지표와 배치 성능 격차의 Spearman 상관을 병리별로 보고한다.
- 외부 데이터에서 사전 기준의 미발동과 사후 탐색 결과를 분리해 보고한다.
