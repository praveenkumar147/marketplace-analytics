"""Tests for Module C. Unit tests need no database; integration tests run only
when MySQL is reachable with the credentials in .env and the data is loaded."""
import re

import pandas as pd
import pytest

from src.config import SQL_DIR
from src.database import connection
from src.database.load import LOAD_ORDER, prepare_frame, to_nullable_bool
from src.database.schema import split_sql_statements

DDL = (SQL_DIR / "schema" / "02_create_tables.sql").read_text(encoding="utf-8")


def ddl_columns(table: str) -> set[str]:
    """Column names declared in CREATE TABLE <table> ( ... ) in the DDL file."""
    block = re.search(rf"CREATE TABLE {table} \((.*?)\) ENGINE", DDL, re.S).group(1)
    cols = set()
    for line in block.splitlines():
        token = line.strip().split(" ")[0]
        if token and token.upper() not in {"PRIMARY", "CONSTRAINT", "INDEX", "FOREIGN", "--"} \
                and not token.startswith(("(", "'")):
            cols.add(token)
    return cols


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #
def test_split_sql_statements_ignores_comments():
    sql = "-- header; with semicolon\nCREATE TABLE a (x INT);\n\n-- note\nDROP TABLE b;\n"
    assert split_sql_statements(sql) == ["CREATE TABLE a (x INT)", "DROP TABLE b"]


def test_build_url_escapes_password(monkeypatch):
    monkeypatch.setenv("MYSQL_USER", "analyst")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss:w/rd")
    monkeypatch.setenv("MYSQL_DATABASE", "testdb")
    url = connection.build_url()
    assert url.password == "p@ss:w/rd"
    assert "p%40ss" in url.render_as_string(hide_password=False)   # '@' is escaped
    assert url.database == "testdb"


def test_missing_password_fails_loudly(monkeypatch):
    monkeypatch.setenv("MYSQL_USER", "analyst")
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="MYSQL_PASSWORD"):
        connection.build_url()


def test_to_nullable_bool():
    s = pd.Series(["True", "False", None, True])
    assert to_nullable_bool(s).tolist() == [True, False, None, True]


def test_load_order_respects_foreign_keys():
    """Every table referenced by a FK must be loaded before the table that references it."""
    position = {spec.table: i for i, spec in enumerate(LOAD_ORDER)}
    for child, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\) ENGINE", DDL, re.S):
        for parent in re.findall(r"REFERENCES (\w+)", body):
            assert position[parent] < position[child], f"{parent} must load before {child}"


@pytest.mark.parametrize("spec", LOAD_ORDER, ids=lambda s: s.table)
def test_prepared_columns_match_ddl(spec):
    """CSV columns after rename/drop must be exactly the DDL columns."""
    try:
        frame = prepare_frame(spec)
    except FileNotFoundError:
        pytest.skip("processed data not built")
    assert set(frame.columns) == ddl_columns(spec.table)


# --------------------------------------------------------------------------- #
# Integration tests (live MySQL)
# --------------------------------------------------------------------------- #
def _engine_or_skip():
    try:
        engine = connection.get_engine()
        connection.check_connection(engine)
        return engine
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not reachable / not configured: {type(exc).__name__}")


@pytest.mark.integration
def test_live_database_counts_and_integrity():
    from sqlalchemy import text
    engine = _engine_or_skip()
    with engine.connect() as conn:
        orders = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
        orphan_items = conn.execute(text(
            "SELECT COUNT(*) FROM order_items i LEFT JOIN orders o USING (order_id) "
            "WHERE o.order_id IS NULL")).scalar_one()
    assert orders == 99441
    assert orphan_items == 0
