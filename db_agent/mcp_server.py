#!/usr/bin/env python3
"""
mcp_server.py
=============
db_validator.py 의 검증 로직을 MCP(Model Context Protocol) 서버로 노출.
GitHub Copilot Agent Mode(VS Code) 또는 다른 MCP 클라이언트가 도구로 호출할 수 있음.

실행 (stdio transport, VS Code가 자동으로 프로세스를 띄움):
    python mcp_server.py

VS Code 설정: .vscode/mcp.json 에 이 스크립트를 등록 (README 참고)
"""

import json
import os
import sys
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer

# db_validator.py 와 같은 폴더에 있다고 가정
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_validator import (  # noqa: E402
    ValidationReport,
    check_data_checksum,
    check_query_pairs,
    check_row_count,
    check_schema,
    get_engine,
    load_config,
)

mcp = MCPServer("db-validator")

# 설정 파일 경로는 환경변수로 지정 (도구 호출 시마다 --config 안 넘겨도 되게)
DEFAULT_CONFIG_PATH = os.environ.get("DB_VALIDATOR_CONFIG", "config.yaml")


@contextmanager
def _load(config_path: str | None):
    """설정을 읽고 엔진을 생성. 도구 호출이 끝나면 커넥션 풀을 반드시 정리한다."""
    path = config_path or DEFAULT_CONFIG_PATH
    config = load_config(path)
    source_engine = get_engine(config["databases"]["source"])
    target_engine = get_engine(config["databases"]["target"])
    try:
        yield config, source_engine, target_engine
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _results_to_dict(results) -> list[dict]:
    return [
        {
            "check_type": r.check_type,
            "table": r.table,
            "passed": r.passed,
            "detail": r.detail,
        }
        for r in results
    ]


def _filter_table_map(config: dict, tables: str | None) -> dict:
    table_map = config.get("table_map", {})
    if tables:
        wanted = {t.strip() for t in tables.split(",")}
        table_map = {k: v for k, v in table_map.items() if k in wanted}
    return table_map


@mcp.tool()
def validate_schema(tables: str = "", strict_types: bool = False, config_path: str = "") -> str:
    """
    소스/타겟 DB의 테이블 스키마(컬럼명/타입)를 비교합니다.

    Args:
        tables: 검증할 테이블명을 콤마로 구분해 지정 (예: "MEMBERS,RESERVATIONS").
                비워두면 config.yaml의 table_map 전체를 검증합니다.
        strict_types: True면 길이/정밀도까지 비교합니다.
                      VARCHAR2(4000) -> VARCHAR(10) 처럼 데이터 잘림이 생길 수 있는
                      타입 축소를 찾으려면 True로 호출하세요.
        config_path: config.yaml 경로. 비워두면 DB_VALIDATOR_CONFIG 환경변수 또는
                     기본값(config.yaml)을 사용합니다.
    """
    with _load(config_path or None) as (config, source_engine, target_engine):
        table_map = _filter_table_map(config, tables or None)
        results = check_schema(source_engine, target_engine, table_map, strict_types)
        return json.dumps(_results_to_dict(results), ensure_ascii=False, indent=2)


@mcp.tool()
def validate_row_count(tables: str = "", config_path: str = "") -> str:
    """
    소스/타겟 DB의 테이블별 행(row) 개수를 비교합니다.

    Args:
        tables: 검증할 테이블명을 콤마로 구분해 지정. 비워두면 전체 테이블 검증.
        config_path: config.yaml 경로. 비워두면 기본 설정 사용.
    """
    with _load(config_path or None) as (config, source_engine, target_engine):
        table_map = _filter_table_map(config, tables or None)
        results = check_row_count(source_engine, target_engine, table_map)
        return json.dumps(_results_to_dict(results), ensure_ascii=False, indent=2)


@mcp.tool()
def validate_checksum(tables: str = "", sample_size: int = 0, config_path: str = "") -> str:
    """
    PK 기준 정렬 후 상위 N행에 대해 소스/타겟 데이터의 행 단위 체크섬을 비교합니다.
    (config.yaml의 key_columns에 PK가 지정된 테이블만 검증 가능)

    Args:
        tables: 검증할 테이블명을 콤마로 구분해 지정. 비워두면 전체 테이블 검증.
        sample_size: 비교할 최대 행 수. 0이면 config.yaml의 sample_size 값 사용.
        config_path: config.yaml 경로. 비워두면 기본 설정 사용.
    """
    with _load(config_path or None) as (config, source_engine, target_engine):
        table_map = _filter_table_map(config, tables or None)
        key_columns = config.get("key_columns", {})
        size = sample_size or config.get("sample_size", 1000)
        results = check_data_checksum(source_engine, target_engine, table_map, key_columns, size)
        return json.dumps(_results_to_dict(results), ensure_ascii=False, indent=2)


@mcp.tool()
def validate_query_pairs(config_path: str = "") -> str:
    """
    config.yaml의 query_pairs에 정의된 소스/타겟 SQL 쿼리 결과를 비교합니다.
    (예: 마이그레이션 전후 동일 의미의 쿼리가 같은 결과를 내는지 확인)

    Args:
        config_path: config.yaml 경로. 비워두면 기본 설정 사용.
    """
    with _load(config_path or None) as (config, source_engine, target_engine):
        query_pairs = config.get("query_pairs", [])
        results = check_query_pairs(source_engine, target_engine, query_pairs)
        return json.dumps(_results_to_dict(results), ensure_ascii=False, indent=2)


@mcp.tool()
def run_full_validation(tables: str = "", strict_types: bool = False, config_path: str = "") -> str:
    """
    스키마, 행 개수, 체크섬, 쿼리 비교를 모두 실행하고 종합 리포트를 반환합니다.
    Copilot Agent가 "DB 마이그레이션 검증해줘" 같은 요청을 받았을 때 기본으로 사용할 도구입니다.

    Args:
        tables: 검증할 테이블명을 콤마로 구분해 지정. 비워두면 전체 테이블 검증.
        strict_types: True면 타입 길이/정밀도까지 비교 (데이터 잘림 위험 탐지).
        config_path: config.yaml 경로. 비워두면 기본 설정 사용.
    """
    with _load(config_path or None) as (config, source_engine, target_engine):
        table_map = _filter_table_map(config, tables or None)
        key_columns = config.get("key_columns", {})
        query_pairs = config.get("query_pairs", [])
        sample_size = config.get("sample_size", 1000)

        report = ValidationReport()
        if table_map:
            report.results += check_schema(source_engine, target_engine, table_map, strict_types)
            report.results += check_row_count(source_engine, target_engine, table_map)
            report.results += check_data_checksum(
                source_engine, target_engine, table_map, key_columns, sample_size
            )
        if query_pairs:
            report.results += check_query_pairs(source_engine, target_engine, query_pairs)

        if not report.results:
            return json.dumps(
                {"error": "검증 대상이 없습니다. config.yaml의 table_map/query_pairs를 확인하세요."},
                ensure_ascii=False,
            )

        output = {
            "summary": report.summary,
            "results": _results_to_dict(report.results),
        }
        return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
def list_configured_tables(config_path: str = "") -> str:
    """
    현재 config.yaml에 등록된 테이블 매핑, DB 타입, PK 설정 현황을 보여줍니다.
    검증을 실행하기 전에 어떤 테이블/DB가 연결되어 있는지 확인할 때 사용합니다.

    Args:
        config_path: config.yaml 경로. 비워두면 기본 설정 사용.
    """
    config = load_config(config_path or DEFAULT_CONFIG_PATH)
    info = {
        "source_type": config["databases"]["source"]["type"],
        "target_type": config["databases"]["target"]["type"],
        "table_map": config.get("table_map", {}),
        "key_columns": config.get("key_columns", {}),
        "query_pairs": [q.get("name") for q in config.get("query_pairs", [])],
    }
    return json.dumps(info, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
