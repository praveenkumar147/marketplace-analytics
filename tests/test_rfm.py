"""Tests for the RFM segmentation (src/analysis/rfm.py and sql/analysis/05_rfm.sql)."""
import pandas as pd
import pytest

from src.analysis.rfm import (
    SEGMENT_ORDER,
    assign_segment,
    compute_rfm_table,
    ntile,
    score_rfm,
    segment_customers,
)
from src.config import PROCESSED_DIR

ts = pd.Timestamp


@pytest.mark.parametrize("rows, expected_sizes", [
    (10, [2, 2, 2, 2, 2]),
    (12, [3, 3, 2, 2, 2]),      # MySQL NTILE: the first (n % 5) groups get the extra row
    (3, [1, 1, 1, 0, 0]),
])
def test_ntile_matches_mysql_group_sizes(rows, expected_sizes):
    groups = ntile(pd.DataFrame(index=range(rows)))
    assert [int((groups == g).sum()) for g in range(1, 6)] == expected_sizes
    assert groups.is_monotonic_increasing


@pytest.mark.parametrize("r, freq, m, expected", [
    (5, 3, 1, "Champions"), (4, 2, 5, "Champions"), (3, 2, 5, "Loyal Customers"),
    (1, 4, 1, "Loyal Customers"), (5, 1, 1, "New Customers"), (4, 1, 5, "Potential Loyalists"),
    (3, 1, 2, "Need Attention"), (2, 1, 4, "At Risk"), (1, 1, 5, "At Risk"),
    (2, 1, 3, "Hibernating"), (1, 1, 1, "Hibernating"),
])
def test_assign_segment_rules(r, freq, m, expected):
    assert assign_segment(r, freq, m) == expected


def test_every_score_combination_gets_a_known_segment():
    for r in range(1, 6):
        for m in range(1, 6):
            for f in (1, 2, 3):
                assert assign_segment(r, f, m) in SEGMENT_ORDER


@pytest.fixture
def tiny():
    orders = pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4", "o5"],
        "customer_id": ["c1", "c2", "c3", "c4", "c5"],
        "is_revenue_order": [True, True, True, True, False],          # o5 canceled
        "order_purchase_timestamp": [ts("2018-01-01 10:00"), ts("2018-03-01 09:00"),
                                     ts("2018-03-10 18:00"), ts("2017-06-01"), ts("2018-03-20")],
    })
    customers = pd.DataFrame({"customer_id": ["c1", "c2", "c3", "c4", "c5"],
                              "customer_unique_id": ["A", "A", "B", "C", "D"]})   # A ordered twice
    items = pd.DataFrame({"order_id": ["o1", "o2", "o2", "o3", "o4", "o5"],
                          "line_total": [10.0, 5.0, 5.0, 100.0, 1.0, 999.0]})
    return orders, items, customers


def test_compute_rfm_table(tiny):
    rfm = compute_rfm_table(*tiny).set_index("customer_unique_id")
    assert rfm.attrs["snapshot"] == ts("2018-03-11")           # day after last revenue purchase
    assert set(rfm.index) == {"A", "B", "C"}                   # D only has a canceled order
    assert rfm.loc["A", "frequency"] == 2
    assert rfm.loc["A", "monetary"] == 20.0
    assert rfm.loc["A", "recency_days"] == 10                  # last order 2018-03-01
    assert rfm.loc["B", "recency_days"] == 1


def test_scores_direction(tiny):
    scored = score_rfm(compute_rfm_table(*tiny)).set_index("customer_unique_id")
    assert scored.loc["B", "r_score"] > scored.loc["C", "r_score"]    # more recent -> higher R
    assert scored.loc["B", "m_score"] > scored.loc["C", "m_score"]    # spent more -> higher M
    assert scored.loc["A", "f_score"] == 4 and scored.loc["B", "f_score"] == 1
    seg = segment_customers(scored)
    assert seg["segment"].notna().all()


# --------------------------------------------------------------------------- #
# Integration: saved output + SQL implementation agree
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(not (PROCESSED_DIR / "customer_rfm.csv").exists(),
                    reason="run `python -m src.analysis.rfm` first")
def test_rfm_output_covers_all_revenue():
    rfm = pd.read_csv(PROCESSED_DIR / "customer_rfm.csv")
    orders = pd.read_csv(PROCESSED_DIR / "orders.csv")
    items = pd.read_csv(PROCESSED_DIR / "order_items.csv")
    revenue = items.merge(orders.loc[orders["is_revenue_order"], ["order_id"]], on="order_id")
    assert rfm["customer_unique_id"].is_unique
    assert rfm["monetary"].sum() == pytest.approx(revenue["line_total"].sum(), abs=0.05)
    assert rfm[["r_score", "m_score"]].isin(range(1, 6)).all().all()


@pytest.mark.integration
@pytest.mark.skipif(not (PROCESSED_DIR / "customer_rfm.csv").exists(), reason="no RFM output")
def test_sql_rfm_matches_python():
    from src.analysis.sql_runner import load_all_queries, run_query
    from src.database import connection
    try:
        engine = connection.get_engine()
        connection.check_connection(engine)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not reachable: {type(exc).__name__}")
    query = next(q for q in load_all_queries() if q.name == "q19_rfm_segments")
    sql = run_query(engine, query).set_index("segment")["customers"]
    py = pd.read_csv(PROCESSED_DIR / "customer_rfm.csv")["segment"].value_counts()
    assert sql.sort_index().to_dict() == py.sort_index().to_dict()
