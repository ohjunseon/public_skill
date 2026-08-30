#!/usr/bin/env bash
# 수정 후 기존 테스트 스위트를 실행해 회귀 여부를 확인한다.
# 프로젝트 종류에 따라 자동 감지해서 적절한 커맨드를 실행한다.
# 필요시 TODO 부분을 프로젝트에 맞게 조정하세요.
set -euo pipefail

echo "[run_tests.sh] 테스트 실행 중..." >&2

if [ -f "pom.xml" ]; then
    mvn -q test
elif [ -f "build.gradle" ] || [ -f "build.gradle.kts" ]; then
    ./gradlew test
elif [ -f "package.json" ]; then
    npm test
elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || ls tests/ >/dev/null 2>&1; then
    python3 -m pytest
else
    echo "[run_tests.sh][WARN] 테스트 러너를 자동으로 판별하지 못했습니다." >&2
    echo "[run_tests.sh][WARN] 이 스크립트의 TODO 부분에 프로젝트 테스트 커맨드를 직접 추가하세요." >&2
    # TODO: 프로젝트 전용 테스트 커맨드 추가
    exit 1
fi

echo "[run_tests.sh] 테스트 통과" >&2
