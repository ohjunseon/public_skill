# DB Validator

Oracle ↔ PostgreSQL/MySQL/MSSQL/SQLite 등 마이그레이션 전후 데이터를 검증하는 독립 실행 Python 에이전트입니다.

## 설치

```bash
pip install -r requirements.txt --break-system-packages
# 필요한 DB 드라이버만 requirements.txt 주석 해제 후 설치
```

## 설정

1. `config.example.yaml` → `config.yaml` 로 복사, DB 타입/테이블 매핑/PK/비교 쿼리 작성
2. `.env.example` → `.env` 로 복사, 실제 접속 정보(호스트/계정/비번) 입력
   - config.yaml에는 `${ORACLE_HOST}` 형태로 참조만 남기고, 값은 `.env`에만 저장

## 실행

```bash
# 전체 검증 (스키마 + 행수 + 체크섬 + 쿼리)
python db_validator.py --config config.yaml

# 특정 검증만
python db_validator.py --config config.yaml --check schema row_count

# 특정 테이블만
python db_validator.py --config config.yaml --tables MEMBERS,RESERVATIONS

# 리포트 파일로 저장
python db_validator.py --config config.yaml --report report.md --json report.json
```

종료 코드: 전체 통과 `0` / 검증 실패 `1` / 설정·연결 오류 `2` (CI 파이프라인에서 그대로 활용 가능).

## 검증 항목

| 항목 | 설명 |
|---|---|
| schema | 테이블/컬럼/타입 비교 (Oracle VARCHAR2↔VARCHAR, NUMBER↔NUMERIC 등 자동 정규화) |
| row_count | 소스/타겟 행 개수 일치 여부 |
| checksum | **PK로 매칭**해 행 단위 MD5 체크섬 비교 (`key_columns` 설정 필요) |
| query | 마이그레이션 전/후 동일 의미 쿼리 결과 비교 (`query_pairs` 설정) |

### 타입 비교 모드 (`--strict-types`)

기본값은 길이/정밀도를 무시하고 기본 타입만 비교합니다.
`--strict-types`를 주면 길이·정밀도까지 비교하여 `VARCHAR2(4000) -> VARCHAR(10)` 처럼
**데이터 잘림이 발생할 수 있는 타입 축소**를 잡아냅니다. 실제 마이그레이션 검수에서는
strict 모드를 함께 돌려보길 권장합니다.

```bash
python db_validator.py --config config.yaml --strict-types
```

config.yaml에 `strict_types: true` 로 기본값을 지정할 수도 있습니다.

### checksum 비교 방식

행을 **위치가 아닌 PK로 매칭**하기 때문에, 중간에 행이 하나 빠져도 뒤 행이 밀려서
전부 불일치로 잡히는 문제가 없습니다. 결과는 세 가지로 구분해 보고합니다.

- 타겟에 없는 행 (누락)
- 소스에 없는 행 (초과)
- 값 불일치 행 (PK는 같지만 내용이 다름)

컬럼 순서가 소스/타겟 간에 달라도 컬럼명 기준으로 정렬해 비교하므로 오탐이 없습니다.

## 확장 아이디어

- mybatis-migration-toolkit의 Oracle 힌트/바인드 보존 규칙과 연계해 마이그레이션된 SQL의 결과까지 자동 검증
- `--tables` 인자를 크론/n8n 파이프라인에서 호출해 배치 검증 자동화

---

## GitHub Copilot Agent Mode(VS Code)에 연결하기

`db_validator.py`의 검증 로직을 `mcp_server.py`로 감싸서 MCP(Model Context Protocol) 서버로
노출합니다. VS Code의 Copilot Agent Mode는 MCP 서버가 제공하는 도구를 자동으로 인식해서
"DB 마이그레이션 검증해줘" 같은 자연어 요청에 알아서 호출합니다.

### 1단계 — MCP SDK 설치

```bash
pip install "mcp>=2.0" --break-system-packages
```

### 2단계 — 노출되는 도구 확인

`mcp_server.py`는 다음 6개 도구를 노출합니다.

| 도구 | 역할 |
|---|---|
| `list_configured_tables` | config.yaml에 설정된 DB/테이블/PK 현황 조회 |
| `validate_schema` | 스키마(컬럼/타입) 비교 |
| `validate_row_count` | 행 개수 비교 |
| `validate_checksum` | PK 기준 행 단위 체크섬 비교 |
| `validate_query_pairs` | 마이그레이션 전후 동일 쿼리 결과 비교 |
| `run_full_validation` | 위 4가지를 한 번에 실행하고 종합 리포트 반환 |

### 3단계 — VS Code에 서버 등록 (`.vscode/mcp.json`)

프로젝트 루트에 이미 포함된 `.vscode/mcp.json`을 사용하거나, 없다면 아래처럼 작성합니다.

```json
{
  "servers": {
    "db-validator": {
      "command": "python3",
      "args": ["${workspaceFolder}/db-validator/mcp_server.py"],
      "env": {
        "DB_VALIDATOR_CONFIG": "${workspaceFolder}/db-validator/config.yaml"
      }
    }
  }
}
```

- `command`/`args`: MCP 서버를 stdio로 실행하는 명령. VS Code가 자동으로 프로세스를 띄우고 종료합니다.
- `env.DB_VALIDATOR_CONFIG`: 도구 호출 시 매번 config 경로를 넘기지 않도록 기본 설정 파일을 지정.
- 접속 정보(.env)는 config.yaml과 같은 폴더에 두면 `db_validator.py`가 자동으로 로드합니다.

### 4단계 — 실제 config.yaml 준비

`config.example.yaml`을 `config.yaml`로 복사하고 실제 소스/타겟 DB 정보를 입력합니다.
민감정보(비번/호스트)는 `.env`에 넣고 `${VAR}` 형태로 참조하세요 (`.env.example` 참고).

### 5단계 — VS Code에서 서버 시작 및 승인

1. VS Code 1.102 이상 확인: `code --version`
2. `.vscode/mcp.json` 저장하면 VS Code가 "Start" 버튼을 표시함 → 클릭해서 서버 기동
   (또는 커맨드 팔레트에서 `MCP: List Servers`로 상태 확인)
3. Copilot Chat을 열고 모드 드롭다운에서 **Agent** 선택 (Ask/Edit 모드에서는 MCP 도구가 보이지 않음)
4. 도구 목록에 `db-validator`의 6개 도구가 자동으로 나타남

### 6단계 — 사용 예시 (Agent Mode 프롬프트)

```
db-validator로 MEMBERS, RESERVATIONS 테이블 마이그레이션 검증해줘.
스키마랑 행 개수만 먼저 확인하고, 문제 없으면 체크섬까지 돌려줘.
```

```
run_full_validation 실행해서 실패한 항목만 요약해줘.
```

Copilot이 민감한 동작(DB 접속, 쿼리 실행)을 수행하기 전에 승인 다이얼로그를 띄우므로,
처음 몇 번은 확인 후 Allow를 눌러야 합니다.

### 보안 주의사항

- `.env`는 절대 git에 커밋하지 마세요 (`.gitignore`에 추가 권장)
- MCP 서버는 로컬 stdio 프로세스로 실행되며 외부로 노출되지 않지만,
  타겟 DB 자격증명이 서버 프로세스 환경변수에 그대로 들어가므로
  운영 DB에는 읽기 전용(read-only) 계정 사용을 권장
- `run_full_validation`은 SELECT만 수행하며 쓰기 작업은 없음 (검증 전용 설계)

### 문제 해결

- 도구가 안 보임 → Agent 모드인지 확인, VS Code 재시작 후 `MCP: List Servers`로 상태 점검
- 연결은 되는데 오류 → `DB_VALIDATOR_CONFIG` 경로가 실제 config.yaml 위치와 맞는지 확인
- 드라이버 오류(oracledb, pyodbc 등) → `requirements.txt`에서 해당 DB 드라이버 주석 해제 후 설치
