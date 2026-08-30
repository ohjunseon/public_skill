#!/usr/bin/env python3
"""
사내 소스보안 CLI의 raw 출력을 표준 findings 스키마(JSON)로 변환한다.

사용법:
    python3 parse_results.py raw_scan_output.txt > findings.json

표준 스키마:
[
  {
    "id": "F-0001",
    "file": "src/main/java/com/example/UserDao.java",
    "line": 42,
    "severity": "critical | high | medium | low",
    "rule_id": "SQL_INJECTION",
    "message": "...",
    "cwe": "CWE-89"   # 없으면 null
  },
  ...
]

*** 이 파일은 플레이스홀더입니다 ***
사내 CLI의 실제 출력 샘플을 받으면 parse_raw_output() 함수 내부 로직만
교체하면 됩니다. 나머지 파이프라인(수정/PR생성)은 이 표준 스키마에만
의존하므로 다른 곳은 건드릴 필요 없습니다.
"""

import sys
import json
import re


# 사내 CLI의 심각도 표기 -> 표준 심각도 매핑
# 예: {"상": "critical", "중": "high", "하": "medium", "정보": "low"}
# 예: {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}
SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    # TODO: 사내 CLI 표기에 맞게 추가/수정
}


_unmapped_severities: set[str] = set()


def normalize_severity(raw_severity: str) -> str:
    """
    매핑되지 않은 심각도는 medium으로 처리하되, 반드시 경고를 남긴다.
    조용히 격하되면 실제 critical 취약점이 자동수정 대상에서 빠지므로 위험하다.
    """
    key = raw_severity.strip().lower()
    if key not in SEVERITY_MAP:
        _unmapped_severities.add(raw_severity.strip())
        return "medium"
    return SEVERITY_MAP[key]


def parse_raw_output(raw_text: str) -> list[dict]:
    """
    TODO: 이 함수를 사내 CLI 출력 형식에 맞게 구현하세요.

    아래는 아주 일반적인 형태 (플레이스홀더 예시):
        [SEVERITY] file:line - RULE_ID - message

    실제 CLI 출력이 JSON이면 json.loads()로 바로 파싱해서
    필드명만 매핑하는 게 훨씬 간단합니다. 아래는 텍스트 파싱 예시.
    """
    findings = []
    # file은 non-greedy + ':<숫자>' 앞까지로 잡아 Windows 경로(C:\...)도 안전하게 처리
    pattern = re.compile(
        r"\[(?P<severity>[^\]]+)\]\s+(?P<file>.+?):(?P<line>\d+)\s*-\s*"
        r"(?P<rule_id>\S+)\s*-\s*(?P<message>.+)"
    )

    for line in raw_text.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        findings.append(
            {
                "id": f"F-{len(findings) + 1:04d}",
                "file": m.group("file").strip(),
                "line": int(m.group("line")),
                "severity": normalize_severity(m.group("severity")),
                "rule_id": m.group("rule_id").strip(),
                "message": m.group("message").strip(),
                "cwe": None,  # TODO: CLI가 CWE를 제공하면 추출해서 채우기
            }
        )

    if not findings:
        print(
            "[parse_results.py][WARN] 파싱된 항목이 없습니다. "
            "parse_raw_output()이 사내 CLI 출력 형식과 맞는지 확인하세요.",
            file=sys.stderr,
        )

    return findings


def main():
    if len(sys.argv) != 2:
        print("사용법: python3 parse_results.py <raw_output_file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        raw_text = f.read()

    findings = parse_raw_output(raw_text)

    if _unmapped_severities:
        print(
            "[parse_results.py][WARN] SEVERITY_MAP에 없는 심각도 표기가 발견되어 "
            f"medium으로 처리했습니다: {sorted(_unmapped_severities)}\n"
            "[parse_results.py][WARN] 실제 critical/high가 자동수정 대상에서 누락될 수 "
            "있으니 SEVERITY_MAP에 매핑을 추가하세요.",
            file=sys.stderr,
        )

    print(json.dumps(findings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
