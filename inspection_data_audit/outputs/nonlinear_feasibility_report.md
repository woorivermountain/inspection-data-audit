# 5단계 — 비선형 모델 feasibility gate

## 사전 고정 프로토콜

- 모델: `HistGradientBoostingClassifier` 단일 설정
- 설정: `{"early_stopping": false, "l2_regularization": 1.0, "learning_rate": 0.08, "max_iter": 200, "max_leaf_nodes": 31, "min_samples_leaf": 50, "random_state": 20260815}`
- 입력: inspection type + mapping.json의 물리 측정 특징 65개
- timestamp와 meta_feat 열은 모델 입력에서 제외
- 분할: 앞 50% 학습 / 다음 20% calibration / 마지막 30% 미래 평가
- 동일 timestamp는 한 구간에만 포함
- calibration gate: slip rate ≤ 1% 및 volume reduction ≥ 40%
- 임계값은 calibration에서만 정하고 미래 구간에서 재조정하지 않음

## 결과

| 구간 | 행 | true defect | AUROC | slip rate | volume reduction | manual review | 목표 |
|---|---:|---:|---:|---:|---:|---:|---|
| 학습 | 194,929 | 1,602 | 0.997 | 0.000 | 0.070 | 0.931 | 참고 |
| calibration | 101,144 | 325 | 0.870 | 0.006 | 0.017 | 0.984 | 미달 |
| 미래 전체 | 144,201 | 2,695 | 0.878 | 0.009 | 0.032 | 0.969 | 미달 |
| 미래 1 | 26,745 | 226 | 0.963 | 0.000 | 0.010 | 0.990 | 미달 |
| 미래 2 | 28,265 | 144 | 0.799 | 0.000 | 0.015 | 0.985 | 미달 |
| 미래 3 | 23,919 | 178 | 0.886 | 0.006 | 0.010 | 0.990 | 미달 |
| 미래 4 | 20,321 | 778 | 0.920 | 0.004 | 0.075 | 0.927 | 미달 |
| 미래 5 | 44,951 | 1,369 | 0.823 | 0.014 | 0.048 | 0.953 | 미달 |

## 판정

Calibration feasibility gate: **미달**.

Gate를 통과하지 못했으므로 미래 결과를 시간 드리프트의 효과로 해석하지 않는다. 모델 변경은 새 실험으로 사전 고정해야 한다.

실행 환경: pandas 3.0.5, scikit-learn 1.9.0, 학습 6.4초.
