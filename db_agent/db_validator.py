#!/usr/bin/env python3
"""
db_validator.py
================
멀티 DB(Oracle/PostgreSQL/MySQL/MSSQL/SQLite 등) 마이그레이션 검증 에이전트.

기능:
  1. 스키마 구조 비교 (테이블/컬럼/타입)
  2. 행(row) 개수 비교
  3. 데이터 내용 비교 (체크섬 기반 + 샘플 diff)
  4. 동일 SQL 쿼리 결과 비교 (마이그레이션 전/후)

사용법:
  python db_validator.py --config config.yaml --check schema row_count checksum query
  python db_validator.py --config config.yaml --tables users,orders
  python db_validator.py --config config.yaml --report report.md

설정:
  - config.yaml: 비교할 DB 목록, 테이블 매핑, 쿼리 페어 정의
  - .env: 실제 접속 정보 (아이디/비번/호스트 등, config.yaml에서 ${VAR} 형태로 참조)
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

# --------------------------------------------------------------------------
# 설정 로딩
# --------------------------------------------------------------------------

def expand_env(value: Any) -> Any:
    """config.yaml 안의 ${VAR_NAME} 문자열을 환경변수 값으로 치환."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        resolved = os.environ.get(var_name)
        if resolved is None:
            raise ValueError(f"환경변수 {var_name} 가 설정되어 있지 않습니다 (.env 확인).")
        return resolved
    return value


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            f"config.example.yaml 을 config.yaml 로 복사한 뒤 접속 정보를 입력하세요."
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "databases" not in raw:
        raise ValueError(f"{path} 에 'databases' 섹션이 없습니다. config.example.yaml 을 참고하세요.")

    for role in ("source", "target"):
        if role not in raw["databases"]:
            raise ValueError(f"{path} 의 databases 에 '{role}' 설정이 없습니다.")

    for db in raw.get("databases", {}).values():
        for key in ("host", "port", "user", "password", "database", "service_name", "dsn"):
            if key in db:
                db[key] = expand_env(db[key])

    return raw


def build_connection_url(db_cfg: dict) -> str:
    """DB 설정에서 SQLAlchemy 접속 URL 생성. 'url' 키가 있으면 그대로 사용."""
    if "url" in db_cfg:
        return expand_env(db_cfg["url"])

    dialect = db_cfg["type"]
    # 비밀번호/계정에 @ # / : 등 특수문자가 있어도 URL이 깨지지 않도록 인코딩
    user = quote_plus(str(db_cfg.get("user", "")))
    password = quote_plus(str(db_cfg.get("password", "")))
    host = db_cfg.get("host", "")
    port = db_cfg.get("port", "")
    database = db_cfg.get("database", "")

    drivers = {
        "oracle": "oracle+oracledb",
        "postgresql": "postgresql+psycopg2",
        "mysql": "mysql+pymysql",
        "mssql": "mssql+pyodbc",
        "sqlite": "sqlite",
    }
    driver = drivers.get(dialect, dialect)

    if dialect == "sqlite":
        return f"sqlite:///{database}"

    if dialect == "oracle" and db_cfg.get("service_name"):
        return f"{driver}://{user}:{password}@{host}:{port}/?service_name={db_cfg['service_name']}"

    extra = ""
    if dialect == "mssql":
        extra = "?driver=ODBC+Driver+17+for+SQL+Server"

    return f"{driver}://{user}:{password}@{host}:{port}/{database}{extra}"


def get_engine(db_cfg: dict) -> Engine:
    url = build_connection_url(db_cfg)
    return create_engine(url, pool_pre_ping=True)


# --------------------------------------------------------------------------
# 결과 데이터 구조
# --------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_type: str
    table: str
    passed: bool
    detail: str
    source_val: Any = None
    target_val: Any = None


@dataclass
class ValidationReport:
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    results: list = field(default_factory=list)

    def add(self, result: CheckResult):
        self.results.append(result)

    @property
    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        return {"total": total, "passed": passed, "failed": total - passed}


# --------------------------------------------------------------------------
# 1. 스키마 구조 비교
# --------------------------------------------------------------------------

def normalize_type(type_str: str, strict: bool = False) -> str:
    """
    DB마다 표기가 다른 타입명을 정규화 (VARCHAR2 vs VARCHAR 등).

    strict=False: 길이/정밀도를 무시하고 기본 타입만 비교 (기본값)
    strict=True : 길이/정밀도까지 비교. VARCHAR2(4000) -> VARCHAR(10) 처럼
                  데이터 잘림이 발생할 수 있는 축소를 잡아내려면 반드시 켜세요.
    """
    t = type_str.upper()
    t = t.replace("VARCHAR2", "VARCHAR")
    t = t.replace("NVARCHAR2", "NVARCHAR")
    t = t.replace("NUMBER", "NUMERIC")
    t = t.replace("CLOB", "TEXT")
    t = t.replace("DATETIME2", "DATETIME")
    t = t.strip()
    if not strict:
        t = t.split("(")[0].strip()  # 길이/정밀도 괄호 제거
    return t


def check_schema(
    source_engine: Engine, target_engine: Engine, table_map: dict, strict_types: bool = False
) -> list[CheckResult]:
    results = []
    src_inspector = inspect(source_engine)
    tgt_inspector = inspect(target_engine)

    for src_table, tgt_table in table_map.items():
        try:
            src_cols = {c["name"].lower(): c for c in src_inspector.get_columns(src_table)}
            tgt_cols = {c["name"].lower(): c for c in tgt_inspector.get_columns(tgt_table)}
        except SQLAlchemyError as e:
            results.append(CheckResult("schema", src_table, False, f"조회 실패: {e}"))
            continue

        if not src_cols:
            results.append(CheckResult("schema", src_table, False, f"소스 테이블 {src_table} 없음 또는 컬럼 없음"))
            continue
        if not tgt_cols:
            results.append(CheckResult("schema", src_table, False, f"타겟 테이블 {tgt_table} 없음 또는 컬럼 없음"))
            continue

        missing_in_target = set(src_cols) - set(tgt_cols)
        missing_in_source = set(tgt_cols) - set(src_cols)

        if missing_in_target:
            results.append(CheckResult(
                "schema", src_table, False,
                f"타겟에 없는 컬럼: {sorted(missing_in_target)}"
            ))
        if missing_in_source:
            results.append(CheckResult(
                "schema", src_table, False,
                f"소스에 없는 컬럼(타겟 전용): {sorted(missing_in_source)}"
            ))

        type_mismatches = []
        for col in sorted(set(src_cols) & set(tgt_cols)):
            src_type = normalize_type(str(src_cols[col]["type"]), strict_types)
            tgt_type = normalize_type(str(tgt_cols[col]["type"]), strict_types)
            if src_type != tgt_type:
                type_mismatches.append(f"{col}: {src_type} -> {tgt_type}")

        if type_mismatches:
            results.append(CheckResult(
                "schema", src_table, False,
                f"타입 불일치: {type_mismatches}"
            ))

        if not missing_in_target and not missing_in_source and not type_mismatches:
            mode = "엄격" if strict_types else "기본"
            results.append(CheckResult(
                "schema", src_table, True,
                f"{len(src_cols)}개 컬럼 일치 ({mode} 타입 비교)"
            ))

    return results


# --------------------------------------------------------------------------
# 2. 행 개수 비교
# --------------------------------------------------------------------------

def check_row_count(source_engine: Engine, target_engine: Engine, table_map: dict) -> list[CheckResult]:
    results = []
    for src_table, tgt_table in table_map.items():
        try:
            with source_engine.connect() as conn:
                src_count = conn.execute(text(f"SELECT COUNT(*) FROM {src_table}")).scalar()
            with target_engine.connect() as conn:
                tgt_count = conn.execute(text(f"SELECT COUNT(*) FROM {tgt_table}")).scalar()
        except SQLAlchemyError as e:
            results.append(CheckResult("row_count", src_table, False, f"조회 실패: {e}"))
            continue

        passed = src_count == tgt_count
        results.append(CheckResult(
            "row_count", src_table, passed,
            f"source={src_count}, target={tgt_count}" + ("" if passed else " (불일치)"),
            src_count, tgt_count
        ))
    return results


# --------------------------------------------------------------------------
# 3. 데이터 내용 비교 (체크섬 + 샘플)
# --------------------------------------------------------------------------

def row_checksum(row: tuple) -> str:
    joined = "|".join("" if v is None else str(v) for v in row)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


def _build_row_map(rows, idx_map: dict, key_cols: list, common_cols: list) -> dict:
    """행 목록을 {PK 튜플: 체크섬} 형태로 변환. 컬럼 순서 차이를 흡수한다."""
    out = {}
    for r in rows:
        pk = tuple(r[idx_map[k]] for k in key_cols)
        out[pk] = row_checksum(tuple(r[idx_map[c]] for c in common_cols))
    return out


def check_data_checksum(
    source_engine: Engine, target_engine: Engine, table_map: dict,
    key_columns: dict, sample_size: int = 1000
) -> list[CheckResult]:
    """
    PK 기준으로 정렬해 상위 N행을 가져온 뒤, PK를 키로 매칭해 행 단위 체크섬을 비교합니다.
    위치가 아닌 PK로 매칭하므로 중간에 행이 누락되어도 어떤 키가 문제인지 정확히 식별합니다.
    """
    results = []
    for src_table, tgt_table in table_map.items():
        keys = key_columns.get(src_table)
        if not keys:
            results.append(CheckResult(
                "checksum", src_table, False,
                "key_columns 설정 없음 - 스킵 (config.yaml에 PK 지정 필요)"
            ))
            continue

        order_by = ", ".join(keys)
        try:
            with source_engine.connect() as conn:
                src_result = conn.execute(text(f"SELECT * FROM {src_table} ORDER BY {order_by}"))
                src_cols = list(src_result.keys())
                src_rows = src_result.fetchmany(sample_size)
            with target_engine.connect() as conn:
                tgt_result = conn.execute(text(f"SELECT * FROM {tgt_table} ORDER BY {order_by}"))
                tgt_cols = list(tgt_result.keys())
                tgt_rows = tgt_result.fetchmany(sample_size)
        except SQLAlchemyError as e:
            results.append(CheckResult("checksum", src_table, False, f"조회 실패: {e}"))
            continue

        # 컬럼 순서가 DB마다 다를 수 있으므로 공통 컬럼을 이름순으로 정렬해 비교 기준을 통일
        src_idx = {c.lower(): i for i, c in enumerate(src_cols)}
        tgt_idx = {c.lower(): i for i, c in enumerate(tgt_cols)}
        common_cols = sorted(set(src_idx) & set(tgt_idx))

        if not common_cols:
            results.append(CheckResult(
                "checksum", src_table, False, "공통 컬럼이 없어 비교 불가"
            ))
            continue

        key_lower = [k.lower() for k in keys]
        missing_keys = [k for k in key_lower if k not in src_idx or k not in tgt_idx]
        if missing_keys:
            results.append(CheckResult(
                "checksum", src_table, False,
                f"PK 컬럼을 양쪽에서 찾을 수 없음: {missing_keys}"
            ))
            continue

        src_map = _build_row_map(src_rows, src_idx, key_lower, common_cols)
        tgt_map = _build_row_map(tgt_rows, tgt_idx, key_lower, common_cols)

        only_in_source = sorted(set(src_map) - set(tgt_map), key=str)
        only_in_target = sorted(set(tgt_map) - set(src_map), key=str)
        value_diffs = sorted(
            (pk for pk in set(src_map) & set(tgt_map) if src_map[pk] != tgt_map[pk]),
            key=str,
        )

        passed = not (only_in_source or only_in_target or value_diffs)

        if passed:
            detail = f"샘플 {len(src_map)}행 / 비교 컬럼 {len(common_cols)}개 - 모두 일치"
        else:
            parts = []
            if only_in_source:
                parts.append(f"타겟에 없는 행 {len(only_in_source)}건 (예: {only_in_source[:3]})")
            if only_in_target:
                parts.append(f"소스에 없는 행 {len(only_in_target)}건 (예: {only_in_target[:3]})")
            if value_diffs:
                parts.append(f"값 불일치 {len(value_diffs)}건 (예: {value_diffs[:3]})")
            detail = " / ".join(parts)

        results.append(CheckResult("checksum", src_table, passed, detail))
    return results


# --------------------------------------------------------------------------
# 4. 동일 쿼리 결과 비교
# --------------------------------------------------------------------------

def check_query_pairs(source_engine: Engine, target_engine: Engine, query_pairs: list) -> list[CheckResult]:
    results = []
    for pair in query_pairs:
        name = pair.get("name", "unnamed_query")
        src_sql = pair["source_sql"]
        tgt_sql = pair.get("target_sql", src_sql)  # 지정 없으면 동일 쿼리 재사용

        try:
            with source_engine.connect() as conn:
                src_rows = [tuple(r) for r in conn.execute(text(src_sql)).fetchall()]
            with target_engine.connect() as conn:
                tgt_rows = [tuple(r) for r in conn.execute(text(tgt_sql)).fetchall()]
        except SQLAlchemyError as e:
            results.append(CheckResult("query", name, False, f"실행 실패: {e}"))
            continue

        src_sorted = sorted(src_rows, key=str)
        tgt_sorted = sorted(tgt_rows, key=str)
        passed = src_sorted == tgt_sorted

        detail = f"source {len(src_rows)}행 vs target {len(tgt_rows)}행"
        if not passed:
            # 위치가 아닌 다중집합으로 비교해 실제 차이 건수를 정확히 산출
            src_counter = Counter(src_rows)
            tgt_counter = Counter(tgt_rows)
            only_src = src_counter - tgt_counter
            only_tgt = tgt_counter - src_counter
            parts = []
            if only_src:
                sample = list(only_src.elements())[:3]
                parts.append(f"타겟에 없는 행 {sum(only_src.values())}건 (예: {sample})")
            if only_tgt:
                sample = list(only_tgt.elements())[:3]
                parts.append(f"소스에 없는 행 {sum(only_tgt.values())}건 (예: {sample})")
            detail += " - " + " / ".join(parts) if parts else " - 결과 불일치"

        results.append(CheckResult("query", name, passed, detail))
    return results


# --------------------------------------------------------------------------
# 리포트 출력
# --------------------------------------------------------------------------

def print_console_report(report: ValidationReport):
    icon = {True: "✅", False: "❌"}
    for r in report.results:
        print(f"{icon[r.passed]} [{r.check_type}] {r.table}: {r.detail}")
    s = report.summary
    print(f"\n총 {s['total']}건 검사 - 성공 {s['passed']} / 실패 {s['failed']}")


def write_markdown_report(report: ValidationReport, path: str):
    lines = ["# DB 검증 리포트", f"생성 시각: {report.started_at}", ""]
    s = report.summary
    lines.append(f"**요약**: 총 {s['total']}건 / 성공 {s['passed']} / 실패 {s['failed']}\n")
    lines.append("| 결과 | 종류 | 대상 | 상세 |")
    lines.append("|---|---|---|---|")
    for r in report.results:
        mark = "✅" if r.passed else "❌"
        lines.append(f"| {mark} | {r.check_type} | {r.table} | {r.detail} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_json_report(report: ValidationReport, path: str):
    data = {
        "started_at": report.started_at,
        "summary": report.summary,
        "results": [r.__dict__ for r in report.results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="멀티 DB 마이그레이션 검증 에이전트")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    parser.add_argument(
        "--check", nargs="+",
        choices=["schema", "row_count", "checksum", "query"],
        default=["schema", "row_count", "checksum", "query"],
        help="실행할 검증 종류 (기본: 전체)"
    )
    parser.add_argument("--tables", help="검증할 테이블만 콤마로 지정 (미지정시 config 전체)")
    parser.add_argument(
        "--strict-types", action="store_true",
        help="타입 비교시 길이/정밀도까지 비교 (VARCHAR2(4000)->VARCHAR(10) 같은 축소를 잡아냄)"
    )
    parser.add_argument("--report", help="마크다운 리포트 저장 경로 (예: report.md)")
    parser.add_argument("--json", help="JSON 리포트 저장 경로 (예: report.json)")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        sys.exit(2)

    source_cfg = config["databases"]["source"]
    target_cfg = config["databases"]["target"]

    try:
        print(f"소스 연결 중: {source_cfg['type']} ...")
        source_engine = get_engine(source_cfg)
        print(f"타겟 연결 중: {target_cfg['type']} ...")
        target_engine = get_engine(target_cfg)
    except Exception as e:
        print(f"DB 연결 실패: {e}", file=sys.stderr)
        print("드라이버가 설치되어 있는지(requirements.txt), .env 값이 맞는지 확인하세요.",
              file=sys.stderr)
        sys.exit(2)

    table_map = config.get("table_map", {})
    if args.tables:
        wanted = {t.strip() for t in args.tables.split(",")}
        unknown = wanted - set(table_map)
        if unknown:
            print(f"경고: config.yaml의 table_map에 없는 테이블 무시됨: {sorted(unknown)}", file=sys.stderr)
        table_map = {k: v for k, v in table_map.items() if k in wanted}

    key_columns = config.get("key_columns", {})
    query_pairs = config.get("query_pairs", [])
    sample_size = config.get("sample_size", 1000)
    strict_types = args.strict_types or config.get("strict_types", False)

    report = ValidationReport()

    if not table_map and not query_pairs:
        print("검증할 대상이 없습니다. config.yaml의 table_map 또는 query_pairs를 확인하세요.",
              file=sys.stderr)
        sys.exit(2)

    if "schema" in args.check and table_map:
        report.results += check_schema(source_engine, target_engine, table_map, strict_types)

    if "row_count" in args.check and table_map:
        report.results += check_row_count(source_engine, target_engine, table_map)

    if "checksum" in args.check and table_map:
        report.results += check_data_checksum(
            source_engine, target_engine, table_map, key_columns, sample_size
        )

    if "query" in args.check and query_pairs:
        report.results += check_query_pairs(source_engine, target_engine, query_pairs)

    if not report.results:
        print("실행된 검증이 없습니다. --check 옵션과 config 설정을 확인하세요.", file=sys.stderr)
        sys.exit(2)

    print_console_report(report)

    if args.report:
        write_markdown_report(report, args.report)
        print(f"\n마크다운 리포트 저장됨: {args.report}")

    if args.json:
        write_json_report(report, args.json)
        print(f"JSON 리포트 저장됨: {args.json}")

    source_engine.dispose()
    target_engine.dispose()

    sys.exit(0 if report.summary["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
