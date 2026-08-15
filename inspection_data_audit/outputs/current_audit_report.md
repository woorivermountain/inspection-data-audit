# 현재 데이터 감사 보고서

## 핵심 결과

- 제품 100개, ROI 7800행, 이미지 364장
- 설비 판정 규칙 재현율: 1.0
- 날짜 재대입 정확도: 0.961538 / 날짜 LODO AUROC: 0.526
- date-only 무작위-LODO 격차: 0.3921
- 테두리 적색 LODO AUROC: 1.0
- PatchCore 무작위-LODO 격차: crop 0.2627, clean 0.2017
- 오버레이 제거 PatchCore LODO AUROC: 0.5578
- 사람 판정: 차이 민감도 1.0, 동일 특이도 0.826087, 반복 일치 0.416667
- 사양 기권 q10 개선: 0.0
- analyst_log: 실제 17건 / 요약 6건

## 자동 플래그

- **high · stale_summary** — analyst_log 실제 17건, summary.csv 6건
- **high · resubstitution_vs_extrapolation** — 날짜 재대입 정확도와 LODO AUROC 차이 0.436
- **high · human_repeatability** — 반복 일치 5/12
- **high · no_lovo_improvement** — q10 무작위=0.014103, 최고 비무작위=0.014103

## 판정

현재 결과는 모델 성능 논문보다 모델링 전 데이터 감사 연구에 더 적합하다. 외부 검증 전에 수치 정의와 stale artifact를 먼저 고정해야 한다.
