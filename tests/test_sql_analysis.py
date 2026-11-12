"""Tests for Module D: query parsing, coverage of the 18 required metrics, and
live cross-checks of SQL results against an independent pandas calculation."""
from decimal import Decimal

import pandas as pd
import pytest

from src.analysis.sql_runner import decimals_to_float, load_all_queries, parse_queries, run_query
from src.config import PROCESSED_DIR
from src.database import connection

SAMPLE = """
-- file header that should be ignored
-- @query q99_example
-- @title Q99. Example
-- @purpose First line of purpose
--          continues here.
SELECT 1 AS one;   -- trailing comment


-- @query q98_second
-- @title Q98. Second
SELECT 2;
"""


def test_parse_queries_reads_markers_and_sql():
    queries = parse_queries(SAMPLE, "sample.sql")
    assert [q.name for q in queries] == ["q99_example", "q98_second"]
    assert queries[0].purpose == "First line of purpose continues here."
    assert queries[0].sql.startswith("SELECT 1 AS one")
    assert queries[1].sql == "SELECT 2"


def test_all_18_required_metrics_have_a_query():
    names = {q.name.split("_")[0] for q in load_all_queries()}
    missing = {f"q{n:02d}" for n in range(1, 19)} - names
    assert not missing, f"missing queries: {sorted(missing)}"


def test_queries_avoid_bind_parameter_syntax():
    """SQLAlchemy text() treats ':name' as a bind parameter; queries must not contain it."""
    import re
    for q in load_all_queries():
        assert not re.search(r"(?<![:\w]):[A-Za-z_]", q.sql), q.name


def test_required_sql_techniques_are_used():
    all_sql = " ".join(q.sql.upper() for q in load_all_queries())
    for keyword in ("JOIN", "GROUP BY", "HAVING", "ORDER BY", "CASE", "WITH ", "OVER (",
                    "LAG(", "RANK()", "NTILE(", "ROW_NUMBER()", "EXISTS (SELECT"):
        assert keyword in all_sql, keyword


def test_decimals_to_float_and_integer_counts():
    df = pd.DataFrame({"revenue": [Decimal("10.50"), Decimal("3.00")],
                       "late_orders": [Decimal("4"), Decimal("7")]})
    out = decimals_to_float(df)
    assert out["revenue"].tolist() == [10.5, 3.0]
    assert str(out["late_orders"].dtype) == "Int64"


# --------------------------------------------------------------------------- #
# Live cross-checks: SQL result == pandas result computed from the clean CSVs
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def engine():
    try:
        eng = connection.get_engine()
        connection.check_connection(eng)
        return eng
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not reachable / not configured: {type(exc).__name__}")


@pytest.fixture(scope="module")
def clean():
    if not (PROCESSED_DIR / "orders.csv").exists():
        pytest.skip("processed data not built")
    return {n: pd.read_csv(PROCESSED_DIR / f"{n}.csv")
            for n in ("orders", "order_items", "customers")}


def _query(name):
    return next(q for q in load_all_queries() if q.name == name)


@pytest.mark.integration
def test_total_revenue_and_aov_match_pandas(engine, clean):
    orders, items = clean["orders"], clean["order_items"]
    revenue_items = items.merge(orders.loc[orders["is_revenue_order"], ["order_id"]], on="order_id")

    q1 = run_query(engine, _query("q01_total_revenue")).iloc[0]
    assert q1["total_revenue"] == pytest.approx(revenue_items["line_total"].sum(), abs=0.01)

    q3 = run_query(engine, _query("q03_average_order_value")).iloc[0]
    expected_aov = revenue_items.groupby("order_id")["line_total"].sum().mean()
    assert q3["average_order_value"] == pytest.approx(expected_aov, abs=0.01)


@pytest.mark.integration
def test_repeat_rate_and_late_rate_match_pandas(engine, clean):
    orders, customers = clean["orders"], clean["customers"]
    per_person = (orders.loc[orders["is_revenue_order"]]
                  .merge(customers, on="customer_id")
                  .groupby("customer_unique_id")["order_id"].nunique())
    q9 = run_query(engine, _query("q09_repeat_customer_rate")).iloc[0]
    assert q9["repeat_customer_pct"] == pytest.approx(100 * (per_person >= 2).mean(), abs=0.01)

    valid = orders.loc[orders["is_valid_delivery"]]
    q15 = run_query(engine, _query("q15_late_delivery_rate")).iloc[0]
    assert q15["late_delivery_pct"] == pytest.approx(100 * valid["is_late"].astype(bool).mean(), abs=0.01)


@pytest.mark.integration
def test_monthly_revenue_sums_to_window_total(engine, clean):
    orders, items = clean["orders"], clean["order_items"]
    window = orders.loc[orders["is_revenue_order"] & orders["in_analysis_window"], ["order_id"]]
    expected = items.merge(window, on="order_id")["line_total"].sum()
    monthly = run_query(engine, _query("q04_monthly_revenue"))
    assert len(monthly) == 20                                    # 2017-01 .. 2018-08
    assert monthly["revenue"].sum() == pytest.approx(expected, abs=0.5)
    assert monthly["cumulative_revenue"].iloc[-1] == pytest.approx(expected, abs=0.5)
