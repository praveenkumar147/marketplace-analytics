"""Create the Power BI reporting views, verify them, and export them to CSV.

Run from the project root (database loaded, RFM table built):
    python -m src.database.powerbi              # create + verify views, write expected values
    python -m src.database.powerbi --export-csv # ...and export every view to data/powerbi/

reports/powerbi_expected_values.md lists the KPI values the finished dashboard
must show. Compare the cards against it to prove the DAX measures are right.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.analysis.sql_runner import decimals_to_float
from src.config import DATA_DIR, REPORTS_DIR, SQL_DIR
from src.database.connection import get_engine
from src.database.schema import run_sql_file
from src.ingestion.quality_report import df_to_markdown

logger = logging.getLogger(__name__)
VIEWS_FILE = SQL_DIR / "views" / "01_powerbi_views.sql"
EXPORT_DIR = DATA_DIR / "powerbi"

# view -> table name to use inside Power BI, and the key that must be unique
VIEWS = {
    "vw_fact_orders": ("fact_orders", "order_id"),
    "vw_fact_sales": ("fact_sales", None),            # composite key order_id + order_item_id
    "vw_fact_payments": ("fact_payments", None),
    "vw_dim_customer": ("dim_customer", "customer_unique_id"),
    "vw_dim_product": ("dim_product", "product_id"),
    "vw_dim_seller": ("dim_seller", "seller_id"),
    "vw_dim_state": ("dim_state", "state"),
}

# Each expected KPI is written the way the matching DAX measure defines it
# (powerbi/measures/measures.dax), so the dashboard can be checked number by number.
EXPECTED_KPIS = {
    "Total Revenue": "SELECT ROUND(SUM(line_total), 2) FROM vw_fact_sales",
    "Product Revenue": "SELECT ROUND(SUM(price), 2) FROM vw_fact_sales",
    "Freight Revenue": "SELECT ROUND(SUM(freight_value), 2) FROM vw_fact_sales",
    "Total Orders": "SELECT COUNT(*) FROM vw_fact_orders",
    "Sales Orders": "SELECT SUM(is_sale) FROM vw_fact_orders",
    "Average Order Value": "SELECT ROUND(SUM(line_total) / COUNT(DISTINCT order_id), 2) FROM vw_fact_sales",
    "Total Customers": "SELECT COUNT(DISTINCT customer_unique_id) FROM vw_fact_orders WHERE is_sale = 1",
    "Repeat Customers": """SELECT COUNT(DISTINCT f.customer_unique_id) FROM vw_fact_orders f
                           JOIN vw_dim_customer d USING (customer_unique_id)
                           WHERE f.is_sale = 1 AND d.lifetime_orders >= 2""",
    "Repeat Purchase Rate %": """SELECT ROUND(100 * AVG(lifetime_orders >= 2), 2)
                                 FROM vw_dim_customer WHERE lifetime_orders >= 1""",
    "Avg Delivery Days": "SELECT ROUND(AVG(delivery_days), 2) FROM vw_fact_orders WHERE is_valid_delivery = 1",
    "Late Delivery %": "SELECT ROUND(100 * AVG(is_late), 2) FROM vw_fact_orders WHERE is_valid_delivery = 1",
    "Cancellation %": "SELECT ROUND(100 * AVG(order_status = 'canceled'), 2) FROM vw_fact_orders",
    "Avg Review Score": "SELECT ROUND(AVG(review_score), 2) FROM vw_fact_orders",
    "Revenue 2018-08 (last complete month)": """SELECT ROUND(SUM(s.line_total), 2) FROM vw_fact_sales s
                           JOIN vw_fact_orders o USING (order_id) WHERE o.purchase_year_month = '2018-08'""",
    "Revenue MoM % 2018-08": """SELECT ROUND(100 * (a.r - b.r) / b.r, 2) FROM
                           (SELECT SUM(s.line_total) r FROM vw_fact_sales s JOIN vw_fact_orders o USING (order_id)
                            WHERE o.purchase_year_month = '2018-08') a,
                           (SELECT SUM(s.line_total) r FROM vw_fact_sales s JOIN vw_fact_orders o USING (order_id)
                            WHERE o.purchase_year_month = '2018-07') b""",
    "SP revenue share %": """SELECT ROUND(100 * SUM(CASE WHEN o.customer_state = 'SP' THEN s.line_total END)
                                    / SUM(s.line_total), 2)
                             FROM vw_fact_sales s JOIN vw_fact_orders o USING (order_id)""",
    "Credit card % of orders (main method)": """SELECT ROUND(100 * AVG(main_payment_type = 'credit_card'), 2)
                                                FROM vw_fact_orders""",
    "Revenue YoY % (date slicer Jan-Aug 2018)": """SELECT ROUND(100 * (a.r - b.r) / b.r, 2) FROM
                           (SELECT SUM(s.line_total) r FROM vw_fact_sales s JOIN vw_fact_orders o USING (order_id)
                            WHERE o.purchase_date BETWEEN '2018-01-01' AND '2018-08-31') a,
                           (SELECT SUM(s.line_total) r FROM vw_fact_sales s JOIN vw_fact_orders o USING (order_id)
                            WHERE o.purchase_date BETWEEN '2017-01-01' AND '2017-08-31') b""",
    "New Customers (Aug 2018)": """SELECT COUNT(DISTINCT customer_unique_id) FROM vw_fact_orders
                                   WHERE is_sale = 1 AND is_first_order = 1 AND purchase_year_month = '2018-08'""",
    "Total Customers (Aug 2018)": """SELECT COUNT(DISTINCT customer_unique_id) FROM vw_fact_orders
                                     WHERE is_sale = 1 AND purchase_year_month = '2018-08'""",
    "health_beauty Product Revenue": """SELECT ROUND(SUM(s.price), 2) FROM vw_fact_sales s
                                        JOIN vw_dim_product p USING (product_id) WHERE p.category = 'health_beauty'""",
    "health_beauty Category Revenue Share %": """SELECT ROUND(100 * SUM(CASE WHEN p.category = 'health_beauty'
                                                 THEN s.price END) / SUM(s.price), 2)
                                                 FROM vw_fact_sales s JOIN vw_dim_product p USING (product_id)""",
    "Late Delivery % - state SP": """SELECT ROUND(100 * AVG(is_late), 2) FROM vw_fact_orders
                                     WHERE is_valid_delivery = 1 AND customer_state = 'SP'""",
    "Champions customers": "SELECT COUNT(*) FROM vw_dim_customer WHERE rfm_segment = 'Champions'",
    "Avg Review Score - 8+ days late": """SELECT ROUND(AVG(review_score), 2) FROM vw_fact_orders
                                          WHERE delivery_outcome = '6. 8+ days late'""",
}

# Reconciliation: the views must reproduce the base-table / Module D figures.
RECONCILIATION = {
    "fact_sales revenue = SQL Q1 total revenue": (
        "SELECT ROUND(SUM(line_total), 2) FROM vw_fact_sales",
        """SELECT ROUND(SUM(i.line_total), 2) FROM order_items i JOIN orders o USING (order_id)
           WHERE o.is_revenue_order = 1"""),
    "fact_orders rows = orders rows": (
        "SELECT COUNT(*) FROM vw_fact_orders", "SELECT COUNT(*) FROM orders"),
    "fact_payments rows = payments rows": (
        "SELECT COUNT(*) FROM vw_fact_payments", "SELECT COUNT(*) FROM payments"),
    "first orders = customers with a sale": (
        "SELECT SUM(is_first_order) FROM vw_fact_orders",
        "SELECT COUNT(*) FROM customer_rfm"),
    "late % = SQL Q15": (
        "SELECT ROUND(100 * AVG(is_late), 2) FROM vw_fact_orders WHERE is_valid_delivery = 1",
        "SELECT ROUND(100 * AVG(is_late), 2) FROM orders WHERE is_valid_delivery = 1"),
}


def scalar(engine: Engine, sql: str):
    """Run a one-value query; DECIMAL -> int when whole (counts) else float."""
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar_one()
    if value is None or isinstance(value, (int, float)):
        return value
    as_float = float(value)
    return int(as_float) if value == value.to_integral_value() and "." not in str(value) else as_float


def create_views(engine: Engine) -> None:
    run_sql_file(engine, VIEWS_FILE)


def verify_views(engine: Engine) -> pd.DataFrame:
    rows = []
    for view, (pbi_name, key) in VIEWS.items():
        count = scalar(engine, f"SELECT COUNT(*) FROM {view}")
        unique = None
        if key:
            unique = scalar(engine, f"SELECT COUNT(DISTINCT {key}) FROM {view}") == count
        rows.append({"view": view, "power_bi_table": pbi_name, "rows": count,
                     "key": key or "(composite)", "key_unique": unique})
    return pd.DataFrame(rows)


def reconcile(engine: Engine) -> pd.DataFrame:
    rows = []
    for check, (view_sql, base_sql) in RECONCILIATION.items():
        a, b = scalar(engine, view_sql), scalar(engine, base_sql)
        rows.append({"check": check, "view_value": a, "base_value": b, "match": a == b})
    return pd.DataFrame(rows, dtype=object)


def expected_values(engine: Engine) -> pd.DataFrame:
    # dtype=object keeps ints as ints (a float column would print counts as 99,441.00)
    return pd.DataFrame([{"kpi": name, "expected_value": scalar(engine, sql)}
                         for name, sql in EXPECTED_KPIS.items()], dtype=object)


def export_csv(engine: Engine) -> pd.DataFrame:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for view, (pbi_name, _key) in VIEWS.items():
        with engine.connect() as conn:
            df = decimals_to_float(pd.read_sql(text(f"SELECT * FROM {view}"), conn))
        path = EXPORT_DIR / f"{pbi_name}.csv"
        df.to_csv(path, index=False, date_format="%Y-%m-%d %H:%M:%S")
        rows.append({"file": f"data/powerbi/{pbi_name}.csv", "rows": len(df)})
        logger.info("Exported %-16s %8d rows", pbi_name, len(df))
    return pd.DataFrame(rows)


def _fmt(value) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def write_report(views: pd.DataFrame, recon: pd.DataFrame, kpis: pd.DataFrame) -> None:
    kpis = kpis.assign(expected_value=kpis["expected_value"].map(_fmt))
    recon = recon.assign(view_value=recon["view_value"].map(_fmt), base_value=recon["base_value"].map(_fmt))
    views = views.assign(rows=views["rows"].map(_fmt))
    md = [
        "# Power BI - Expected Values",
        "",
        f"_Auto-generated by `src/database/powerbi.py` on {datetime.now():%Y-%m-%d %H:%M}._",
        "",
        "After building the dashboard, with **no slicers applied**, each card or measure must "
        "show these values. Any difference means a DAX measure or relationship is wrong.",
        "",
        "## Expected KPI values",
        "",
        df_to_markdown(kpis),
        "## Reporting views",
        "",
        df_to_markdown(views),
        "## Reconciliation with base tables / SQL analysis",
        "",
        df_to_markdown(recon),
    ]
    (REPORTS_DIR / "powerbi_expected_values.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Power BI reporting views")
    parser.add_argument("--export-csv", action="store_true", help="export views to data/powerbi/")
    args = parser.parse_args()

    try:
        engine = get_engine()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    create_views(engine)
    views = verify_views(engine)
    recon = reconcile(engine)
    kpis = expected_values(engine)
    write_report(views, recon, kpis)
    logger.info("\n%s", views.to_string(index=False))
    logger.info("\n%s", recon.to_string(index=False))
    logger.info("\n%s", kpis.to_string(index=False))
    if args.export_csv:
        export_csv(engine)

    ok = recon["match"].all() and views["key_unique"].dropna().all()
    logger.info("Views %s", "VERIFIED" if ok else "FAILED VERIFICATION")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
