"""Tests for Module A: loader and profiling checks.

Unit tests use tiny hand-made DataFrames so the expected answer is obvious.
Integration tests run against the real CSVs and are skipped if they are absent.
"""
import pandas as pd
import pytest

from src.config import RAW_DIR
from src.ingestion.loader import MissingDatasetError, load_raw_table
from src.ingestion.profiler import (
    check_primary_key,
    count_inconsistent_spellings,
    find_orphans,
    normalize_text,
    profile_columns,
)
from src.ingestion.registry import TABLES

# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #

def test_profile_columns_counts_nulls_and_distinct():
    df = pd.DataFrame({"a": [1, 2, None, 2], "b": ["x", "x", "x", None]})
    prof = profile_columns(df).set_index("column")
    assert prof.loc["a", "nulls"] == 1
    assert prof.loc["a", "distinct"] == 2
    assert prof.loc["b", "null_pct"] == 25.0


def test_primary_key_detects_duplicates_and_nulls():
    df = pd.DataFrame({"id": ["a", "a", "b", None], "v": [1, 2, 3, 4]})
    result = check_primary_key(df, ("id",))
    assert result["duplicate_key_rows"] == 2      # both 'a' rows are flagged
    assert result["null_key_rows"] == 1
    assert result["is_unique"] is False


def test_composite_primary_key_is_unique():
    df = pd.DataFrame({"order_id": ["o1", "o1", "o2"], "item": [1, 2, 1]})
    assert check_primary_key(df, ("order_id", "item"))["is_unique"] is True


def test_full_row_duplicates_counted_without_key():
    df = pd.DataFrame({"zip": ["01", "01", "02"], "lat": [1.0, 1.0, 2.0]})
    assert check_primary_key(df, ())["full_row_duplicates"] == 1


def test_find_orphans_ignores_nulls():
    child = pd.Series(["p1", "p2", None, "p9"])
    parent = pd.Series(["p1", "p2"])
    assert find_orphans(child, parent).tolist() == ["p9"]


@pytest.mark.parametrize("raw, expected", [
    ("São Paulo", "sao paulo"),
    ("  sao   paulo ", "sao paulo"),
    ("BRASÍLIA", "brasilia"),
])
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_count_inconsistent_spellings():
    cities = pd.Series(["sao paulo", "são paulo", "sao  paulo", "campinas", None])
    assert count_inconsistent_spellings(cities) == 2


def test_missing_file_raises_helpful_error(tmp_path):
    with pytest.raises(MissingDatasetError, match="kaggle"):
        load_raw_table("orders", raw_dir=tmp_path)


def test_unknown_table_name_raises():
    with pytest.raises(KeyError):
        load_raw_table("not_a_table")


def test_loader_keeps_zip_leading_zeros(tmp_path):
    spec = TABLES["sellers"]
    (tmp_path / spec.file_name).write_text(
        "seller_id,seller_zip_code_prefix,seller_city,seller_state\n"
        "s1,01037,sao paulo,SP\n", encoding="utf-8"
    )
    df = load_raw_table("sellers", raw_dir=tmp_path)
    assert df.loc[0, "seller_zip_code_prefix"] == "01037"


# --------------------------------------------------------------------------- #
# Integration tests (real data)
# --------------------------------------------------------------------------- #
real_data = pytest.mark.skipif(
    not (RAW_DIR / TABLES["orders"].file_name).exists(),
    reason="Olist CSVs not present in data/raw/",
)


@pytest.mark.integration
@real_data
def test_real_orders_have_unique_ids_and_parsed_dates():
    orders = load_raw_table("orders")
    assert orders["order_id"].is_unique
    assert pd.api.types.is_datetime64_any_dtype(orders["order_purchase_timestamp"])
    assert orders["order_purchase_timestamp"].notna().all()


@pytest.mark.integration
@real_data
def test_real_translation_file_bom_is_handled():
    df = load_raw_table("category_translation")
    assert "product_category_name" in df.columns   # would be '﻿product...' without utf-8-sig
