# 다른 환경에서 재현하는 방법

이 저장소의 실행은 두 부분으로 나뉜다.

1. **공개 재현:** GitHub 코드와 Siemens 공개 데이터만 사용한다. 다른 컴퓨터에서 그대로 실행할 수 있다.
2. **현장 데이터 감사:** 로컬 퓨즈박스 원자료가 필요하다. 원자료는 Git에 포함되지 않으므로 별도로 안전하게 전달해야 한다.

## 1. 권장 환경

- Python 3.11 권장
- 기록된 5~6단계 결과 환경: Python 3.11.15
- 빈 디스크 공간 최소 2GB 권장
- Siemens 원자료 다운로드 용량 약 318MiB
- 10시드 논문 프로토콜 실행은 현재 Mac에서 약 213초

CPU와 운영체제가 다르면 부동소수점 계산과 모델 병렬화 때문에 마지막 자릿수가 달라질 수 있다. 같은 판정과 가까운 수치를 재현하는 것이 목적이며, byte 단위로 동일한 CSV가 필요하면 Python과 패키지 버전뿐 아니라 OS·CPU·스레드 수도 고정해야 한다.

## 2. 저장소 받기

```bash
git clone https://github.com/woorivermountain/inspection-data-audit.git
cd inspection-data-audit/inspection_data_audit
git switch codex/inspection-data-audit
```

저장소가 비공개 상태라면 먼저 GitHub CLI의 `gh auth login` 또는 개인 액세스 방식으로 인증해야 한다. 현재 작업 브랜치가 기본 브랜치에 아직 병합되지 않았다면 `git switch`가 필요하다.

## 3. Python 가상환경 만들기

### macOS 또는 Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

호환 가능한 최신 버전으로 실행만 확인하려면 마지막 줄에서 `requirements-ml.txt`를 사용할 수 있다. 기존 결과와 비교하려면 `requirements-lock.txt`를 권장한다.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

## 4. 설치 확인

```bash
python -m unittest discover -s tests -v
python nonlinear_feasibility_siemens.py --check-only
python paper_protocol_reconstruction_siemens.py --check-only
```

단위 테스트 14개가 통과하고 고정 모델 설정이 출력되어야 한다. Siemens 파일을 아직 받지 않았다면 두 `--check-only` 명령은 파일 없음으로 중단될 수 있으므로 다음 단계 후 다시 실행한다.

## 5. Siemens 공개 데이터 받기

먼저 공식 데이터 페이지의 CC BY-NC 3.0 표시와 Siemens Legal Notice를 직접 확인한다.

- 공식 페이지: https://data.mendeley.com/datasets/99jzmh9658/1

조건을 확인하고 비상업적 논문 연계 테스트 범위에 동의한 경우에만 실행한다.

```bash
python download_siemens.py --accept-license
```

다운로드 위치는 `external_data/siemens/`다. 스크립트가 공식 파일 크기와 SHA-256을 확인하며 중단된 다운로드는 다음 실행에서 이어받는다. 원자료는 `.gitignore`에 의해 Git에 올라가지 않는다.

검증만 다시 수행하려면 다음 명령을 사용한다.

```bash
python download_siemens.py --accept-license --verify-only
```

## 6. 공개 실험 실행

### macOS 또는 Linux 전체 실행

```bash
chmod +x run_public.sh
./run_public.sh
```

빠른 설치 점검에서는 논문 프로토콜만 1시드로 줄일 수 있다.

```bash
PAPER_SEEDS=1 ./run_public.sh
```

이 설정도 합성 실험과 다른 외부 실험은 모두 수행한다. 최종 보고용 결과는 기본값인 10시드로 다시 실행해야 한다.

### Windows PowerShell 개별 실행

```powershell
python simulate_pathologies.py --seeds 100 --events 300 --output-dir outputs
python performance_gap_experiment.py --seeds 100 --events 300 --output-dir outputs
python external_validate_siemens.py
python temporal_followup_siemens.py --skip-hash
python nonlinear_feasibility_siemens.py --skip-hash
python paper_protocol_reconstruction_siemens.py --skip-hash --seeds 10
python -m unittest discover -s tests -v
```

결과는 모두 `outputs/`에 저장된다. 실행 전 Git에 기록된 결과와 실행 후 결과의 차이는 다음 명령으로 확인한다.

```bash
git diff -- outputs
```

## 7. 현장 데이터 감사 실행

단계 0의 현장 데이터 감사에는 아래 구조를 가진 원자료 폴더가 필요하다.

```text
DATA_ROOT/
├── analysis/out/
│   ├── 01_product_log.csv
│   ├── 02_roi_records.csv
│   ├── 04_image_index.csv
│   └── 10_warnings.csv
├── experiments/results/results.csv
├── derive/results/stagen_lovo.csv
├── roi_perception_labels.csv
└── cases/
    ├── STG-2026-01/case.yaml
    └── summary.csv
```

원자료를 별도로 전달받은 뒤 경로를 명시한다.

```bash
python audit_current_data.py \
  --data-root "/absolute/path/to/data" \
  --output-dir outputs
```

기존 `run.sh`는 이 현장 원자료와 단계 1~4를 함께 실행하는 로컬용 진입점이다. 다른 환경에서 공개 데이터만 재현할 때는 `run_public.sh`를 사용한다.

## 8. 재현 확인 기준

- 단위 테스트 14개 통과
- Siemens 파일 크기와 SHA-256 일치
- 합성 병리 4개 모두 내부 단조성 통과
- 성능 격차 내부 파일럿 4/4 통과
- Siemens 행 수 440,274, true defect 4,622
- 논문 프로토콜 10시드에서 미래 50개 시드-구간의 업무목표 달성 0개

패키지 또는 플랫폼 차이로 세부 소수점이 달라도 위 구조적 판정이 같아야 한다. 판정이 달라지면 `python --version`, `pip freeze`, Git commit, 실행 명령과 생성된 보고서를 함께 보존한다.
