"""Build the 5-page Marketplace Analytics dashboard as Power BI report (PBIR) files.

Run from the project root, with Power BI Desktop CLOSED:
    python -m src.reporting.build_dashboard            # write the report
    python -m src.reporting.build_dashboard --dry-run  # show what would change

What it does
  * Executive Overview (the page built by hand) is preserved and upgraded:
    single-measure KPI tables become cards, an exact duplicate is removed, the
    existing charts are kept and re-positioned, missing visuals are added.
  * Pages 2-5 are generated from the specification in powerbi/dashboard_guide.md.
  * Every page gets the same 5 slicers, synced across pages (syncGroup).
  * Only measures and columns that exist in the semantic model are used
    (checked by tests/test_dashboard.py against the TMDL files).
  * Visual ids are derived from stable keys, so re-running updates the same
    visuals instead of duplicating them.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT
from src.reporting.pbir import (
    PAGE_SCHEMA, PAGES_SCHEMA, VISUAL_SCHEMA, color, column, field_name, in_filter, lit, measure,
    projection, props, sort_by, stable_id, top_n_filter,
)

REPORT_DIR = PROJECT_ROOT / "powerbi" / "marketplace_analytics.Report" / "definition"
EXEC_PAGE_NAME = "Executive Overview"

# --- Layout grid for a 1920 x 1080 page -------------------------------------
MARGIN, GAP, PAGE_W, PAGE_H = 40, 16, 1920, 1080
CONTENT_W = PAGE_W - 2 * MARGIN
SLICER_Y, SLICER_H = 104, 64
CARD_Y, CARD_H = 184, 112
ROW1_Y, ROW_H = 312, 360
ROW2_Y = ROW1_Y + ROW_H + GAP                      # 688 -> ends at 1048

TEXT = "#0B0B0B"
MUTED = "#52514E"
COMPLETE_MONTHS = ("dim_date", "Analysis Window", ["Complete months"])


# --------------------------------------------------------------------------- #
# Visual specifications
# --------------------------------------------------------------------------- #
@dataclass
class Visual:
    key: str                     # stable identity within the page
    x: float
    y: float
    w: float
    h: float
    body: dict                   # the "visual" object
    title: str | None = None
    subtitle: str | None = None
    filters: list[dict] = field(default_factory=list)
    container_objects: dict = field(default_factory=dict)


def columns_across(n: int, y: float, h: float, x0: float = MARGIN, width: float = CONTENT_W):
    w = (width - GAP * (n - 1)) / n
    return [(x0 + i * (w + GAP), y, w, h) for i in range(n)]


def card_objects() -> dict:
    # labelDisplayUnits = 1 -> show the full number (R$ 15,735,527), not "R$ 16M",
    # so every card can be checked against reports/powerbi_expected_values.md.
    return {"labels": props(fontSize=26, color=color(TEXT), labelDisplayUnits=1),
            "categoryLabels": props(show=False)}


def card(key: str, measure_name: str, title: str, pos) -> Visual:
    body = {
        "visualType": "card",
        "query": {"queryState": {"Values": {"projections": [projection(measure(measure_name))]}}},
        "objects": card_objects(),
    }
    return Visual(key, *pos, body=body, title=title)


def chart(key: str, visual_type: str, category: list[dict], values: list[dict], pos, title: str,
          subtitle: str | None = None, sort: dict | None = None, value_role: str = "Y",
          data_labels: bool = False, filters: list[dict] | None = None, legend: bool = False,
          exact_labels: bool = False) -> Visual:
    query = {"queryState": {
        "Category": {"projections": [projection(c, active=True) for c in category]},
        value_role: {"projections": [projection(v) for v in values]},
    }}
    if sort:
        query["sortDefinition"] = sort
    objects = {"legend": props(show=legend)}
    if data_labels:
        # exact_labels: counts like 188 would otherwise display as "0K"
        objects["labels"] = props(show=True, labelDisplayUnits=1) if exact_labels else props(show=True)
    return Visual(key, *pos, body={"visualType": visual_type, "query": query, "objects": objects},
                  title=title, subtitle=subtitle, filters=filters or [])


def table(key: str, fields: list[dict], pos, title: str, sort: dict | None = None,
          filters: list[dict] | None = None, subtitle: str | None = None) -> Visual:
    query = {"queryState": {"Values": {"projections": [projection(f) for f in fields]}}}
    if sort:
        query["sortDefinition"] = sort
    return Visual(key, *pos, body={"visualType": "tableEx", "query": query},
                  title=title, subtitle=subtitle, filters=filters or [])


def scatter(key: str, details: dict, x: str, y: str, size: str, pos, title: str, subtitle: str) -> Visual:
    query = {"queryState": {
        "Category": {"projections": [projection(details, active=True)]},
        "X": {"projections": [projection(measure(x))]},
        "Y": {"projections": [projection(measure(y))]},
        "Size": {"projections": [projection(measure(size))]},
    }}
    # Category labels overlap badly with ~50 bubbles; names are shown on hover instead.
    return Visual(key, *pos, body={"visualType": "scatterChart", "query": query,
                                   "objects": {"categoryLabels": props(show=False)}},
                  title=title, subtitle=subtitle)


def textbox(key: str, text: str, pos, size: str = "11pt", bold: bool = False, colour: str = MUTED) -> Visual:
    style = {"fontSize": size, "color": colour}
    if bold:
        style["fontWeight"] = "bold"
    body = {"visualType": "textbox",
            "objects": {"general": [{"properties": {"paragraphs": [
                {"textRuns": [{"value": text, "textStyle": style}]}]}}]}}
    return Visual(key, *pos, body=body, container_objects={
        "border": props(show=False), "background": props(show=False), "title": props(show=False)})


SLICERS = [  # key, fields, header, mode
    ("date", [column("dim_date", "Date")], "Date", "Between"),
    ("category", [column("dim_product", "category")], "Product Category", "Dropdown"),
    ("state", [column("dim_state", "region"), column("dim_state", "state_name")], "Region / State", "Dropdown"),
    ("status", [column("fact_orders", "order_status")], "Order Status", "Dropdown"),
    ("payment", [column("fact_orders", "main_payment_type")], "Payment Type", "Dropdown"),
]


def slicers() -> list[Visual]:
    out = []
    for (key, fields, header, mode), pos in zip(SLICERS, columns_across(len(SLICERS), SLICER_Y, SLICER_H)):
        body = {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [
                projection(f, active=True, display_name=header if len(fields) == 1 else None)
                for f in fields]}}},
            "objects": {"data": props(mode=mode), "header": props(show=True, text=header)},
            # Same groupName on every page = synced slicers
            "syncGroup": {"groupName": f"slicer_{key}", "fieldChanges": True, "filterChanges": True},
        }
        out.append(Visual(f"slicer_{key}", *pos, body=body))
    return out


def header(page_title: str, insight: str) -> list[Visual]:
    return [
        textbox("hdr_brand", "MARKETPLACE ANALYTICS", (MARGIN, 20, 520, 36), size="18pt", bold=True, colour=TEXT),
        textbox("hdr_page", page_title, (MARGIN, 58, 700, 30), size="12pt", colour=MUTED),
        textbox("hdr_insight", "Key insight (all data, no filters): " + insight,
                (820, 24, PAGE_W - 820 - MARGIN, 64), size="11pt", colour=MUTED),
    ]


def window_filter(name: str) -> dict:
    entity, prop, values = COMPLETE_MONTHS
    return in_filter(entity, prop, values, name)


def month_axis() -> dict:
    return column("dim_date", "Month Start")


# --------------------------------------------------------------------------- #
# Page specifications
# --------------------------------------------------------------------------- #
INSIGHTS = {  # every figure is from reports/business_insights.md (calculated, no filters)
    "exec": "Revenue R$15.74M. Jan-Aug 2018 was +140.4% vs Jan-Aug 2017, but monthly revenue "
            "has been flat at R$0.98-1.16M since Jan 2018.",
    "product": "The top 10 of 74 categories make 62.4% of product revenue. Revenue and average "
               "rating are unrelated (r = 0.00); office_furniture is rated lowest (3.62).",
    "customer": "97.0% of customers bought once (repeat rate 3.04%). 'At Risk' customers are 14.7% "
                "of customers but 27.5% of revenue.",
    "region": "SP = 37.4% of revenue. AL, MA and SE are late 15-21% of the time; RJ has the most "
              "late orders (1,495). North takes 26-29 days.",
    "review": "Late or undelivered orders cause 42.9% of all 1-2 star reviews; the average score "
              "falls from 4.31 (7+ days early) to 1.70 (8+ days late).",
}


def page_exec_new_visuals() -> list[Visual]:
    """Visuals added to the existing Executive Overview page."""
    return [
        chart("exec_region", "clusteredBarChart", [column("dim_state", "region")], [measure("Total Revenue")],
              (MARGIN + 1112 + GAP, ROW1_Y, CONTENT_W - 1112 - GAP, ROW_H),
              "Which regions generate the revenue?", "Total revenue by customer region",
              sort=sort_by(measure("Total Revenue")), data_labels=True),
        textbox("hdr_insight", "Key insight (all data, no filters): " + INSIGHTS["exec"],
                (820, 24, PAGE_W - 820 - MARGIN, 64)),
        *slicers(),
    ]


def page_product() -> list[Visual]:
    cards = columns_across(3, CARD_Y, CARD_H)
    return [
        *header("Product and Category Analysis", INSIGHTS["product"]), *slicers(),
        card("c_prod_rev", "Product Revenue", "Product Revenue", cards[0]),
        card("c_units", "Units Sold", "Units Sold", cards[1]),
        card("c_item_price", "Avg Item Price", "Average Item Price", cards[2]),
        chart("top_categories", "clusteredBarChart", [column("dim_product", "category")],
              [measure("Product Revenue")], (MARGIN, ROW1_Y, 900, ROW_H),
              "Which categories generate the most revenue?", "Top 10 categories by product revenue",
              sort=sort_by(measure("Product Revenue")), data_labels=True,
              filters=[top_n_filter("dim_product", "category", "Product Revenue", 10, stable_id("f", "topcat"))]),
        scatter("cat_scatter", column("dim_product", "category"), "Product Revenue", "Avg Review Score",
                "Units Sold", (MARGIN + 900 + GAP, ROW1_Y, CONTENT_W - 900 - GAP, ROW_H),
                "Do the biggest categories also satisfy customers?",
                "Product revenue vs average review score (bubble size = units sold; hover for category)"),
        table("cat_table", [column("dim_product", "category"), measure("Product Revenue"),
                            measure("Category Revenue Share %"), measure("Units Sold"),
                            measure("Avg Item Price"), measure("Avg Review Score"), measure("Category Rank")],
              (MARGIN, ROW2_Y, 1100, ROW_H), "Category performance",
              sort=sort_by(measure("Product Revenue")),
              subtitle="Share is of all categories; rank 1 = highest product revenue"),
        table("top_products", [column("dim_product", "product_id"), column("dim_product", "category"),
                               measure("Product Revenue"), measure("Units Sold")],
              (MARGIN + 1100 + GAP, ROW2_Y, CONTENT_W - 1100 - GAP, ROW_H), "Top 10 products",
              sort=sort_by(measure("Product Revenue")),
              filters=[top_n_filter("dim_product", "product_id", "Product Revenue", 10, stable_id("f", "topprod"))],
              subtitle="The dataset has no product names, only ids"),
    ]


def page_customer() -> list[Visual]:
    cards = columns_across(4, CARD_Y, CARD_H)
    return [
        *header("Customer Analytics", INSIGHTS["customer"]), *slicers(),
        card("c_customers", "Total Customers", "Total Customers", cards[0]),
        card("c_repeat", "Repeat Purchase Rate %", "Repeat Purchase Rate", cards[1]),
        # Returning Customer % is 0% without a date filter (everyone's first order is inside the
        # period), so the all-period card shows the repeat-customer count instead.
        card("c_repeat_n", "Repeat Customers", "Repeat Customers (2+ orders)", cards[2]),
        card("c_orders_per", "Orders per Customer", "Orders per Customer", cards[3]),
        chart("new_vs_returning", "columnChart", [month_axis()],
              [measure("New Customers"), measure("Returning Customers")], (MARGIN, ROW1_Y, 1112, ROW_H),
              "Are we keeping the customers we win?", "New vs returning customers per month (complete months)",
              sort=sort_by(month_axis(), "Ascending"), legend=True,
              filters=[window_filter(stable_id("f", "win_nvr"))]),
        chart("frequency", "clusteredBarChart", [column("dim_customer", "frequency_bucket")],
              [measure("Total Customers")], (MARGIN + 1112 + GAP, ROW1_Y, CONTENT_W - 1112 - GAP, ROW_H),
              "How often do customers buy?", "Customers by lifetime number of orders",
              sort=sort_by(column("dim_customer", "frequency_bucket"), "Ascending"), data_labels=True,
              exact_labels=True),
        chart("rfm_share", "clusteredBarChart", [column("dim_customer", "rfm_segment")],
              [measure("Customer Share %"), measure("Customer Revenue Share %")], (MARGIN, ROW2_Y, 900, ROW_H),
              "Which customer segments hold the value?", "RFM segments: share of customers vs share of revenue",
              sort=sort_by(column("dim_customer", "rfm_segment"), "Ascending"), legend=True, data_labels=True),
        table("rfm_table", [column("dim_customer", "rfm_segment"), measure("Total Customers"),
                            measure("Total Revenue"), measure("Average Order Value"),
                            measure("Orders per Customer")],
              (MARGIN + 900 + GAP, ROW2_Y, CONTENT_W - 900 - GAP, ROW_H), "Segment detail",
              sort=sort_by(column("dim_customer", "rfm_segment"), "Ascending"),
              subtitle="Segment rules: docs/rfm.md"),
    ]


def page_region() -> list[Visual]:
    cards = columns_across(4, CARD_Y, CARD_H)
    left_w = 600
    return [
        *header("Regional and Delivery Analytics", INSIGHTS["region"]), *slicers(),
        card("c_delivery", "Avg Delivery Days", "Avg Delivery Days", cards[0]),
        card("c_promised", "Avg Promised Days", "Avg Promised Days", cards[1]),
        card("c_late_pct", "Late Delivery %", "Late Delivery %", cards[2]),
        card("c_late_n", "Late Deliveries", "Late Deliveries", cards[3]),
        chart("state_revenue", "clusteredBarChart", [column("dim_state", "state_name")],
              [measure("Total Revenue")], (MARGIN, ROW1_Y, left_w, ROW_H * 2 + GAP),
              "Which states generate the revenue?", "Total revenue by customer state",
              sort=sort_by(measure("Total Revenue"))),
        chart("late_by_month", "lineChart", [month_axis()], [measure("Late Delivery %")],
              (MARGIN + left_w + GAP, ROW1_Y, CONTENT_W - left_w - GAP, ROW_H),
              "Is delivery reliability getting better or worse?",
              "Share of valid deliveries that arrived late, by purchase month (complete months)",
              sort=sort_by(month_axis(), "Ascending"), filters=[window_filter(stable_id("f", "win_late"))]),
        table("state_table", [column("dim_state", "state_name"), column("dim_state", "region"),
                              measure("Total Revenue"), measure("State Revenue Share %"),
                              measure("Avg Delivery Days"), measure("Late Delivery %")],
              (MARGIN + left_w + GAP, ROW2_Y, CONTENT_W - left_w - GAP, ROW_H), "State performance",
              sort=sort_by(measure("Total Revenue"))),
    ]


def page_review() -> list[Visual]:
    cards = columns_across(4, CARD_Y, CARD_H)
    return [
        *header("Payment and Review Analysis", INSIGHTS["review"]), *slicers(),
        card("c_review", "Avg Review Score", "Avg Review Score", cards[0]),
        card("c_negative", "Negative Review %", "Negative Reviews (1-2 stars)", cards[1]),
        card("c_cancel", "Cancellation %", "Cancellation %", cards[2]),
        card("c_payment", "Payment Value", "Payment Value", cards[3]),
        chart("review_vs_delivery", "clusteredColumnChart", [column("fact_orders", "delivery_outcome")],
              [measure("Avg Review Score")], (MARGIN, ROW1_Y, 1112, ROW_H),
              "Does late delivery hurt review scores?", "Average review score by delivery outcome vs promised date",
              sort=sort_by(column("fact_orders", "delivery_outcome"), "Ascending"), data_labels=True),
        chart("review_dist", "clusteredColumnChart", [column("fact_orders", "review_score")],
              [measure("Review Share %")], (MARGIN + 1112 + GAP, ROW1_Y, CONTENT_W - 1112 - GAP, ROW_H),
              "How satisfied are customers?", "Share of reviews by score (one review per order)",
              sort=sort_by(column("fact_orders", "review_score"), "Ascending"), data_labels=True,
              filters=[in_filter("fact_orders", "review_score", [1, 2, 3, 4, 5], stable_id("f", "scores"))]),
        chart("payment_methods", "clusteredBarChart", [column("fact_payments", "payment_type")],
              [measure("Orders Using Method")], (MARGIN, ROW2_Y, 900, ROW_H),
              "How do customers pay?", "Orders using each payment method (an order can use two)",
              sort=sort_by(measure("Orders Using Method")), data_labels=True, exact_labels=True),
        chart("order_status", "clusteredBarChart", [column("fact_orders", "order_status")],
              [measure("Order Status Share %")], (MARGIN + 900 + GAP, ROW2_Y, CONTENT_W - 900 - GAP, ROW_H),
              "What happens to orders?", "Share of all orders by final status",
              sort=sort_by(measure("Order Status Share %")), data_labels=True),
    ]


GENERATED_PAGES = [  # (display name, builder)
    ("Product and Category Analysis", page_product),
    ("Customer Analytics", page_customer),
    ("Regional and Delivery Analytics", page_region),
    ("Payment and Review Analysis", page_review),
]


# --------------------------------------------------------------------------- #
# Writing PBIR files
# --------------------------------------------------------------------------- #
def container_json(page_key: str, v: Visual, z: int) -> dict:
    name = stable_id(page_key, v.key)
    vco = dict(v.container_objects)
    if v.title:
        vco["title"] = props(show=True, text=v.title)
    if v.subtitle:
        vco["subTitle"] = props(show=True, text=v.subtitle)
    body = dict(v.body)
    body["drillFilterOtherVisuals"] = True
    out = {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": round(v.x, 2), "y": round(v.y, 2), "z": z,
                     "height": round(v.h, 2), "width": round(v.w, 2), "tabOrder": z},
        "visual": body,
    }
    if vco:
        out["visual"]["visualContainerObjects"] = vco
    if v.filters:
        out["filterConfig"] = {"filters": v.filters}
    return out


def write_json(path: Path, data: dict, dry_run: bool, log: list[str]) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    log.append(("update " if path.exists() else "create ") + str(path.relative_to(REPORT_DIR)))
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def load_visuals(page_dir: Path) -> dict[str, dict]:
    return {p.parent.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(page_dir.glob("visuals/*/visual.json"))}


def single_measure(v: dict) -> str | None:
    """Measure name if the visual shows exactly one measure, else None."""
    projs = [p for role in v["visual"].get("query", {}).get("queryState", {}).values()
             for p in role.get("projections", [])]
    if len(projs) == 1 and "Measure" in projs[0]["field"]:
        return projs[0]["field"]["Measure"]["Property"]
    return None


EXEC_CARD_ORDER = ["Total Revenue", "Sales Orders", "Average Order Value", "Total Customers", "Revenue YoY %"]
EXEC_CARD_TITLES = {"Revenue YoY %": "Revenue YoY % (Jan-Aug 2018 vs 2017)"}
YOY_MONTHS = [f"2018-0{m}" for m in range(1, 9)]
MOM_MONTHS = [f"2017-{m:02d}" for m in range(2, 13)] + [f"2018-0{m}" for m in range(1, 9)]


def upgrade_exec_page(page_dir: Path, page_key: str, dry_run: bool, log: list[str]) -> None:
    """Preserve the hand-built Executive Overview; upgrade and complete it."""
    generated = {stable_id(page_key, nv.key) for nv in page_exec_new_visuals()}
    # Only hand-made visuals are upgraded here; the script's own visuals are written below.
    visuals = {k: v for k, v in load_visuals(page_dir).items() if k not in generated}
    seen_measures: set[str] = set()
    cards = dict(zip(EXEC_CARD_ORDER, columns_across(len(EXEC_CARD_ORDER), CARD_Y, CARD_H)))
    z = 100

    for name, v in visuals.items():
        vt = v["visual"]["visualType"]
        m = single_measure(v)
        path = page_dir / "visuals" / name / "visual.json"
        if vt in ("tableEx", "card") and m in cards:
            if m in seen_measures:                       # exact duplicate KPI -> remove
                log.append(f"remove duplicate {m} visual {name} (copy kept in powerbi/backups/)")
                if not dry_run:
                    shutil.rmtree(path.parent)
                continue
            seen_measures.add(m)
            x, y, w, h = cards[m]
            v["visual"]["visualType"] = "card"
            v["visual"]["query"].pop("sortDefinition", None)
            v["visual"]["objects"] = card_objects()
            v["visual"].setdefault("visualContainerObjects", {})
            v["visual"]["visualContainerObjects"]["title"] = props(show=True, text=EXEC_CARD_TITLES.get(m, m))
            v["visual"]["visualContainerObjects"].pop("border", None)
            v["position"].update({"x": round(x, 2), "y": CARD_Y, "width": round(w, 2), "height": CARD_H})
            if m == "Revenue YoY %":       # YoY needs a defined period; label says which
                v["filterConfig"] = {"filters": [in_filter("dim_date", "Year Month", YOY_MONTHS,
                                                           stable_id("f", "yoy_period"))]}
        elif vt == "lineChart":
            v["position"].update({"x": MARGIN, "y": ROW1_Y, "width": 1112, "height": ROW_H})
            v["visual"].setdefault("visualContainerObjects", {})
            v["visual"]["visualContainerObjects"]["title"] = props(show=True, text="How has revenue developed?")
            v["visual"]["visualContainerObjects"]["subTitle"] = props(
                show=True, text="Monthly total revenue (complete months 2017-01 to 2018-08)")
        elif vt == "clusteredColumnChart":
            v["position"].update({"x": MARGIN, "y": ROW2_Y, "width": CONTENT_W, "height": ROW_H})
            v["visual"].setdefault("visualContainerObjects", {})
            v["visual"]["visualContainerObjects"]["title"] = props(show=True, text="Month-over-month revenue growth")
            v["visual"]["visualContainerObjects"]["subTitle"] = props(
                show=True, text="Revenue MoM %, Feb 2017 to Aug 2018 (Jan 2017 is excluded: "
                                "its previous month, Dec 2016, had only 1 order)")
            # Jan 2017 compared with near-empty Dec 2016 showed ~+600,000%. SQL Q18 leaves it
            # blank, so the chart shows Feb 2017 - Aug 2018 only (same months as Q18's growth values).
            filters = v.setdefault("filterConfig", {}).setdefault("filters", [])
            filters[:] = [f for f in filters if f["name"] != stable_id("f", "win_mom")]
            name = stable_id("f", "mom_months")
            filters[:] = [f for f in filters if f["name"] != name] + [
                in_filter("dim_date", "Year Month", MOM_MONTHS, name)]
        elif vt == "textbox":
            run = v["visual"]["objects"]["general"][0]["properties"]["paragraphs"][0]["textRuns"][0]
            brand = run["value"].isupper()
            # same header style as the generated pages; the text itself is kept
            run["textStyle"] = ({"fontSize": "18pt", "color": TEXT, "fontWeight": "bold"} if brand
                                else {"fontSize": "12pt", "color": MUTED})
            v["position"].update({"x": MARGIN, "y": 20 if brand else 58,
                                  "width": 520 if brand else 700, "height": 36 if brand else 30})
        else:
            continue
        v["position"]["z"] = v["position"]["tabOrder"] = z
        z += 1
        write_json(path, v, dry_run, log)

    for i, nv in enumerate(page_exec_new_visuals()):
        out = container_json(page_key, nv, z + i)
        write_json(page_dir / "visuals" / out["name"] / "visual.json", out, dry_run, log)

    # The page-level "Complete months" filter also filtered the KPI cards; trend
    # charts now carry that filter themselves, so the cards show all-data totals.
    page_json = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
    if page_json.pop("filterConfig", None) is not None:
        log.append("remove page-level 'Complete months' filter from Executive Overview (moved to trend charts)")
        write_json(page_dir / "page.json", page_json, dry_run, log)


def page_json(page_id: str, display_name: str, template: dict) -> dict:
    return {
        "$schema": PAGE_SCHEMA,
        "name": page_id,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "height": PAGE_H,
        "width": PAGE_W,
        "pageBinding": {"name": stable_id("binding", page_id), "type": "Default", "parameters": []},
        "objects": template.get("objects", {}),
    }


def build(report_dir: Path = REPORT_DIR, dry_run: bool = False) -> list[str]:
    log: list[str] = []
    pages_meta = json.loads((report_dir / "pages" / "pages.json").read_text(encoding="utf-8"))
    pages = {pid: json.loads((report_dir / "pages" / pid / "page.json").read_text(encoding="utf-8"))
             for pid in pages_meta["pageOrder"]}
    exec_id = next((pid for pid, p in pages.items() if p["displayName"] == EXEC_PAGE_NAME), None)
    if exec_id is None:
        raise SystemExit(f"Page '{EXEC_PAGE_NAME}' not found - is this the right report?")

    upgrade_exec_page(report_dir / "pages" / exec_id, exec_id, dry_run, log)

    order = [exec_id]
    for display_name, builder in GENERATED_PAGES:
        page_id = stable_id("page", display_name)
        page_dir = report_dir / "pages" / page_id
        if page_dir.exists() and not dry_run:
            shutil.rmtree(page_dir)                   # generated pages are fully owned by this script
        write_json(page_dir / "page.json", page_json(page_id, display_name, pages[exec_id]), dry_run, log)
        for z, v in enumerate(builder()):
            out = container_json(page_id, v, z)
            write_json(page_dir / "visuals" / out["name"] / "visual.json", out, dry_run, log)
        order.append(page_id)

    # keep any other user-made pages, after the five dashboard pages
    order += [pid for pid in pages_meta["pageOrder"] if pid not in order]
    new_meta = {"$schema": PAGES_SCHEMA, "pageOrder": order, "activePageName": exec_id}
    write_json(report_dir / "pages" / "pages.json", new_meta, dry_run, log)
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Power BI dashboard pages (PBIR)")
    parser.add_argument("--dry-run", action="store_true", help="only list the changes")
    args = parser.parse_args()
    log = build(dry_run=args.dry_run)
    print("\n".join(log) if log else "No changes.")
    print(f"{len(log)} change(s){' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
