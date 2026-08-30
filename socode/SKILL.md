---
name: security-fix-copilot
description: 사내 소스코드 취약점 진단 CLI 툴을 실행하고, 결과를 분석해 Critical/High 취약점은 자동으로 코드를 수정한 뒤 PR을 생성하는 보안 코파일럿 스킬. "보안 스캔", "취약점 점검", "시큐어코딩 점검", "소스보안 진단", 사내 보안툴 이름 언급, "취약점 자동수정", "보안 PR 만들어줘" 등의 요청이 있을 때 반드시 사용할 것. 코드 리뷰나 커밋 전 보안 점검이 필요한 상황에도 적극적으로 이 스킬을 제안할 것.
---

# 소스보안 자동수정 코파일럿

사내 정적분석(SAST) CLI의 출력을 표준 스키마로 정규화한 뒤, Claude가 직접 코드를 읽고 수정하고, 테스트 검증 후 PR까지 생성한다.

## 전체 흐름

```
1. run_scan.sh       : 사내 CLI 실행 → raw 결과 저장
2. parse_results.py  : raw 출력 → 표준 JSON (findings.json)
3. (Claude 직접)     : 분석 & 코드 수정
4. run_tests.sh      : 회귀 테스트 통과 확인
5. (Claude 직접)     : PR 본문 작성 (pr_body.md)
6. create_pr.sh      : 브랜치 → 커밋 → PR
```

---

## Step 1. 스캔 실행

```bash
bash scripts/run_scan.sh <target_path> > raw_scan_output.txt
```

사내 CLI는 두 방법 중 하나로 지정한다:

```bash
export SECURITY_CLI="사내툴명"
export SECURITY_CLI_ARGS="scan --path {TARGET} --format json"   # {TARGET}이 경로로 치환됨
```

또는 `run_scan.sh` 안의 `DEFAULT_CLI` / `DEFAULT_ARGS`를 직접 수정한다.

`exit code 1`은 "취약점 발견"으로 간주해 정상 처리하고, 2 이상만 실패로 본다. 사내 CLI의 exit code 규약이 다르면 이 임계값을 조정할 것.

## Step 2. 결과 정규화

```bash
python3 scripts/parse_results.py raw_scan_output.txt > findings.json
```

표준 스키마:

```json
{
  "id": "F-0001",
  "file": "src/main/java/com/example/UserDao.java",
  "line": 42,
  "severity": "critical | high | medium | low",
  "rule_id": "SQL_INJECTION",
  "message": "사용자 입력이 SQL 쿼리에 직접 연결됨",
  "cwe": "CWE-89"
}
```

**현재 파서는 플레이스홀더다.** 실제 CLI 출력 샘플을 확보하면 `parse_raw_output()` 함수 내부와 `SEVERITY_MAP`만 교체하면 되고, 이후 단계는 표준 스키마에만 의존하므로 영향받지 않는다.

파서 실행 시 **stderr 경고를 반드시 확인할 것**:
- `파싱된 항목이 없습니다` → 정규식이 CLI 출력 형식과 불일치
- `SEVERITY_MAP에 없는 심각도` → 미매핑 심각도가 medium으로 격하된 상태. 실제 critical이 자동수정 대상에서 누락될 수 있으므로 **반드시 매핑을 먼저 추가한 뒤 재실행**한다.

## Step 3. 분석 및 수정 (Claude가 직접 수행)

`findings.json`을 읽고 심각도 정책에 따라 처리한다.

| 심각도 | 처리 |
|---|---|
| critical, high | 코드 직접 수정 → 커밋 대상 |
| medium | 리포트에만 포함, 수정 제안 텍스트 작성 |
| low | 리포트에만 포함 |

수정 시 반드시:

1. `view`로 문제 라인 주변(최소 앞뒤 20줄) 컨텍스트를 확인한다. findings의 라인 번호만 믿고 바로 고치지 않는다 — 정적분석 도구는 라인이 어긋나거나 오탐이 나는 경우가 흔하다.
2. `references/vuln_patterns.md`에서 해당 `rule_id`/CWE의 표준 수정 패턴을 확인한다.
3. **오탐이라고 판단되면 수정하지 않는다.** 판단 근거를 기록해 리포트의 오탐 섹션에 넣는다.
4. 비즈니스 로직 변경이 필요하거나 방향이 모호하면 자동수정하지 않고 `manual_review_needed`로 분류한다.
5. 한 취약점당 최소 diff로 수정한다. 관련 없는 리팩토링·포맷팅 변경을 섞지 않는다.
6. **수정한 파일 경로 목록을 반드시 기록해 둔다.** Step 6에서 커밋 대상으로 명시해야 한다.

## Step 4. 테스트 검증

```bash
bash scripts/run_tests.sh
```

실패하면 해당 수정을 되돌리고 `manual_review_needed`로 전환한다. **테스트 통과 없이는 PR을 만들지 않는다.**

테스트 러너를 자동 판별하지 못하면 스크립트가 실패하므로, 프로젝트 전용 커맨드를 `run_tests.sh`에 추가한다.

## Step 5. PR 본문 작성

`pr_body.md` 파일을 직접 작성한다. 반드시 포함할 항목:

- 수정된 취약점 목록 (rule_id, CWE, `파일:라인`, 한 줄 수정 요약)
- 회귀 테스트 통과 여부
- 오탐으로 판단해 수정하지 않은 항목과 그 근거
- `manual_review_needed` 항목 (별도 이슈 등록 권장 문구 포함)
- 자동수정 대상이 아닌 medium/low 항목 요약

**하드코딩된 credential 관련 수정은 실제 값을 PR 본문이나 커밋 메시지에 절대 남기지 않는다.**

## Step 6. PR 생성

```bash
bash scripts/create_pr.sh <branch_name> <pr_title> pr_body.md <수정파일1> [수정파일2 ...]
```

수정한 파일을 **모두 명시적으로 나열**해야 한다. 이 스크립트는 `git add -A`를 쓰지 않으므로 작업 트리의 무관한 변경이 섞이지 않는다.

PR 대상 브랜치를 지정하려면:

```bash
BASE_BRANCH=develop bash scripts/create_pr.sh ...
```

GitHub `gh` CLI 기준이다. GitLab이면 스크립트 하단의 `gh pr create`를 `glab mr create`로 교체한다.

push나 PR 생성이 실패해도 **커밋된 브랜치는 삭제되지 않으므로** 수정 내용은 보존된다. 이 경우 push만 수동으로 진행하면 된다.

---

## 안전 원칙

- **한 번에 3~5개씩** 수정하고 사용자 확인을 받는다. 대량 파일을 한꺼번에 고치라는 요청이어도 시범 실행부터 제안한다.
- 자동수정은 되돌리기 쉬운 변경(파라미터 바인딩 교체, 이스케이핑 추가, 입력 검증 추가)에 한정한다. 아키텍처 변경이 필요한 취약점은 자동수정하지 않는다.
- `references/vuln_patterns.md`에 없는 유형은 수정 전에 사용자에게 방향을 확인한다.
- 정적분석 결과를 무비판적으로 신뢰하지 않는다. 오탐 가능성을 항상 검토하고, 판단이 서지 않으면 사용자에게 묻는다.
