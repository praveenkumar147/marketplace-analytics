"""Data profiling and quality checks for the raw Olist tables.

Four kinds of checks:
  1. Column profile   - dtype, nulls, distinct values per column
  2. Primary keys     - is the expected key really unique and non-null?
  3. Foreign keys     - do child rows point at parent rows that exist? (orphans)
  4. Validity rules   - business rules, e.g. "delivery cannot happen before purchase"

All functions are pure (DataFrames in, DataFrames/dicts out) so they are easy
to unit-test with tiny hand-made DataFrames.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from src.ingestion.registry import TABLES

# The 26 Brazilian states + the Federal District (official 2-letter UF codes).
VALID_BR_STATES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})

# Approximate bounding box of Brazil (incl. offshore islands), used to flag
# geolocation points that are obviously outside the country.
BRAZIL_LAT_RANGE = (-33.75, 5.27)
BRAZIL_LNG_RANGE = (-73.99, -32.39)

VALID_ORDER_STATUSES = frozenset({
    "created", "approved", "invoiced", "processing", "shipped",
    "delivered", "canceled", "unavailable",
})


# --------------------------------------------------------------------------- #
# 1. Column profile
# --------------------------------------------------------------------------- #
def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per column with dtype, null counts and cardinality."""
    total = len(df)
    rows = []
    for col in df.columns:
        series = df[col]
        nulls = int(series.isna().sum())
        non_null = series.dropna()
        rows.append({
            "column": col,
            "dtype": str(series.dtype),
            "non_null": total - nulls,
            "nulls": nulls,
            "null_pct": round(100 * nulls / total, 2) if total else 0.0,
            "distinct": int(series.nunique(dropna=True)),
            "example": str(non_null.iloc[0])[:40] if len(non_null) else "",
        })
    return pd.DataFrame(rows)


def numeric_summary(df: pd.DataFrame) -> pd.DataFrame:
    """min / median / max / mean for numeric columns (useful to spot outliers)."""
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return pd.DataFrame()
    summary = numeric.describe(percentiles=[0.5, 0.99]).T
    return summary[["min", "50%", "99%", "max", "mean"]].round(2)


# --------------------------------------------------------------------------- #
# 2. Primary keys
# --------------------------------------------------------------------------- #
def check_primary_key(df: pd.DataFrame, key: tuple[str, ...]) -> dict:
    """Check that `key` uniquely identifies rows and contains no nulls."""
    result = {
        "key": ", ".join(key) if key else "(none)",
        "rows": len(df),
        "full_row_duplicates": int(df.duplicated().sum()),
        "null_key_rows": 0,
        "duplicate_key_rows": 0,
        "is_unique": None,
    }
    if not key:
        return result
    key_cols = list(key)
    result["null_key_rows"] = int(df[key_cols].isna().any(axis=1).sum())
    # keep=False marks every row that belongs to a duplicated key group
    result["duplicate_key_rows"] = int(df.duplicated(subset=key_cols, keep=False).sum())
    result["is_unique"] = result["duplicate_key_rows"] == 0 and result["null_key_rows"] == 0
    return result


# --------------------------------------------------------------------------- #
# 3. Foreign keys (referential integrity)
# --------------------------------------------------------------------------- #
def find_orphans(child: pd.Series, parent: pd.Series) -> pd.Series:
    """Return the non-null child values that do not exist in the parent column."""
    child_values = child.dropna()
    return child_values[~child_values.isin(set(parent.dropna()))]


def check_foreign_keys(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Check every foreign key declared in the registry."""
    rows = []
    for name, spec in TABLES.items():
        if name not in tables:
            continue
        for fk in spec.foreign_keys:
            if fk.ref_table not in tables:
                continue
            child = tables[name][fk.column]
            orphans = find_orphans(child, tables[fk.ref_table][fk.ref_column])
            checked = int(child.notna().sum())
            rows.append({
                "relationship": f"{name}.{fk.column} -> {fk.ref_table}.{fk.ref_column}",
                "child_rows_checked": checked,
                "null_fk_rows": int(child.isna().sum()),
                "orphan_rows": len(orphans),
                "orphan_pct": round(100 * len(orphans) / checked, 3) if checked else 0.0,
                "distinct_orphan_values": int(orphans.nunique()),
                "orphan_examples": ", ".join(map(str, orphans.unique()[:3])),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 4. Validity / business rules
# --------------------------------------------------------------------------- #
def normalize_text(value: str) -> str:
    """Lower-case, trim and strip accents: 'São Paulo ' -> 'sao paulo'."""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.lower().split())


def count_inconsistent_spellings(series: pd.Series) -> int:
    """How many distinct raw values collapse into an already-seen normalized value.

    e.g. {'sao paulo', 'São Paulo', 'sao paulo '} -> 3 raw, 1 normalized -> 2.
    """
    raw = series.dropna().unique()
    normalized = {normalize_text(v) for v in raw}
    return len(raw) - len(normalized)


@dataclass(frozen=True)
class Rule:
    table: str
    check: str
    why: str
    # function(tables) -> (violations, rows_evaluated)
    evaluate: Callable[[dict[str, pd.DataFrame]], tuple[int, int]]


def _count(mask: pd.Series) -> tuple[int, int]:
    return int(mask.sum()), int(len(mask))


def _order_totals_mismatch(t: dict[str, pd.DataFrame], tolerance: float = 1.0) -> tuple[int, int]:
    """Orders whose item total (price + freight) differs from payments by > tolerance."""
    items = t["order_items"].assign(line_total=lambda d: d["price"] + d["freight_value"])
    item_totals = items.groupby("order_id")["line_total"].sum()
    pay_totals = t["payments"].groupby("order_id")["payment_value"].sum()
    both = pd.concat([item_totals, pay_totals], axis=1, join="inner")
    mismatch = (both["line_total"] - both["payment_value"]).abs() > tolerance
    return _count(mismatch)


def build_rules() -> list[Rule]:
    """All validity rules evaluated in the quality report."""
    o = "orders"
    return [
        # ---- orders --------------------------------------------------------
        Rule(o, "order_status not in known set",
             "Unknown statuses would break status-based metrics.",
             lambda t: _count(~t[o]["order_status"].isin(VALID_ORDER_STATUSES))),
        Rule(o, "unparseable / missing purchase timestamp",
             "Purchase date drives every time-series metric.",
             lambda t: _count(t[o]["order_purchase_timestamp"].isna())),
        Rule(o, "approved_at earlier than purchase",
             "Approval cannot precede purchase.",
             lambda t: _count(t[o]["order_approved_at"] < t[o]["order_purchase_timestamp"])),
        Rule(o, "delivered to customer before purchase",
             "Impossible sequence; would give negative delivery time.",
             lambda t: _count(t[o]["order_delivered_customer_date"] < t[o]["order_purchase_timestamp"])),
        Rule(o, "delivered to customer before handed to carrier",
             "Sequence error in logistics timestamps.",
             lambda t: _count(t[o]["order_delivered_customer_date"] < t[o]["order_delivered_carrier_date"])),
        Rule(o, "handed to carrier before purchase",
             "Sequence error in logistics timestamps.",
             lambda t: _count(t[o]["order_delivered_carrier_date"] < t[o]["order_purchase_timestamp"])),
        Rule(o, "status 'delivered' but no customer delivery date",
             "Cannot compute delivery time for these orders.",
             lambda t: _count((t[o]["order_status"] == "delivered")
                              & t[o]["order_delivered_customer_date"].isna())),
        Rule(o, "status not 'delivered' but has customer delivery date",
             "Status and timestamps disagree.",
             lambda t: _count((t[o]["order_status"] != "delivered")
                              & t[o]["order_delivered_customer_date"].notna())),
        Rule(o, "orders with no order_items rows",
             "These orders contribute no item-level revenue.",
             lambda t: _count(~t[o]["order_id"].isin(set(t["order_items"]["order_id"])))),
        Rule(o, "orders with no payment rows",
             "Payment-based revenue will miss these orders.",
             lambda t: _count(~t[o]["order_id"].isin(set(t["payments"]["order_id"])))),
        Rule(o, "item total vs payment total differs by > R$1",
             "Shows whether item-based and payment-based revenue agree.",
             _order_totals_mismatch),
        # ---- order_items ---------------------------------------------------
        Rule("order_items", "price <= 0",
             "Items must have a positive price.",
             lambda t: _count(t["order_items"]["price"] <= 0)),
        Rule("order_items", "freight_value < 0",
             "Freight can be zero (free shipping) but not negative.",
             lambda t: _count(t["order_items"]["freight_value"] < 0)),
        Rule("order_items", "freight_value == 0",
             "Informational: free-shipping items.",
             lambda t: _count(t["order_items"]["freight_value"] == 0)),
        # ---- payments ------------------------------------------------------
        Rule("payments", "payment_value <= 0",
             "Zero-value lines are usually vouchers or data errors.",
             lambda t: _count(t["payments"]["payment_value"] <= 0)),
        Rule("payments", "payment_installments < 1",
             "Every payment has at least one installment.",
             lambda t: _count(t["payments"]["payment_installments"] < 1)),
        Rule("payments", "payment_type == 'not_defined'",
             "Unusable for payment-method analysis.",
             lambda t: _count(t["payments"]["payment_type"] == "not_defined")),
        # ---- reviews -------------------------------------------------------
        Rule("reviews", "review_score outside 1-5",
             "Score scale is 1 to 5.",
             lambda t: _count(~t["reviews"]["review_score"].between(1, 5))),
        Rule("reviews", "review_id appearing more than once",
             "Same review attached to several orders.",
             lambda t: _count(t["reviews"]["review_id"].duplicated(keep=False))),
        Rule("reviews", "orders with more than one review",
             "Need a rule (e.g. latest review) to get one score per order.",
             lambda t: _count(t["reviews"]["order_id"].duplicated(keep=False))),
        Rule("reviews", "answer timestamp before creation date",
             "Sequence error.",
             lambda t: _count(t["reviews"]["review_answer_timestamp"]
                              < t["reviews"]["review_creation_date"])),
        # ---- products ------------------------------------------------------
        Rule("products", "missing product_category_name",
             "Uncategorized products need an 'unknown' bucket.",
             lambda t: _count(t["products"]["product_category_name"].isna())),
        Rule("products", "product_weight_g == 0",
             "Zero weight is physically implausible.",
             lambda t: _count(t["products"]["product_weight_g"] == 0)),
        # ---- customers / sellers -------------------------------------------
        Rule("customers", "customer_state not a valid Brazilian UF",
             "State drives regional analysis.",
             lambda t: _count(~t["customers"]["customer_state"].isin(VALID_BR_STATES))),
        Rule("customers", "city spellings that collapse after normalization",
             "Same city written differently splits regional totals.",
             lambda t: (count_inconsistent_spellings(t["customers"]["customer_city"]),
                        int(t["customers"]["customer_city"].nunique()))),
        Rule("sellers", "seller_state not a valid Brazilian UF",
             "State drives regional analysis.",
             lambda t: _count(~t["sellers"]["seller_state"].isin(VALID_BR_STATES))),
        Rule("sellers", "city spellings that collapse after normalization",
             "Same city written differently splits regional totals.",
             lambda t: (count_inconsistent_spellings(t["sellers"]["seller_city"]),
                        int(t["sellers"]["seller_city"].nunique()))),
        # ---- geolocation ---------------------------------------------------
        Rule("geolocation", "coordinates outside Brazil bounding box",
             "Obvious GPS errors would distort maps.",
             lambda t: _count(~(t["geolocation"]["geolocation_lat"].between(*BRAZIL_LAT_RANGE)
                                & t["geolocation"]["geolocation_lng"].between(*BRAZIL_LNG_RANGE)))),
        Rule("geolocation", "city spellings that collapse after normalization",
             "Accents/case variants (e.g. 'são paulo' vs 'sao paulo').",
             lambda t: (count_inconsistent_spellings(t["geolocation"]["geolocation_city"]),
                        int(t["geolocation"]["geolocation_city"].nunique()))),
        Rule("customers", "zip prefix not found in geolocation table",
             "These customers cannot be placed on a map.",
             lambda t: _count(~t["customers"]["customer_zip_code_prefix"]
                              .isin(set(t["geolocation"]["geolocation_zip_code_prefix"])))),
    ]


def run_validity_checks(tables: dict[str, pd.DataFrame], rules: list[Rule] | None = None) -> pd.DataFrame:
    """Evaluate every rule and return a tidy results table."""
    rows = []
    for rule in rules or build_rules():
        violations, evaluated = rule.evaluate(tables)
        rows.append({
            "table": rule.table,
            "check": rule.check,
            "violations": violations,
            "rows_evaluated": evaluated,
            "pct": round(100 * violations / evaluated, 3) if evaluated else 0.0,
            "why_it_matters": rule.why,
        })
    return pd.DataFrame(rows)
