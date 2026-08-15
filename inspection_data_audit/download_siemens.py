from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path


DATASET_PAGE = "https://data.mendeley.com/datasets/99jzmh9658/1"
FILES = {
    "dataset.csv": {
        "size": 333_590_345,
        "sha256": "53e8568743216d556856ed69b388f6750fbfa0b8c59ad31f970515ac9eb10e62",
        "url": "https://data.mendeley.com/public-files/datasets/99jzmh9658/files/3bcbede5-358b-489a-b4b8-9ac3a06e89b9/file_downloaded",
    },
    "mapping.json": {
        "size": 7_205,
        "sha256": "3b20f440b6d9ed0baefa662e1a6f03688befbe0f28341a3b54655d3058c6e486",
        "url": "https://data.mendeley.com/public-files/datasets/99jzmh9658/files/e22624d7-0124-410c-8528-715b1e1c8c87/file_downloaded",
    },
}
LEGAL_SUMMARY = (
    "Siemens 원자료는 CC BY-NC 3.0 표시와 별도 Legal Notice의 적용을 받습니다. "
    "비상업적이며 연결 논문을 위한 테스트에만 사용하고, 상세 조건은 공식 페이지에서 확인해야 합니다."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path: Path, metadata: dict[str, object]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == int(metadata["size"])
        and sha256_file(path) == str(metadata["sha256"])
    )


def download(name: str, output_dir: Path) -> None:
    metadata = FILES[name]
    destination = output_dir / name
    if destination.exists():
        if verified(destination, metadata):
            print(f"[확인] {name}: 기존 파일의 크기와 SHA-256이 일치합니다")
            return
        raise RuntimeError(f"기존 파일이 공식 해시와 다릅니다: {destination}")

    temporary = destination.with_suffix(destination.suffix + ".part")
    received = temporary.stat().st_size if temporary.exists() else 0
    if received > int(metadata["size"]):
        raise RuntimeError(f"미완료 파일이 공식 크기보다 큽니다: {temporary}")
    headers = {"User-Agent": "inspection-data-audit/1.0"}
    if received:
        headers["Range"] = f"bytes={received}-"
        print(f"[이어받기] {name}: {received / 1024 / 1024:.0f} MiB부터")
    request = urllib.request.Request(str(metadata["url"]), headers=headers)
    digest = hashlib.sha256()
    if received:
        with temporary.open("rb") as existing:
            for chunk in iter(lambda: existing.read(1024 * 1024), b""):
                digest.update(chunk)
    next_progress = (received // (50 * 1024 * 1024) + 1) * 50 * 1024 * 1024
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if received and response.status != 206:
                raise RuntimeError("서버가 Range 이어받기를 허용하지 않았습니다")
            mode = "ab" if received else "xb"
            with temporary.open(mode) as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if received >= next_progress:
                        print(f"[다운로드] {name}: {received / 1024 / 1024:.0f} MiB", flush=True)
                        next_progress += 50 * 1024 * 1024
        if received != int(metadata["size"]) or digest.hexdigest() != str(metadata["sha256"]):
            raise RuntimeError(f"다운로드 검증 실패: {name} (크기 또는 SHA-256 불일치)")
        os.replace(temporary, destination)
        print(f"[완료] {name}: {received:,} bytes, SHA-256 일치")
    except Exception:
        # 부분 파일은 다음 실행에서 Range 요청으로 이어받는다.
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="공식 Mendeley 저장소에서 Siemens SMT AOI 외부 검증 데이터를 받는다.")
    parser.add_argument("--output-dir", type=Path, default=Path("external_data/siemens"))
    parser.add_argument("--accept-license", action="store_true", help="공식 페이지의 라이선스·Legal Notice 확인을 명시")
    parser.add_argument("--verify-only", action="store_true", help="다운로드 없이 기존 파일 검증")
    args = parser.parse_args()

    print(LEGAL_SUMMARY)
    print(f"공식 조건: {DATASET_PAGE}")
    if not args.accept_license:
        parser.error("다운로드 또는 검증 전에 공식 조건을 확인하고 --accept-license를 지정하세요")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        failed = [name for name, metadata in FILES.items() if not verified(args.output_dir / name, metadata)]
        if failed:
            print(f"[실패] 검증되지 않은 파일: {', '.join(failed)}", file=sys.stderr)
            raise SystemExit(1)
        print("[확인] 공식 파일 2개의 크기와 SHA-256이 모두 일치합니다")
        return

    for name in ("mapping.json", "dataset.csv"):
        download(name, args.output_dir)


if __name__ == "__main__":
    main()
