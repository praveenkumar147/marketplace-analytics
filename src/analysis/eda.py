"""Helpers for the EDA notebook: load clean data, build analysis tables, style
and save charts. Keeping this logic in a module (not the notebook) means it can
be unit-tested and reused by the Power BI / insights steps.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from src.config import CHARTS_DIR, PROCESSED_DIR

# --------------------------------------------------------------------------- #
# Chart style - validated colour-blind-safe palette (fixed order, never cycled)
# --------------------------------------------------------------------------- #
COLORS = {
    "primary": "#2a78d6",    # slot 1 blue   - default single series
    "secondary": "#eb6834",  # slot 2 orange - second series / highlight
    "tertiary": "#1baf7a",   # slot 3 aqua
    "neutral": "#b9b8b2",    # de-emphasised marks
    "text": "#0b0b0b",
    "text_muted": "#52514e",
    "grid": "#e6e5e1",
    "surface": "#fcfcfb",
}
SEQUENTIAL_BLUES = "Blues"   # one hue, light -> dark, for heatmaps

REGION_BY_STATE = {
    **dict.fromkeys(["SP", "RJ", "MG", "ES"], "Southeast"),
    **dict.fromkeys(["PR", "SC", "RS"], "South"),
    **dict.fromkeys(["DF", "GO", "MT", "MS"], "Central-West"),
    **dict.fromkeys(["BA", "PE", "CE", "MA", "PB", "RN", "AL", "PI", "SE"], "Northeast"),
    **dict.fromkeys(["AM", "PA", "RO", "AC", "AP", "RR", "TO"], "North"),
}


def apply_style() -> None:
    """Quiet, consistent matplotlib defaults for every chart in the project."""
    plt.rcParams.update({
        "figure.facecolor": COLORS["surface"],
        "axes.facecolor": COLORS["surface"],
        "savefig.facecolor": COLORS["surface"],
        "figure.dpi": 110,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 26,          # room for the subtitle line under the title
        "axes.labelcolor": COLORS["text_muted"],
        "axes.edgecolor": COLORS["grid"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "grid.color": COLORS["grid"],
        "grid.linewidth": 0.8,
        "xtick.color": COLORS["text_muted"],
        "ytick.color": COLORS["text_muted"],
        "text.color": COLORS["text"],
        "lines.linewidth": 2,
        "legend.frameon": False,
    })


def brl(value: float, _pos=None) -> str:
    """Axis formatter: 1_250_000 -> 'R$1.25M', 45_000 -> 'R$45k'."""
    if abs(value) >= 1_000_000:
        return f"R${value / 1_000_000:.2f}M".replace(".00M", "M")
    if abs(value) >= 1_000:
        return f"R${value / 1_000:.0f}k"
    if abs(value) >= 10 or value == 0:
        return f"R${value:.0f}"
    return f"R${value:.2g}"          # small values on log axes: R$0.1, R$1, R$5


BRL_FORMATTER = mticker.FuncFormatter(brl)
PCT_FORMATTER = mticker.FuncFormatter(lambda v, _p: f"{v:.0f}%")


def add_subtitle(ax, text: str) -> None:
    """Muted one-line subtitle under the title (the takeaway or the definition)."""
    ax.text(0, 1.015, text, transform=ax.transAxes, fontsize=9,
            color=COLORS["text_muted"], va="bottom")


def save_chart(fig, name: str, directory: Path = CHARTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return path


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
_DATE_COLS = {
    "orders": ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date", "purchase_date"],
    "order_items": ["shipping_limit_date"],
    "reviews": ["review_creation_date", "review_answer_timestamp"],
}
_FILES = {
    "orders": "orders.csv", "order_items": "order_items.csv", "customers": "customers.csv",
    "products": "products.csv", "sellers": "sellers.csv", "payments": "payments.csv",
    "reviews": "reviews.csv", "geolocation": "geolocation.csv",
    "categories": "product_categories.csv",
}
_ZIP_COLS = ["customer_zip_code_prefix", "seller_zip_code_prefix", "geolocation_zip_code_prefix"]


def load_clean(directory: Path = PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    """Load every clean CSV produced by Module B with correct dtypes."""
    tables = {}
    for name, file in _FILES.items():
        path = directory / file
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run `python -m src.cleaning.pipeline`")
        df = pd.read_csv(path, dtype={c: "string" for c in _ZIP_COLS},
                         parse_dates=_DATE_COLS.get(name, []))
        tables[name] = df
    # is_late has blanks (not a valid delivery) -> nullable boolean
    tables["orders"]["is_late"] = tables["orders"]["is_late"].map(
        {True: True, False: False, "True": True, "False": False}).astype("boolean")
    return tables


def build_order_table(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per order with value, customer, location, delivery and review fields.

    Each source is aggregated to order level BEFORE joining, so multi-item orders
    are never double counted.
    """
    items = (t["order_items"].groupby("order_id")
             .agg(order_value=("line_total", "sum"), product_value=("price", "sum"),
                  freight=("freight_value", "sum"), items=("order_item_id", "count"))
             .reset_index())
    reviews = (t["reviews"].loc[t["reviews"]["is_latest_for_order"], ["order_id", "review_score"]])
    main_payment = (t["payments"].sort_values("payment_value", ascending=False)
                    .drop_duplicates("order_id")[["order_id", "payment_type", "payment_installments"]])

    orders = (t["orders"]
              .merge(t["customers"][["customer_id", "customer_unique_id", "customer_state"]],
                     on="customer_id", how="left", validate="one_to_one")
              .merge(items, on="order_id", how="left", validate="one_to_one")
              .merge(reviews, on="order_id", how="left", validate="one_to_one")
              .merge(main_payment, on="order_id", how="left", validate="one_to_one"))
    orders["region"] = orders["customer_state"].map(REGION_BY_STATE)
    return orders


def build_item_table(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Item-level rows for revenue orders, with category (English) and seller state."""
    revenue_orders = t["orders"].loc[t["orders"]["is_revenue_order"], ["order_id", "purchase_year_month"]]
    return (t["order_items"]
            .merge(revenue_orders, on="order_id", how="inner")
            .merge(t["products"][["product_id", "product_category_name_english", "product_weight_g"]],
                   on="product_id", how="left", validate="many_to_one")
            .merge(t["sellers"][["seller_id", "seller_state"]], on="seller_id", how="left",
                   validate="many_to_one")
            .rename(columns={"product_category_name_english": "category"}))


def delivery_bucket(delay_days: float, valid: bool) -> str:
    """Same buckets as SQL query q16b, so Python and SQL results line up."""
    if not valid or pd.isna(delay_days):
        return "not delivered / no valid date"
    if delay_days <= -7:
        return "7+ days early"
    if delay_days <= -1:
        return "1-6 days early"
    if delay_days == 0:
        return "on promised day"
    if delay_days <= 3:
        return "1-3 days late"
    if delay_days <= 7:
        return "4-7 days late"
    return "8+ days late"


DELIVERY_BUCKET_ORDER = ["7+ days early", "1-6 days early", "on promised day", "1-3 days late",
                         "4-7 days late", "8+ days late", "not delivered / no valid date"]
