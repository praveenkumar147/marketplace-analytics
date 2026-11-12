"""Metadata for every raw table: file name, keys, dtypes and relationships.

Keeping this in one place means the loader, the profiler, the cleaner and
the database loader all agree on what each table looks like.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ForeignKey:
    """`column` in this table should match `ref_column` in `ref_table`."""
    column: str
    ref_table: str
    ref_column: str


@dataclass(frozen=True)
class TableSpec:
    name: str                       # short logical name used across the project
    file_name: str                  # CSV file in data/raw/
    description: str
    primary_key: tuple[str, ...]    # expected unique key (may be composite)
    date_columns: tuple[str, ...] = ()
    # Columns that must be read as text, e.g. zip codes with leading zeros
    string_columns: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = field(default_factory=tuple)
    encoding: str = "utf-8"


TABLES: dict[str, TableSpec] = {
    "customers": TableSpec(
        name="customers",
        file_name="olist_customers_dataset.csv",
        description="One row per customer_id. customer_id is generated per order; "
                    "customer_unique_id identifies the real person.",
        primary_key=("customer_id",),
        string_columns=("customer_zip_code_prefix",),
    ),
    "orders": TableSpec(
        name="orders",
        file_name="olist_orders_dataset.csv",
        description="One row per order with status and lifecycle timestamps.",
        primary_key=("order_id",),
        date_columns=(
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        foreign_keys=(ForeignKey("customer_id", "customers", "customer_id"),),
    ),
    "order_items": TableSpec(
        name="order_items",
        file_name="olist_order_items_dataset.csv",
        description="One row per item in an order (order_item_id = 1, 2, 3 ...).",
        primary_key=("order_id", "order_item_id"),
        date_columns=("shipping_limit_date",),
        foreign_keys=(
            ForeignKey("order_id", "orders", "order_id"),
            ForeignKey("product_id", "products", "product_id"),
            ForeignKey("seller_id", "sellers", "seller_id"),
        ),
    ),
    "payments": TableSpec(
        name="payments",
        file_name="olist_order_payments_dataset.csv",
        description="One row per payment line; an order can be split across methods.",
        primary_key=("order_id", "payment_sequential"),
        foreign_keys=(ForeignKey("order_id", "orders", "order_id"),),
    ),
    "reviews": TableSpec(
        name="reviews",
        file_name="olist_order_reviews_dataset.csv",
        description="Customer review (1-5 score and optional comment) per order.",
        primary_key=("review_id", "order_id"),
        date_columns=("review_creation_date", "review_answer_timestamp"),
        foreign_keys=(ForeignKey("order_id", "orders", "order_id"),),
    ),
    "products": TableSpec(
        name="products",
        file_name="olist_products_dataset.csv",
        description="Product catalogue with category and physical dimensions.",
        primary_key=("product_id",),
        foreign_keys=(
            ForeignKey("product_category_name", "category_translation", "product_category_name"),
        ),
    ),
    "sellers": TableSpec(
        name="sellers",
        file_name="olist_sellers_dataset.csv",
        description="One row per seller with location.",
        primary_key=("seller_id",),
        string_columns=("seller_zip_code_prefix",),
    ),
    "geolocation": TableSpec(
        name="geolocation",
        file_name="olist_geolocation_dataset.csv",
        description="Lat/long samples per zip code prefix (many rows per prefix).",
        primary_key=(),  # no natural key: many coordinates per zip prefix
        string_columns=("geolocation_zip_code_prefix",),
    ),
    "category_translation": TableSpec(
        name="category_translation",
        file_name="product_category_name_translation.csv",
        description="Portuguese -> English product category names.",
        primary_key=("product_category_name",),
        encoding="utf-8-sig",  # file starts with a UTF-8 byte-order mark
    ),
}
