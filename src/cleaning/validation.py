"""Post-cleaning validation: the clean data must satisfy these rules or the
pipeline stops before saving anything. This is the contract that Module C
(MySQL constraints) relies on.
"""
from __future__ import annotations

import pandas as pd

from src.ingestion.profiler import find_orphans

# table -> primary key columns, for the CLEAN tables
CLEAN_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "customers": ("customer_id",),
    "orders": ("order_id",),
    "order_items": ("order_id", "order_item_id"),
    "payments": ("order_id", "payment_sequential"),
    "reviews": ("review_id", "order_id"),
    "products": ("product_id",),
    "sellers": ("seller_id",),
    "category_translation": ("product_category_name",),
    "geolocation": ("geolocation_zip_code_prefix",),
}

# (child_table, child_col, parent_table, parent_col)
CLEAN_FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_items", "seller_id", "sellers", "seller_id"),
    ("payments", "order_id", "orders", "order_id"),
    ("reviews", "order_id", "orders", "order_id"),
    ("products", "product_category_name", "category_translation", "product_category_name"),
]


class DataValidationError(Exception):
    """Raised when cleaned data breaks a rule that downstream modules depend on."""


def validate_clean_tables(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Return a list of human-readable failures (empty list = all good)."""
    failures: list[str] = []

    for table, key in CLEAN_PRIMARY_KEYS.items():
        df = tables[table]
        if df[list(key)].isna().any(axis=None):
            failures.append(f"{table}: NULL in primary key {key}")
        if df.duplicated(list(key)).any():
            failures.append(f"{table}: duplicate primary key {key}")

    for child, ccol, parent, pcol in CLEAN_FOREIGN_KEYS:
        orphans = find_orphans(tables[child][ccol], tables[parent][pcol])
        if len(orphans):
            failures.append(f"{child}.{ccol}: {len(orphans)} orphan rows vs {parent}.{pcol}")

    orders = tables["orders"]
    if (orders["delivery_days"] < 0).any():
        failures.append("orders: negative delivery_days")
    if orders.loc[orders["is_valid_delivery"], "delivery_days"].isna().any():
        failures.append("orders: valid delivery without delivery_days")

    items = tables["order_items"]
    if (items["price"] <= 0).any() or (items["freight_value"] < 0).any():
        failures.append("order_items: non-positive price or negative freight")

    payments = tables["payments"]
    if (payments["payment_installments"] < 1).any():
        failures.append("payments: installments < 1")
    if payments["payment_type"].eq("not_defined").any():
        failures.append("payments: 'not_defined' payment type still present")

    reviews = tables["reviews"]
    if not reviews["review_score"].between(1, 5).all():
        failures.append("reviews: score outside 1-5")
    latest_per_order = reviews.loc[reviews["is_latest_for_order"]].groupby("order_id").size()
    if (latest_per_order != 1).any():
        failures.append("reviews: is_latest_for_order not exactly one per order")

    if tables["products"]["product_category_name"].isna().any():
        failures.append("products: NULL category after cleaning")

    return failures


def assert_valid(tables: dict[str, pd.DataFrame]) -> None:
    failures = validate_clean_tables(tables)
    if failures:
        raise DataValidationError("Clean data failed validation:\n  - " + "\n  - ".join(failures))
