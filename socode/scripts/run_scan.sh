#!/usr/bin/env bash
# 사내 소스보안 CLI 실행 wrapper
#
# 사용법:
#   bash run_scan.sh <target_path> > raw_scan_output.txt
#
# 설정 방법 (둘 중 하나):
#   1) 환경변수로 지정 — 스크립트 수정 없이 사용 가능
#        export SECURITY_CLI="secutool"
#        export SECURITY_CLI_ARGS="scan --path {TARGET} --format json"
#        ({TARGET} 자리에 대상 경로가 치환됩니다)
#   2) 아래 기본값(DEFAULT_*)을 사내 CLI에 맞게 직접 수정
set -euo pipefail

TARGET_PATH="${1:?사용법: run_scan.sh <target_path>}"

if [ ! -e "$TARGET_PATH" ]; then
    echo "[run_scan.sh][ERROR] 대상 경로가 존재하지 않습니다: $TARGET_PATH" >&2
    exit 1
fi

# ==== TODO: 사내 CLI에 맞게 수정 ====
DEFAULT_CLI="secutool"
DEFAULT_ARGS="scan --path {TARGET}"

SECURITY_CLI="${SECURITY_CLI:-$DEFAULT_CLI}"
SECURITY_CLI_ARGS="${SECURITY_CLI_ARGS:-$DEFAULT_ARGS}"

if ! command -v "$SECURITY_CLI" >/dev/null 2>&1; then
    echo "[run_scan.sh][ERROR] 보안 CLI를 찾을 수 없습니다: $SECURITY_CLI" >&2
    echo "[run_scan.sh][ERROR] SECURITY_CLI / SECURITY_CLI_ARGS 환경변수를 설정하거나," >&2
    echo "[run_scan.sh][ERROR] 이 스크립트의 DEFAULT_CLI / DEFAULT_ARGS를 수정하세요." >&2
    exit 1
fi

# {TARGET} 치환 후 인자 배열로 변환
RESOLVED_ARGS="${SECURITY_CLI_ARGS//\{TARGET\}/$TARGET_PATH}"
read -r -a ARG_ARRAY <<< "$RESOLVED_ARGS"

echo "[run_scan.sh] 실행: $SECURITY_CLI ${ARG_ARRAY[*]}" >&2

# CLI가 취약점 발견 시 non-zero exit code를 반환하는 경우가 많으므로
# set -e에 걸려 중단되지 않도록 exit code를 별도 처리한다.
set +e
"$SECURITY_CLI" "${ARG_ARRAY[@]}"
SCAN_EXIT=$?
set -e

if [ "$SCAN_EXIT" -gt 1 ]; then
    echo "[run_scan.sh][ERROR] 스캔이 비정상 종료되었습니다 (exit=$SCAN_EXIT)." >&2
    exit "$SCAN_EXIT"
fi

echo "[run_scan.sh] 스캔 완료 (exit=$SCAN_EXIT)" >&2
