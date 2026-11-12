"""Build the MySQL database and load the clean CSVs from data/processed/.

Run from the project root (needs .env with MySQL credentials):
    python -m src.database.load

Steps
  1. CREATE DATABASE IF NOT EXISTS                (sql/schema/01_create_database.sql)
  2. DROP + CREATE all project tables             (sql/schema/02_create_tables.sql)
  3. Insert CSVs parent-first so FKs always resolve
  4. Verify: row counts match the CSVs, zero orphans, constraints enforced
  5. Write reports/database_load_report.md
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from src.config import PROCESSED_DIR, REPORTS_DIR
from src.database.connection import check_connection, database_name, get_engine
from src.database.schema import create_database, create_tables
from src.ingestion.quality_report import df_to_markdown

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadSpec:
    table: str                                   # MySQL table
    csv: str                                     # file in data/processed/
    rename: dict[str, str] = field(default_factory=dict)
    drop: tuple[str, ...] = ()                   # columns not stored (normalization)
    dates: tuple[str, ...] = ()
    bools: tuple[str, ...] = ()


# Parent tables first, children after: the order foreign keys require.
LOAD_ORDER: list[LoadSpec] = [
    LoadSpec("geolocation", "geolocation.csv", rename={
        "geolocation_zip_code_prefix": "zip_code_prefix", "geolocation_lat": "latitude",
        "geolocation_lng": "longitude", "geolocation_city": "city", "geolocation_state": "state"}),
    LoadSpec("customers", "customers.csv", rename={
        "customer_zip_code_prefix": "zip_code_prefix", "customer_city": "city",
        "customer_state": "state"}),
    LoadSpec("sellers", "sellers.csv", rename={
        "seller_zip_code_prefix": "zip_code_prefix", "seller_city": "city",
        "seller_state": "state"}),
    LoadSpec("product_categories", "product_categories.csv", rename={
        "product_category_name": "category_name",
        "product_category_name_english": "category_name_en"}),
    # English name lives only in product_categories (3NF: no duplicated facts).
    LoadSpec("products", "products.csv", rename={"product_category_name": "category_name"},
             drop=("product_category_name_english",)),
    LoadSpec("orders", "orders.csv",
             dates=("order_purchase_timestamp", "order_approved_at",
                    "order_delivered_carrier_date", "order_delivered_customer_date",
                    "order_estimated_delivery_date", "purchase_date"),
             bools=("in_analysis_window", "is_revenue_order", "is_valid_delivery", "is_late")),
    LoadSpec("order_items", "order_items.csv", dates=("shipping_limit_date",),
             bools=("is_price_outlier",)),
    LoadSpec("payments", "payments.csv"),
    LoadSpec("reviews", "reviews.csv", dates=("review_creation_date", "review_answer_timestamp"),
             bools=("has_comment", "is_latest_for_order")),
]

ZIP_COLUMNS = {"geolocation_zip_code_prefix", "customer_zip_code_prefix", "seller_zip_code_prefix"}

# Each query must return 0 when referential integrity holds.
ORPHAN_QUERIES = {
    "orders without customer": """
        SELECT COUNT(*) FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id
        WHERE c.customer_id IS NULL""",
    "items without order": """
        SELECT COUNT(*) FROM order_items i LEFT JOIN orders o ON o.order_id = i.order_id
        WHERE o.order_id IS NULL""",
    "items without product": """
        SELECT COUNT(*) FROM order_items i LEFT JOIN products p ON p.product_id = i.product_id
        WHERE p.product_id IS NULL""",
    "items without seller": """
        SELECT COUNT(*) FROM order_items i LEFT JOIN sellers s ON s.seller_id = i.seller_id
        WHERE s.seller_id IS NULL""",
    "payments without order": """
        SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.order_id = p.order_id
        WHERE o.order_id IS NULL""",
    "reviews without order": """
        SELECT COUNT(*) FROM reviews r LEFT JOIN orders o ON o.order_id = r.order_id
        WHERE o.order_id IS NULL""",
    "products without category": """
        SELECT COUNT(*) FROM products p LEFT JOIN product_categories c
        ON c.category_name = p.category_name WHERE c.category_name IS NULL""",
}

MYSQL_ERROR_MEANING = {
    1062: "duplicate primary key",
    1452: "foreign key violation",
    3819: "CHECK constraint violated",
}

# Soft link (not a FK): reported for transparency, not required to be 0.
SOFT_LINK_QUERY = """
    SELECT COUNT(*) FROM customers c LEFT JOIN geolocation g
    ON g.zip_code_prefix = c.zip_code_prefix WHERE g.zip_code_prefix IS NULL"""


# --------------------------------------------------------------------------- #
# Preparing DataFrames
# --------------------------------------------------------------------------- #
def to_nullable_bool(series: pd.Series) -> pd.Series:
    """'True'/'False'/True/False/blank -> True/False/None (None becomes SQL NULL)."""
    mapping = {"True": True, "False": False, True: True, False: False}
    return series.map(mapping).astype(object).where(series.notna(), None)


def prepare_frame(spec: LoadSpec) -> pd.DataFrame:
    path = PROCESSED_DIR / spec.csv
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run `python -m src.cleaning.pipeline` first")
    df = pd.read_csv(path, dtype={c: "string" for c in ZIP_COLUMNS}, keep_default_na=True)
    for col in spec.dates:
        df[col] = pd.to_datetime(df[col], errors="raise")
    for col in spec.bools:
        df[col] = to_nullable_bool(df[col])
    df = df.drop(columns=list(spec.drop)).rename(columns=spec.rename)
    # NaN -> None so the driver sends SQL NULL
    return df.astype(object).where(df.notna(), None)


def load_table(engine: Engine, spec: LoadSpec) -> int:
    df = prepare_frame(spec)
    start = time.perf_counter()
    df.to_sql(spec.table, engine, if_exists="append", index=False,
              chunksize=2000, method="multi")
    logger.info("Loaded %-20s %8d rows in %5.1fs", spec.table, len(df), time.perf_counter() - start)
    return len(df)


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def scalar(engine: Engine, sql: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


def verify(engine: Engine, csv_counts: dict[str, int]) -> dict:
    counts = [{"table": t, "csv_rows": n, "mysql_rows": scalar(engine, f"SELECT COUNT(*) FROM {t}")}
              for t, n in csv_counts.items()]
    for row in counts:
        row["match"] = row["csv_rows"] == row["mysql_rows"]
    orphans = [{"check": name, "orphan_rows": scalar(engine, sql)}
               for name, sql in ORPHAN_QUERIES.items()]

    db = database_name()
    with engine.connect() as conn:
        fks = conn.execute(text("""
            SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = :db AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_NAME, CONSTRAINT_NAME"""), {"db": db}).fetchall()
        checks = conn.execute(text("""
            SELECT tc.TABLE_NAME, cc.CONSTRAINT_NAME, cc.CHECK_CLAUSE
            FROM information_schema.CHECK_CONSTRAINTS cc
            JOIN information_schema.TABLE_CONSTRAINTS tc
              ON tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME AND tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
            WHERE cc.CONSTRAINT_SCHEMA = :db ORDER BY tc.TABLE_NAME"""), {"db": db}).fetchall()
        indexes = conn.execute(text("""
            SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cols
            FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = :db
            GROUP BY TABLE_NAME, INDEX_NAME ORDER BY TABLE_NAME, INDEX_NAME"""), {"db": db}).fetchall()

    return {
        "counts": pd.DataFrame(counts),
        "orphans": pd.DataFrame(orphans),
        "soft_link_missing": scalar(engine, SOFT_LINK_QUERY),
        "foreign_keys": pd.DataFrame(fks, columns=["table", "constraint", "column", "ref_table", "ref_column"]),
        "checks": pd.DataFrame(checks, columns=["table", "constraint", "rule"]),
        "indexes": pd.DataFrame(indexes, columns=["table", "index", "columns"]),
    }


def constraint_smoke_test(engine: Engine) -> pd.DataFrame:
    """Try inserting bad rows inside a rolled-back transaction; each must be rejected."""
    attempts = {
        # A 32-char id that matches no order: the FK must reject it.
        "order_items with unknown order_id (FK)": (
            "INSERT INTO order_items VALUES "
            "(REPEAT('0',32),1,(SELECT product_id FROM products LIMIT 1),"
            "(SELECT seller_id FROM sellers LIMIT 1),NOW(),10,0,10,0)"),
        "review_score = 9 (CHECK)":
            "UPDATE reviews SET review_score = 9 LIMIT 1",
        "negative price (CHECK)":
            "UPDATE order_items SET price = -1 LIMIT 1",
        "duplicate order_id (PRIMARY KEY)":
            "INSERT INTO orders SELECT * FROM orders LIMIT 1",
    }
    rows = []
    for name, sql in attempts.items():
        conn = engine.connect()
        trans = conn.begin()
        try:
            conn.execute(text(sql))
            outcome = "ACCEPTED (constraint missing!)"
        except Exception as exc:  # noqa: BLE001 - we expect a DB error here
            code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            outcome = f"rejected (MySQL {code}: {MYSQL_ERROR_MEANING.get(code, 'error')})"
        finally:
            trans.rollback()
            conn.close()
        rows.append({"attempt": name, "result": outcome})
    return pd.DataFrame(rows)


def write_report(result: dict, smoke: pd.DataFrame, server_version: str) -> None:
    md = [
        "# Database Load Report",
        "",
        f"_Auto-generated by `src/database/load.py` on {datetime.now():%Y-%m-%d %H:%M}._",
        "",
        f"- MySQL server version: **{server_version}**",
        f"- Database: **`{database_name()}`**",
        "",
        "## Row counts: clean CSV vs MySQL",
        "",
        df_to_markdown(result["counts"]),
        "## Referential integrity (each must be 0)",
        "",
        df_to_markdown(result["orphans"]),
        f"Soft link (not a FK): **{result['soft_link_missing']:,}** customers have a zip prefix "
        "with no geolocation point.",
        "",
        "## Constraint enforcement test (bad writes, rolled back)",
        "",
        df_to_markdown(smoke),
        "## Foreign keys",
        "",
        df_to_markdown(result["foreign_keys"]),
        "## CHECK constraints",
        "",
        df_to_markdown(result["checks"]),
        "## Indexes",
        "",
        df_to_markdown(result["indexes"]),
    ]
    (REPORTS_DIR / "database_load_report.md").write_text("\n".join(md), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = database_name()
    try:
        server = get_engine(include_database=False)
        version = check_connection(server)
    except RuntimeError as exc:            # missing .env values
        logger.error(str(exc))
        return 1
    except OperationalError as exc:
        logger.error("Cannot connect to MySQL: %s", exc.orig)
        return 1
    logger.info("Connected to MySQL %s", version)

    create_database(server, db)
    engine = get_engine()

    # Safety: refuse to rebuild a database that holds tables this project didn't create.
    ours = {spec.table for spec in LOAD_ORDER}
    foreign = set(inspect(engine).get_table_names()) - ours
    if foreign:
        logger.error("Database '%s' contains other tables %s; refusing to drop anything. "
                     "Set MYSQL_DATABASE to a different name.", db, sorted(foreign))
        return 3

    create_tables(engine)
    csv_counts = {spec.table: load_table(engine, spec) for spec in LOAD_ORDER}

    result = verify(engine, csv_counts)
    smoke = constraint_smoke_test(engine)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_report(result, smoke, version)

    ok = (result["counts"]["match"].all() and result["orphans"]["orphan_rows"].eq(0).all()
          and smoke["result"].str.startswith("rejected").all())
    logger.info("Verification %s - see reports/database_load_report.md", "PASSED" if ok else "FAILED")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
