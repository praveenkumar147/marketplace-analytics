"""Tests for Module E helpers (src/analysis/eda.py)."""
import pandas as pd
import pytest

from src.analysis.eda import (
    DELIVERY_BUCKET_ORDER,
    REGION_BY_STATE,
    brl,
    build_order_table,
    delivery_bucket,
)
from src.ingestion.profiler import VALID_BR_STATES


@pytest.mark.parametrize("value, expected", [
    (1_250_000, "R$1.25M"), (1_000_000, "R$1M"), (45_000, "R$45k"),
    (150, "R$150"), (5, "R$5"), (0.1, "R$0.1"), (0, "R$0"),
])
def test_brl_formatter(value, expected):
    assert brl(value) == expected


def test_every_state_has_a_region():
    assert set(REGION_BY_STATE) == set(VALID_BR_STATES)
    assert set(REGION_BY_STATE.values()) == {"Southeast", "South", "Central-West", "Northeast", "North"}


@pytest.mark.parametrize("delay, valid, expected", [
    (-10, True, "7+ days early"), (-7, True, "7+ days early"), (-1, True, "1-6 days early"),
    (0, True, "on promised day"), (3, True, "1-3 days late"), (7, True, "4-7 days late"),
    (8, True, "8+ days late"), (None, False, "not delivered / no valid date"),
    (5, False, "not delivered / no valid date"),
])
def test_delivery_bucket_matches_sql_buckets(delay, valid, expected):
    assert delivery_bucket(delay, valid) == expected
    assert expected in DELIVERY_BUCKET_ORDER


def test_order_table_aggregates_before_joining():
    """A 2-item order with 2 payment lines must stay ONE row with the right value."""
    t = {
        "orders": pd.DataFrame({"order_id": ["o1"], "customer_id": ["c1"]}),
        "customers": pd.DataFrame({"customer_id": ["c1"], "customer_unique_id": ["u1"],
                                   "customer_state": ["SP"]}),
        "order_items": pd.DataFrame({"order_id": ["o1", "o1"], "order_item_id": [1, 2],
                                     "price": [10.0, 20.0], "freight_value": [1.0, 2.0],
                                     "line_total": [11.0, 22.0]}),
        "reviews": pd.DataFrame({"order_id": ["o1", "o1"], "review_score": [1, 5],
                                 "is_latest_for_order": [False, True]}),
        "payments": pd.DataFrame({"order_id": ["o1", "o1"], "payment_type": ["voucher", "credit_card"],
                                  "payment_installments": [1, 3], "payment_value": [5.0, 28.0]}),
    }
    out = build_order_table(t)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["order_value"] == 33.0 and row["items"] == 2
    assert row["review_score"] == 5                     # latest review only
    assert row["payment_type"] == "credit_card"         # largest payment line
    assert row["region"] == "Southeast"
