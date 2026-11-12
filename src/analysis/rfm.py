"""RFM customer segmentation (Recency, Frequency, Monetary).

Run from the project root:
    python -m src.analysis.rfm              # build segments, charts, report
    python -m src.analysis.rfm --to-mysql   # ...and load table customer_rfm for Power BI

Method (details and reasoning in docs/rfm.md)
  * Customer   = customer_unique_id; only revenue orders with items count.
  * Snapshot   = the day after the last purchase in the data (2018-09-04).
  * Recency    = days since the customer's last purchase  -> R score 1-5 (5 = most recent)
  * Monetary   = total spent (price + freight)              -> M score 1-5 (5 = highest)
  * R and M use quintiles with NTILE semantics (equal-sized groups, ties broken by
    customer id), so this module and sql/analysis/05_rfm.sql give identical scores.
  * Frequency  = number of orders. 97% of customers have exactly one order, so
    frequency quintiles are meaningless (four of five "quintiles" would all be 1).
    F is therefore used as a business rule: one-time vs repeat customer.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

import matplotlib
import numpy as np
import pandas as pd

from src.config import CHARTS_DIR, PROCESSED_DIR, REPORTS_DIR
from src.ingestion.quality_report import df_to_markdown

logger = logging.getLogger(__name__)

# Ordered from most to least valuable; used for sorting and charts.
SEGMENT_ORDER = [
    "Champions", "Loyal Customers", "New Customers", "Potential Loyalists",
    "Need Attention", "At Risk", "Hibernating",
]

SEGMENT_RULES = pd.DataFrame([
    ("Champions",           "2+ orders", "4-5", "any", "Bought more than once and recently. Reward; ask for reviews/referrals."),
    ("Loyal Customers",     "2+ orders", "1-3", "any", "Proven repeat buyers, not recent. Win-back offer before they lapse."),
    ("New Customers",       "1 order",   "5",   "any", "First purchase in the most recent fifth. Onboarding; drive a 2nd order."),
    ("Potential Loyalists", "1 order",   "4",   "any", "Recent one-time buyers. Best target to convert into repeat customers."),
    ("Need Attention",      "1 order",   "3",   "any", "Middle-aged one-time buyers. Reminder campaigns, recommendations."),
    ("At Risk",             "1 order",   "1-2", "4-5", "Big spenders who never came back. Personal win-back offer."),
    ("Hibernating",         "1 order",   "1-2", "1-3", "Low-value, long-inactive one-time buyers. Low-cost mass campaigns only."),
], columns=["segment", "frequency", "R score", "M score", "meaning / action"])


# --------------------------------------------------------------------------- #
# Core calculation (pure functions - unit tested)
# --------------------------------------------------------------------------- #
def ntile(order_keys: pd.DataFrame, n: int = 5) -> pd.Series:
    """Replicates SQL NTILE(n): rows already sorted -> groups 1..n of (almost) equal size.

    If rows don't divide evenly, the first (len % n) groups get one extra row,
    exactly like MySQL.
    """
    size = len(order_keys)
    base, extra = divmod(size, n)
    group_sizes = [base + 1 if i < extra else base for i in range(n)]
    labels = np.repeat(np.arange(1, n + 1), group_sizes)
    return pd.Series(labels, index=order_keys.index)


def compute_rfm_table(orders: pd.DataFrame, items: pd.DataFrame, customers: pd.DataFrame,
                      snapshot: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per customer_unique_id with recency, frequency, monetary."""
    revenue_orders = orders.loc[orders["is_revenue_order"].astype(bool),
                                ["order_id", "customer_id", "order_purchase_timestamp"]]
    order_values = items.groupby("order_id", as_index=False)["line_total"].sum()
    df = (revenue_orders
          .merge(order_values, on="order_id", how="inner")       # orders with items only
          .merge(customers[["customer_id", "customer_unique_id"]], on="customer_id",
                 how="left", validate="many_to_one"))
    purchase_day = df["order_purchase_timestamp"].dt.normalize()
    if snapshot is None:
        # DateOffset avoids a NumPy 2.x deprecation warning raised by Timedelta here.
        snapshot = purchase_day.max() + pd.DateOffset(days=1)

    rfm = (df.assign(purchase_day=purchase_day)
             .groupby("customer_unique_id")
             .agg(last_purchase=("purchase_day", "max"),
                  frequency=("order_id", "nunique"),
                  monetary=("line_total", "sum"))
             .reset_index())
    rfm["recency_days"] = (snapshot - rfm["last_purchase"]).dt.days
    rfm["monetary"] = rfm["monetary"].round(2)       # exact cents -> same ordering as SQL DECIMAL
    rfm.attrs["snapshot"] = snapshot
    return rfm


def score_rfm(rfm: pd.DataFrame) -> pd.DataFrame:
    """Add r_score, m_score, f_score (1-5) using NTILE semantics."""
    out = rfm.copy()
    # R: oldest first -> NTILE 1 = least recent, 5 = most recent
    by_recency = out.sort_values(["recency_days", "customer_unique_id"], ascending=[False, True])
    out["r_score"] = ntile(by_recency)
    # M: lowest spend first -> NTILE 1 = lowest, 5 = highest
    by_money = out.sort_values(["monetary", "customer_unique_id"], ascending=[True, True])
    out["m_score"] = ntile(by_money)
    # F: rule-based (see module docstring) - 1 order = 1, 2 orders = 4, 3+ orders = 5
    out["f_score"] = np.select([out["frequency"] >= 3, out["frequency"] == 2], [5, 4], default=1)
    out["rfm_code"] = (out["r_score"].astype(str) + out["f_score"].astype(str)
                       + out["m_score"].astype(str))
    return out


def assign_segment(r: int, frequency: int, m: int) -> str:
    """Rule table SEGMENT_RULES, evaluated top to bottom (mutually exclusive, exhaustive)."""
    if frequency >= 2:
        return "Champions" if r >= 4 else "Loyal Customers"
    if r == 5:
        return "New Customers"
    if r == 4:
        return "Potential Loyalists"
    if r == 3:
        return "Need Attention"
    return "At Risk" if m >= 4 else "Hibernating"


def segment_customers(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    out["segment"] = [assign_segment(r, f, m) for r, f, m in
                      zip(out["r_score"], out["frequency"], out["m_score"])]
    out["segment"] = pd.Categorical(out["segment"], categories=SEGMENT_ORDER, ordered=True)
    return out


def segment_summary(segmented: pd.DataFrame) -> pd.DataFrame:
    total_customers, total_revenue = len(segmented), segmented["monetary"].sum()
    summary = (segmented.groupby("segment", observed=False)
               .agg(customers=("customer_unique_id", "size"),
                    revenue=("monetary", "sum"),
                    avg_recency_days=("recency_days", "mean"),
                    avg_orders=("frequency", "mean"),
                    avg_monetary=("monetary", "mean"),
                    median_monetary=("monetary", "median")))
    summary.insert(1, "customer_pct", summary["customers"] / total_customers * 100)
    summary.insert(3, "revenue_pct", summary["revenue"] / total_revenue * 100)
    return summary.round(2)


def score_boundaries(scored: pd.DataFrame) -> pd.DataFrame:
    """What each score actually means in days / R$ (for the report and the dashboard)."""
    rows = []
    for score in range(1, 6):
        r = scored.loc[scored["r_score"] == score, "recency_days"]
        m = scored.loc[scored["m_score"] == score, "monetary"]
        rows.append({"score": score,
                     "recency_days_range": f"{r.min()}-{r.max()}",
                     "monetary_range": f"R${m.min():,.2f} - R${m.max():,.2f}",
                     "customers_r": len(r), "customers_m": len(m)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Outputs: charts, report, MySQL table
# --------------------------------------------------------------------------- #
def make_charts(segmented: pd.DataFrame, summary: pd.DataFrame) -> list[str]:
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from src.analysis.eda import COLORS, PCT_FORMATTER, SEQUENTIAL_BLUES, add_subtitle, apply_style, save_chart

    apply_style()
    saved = []

    # 1. Customers vs revenue share per segment: two aligned panels, same order.
    s = summary.iloc[::-1]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    for ax, col, title in [(ax1, "customer_pct", "Share of customers"),
                           (ax2, "revenue_pct", "Share of revenue")]:
        ax.barh(s.index.astype(str), s[col], color=COLORS["primary"], height=0.6)
        for y, v in enumerate(s[col]):
            ax.text(v + 0.8, y, f"{v:.1f}%", va="center", fontsize=9)
        ax.set_xlim(0, max(summary["customer_pct"].max(), summary["revenue_pct"].max()) * 1.18)
        ax.xaxis.set_major_formatter(PCT_FORMATTER)
        ax.grid(axis="x"); ax.grid(axis="y", visible=False)
        ax.set_title(title, fontsize=11)
    fig.suptitle("Which customer segments hold the value?", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")
    fig.text(0.01, 0.905, "RFM segments: share of customers (left) vs share of revenue (right)",
             fontsize=9, color=COLORS["text_muted"])
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    saved.append(save_chart(fig, "10_rfm_segment_share").name)
    plt.close(fig)

    # 2. How the segment rules carve up the R x M grid (one-time buyers).
    #    Colour = segment (categorical, fixed palette order); number = customers in cell.
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    one_time_segments = ["New Customers", "Potential Loyalists", "Need Attention", "At Risk", "Hibernating"]
    palette = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]     # validated slots 1-5
    one_time = segmented[segmented["frequency"] == 1]
    counts = pd.crosstab(one_time["m_score"], one_time["r_score"]).sort_index(ascending=False)
    seg_idx = np.array([[one_time_segments.index(assign_segment(r, 1, m)) for r in counts.columns]
                        for m in counts.index])
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.imshow(seg_idx, cmap=ListedColormap(palette), vmin=-0.5, vmax=4.5)
    for i, m in enumerate(counts.index):
        for j, r in enumerate(counts.columns):
            ax.text(j, i, f"{counts.loc[m, r]:,}", ha="center", va="center",
                    fontsize=9, color=COLORS["text"])
    # 2px surface-coloured gaps between cells
    for k in range(6):
        ax.axhline(k - 0.5, color=COLORS["surface"], linewidth=2)
        ax.axvline(k - 0.5, color=COLORS["surface"], linewidth=2)
    ax.set_xticks(range(5)); ax.set_xticklabels(counts.columns)
    ax.set_yticks(range(5)); ax.set_yticklabels(counts.index)
    ax.set_xlabel("Recency score (5 = most recent)"); ax.set_ylabel("Monetary score (5 = highest spend)")
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(handles=[Patch(color=c, label=s) for s, c in zip(one_time_segments, palette)],
              loc="center left", bbox_to_anchor=(1.02, 0.5), title="Segment")
    repeat = int((segmented["frequency"] >= 2).sum())
    ax.set_title("How do the segment rules divide one-time buyers?")
    add_subtitle(ax, f"Customers per Recency x Monetary cell; {repeat:,} repeat buyers "
                     "(Champions, Loyal) shown separately")
    saved.append(save_chart(fig, "10_rfm_recency_monetary_grid").name)
    plt.close(fig)
    return saved


def write_report(segmented: pd.DataFrame, summary: pd.DataFrame, bounds: pd.DataFrame,
                 snapshot: pd.Timestamp) -> None:
    freq = segmented["frequency"].clip(upper=4).value_counts().sort_index()
    freq_df = pd.DataFrame({"orders": ["1", "2", "3", "4+"], "customers": freq.values,
                            "pct": (freq.values / freq.sum() * 100).round(2)})
    md = [
        "# RFM Customer Segmentation - Results",
        "",
        f"_Auto-generated by `src/analysis/rfm.py` on {datetime.now():%Y-%m-%d %H:%M}. "
        "Method and reasoning: `docs/rfm.md`._",
        "",
        f"- Snapshot date: **{snapshot:%Y-%m-%d}** (day after the last purchase)",
        f"- Customers scored: **{len(segmented):,}** (`customer_unique_id` with at least one revenue order with items)",
        f"- Revenue covered: **R${segmented['monetary'].sum():,.2f}**",
        "",
        "## Why frequency is rule-based",
        "",
        df_to_markdown(freq_df),
        "With most customers on exactly 1 order, frequency quintiles would put the same value in "
        "four of the five groups, so F is used as one-time (F=1), 2 orders (F=4) or 3+ orders (F=5).",
        "",
        "## What each score means",
        "",
        df_to_markdown(bounds),
        "## Segment rules",
        "",
        df_to_markdown(SEGMENT_RULES),
        "## Segment summary",
        "",
        df_to_markdown(summary, index=True),
        "## Charts",
        "",
        "![Segment share](charts/10_rfm_segment_share.png)",
        "",
        "![Recency x Monetary grid](charts/10_rfm_recency_monetary_grid.png)",
        "",
    ]
    (REPORTS_DIR / "rfm_segmentation.md").write_text("\n".join(md), encoding="utf-8")


def to_mysql(segmented: pd.DataFrame) -> int:
    """Replace table customer_rfm (used by the Power BI customer page)."""
    from sqlalchemy import text
    from src.database.connection import get_engine

    engine = get_engine()
    cols = ["customer_unique_id", "last_purchase", "recency_days", "frequency", "monetary",
            "r_score", "f_score", "m_score", "rfm_code", "segment"]
    data = segmented[cols].astype({"segment": str})
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS customer_rfm"))
        conn.execute(text("""
            CREATE TABLE customer_rfm (
                customer_unique_id CHAR(32)      NOT NULL,
                last_purchase      DATE          NOT NULL,
                recency_days       SMALLINT UNSIGNED NOT NULL,
                frequency          SMALLINT UNSIGNED NOT NULL,
                monetary           DECIMAL(10,2) NOT NULL,
                r_score            TINYINT UNSIGNED NOT NULL,
                f_score            TINYINT UNSIGNED NOT NULL,
                m_score            TINYINT UNSIGNED NOT NULL,
                rfm_code           CHAR(3)       NOT NULL,
                segment            VARCHAR(32)   NOT NULL,
                PRIMARY KEY (customer_unique_id),
                INDEX idx_rfm_segment (segment),
                CONSTRAINT chk_rfm_scores CHECK (r_score BETWEEN 1 AND 5 AND f_score BETWEEN 1 AND 5
                                                 AND m_score BETWEEN 1 AND 5)
            ) ENGINE = InnoDB COMMENT = 'Built by src/analysis/rfm.py'"""))
    data.to_sql("customer_rfm", engine, if_exists="append", index=False, chunksize=2000, method="multi")
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM customer_rfm")).scalar_one())


def run(load_mysql: bool = False) -> pd.DataFrame:
    from src.analysis.eda import load_clean

    t = load_clean()
    rfm = compute_rfm_table(t["orders"], t["order_items"], t["customers"])
    snapshot = rfm.attrs["snapshot"]
    segmented = segment_customers(score_rfm(rfm))
    summary = segment_summary(segmented)
    bounds = score_boundaries(segmented)

    segmented.to_csv(PROCESSED_DIR / "customer_rfm.csv", index=False)
    make_charts(segmented, summary)
    write_report(segmented, summary, bounds, snapshot)
    logger.info("Scored %d customers, snapshot %s", len(segmented), snapshot.date())
    logger.info("\n%s", summary[["customers", "customer_pct", "revenue_pct", "avg_monetary"]])
    if load_mysql:
        logger.info("Loaded customer_rfm into MySQL: %d rows", to_mysql(segmented))
    return segmented


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="RFM customer segmentation")
    parser.add_argument("--to-mysql", action="store_true", help="also load table customer_rfm")
    args = parser.parse_args()
    run(load_mysql=args.to_mysql)
    return 0


if __name__ == "__main__":
    sys.exit(main())
