"""Run the business-analysis SQL files and save every result.

Run from the project root (MySQL must be loaded - see src/database/load.py):
    python -m src.analysis.sql_runner              # all queries
    python -m src.analysis.sql_runner q04 q18      # only queries whose name starts with these

Each query in sql/analysis/*.sql begins with marker comments:
    -- @query   q04_monthly_revenue
    -- @title   Q4. Monthly revenue trend
    -- @purpose What business question it answers (may continue on following
    --          comment lines)
Outputs:
    reports/sql_results/<query>.csv
    reports/sql_analysis_results.md
"""
from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.config import REPORTS_DIR, SQL_DIR
from src.database.connection import get_engine
from src.ingestion.quality_report import df_to_markdown

logger = logging.getLogger(__name__)
ANALYSIS_DIR = SQL_DIR / "analysis"
RESULTS_DIR = REPORTS_DIR / "sql_results"

_MARKER = re.compile(r"^--\s*@(query|title|purpose)\s+(.*)$")


@dataclass
class Query:
    name: str
    title: str
    purpose: str
    sql: str
    source: str


def parse_queries(sql_text: str, source: str = "") -> list[Query]:
    """Split a .sql file into Query objects using the @query/@title/@purpose markers."""
    queries: list[Query] = []
    current: dict | None = None
    in_purpose = False

    def finish():
        if current is not None:
            body = "\n".join(current["sql"]).strip().rstrip(";").strip()
            if not body:
                raise ValueError(f"Query {current['name']} in {source} has no SQL")
            queries.append(Query(current["name"], current.get("title", current["name"]),
                                 " ".join(current.get("purpose", [])), body, source))

    for line in sql_text.splitlines():
        stripped = line.strip()
        marker = _MARKER.match(stripped)
        if marker:
            key, value = marker.groups()
            if key == "query":
                finish()
                current = {"name": value.strip(), "sql": [], "purpose": []}
                in_purpose = False
            elif current is not None:
                if key == "title":
                    current["title"] = value.strip()
                    in_purpose = False
                else:
                    current["purpose"] = [value.strip()]
                    in_purpose = True
            continue
        if current is None:
            continue                      # file header before the first query
        if in_purpose and stripped.startswith("--"):
            current["purpose"].append(stripped.lstrip("-").strip())
            continue
        in_purpose = False
        current["sql"].append(line)
    finish()
    return queries


def load_all_queries(directory: Path = ANALYSIS_DIR) -> list[Query]:
    queries = []
    for path in sorted(directory.glob("*.sql")):
        queries.extend(parse_queries(path.read_text(encoding="utf-8"), path.name))
    names = [q.name for q in queries]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(f"Duplicate query names: {sorted(duplicates)}")
    return queries


def decimals_to_float(df: pd.DataFrame) -> pd.DataFrame:
    """MySQL DECIMAL arrives as Python Decimal objects; convert for CSV/plots."""
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].map(lambda v: float(v) if isinstance(v, Decimal) else v)
        # Counts computed with SUM(...) come back as DECIMAL/float; show them as integers.
        if pd.api.types.is_float_dtype(df[col]):
            values = df[col].dropna()
            if len(values) and (values == values.round()).all() and not _looks_like_measure(col):
                df[col] = df[col].astype("Int64")
    return df


def _looks_like_measure(column: str) -> bool:
    """Money / rate / average columns stay float even if a value happens to be whole.

    Count-style names ('revenue_orders', 'late_orders') are counts, not measures.
    """
    count_tokens = ("orders", "customers", "sellers", "reviews", "payments", "lines", "units", "items")
    if column.endswith(count_tokens) and not column.startswith(("avg", "pct")):
        return False
    return any(token in column for token in
               ("revenue", "value", "pct", "avg", "aov", "days", "price", "growth", "rate", "score"))


def run_query(engine: Engine, query: Query) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(text(query.sql), conn)
    return decimals_to_float(df)


def write_markdown(results: list[tuple[Query, pd.DataFrame, float]], path: Path) -> None:
    md = [
        "# SQL Business Analysis - Results",
        "",
        f"_Auto-generated by `src/analysis/sql_runner.py` on {datetime.now():%Y-%m-%d %H:%M} "
        "against the `marketplace_analytics` MySQL database. Every number below is the "
        "actual output of the query in the named file._",
        "",
        "Definitions: revenue = price + freight on orders not canceled/unavailable; "
        "trend queries use complete months 2017-01 to 2018-08; customers = "
        "`customer_unique_id`; delivery metrics use valid deliveries only.",
        "",
        "## Contents",
        "",
        *[f"- [{q.title}](#{q.name})" for q, _, _ in results],
        "",
    ]
    for q, df, secs in results:
        md += [
            f'<a id="{q.name}"></a>',
            f"## {q.title}",
            "",
            f"**Question:** {q.purpose}",
            "",
            f"Source: `sql/analysis/{q.source}` (`{q.name}`) | {len(df)} rows | {secs:.2f}s",
            "",
            df_to_markdown(df),
        ]
    path.write_text("\n".join(md), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    prefixes = argv if argv is not None else sys.argv[1:]
    queries = [q for q in load_all_queries()
               if not prefixes or any(q.name.startswith(p) for p in prefixes)]
    if not queries:
        logger.error("No queries matched %s", prefixes)
        return 1

    try:
        engine = get_engine()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results, failed = [], []
    for q in queries:
        start = time.perf_counter()
        try:
            df = run_query(engine, q)
        except Exception as exc:  # noqa: BLE001 - report every failing query, keep going
            logger.error("%s FAILED: %s", q.name, getattr(exc, "orig", exc))
            failed.append(q.name)
            continue
        secs = time.perf_counter() - start
        df.to_csv(RESULTS_DIR / f"{q.name}.csv", index=False)
        results.append((q, df, secs))
        logger.info("%-28s %4d rows  %5.2fs", q.name, len(df), secs)

    if not prefixes:   # full run -> refresh the combined report
        write_markdown(results, REPORTS_DIR / "sql_analysis_results.md")
        logger.info("Wrote %s", REPORTS_DIR / "sql_analysis_results.md")
    if failed:
        logger.error("%d queries failed: %s", len(failed), failed)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
