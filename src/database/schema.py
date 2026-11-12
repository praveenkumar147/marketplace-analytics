"""Execute the SQL scripts in sql/schema/ in file-name order."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.config import SQL_DIR

logger = logging.getLogger(__name__)
SCHEMA_DIR = SQL_DIR / "schema"
DEFAULT_DB_NAME = "marketplace_analytics"


def split_sql_statements(sql: str) -> list[str]:
    """Split a script into statements: drop '--' comment lines, split on ';'.

    Our scripts keep ';' out of string literals, so a simple split is enough.
    """
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    # Remove trailing inline comments (`... NOT NULL,  -- note`) outside of quotes.
    without_comments = re.sub(r"\s--\s[^'\n]*$", "", without_comments, flags=re.MULTILINE)
    return [stmt.strip() for stmt in without_comments.split(";") if stmt.strip()]


def run_sql_file(engine: Engine, path: Path, replacements: dict[str, str] | None = None) -> int:
    """Run every statement in one file inside a single connection. Returns count."""
    sql = path.read_text(encoding="utf-8")
    for old, new in (replacements or {}).items():
        sql = sql.replace(old, new)
    statements = split_sql_statements(sql)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    logger.info("Ran %-28s %d statements", path.name, len(statements))
    return len(statements)


def create_database(server_engine: Engine, db_name: str) -> None:
    """Run 01_create_database.sql, using the configured database name."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
        raise ValueError(f"Unsafe database name: {db_name!r}")
    run_sql_file(server_engine, SCHEMA_DIR / "01_create_database.sql",
                 {DEFAULT_DB_NAME: db_name})


def create_tables(db_engine: Engine) -> None:
    """Run every remaining schema script (02_, 03_, ...) against the project DB."""
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        if path.name.startswith("01_"):
            continue
        run_sql_file(db_engine, path)
