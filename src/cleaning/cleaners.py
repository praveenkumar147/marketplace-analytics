"""One cleaning function per table.

Each function:
  * receives a *copy-safe* raw DataFrame (it never mutates its input),
  * applies the decisions documented in docs/data_model.md section 4,
  * records every change in a CleaningLog so the report can show
    exactly how many rows each step touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.cleaning.text import clean_city_series, clean_state_series, clean_zip_series
from src.ingestion.profiler import BRAZIL_LAT_RANGE, BRAZIL_LNG_RANGE

# ---- Business constants (single source of truth) ---------------------------
NON_REVENUE_STATUSES = frozenset({"canceled", "unavailable"})
ANALYSIS_WINDOW = (pd.Timestamp("2017-01-01"), pd.Timestamp("2018-09-01"))  # [start, end)
UNKNOWN_CATEGORY = "unknown"
# Two categories present in products but missing from the translation file.
MANUAL_CATEGORY_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_food_preparers",
    UNKNOWN_CATEGORY: UNKNOWN_CATEGORY,
}


@dataclass
class CleaningLog:
    """Collects (table, step, rows_affected) records for the cleaning report."""
    entries: list[dict] = field(default_factory=list)

    def record(self, table: str, step: str, rows_affected: int, note: str = "") -> None:
        self.entries.append({"table": table, "step": step,
                             "rows_affected": int(rows_affected), "note": note})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries, columns=["table", "step", "rows_affected", "note"])


# --------------------------------------------------------------------------- #
# Customers and sellers
# --------------------------------------------------------------------------- #
def clean_customers(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    df["customer_zip_code_prefix"] = clean_zip_series(df["customer_zip_code_prefix"])
    before = df["customer_city"].astype("string")
    df["customer_city"] = clean_city_series(df["customer_city"])
    log.record("customers", "standardize city text",
               (before.fillna("") != df["customer_city"].fillna("")).sum(),
               "lower-case, strip accents/apostrophes/suffixes")
    df["customer_state"] = clean_state_series(df["customer_state"])
    return df


def clean_sellers(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    df["seller_zip_code_prefix"] = clean_zip_series(df["seller_zip_code_prefix"])
    before = df["seller_city"].astype("string")
    df["seller_city"] = clean_city_series(df["seller_city"])
    changed = (before.fillna("") != df["seller_city"].fillna("")).sum()
    log.record("sellers", "standardize city text", changed,
               "e.g. 'auriflama/sp' -> 'auriflama', 'são paulo' -> 'sao paulo'")
    log.record("sellers", "junk city -> NULL",
               (before.notna() & df["seller_city"].isna()).sum(),
               "digits, e-mail addresses or a bare state code")
    df["seller_state"] = clean_state_series(df["seller_state"])
    return df


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
def clean_orders(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    """Standardize status and add derived delivery / time columns.

    No rows are removed: every order is needed for status and cancellation
    metrics. Flags tell later queries which rows are valid for which metric.
    """
    df = raw.copy()
    df["order_status"] = df["order_status"].astype("string").str.strip().str.lower()

    purchase = df["order_purchase_timestamp"]
    carrier = df["order_delivered_carrier_date"]
    delivered = df["order_delivered_customer_date"]
    estimated = df["order_estimated_delivery_date"]

    # --- time helpers
    df["purchase_date"] = purchase.dt.normalize()
    df["purchase_year_month"] = purchase.dt.strftime("%Y-%m")
    df["in_analysis_window"] = purchase.ge(ANALYSIS_WINDOW[0]) & purchase.lt(ANALYSIS_WINDOW[1])
    log.record("orders", "flag outside complete-month window", (~df["in_analysis_window"]).sum(),
               "2017-01 to 2018-08 kept as trend window; rows are not deleted")

    # --- revenue flag
    df["is_revenue_order"] = ~df["order_status"].isin(NON_REVENUE_STATUSES)
    log.record("orders", "flag non-revenue orders (canceled/unavailable)",
               (~df["is_revenue_order"]).sum())

    # --- delivery validity
    sequence_error = delivered.notna() & carrier.notna() & (delivered < carrier)
    df["is_valid_delivery"] = (
        df["order_status"].eq("delivered") & delivered.notna() & ~sequence_error
    )
    log.record("orders", "delivered status but no delivery date",
               (df["order_status"].eq("delivered") & delivered.isna()).sum(),
               "excluded from delivery metrics")
    log.record("orders", "delivered before handed to carrier", sequence_error.sum(),
               "excluded from delivery metrics")
    log.record("orders", "carrier date before purchase",
               (carrier.notna() & (carrier < purchase)).sum(),
               "flag only; carrier date not used in KPIs")

    # --- delivery metrics (only for valid deliveries; NaN elsewhere)
    valid = df["is_valid_delivery"]
    seconds_per_day = 86_400
    df["delivery_days"] = ((delivered - purchase).dt.total_seconds() / seconds_per_day).where(valid).round(2)
    df["estimated_delivery_days"] = (
        (estimated - purchase).dt.total_seconds() / seconds_per_day).where(valid).round(2)
    # Estimated date has no time part, so compare at the calendar-date level.
    delay = (delivered.dt.normalize() - estimated.dt.normalize()).dt.days
    df["delivery_delay_days"] = delay.where(valid)
    # Nullable boolean: True/False for valid deliveries, <NA> otherwise.
    df["is_late"] = (delay > 0).astype("boolean").where(valid, pd.NA)
    return df


# --------------------------------------------------------------------------- #
# Order items
# --------------------------------------------------------------------------- #
def iqr_upper_fence(series: pd.Series, k: float = 3.0) -> float:
    """Q3 + k*IQR. k=3 marks only *extreme* values (k=1.5 is the usual 'mild')."""
    q1, q3 = series.quantile([0.25, 0.75])
    return float(q3 + k * (q3 - q1))


def clean_order_items(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    df["line_total"] = (df["price"] + df["freight_value"]).round(2)
    fence = iqr_upper_fence(df["price"])
    df["is_price_outlier"] = df["price"] > fence
    log.record("order_items", "flag extreme price outliers (kept)", df["is_price_outlier"].sum(),
               f"price > Q3 + 3*IQR = R${fence:,.2f}; genuine high-value sales, not removed")
    return df


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #
def clean_payments(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    df["payment_type"] = df["payment_type"].astype("string").str.strip().str.lower()

    not_defined = df["payment_type"].eq("not_defined")
    log.record("payments", "drop payment_type 'not_defined'", not_defined.sum(),
               "zero-value rows with no usable payment method")
    df = df.loc[~not_defined].copy()

    zero_inst = df["payment_installments"] < 1
    df.loc[zero_inst, "payment_installments"] = 1
    log.record("payments", "installments 0 -> 1", zero_inst.sum())
    return df


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #
def clean_reviews(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    for col in ("review_comment_title", "review_comment_message"):
        text = df[col].astype("string")
        # Multi-line comments break some CSV readers; flatten to one line.
        text = text.str.replace(r"\s+", " ", regex=True).str.strip()
        df[col] = text.mask(text.eq(""))
    df["has_comment"] = df["review_comment_message"].notna()

    # One score per order: keep the most recent review as the "official" one.
    latest_first = df.sort_values(
        ["order_id", "review_answer_timestamp", "review_creation_date"],
        ascending=[True, False, False],
    )
    df["is_latest_for_order"] = ~latest_first.duplicated("order_id").reindex(df.index)
    log.record("reviews", "flag older duplicate reviews per order",
               (~df["is_latest_for_order"]).sum(), "kept; use is_latest_for_order = 1")
    return df


# --------------------------------------------------------------------------- #
# Products and categories
# --------------------------------------------------------------------------- #
def clean_categories(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    for col in df.columns:
        df[col] = df[col].astype("string").str.strip().str.lower()
    missing = {k: v for k, v in MANUAL_CATEGORY_TRANSLATIONS.items()
               if k not in set(df["product_category_name"])}
    extra = pd.DataFrame({"product_category_name": list(missing),
                          "product_category_name_english": list(missing.values())},
                         dtype="string")
    log.record("category_translation", "add manual translations", len(extra),
               ", ".join(missing))
    return pd.concat([df, extra], ignore_index=True)


def clean_products(raw: pd.DataFrame, categories: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    df = raw.copy()
    # Fix the original dataset's misspelled column names.
    df = df.rename(columns={"product_name_lenght": "product_name_length",
                            "product_description_lenght": "product_description_length"})

    df["product_category_name"] = df["product_category_name"].astype("string").str.strip().str.lower()
    missing_cat = df["product_category_name"].isna()
    df["product_category_name"] = df["product_category_name"].fillna(UNKNOWN_CATEGORY)
    log.record("products", "missing category -> 'unknown'", missing_cat.sum())

    zero_weight = df["product_weight_g"].eq(0)
    df.loc[zero_weight, "product_weight_g"] = np.nan
    log.record("products", "weight 0 g -> NULL", zero_weight.sum(), "physically impossible")

    df = df.merge(categories, on="product_category_name", how="left", validate="many_to_one")
    unmatched = df["product_category_name_english"].isna().sum()
    if unmatched:
        raise ValueError(f"{unmatched} products have a category with no English translation")
    return df


# --------------------------------------------------------------------------- #
# Geolocation
# --------------------------------------------------------------------------- #
def clean_geolocation(raw: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    """Reduce ~1M GPS samples to one representative point per zip prefix."""
    df = raw.copy()
    df["geolocation_zip_code_prefix"] = clean_zip_series(df["geolocation_zip_code_prefix"])

    dupes = df.duplicated()
    log.record("geolocation", "drop exact duplicate rows", dupes.sum())
    df = df.loc[~dupes]

    inside = (df["geolocation_lat"].between(*BRAZIL_LAT_RANGE)
              & df["geolocation_lng"].between(*BRAZIL_LNG_RANGE))
    log.record("geolocation", "drop points outside Brazil", (~inside).sum())
    df = df.loc[inside].copy()

    df["geolocation_city"] = clean_city_series(df["geolocation_city"])
    df["geolocation_state"] = clean_state_series(df["geolocation_state"])

    def most_common(s: pd.Series):
        counts = s.dropna().value_counts()
        return counts.index[0] if len(counts) else pd.NA

    agg = (df.groupby("geolocation_zip_code_prefix")
             .agg(geolocation_lat=("geolocation_lat", "median"),
                  geolocation_lng=("geolocation_lng", "median"),
                  geolocation_city=("geolocation_city", most_common),
                  geolocation_state=("geolocation_state", most_common),
                  sample_count=("geolocation_lat", "size"))
             .reset_index())
    agg[["geolocation_lat", "geolocation_lng"]] = agg[["geolocation_lat", "geolocation_lng"]].round(6)
    log.record("geolocation", "aggregate to one row per zip prefix", len(df) - len(agg),
               f"{len(df):,} points -> {len(agg):,} prefixes (median lat/lng)")
    return agg
