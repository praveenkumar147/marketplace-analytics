"""Tests for Module B: every cleaning decision is checked on a tiny DataFrame."""
import numpy as np
import pandas as pd
import pytest

from src.cleaning.cleaners import (
    CleaningLog,
    clean_categories,
    clean_geolocation,
    clean_order_items,
    clean_orders,
    clean_payments,
    clean_products,
    clean_reviews,
    iqr_upper_fence,
)
from src.cleaning.text import clean_city, clean_zip_series
from src.cleaning.validation import validate_clean_tables
from src.config import PROCESSED_DIR

ts = pd.Timestamp


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw, expected", [
    ("São Paulo", "sao paulo"),
    ("sao  paulo", "sao paulo"),
    ("santa barbara d´oeste", "santa barbara d oeste"),
    ("santa barbara d'oeste", "santa barbara d oeste"),
    ("auriflama/sp", "auriflama"),
    ("mogi das cruzes / sp", "mogi das cruzes"),
    ("novo hamburgo, rio grande do sul, brasil", "novo hamburgo"),
    ("rio de janeiro \\rio de janeiro", "rio de janeiro"),
    ("sp / sp", None),                     # only a state code
    ("04482255", None),                    # a zip code in the city field
    ("vendas@creditparts.com.br", None),   # an e-mail address
    (None, None),
    (np.nan, None),
])
def test_clean_city(raw, expected):
    assert clean_city(raw) == expected


def test_clean_zip_restores_leading_zeros():
    assert clean_zip_series(pd.Series(["1037", "01037", " 99990 "])).tolist() == ["01037", "01037", "99990"]


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
@pytest.fixture
def raw_orders():
    return pd.DataFrame({
        "order_id": ["on_time", "late", "seq_error", "no_date", "canceled"],
        "customer_id": ["c1", "c2", "c3", "c4", "c5"],
        "order_status": ["delivered", "Delivered ", "delivered", "delivered", "canceled"],
        "order_purchase_timestamp": [ts("2017-03-01 10:00")] * 4 + [ts("2016-10-05")],
        "order_approved_at": [ts("2017-03-01 11:00")] * 5,
        "order_delivered_carrier_date": [ts("2017-03-02"), ts("2017-03-02"), ts("2017-03-09"), pd.NaT, pd.NaT],
        "order_delivered_customer_date": [ts("2017-03-06 10:00"), ts("2017-03-12 08:00"),
                                          ts("2017-03-05"), pd.NaT, pd.NaT],
        "order_estimated_delivery_date": [ts("2017-03-10")] * 5,
    })


def test_orders_delivery_metrics(raw_orders):
    out = clean_orders(raw_orders, CleaningLog()).set_index("order_id")
    assert out.loc["on_time", "delivery_days"] == 5.0
    assert out.loc["on_time", "is_late"] == False   # noqa: E712 (nullable boolean)
    assert out.loc["late", "is_late"] == True       # noqa: E712
    assert out.loc["late", "delivery_delay_days"] == 2.0
    assert out.loc["late", "order_status"] == "delivered"    # trimmed + lower-cased


def test_orders_invalid_deliveries_excluded_not_deleted(raw_orders):
    out = clean_orders(raw_orders, CleaningLog()).set_index("order_id")
    assert len(out) == 5                                      # no rows removed
    for oid in ("seq_error", "no_date", "canceled"):
        assert not out.loc[oid, "is_valid_delivery"]
        assert pd.isna(out.loc[oid, "delivery_days"])
        assert pd.isna(out.loc[oid, "is_late"])


def test_orders_revenue_and_window_flags(raw_orders):
    out = clean_orders(raw_orders, CleaningLog()).set_index("order_id")
    assert not out.loc["canceled", "is_revenue_order"]
    assert out.loc["on_time", "is_revenue_order"]
    assert not out.loc["canceled", "in_analysis_window"]      # 2016 -> outside
    assert out.loc["on_time", "purchase_year_month"] == "2017-03"


# --------------------------------------------------------------------------- #
# Items, payments, reviews
# --------------------------------------------------------------------------- #
def test_iqr_fence():
    # Q1=2, Q3=4, IQR=2 -> fence with k=3 is 4 + 6 = 10
    assert iqr_upper_fence(pd.Series([1, 2, 3, 4, 5])) == 10.0


def test_order_items_line_total_and_outliers_kept():
    raw = pd.DataFrame({"order_id": list("abcdef"), "order_item_id": [1] * 6,
                        "price": [10, 11, 12, 13, 14, 5000.0], "freight_value": [1.5] * 6})
    out = clean_order_items(raw, CleaningLog())
    assert len(out) == 6                                      # outlier NOT removed
    assert out["line_total"].iloc[0] == 11.5
    assert out["is_price_outlier"].tolist() == [False] * 5 + [True]


def test_payments_drop_not_defined_and_fix_installments():
    raw = pd.DataFrame({"order_id": ["a", "b", "c"], "payment_sequential": [1, 1, 1],
                        "payment_type": ["credit_card", "not_defined", "Boleto"],
                        "payment_installments": [0, 1, 1], "payment_value": [10.0, 0.0, 5.0]})
    out = clean_payments(raw, CleaningLog())
    assert out["order_id"].tolist() == ["a", "c"]
    assert out["payment_installments"].min() == 1
    assert "boleto" in out["payment_type"].tolist()


def test_reviews_latest_flag_and_comment_flattening():
    raw = pd.DataFrame({
        "review_id": ["r1", "r2", "r3"], "order_id": ["o1", "o1", "o2"], "review_score": [1, 5, 4],
        "review_comment_title": [None, "  ", "ok"],
        "review_comment_message": ["bad\r\nproduct", None, "   "],
        "review_creation_date": [ts("2018-01-01"), ts("2018-02-01"), ts("2018-01-05")],
        "review_answer_timestamp": [ts("2018-01-02"), ts("2018-02-02"), ts("2018-01-06")],
    })
    out = clean_reviews(raw, CleaningLog()).set_index("review_id")
    assert out.loc["r2", "is_latest_for_order"] and not out.loc["r1", "is_latest_for_order"]
    assert out.loc["r3", "is_latest_for_order"]
    assert out.loc["r1", "review_comment_message"] == "bad product"
    assert pd.isna(out.loc["r3", "review_comment_message"])   # whitespace-only -> NULL
    assert not out.loc["r3", "has_comment"]


# --------------------------------------------------------------------------- #
# Products, categories, geolocation
# --------------------------------------------------------------------------- #
def test_products_unknown_category_and_translation():
    cats = clean_categories(pd.DataFrame({"product_category_name": ["beleza_saude"],
                                          "product_category_name_english": ["health_beauty"]}),
                            CleaningLog())
    raw = pd.DataFrame({"product_id": ["p1", "p2", "p3"],
                        "product_category_name": ["beleza_saude", None, "pc_gamer"],
                        "product_name_lenght": [10, 20, 30], "product_description_lenght": [1, 2, 3],
                        "product_weight_g": [100.0, 0.0, 50.0]})
    out = clean_products(raw, cats, CleaningLog()).set_index("product_id")
    assert out.loc["p2", "product_category_name"] == "unknown"
    assert out.loc["p3", "product_category_name_english"] == "pc_gamer"
    assert pd.isna(out.loc["p2", "product_weight_g"])
    assert "product_name_length" in out.columns               # typo fixed


def test_geolocation_dedup_bounds_and_aggregate():
    raw = pd.DataFrame({
        "geolocation_zip_code_prefix": ["01001", "01001", "01001", "1002", "01003"],
        "geolocation_lat": [-23.0, -23.0, -24.0, -22.0, 40.0],      # last one is outside Brazil
        "geolocation_lng": [-46.0, -46.0, -47.0, -45.0, -3.0],
        "geolocation_city": ["são paulo", "são paulo", "sao paulo", "x", "y"],
        "geolocation_state": ["SP"] * 5,
    })
    out = clean_geolocation(raw, CleaningLog()).set_index("geolocation_zip_code_prefix")
    assert list(out.index) == ["01001", "01002"]               # 01003 dropped, 1002 zero-padded
    assert out.loc["01001", "sample_count"] == 2              # exact duplicate removed
    assert out.loc["01001", "geolocation_lat"] == -23.5       # median of -23, -24
    assert out.loc["01001", "geolocation_city"] == "sao paulo"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_validation_catches_orphan_and_duplicate_key():
    def frame(**cols):
        return pd.DataFrame(cols)
    tables = {
        "customers": frame(customer_id=["c1"]),
        "orders": frame(order_id=["o1", "o1"], customer_id=["c1", "c1"],
                        delivery_days=[1.0, 1.0], is_valid_delivery=[True, True]),
        "order_items": frame(order_id=["o9"], order_item_id=[1], product_id=["p1"], seller_id=["s1"],
                             price=[10.0], freight_value=[0.0]),
        "payments": frame(order_id=["o1"], payment_sequential=[1], payment_installments=[1],
                          payment_type=["boleto"]),
        "reviews": frame(review_id=["r1"], order_id=["o1"], review_score=[5], is_latest_for_order=[True]),
        "products": frame(product_id=["p1"], product_category_name=["x"]),
        "sellers": frame(seller_id=["s1"]),
        "category_translation": frame(product_category_name=["x"]),
        "geolocation": frame(geolocation_zip_code_prefix=["01001"]),
    }
    failures = validate_clean_tables(tables)
    assert any("orders: duplicate primary key" in f for f in failures)
    assert any("order_items.order_id: 1 orphan" in f for f in failures)


# --------------------------------------------------------------------------- #
# Integration: the saved clean files
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(not (PROCESSED_DIR / "orders.csv").exists(),
                    reason="run `python -m src.cleaning.pipeline` first")
def test_processed_files_are_consistent():
    orders = pd.read_csv(PROCESSED_DIR / "orders.csv")
    items = pd.read_csv(PROCESSED_DIR / "order_items.csv")
    payments = pd.read_csv(PROCESSED_DIR / "payments.csv")
    assert orders["order_id"].is_unique
    assert items["order_id"].isin(orders["order_id"]).all()
    assert (orders["delivery_days"].dropna() >= 0).all()
    assert not payments["payment_type"].eq("not_defined").any()
