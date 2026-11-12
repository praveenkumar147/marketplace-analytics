# Power BI Dashboard - Build Guide

This guide builds the 5-page **Marketplace Analytics** dashboard in Power BI
Desktop. The data model, SQL views, DAX measures and theme are already prepared
and verified. This file walks you through assembling them. Budget about 2-3
hours the first time.

> **Why there's no .pbix in the repo yet:** a `.pbix` can't be generated reliably
> outside Power BI Desktop. Everything it needs is scripted and tested here, so
> you build the visuals once and save the file as `powerbi/marketplace_analytics.pbix`.

| File | Purpose |
|---|---|
| `sql/views/01_powerbi_views.sql` | The 7 reporting views (star schema) Power BI reads |
| `powerbi/measures/measures.dax` | Every DAX measure plus the `dim_date` table, with formats |
| `powerbi/theme/marketplace_theme.json` | Colours, fonts and card styling |
| `reports/powerbi_expected_values.md` | The numbers your cards must show (your test) |

---

## Step 0. Prepare the data (once)

From the project root:

```bash
python -m src.database.powerbi --export-csv
```

This creates or refreshes the views in MySQL, checks them against the base tables,
writes the expected values, and exports each view to `data/powerbi/*.csv`.

## Step 1. Choose a connection route

**Route A: MySQL (recommended; shows the full pipeline).**
Power BI's MySQL connector needs **MySQL Connector/NET**, which isn't installed on this machine yet.
1. Open **MySQL Installer** (already installed), click **Add...**, go to **MySQL Connectors**, choose **Connector/NET**, and install it.
2. Restart Power BI Desktop.
3. **Home > Get data > MySQL database**. Server: `localhost:3306`, Database: `marketplace_analytics`.
4. Choose **Database** credentials and type your MySQL user and password. They're stored by Power BI, never in this repo.
5. In the Navigator, tick the 7 views `vw_fact_orders`, `vw_fact_sales`, `vw_fact_payments`, `vw_dim_customer`, `vw_dim_product`, `vw_dim_seller`, `vw_dim_state`, then **Transform Data**.

**Route B: CSV (no installs).**
**Home > Get data > Text/CSV** and load each file in `data/powerbi/`, then **Transform Data**.
Refresh the CSVs with Step 0 whenever the data changes.

## Step 2. Power Query: names and data types

1. Rename the queries to: `fact_orders`, `fact_sales`, `fact_payments`, `dim_customer`, `dim_product`, `dim_seller`, `dim_state` (Route A: drop the `vw_` prefix).
2. Check the column types (click the type icon in each column header):

| Table | Columns | Type |
|---|---|---|
| all | every `*_id` column, `customer_state`, `state`, `category`, `order_status`, `main_payment_type`, `payment_type`, `delivery_outcome`, `rfm_segment` | Text |
| fact_orders | `purchase_date` | **Date** (not Date/Time; the relationship to `dim_date` needs it) |
| fact_orders | `order_purchase_timestamp` | Date/Time |
| fact_orders | `is_*`, `in_analysis_window`, `items`, `purchase_hour`, `review_score`, `customer_order_number` | Whole number |
| fact_orders | `order_value`, `product_value`, `freight_value`, `delivery_days`, `estimated_delivery_days`, `delivery_delay_days` | Decimal number |
| fact_sales / fact_payments | `price`, `freight_value`, `line_total`, `payment_value` | Decimal number |
| dim_customer | `lifetime_orders`, `r_score`, `f_score`, `m_score`, `rfm_segment_order` | Whole number |

3. **Close & Apply.**

## Step 3. Theme

**View > Themes > Browse for themes** and select `powerbi/theme/marketplace_theme.json`.
Page canvas: **Format page > Canvas settings > 16:9 (1280 x 720)**.

## Step 4. Date table

1. **Modeling > New table** and paste the `dim_date` block from `measures.dax`.
2. With `dim_date` selected: **Table tools > Mark as date table** and choose `Date`.
3. Sort `dim_date[Month]` by `Month Number` (**Column tools > Sort by column**).

## Step 5. Relationships (Model view)

Delete any relationships Power BI auto-detected, then create exactly these:

| From (one side) | To (many side) | Cardinality | Cross-filter |
|---|---|---|---|
| `dim_date[Date]` | `fact_orders[purchase_date]` | 1 : * | Single |
| `dim_state[state]` | `fact_orders[customer_state]` | 1 : * | Single |
| `dim_customer[customer_unique_id]` | `fact_orders[customer_unique_id]` | 1 : * | Single |
| `fact_orders[order_id]` | `fact_payments[order_id]` | 1 : * | Single |
| `fact_orders[order_id]` | `fact_sales[order_id]` | 1 : * | **Both** |
| `dim_product[product_id]` | `fact_sales[product_id]` | 1 : * | Single |
| `dim_seller[seller_id]` | `fact_sales[seller_id]` | 1 : * | Single |

**Why one relationship is set to "Both":**
- The Category slicer lives on `dim_product`, which filters `fact_sales`.
- For category to also filter order-level measures (delivery, reviews, customers), the filter has to flow on from `fact_sales` up to `fact_orders`.
- This is the only bidirectional link, and every pair of tables has exactly one path, so the model stays unambiguous.

Hide the key columns in report view (right-click, **Hide in report view**) so only meaningful fields show in the field list.

## Step 6. Measures

1. **Home > Enter data**, name the table `_Measures`, and click **Load**.
2. For each measure in `measures.dax`, select `_Measures`, then **Modeling > New measure**, paste it, and set the format from its `[Format]` comment.
3. Delete the dummy `Column1` from `_Measures`. It then shows as a measure table at the top of the field list.

**Check before building visuals.** Put these measures in cards on a scratch page, with no filters:
- Total Revenue **15,735,527.03**
- Average Order Value **160.24**
- Total Customers **94,983**
- Late Delivery % **6.77%**
- Avg Review Score **4.09**

Compare them with `reports/powerbi_expected_values.md`. Fix any mismatch now; it is almost always a data type or relationship mistake.

## Step 7. Slicers (build once, sync to every page)

Put a slicer bar across the top of page 1, then **View > Sync slicers** and tick all 5 pages for each:

| Slicer | Field | Style |
|---|---|---|
| Date | `dim_date[Date]` | Between (date range) |
| Product Category | `dim_product[category]` | Dropdown, search on |
| State | `dim_state[region]` > `dim_state[state_name]` | Dropdown hierarchy |
| Order Status | `fact_orders[order_status]` | Dropdown |
| Payment Type | `fact_orders[main_payment_type]` | Dropdown (the largest payment method of the order) |

On trend visuals, add a **visual-level filter** `dim_date[Analysis Window] = Complete months` so the near-empty months (2016 and Sep-Oct 2018) don't distort the lines. State this in the visual's subtitle.

## Step 8. Pages

Layout grid for 1280 x 720: the slicer bar is at the top (y = 60-110), the KPI cards row is at y = 120-200, and the charts fill the rest. Use the measure name or the question as each visual's title.

### Page 1 - Executive Overview
| Visual | Fields | Notes |
|---|---|---|
| 5 cards | Total Revenue, Sales Orders, Average Order Value, Total Customers, Revenue YoY % | Card titles = measure names |
| Line chart "Monthly revenue" | X `dim_date[Month Start]` (continuous), Y Total Revenue | Analysis Window filter |
| Column chart "Revenue growth vs previous month" | X `dim_date[Month Start]`, Y Revenue MoM % | Conditional colour: negative = `#E34948`, positive = `#2A78D6` |
| Bar chart "Revenue by region" | Y `dim_state[region]`, X Total Revenue | Sort descending; data labels on |

**Check:** with the date slicer set to 2018-01-01 to 2018-08-31, Revenue YoY % = **140.4%**.

### Page 2 - Product and Category Analysis
| Visual | Fields | Notes |
|---|---|---|
| Bar chart "Top 10 categories" | Y `dim_product[category]`, X Product Revenue | Filter: Top N = 10 by Product Revenue |
| Table "Category performance" | category, Product Revenue, Category Revenue Share %, Units Sold, Avg Item Price, Avg Review Score, Category Rank | Sort by Product Revenue; data bars on revenue |
| Table "Top 10 products" | `dim_product[product_id]`, `dim_product[category]`, Product Revenue, Units Sold | Top N = 10 by Product Revenue (the dataset has no product names) |
| Scatter "Revenue vs satisfaction" | Values `dim_product[category]`, X Product Revenue, Y Avg Review Score, Size Units Sold | Add an average line on Y |

**Check:** health_beauty Product Revenue **R$1,255,695.13**, share **9.31%**.

### Page 3 - Customer Analytics
| Visual | Fields | Notes |
|---|---|---|
| 4 cards | Total Customers, Repeat Purchase Rate %, Returning Customer %, Orders per Customer | |
| Stacked column "New vs returning customers" | X `dim_date[Month Start]`, Y New Customers + Returning Customers | Analysis Window filter |
| Bar "Purchase frequency" | Y `dim_customer[frequency_bucket]`, X Total Customers | Data labels = Customer Share % (tooltip) |
| Clustered bar "RFM segments: customers vs revenue" | Y `dim_customer[rfm_segment]`, X Customer Share % and Customer Revenue Share % | Sort `rfm_segment` by `rfm_segment_order` |
| Table "Segment detail" | rfm_segment, Total Customers, Total Revenue, Average Order Value, Orders per Customer | |

**Check:** Repeat Purchase Rate % **3.04%**; Champions **1,235** customers; August 2018 New Customers **6,209** out of **6,380**.

### Page 4 - Regional and Delivery Analytics
| Visual | Fields | Notes |
|---|---|---|
| 3 cards | Avg Delivery Days, Avg Promised Days, Late Delivery % | |
| Filled map "Revenue by state" | Location `dim_state[map_location]`, colour saturation Total Revenue | Set `map_location` Data category = **Place**. If maps are disabled in your tenant, use a bar chart instead. |
| Table "State performance" | state_name, Total Revenue, State Revenue Share %, Avg Delivery Days, Late Delivery % | Conditional formatting on Late Delivery % (background, white to `#E34948`) |
| Line "Late deliveries by month" | X `dim_date[Month Start]`, Y Late Delivery % | Analysis Window filter |

**Check:** SP Late Delivery % **4.50%**; SP share of revenue **37.36%**.

### Page 5 - Payment and Review Analysis
| Visual | Fields | Notes |
|---|---|---|
| 3 cards | Avg Review Score, Negative Review %, Cancellation % | |
| Bar "Payment methods" | Y `fact_payments[payment_type]`, X Orders Using Method | Tooltip: Payment Value Share % |
| Bar "Order status" | Y `fact_orders[order_status]`, X Order Status Share % | |
| Column "Review score distribution" | X `fact_orders[review_score]`, Y Review Share % | |
| Column "Review score vs delivery" | X `fact_orders[delivery_outcome]`, Y Avg Review Score | The key insight: late orders score far lower |

**Check:** Avg Review Score for "6. 8+ days late" = **1.70**; Cancellation % **0.63%**.

## Step 9. Finish and save

1. Turn on **tooltips** for all visuals (the default) and add measure tooltips where they help (e.g. Units Sold on category bars).
2. Add a text box on each page with a one-line takeaway taken from `reports/business_insights.md`.
3. Save as `powerbi/marketplace_analytics.pbix`. Optionally **File > Export > Power BI template** saves `powerbi/marketplace_analytics.pbit`, a small, data-free version that's ideal for GitHub.
4. Export one screenshot per page (Windows + Shift + S) to `reports/dashboard/page1_executive.png` ... `page5_payments_reviews.png`. The README will use them.

## Validation checklist

- [ ] All Step 6 cards match `reports/powerbi_expected_values.md`
- [ ] Every page's **Check** value matches
- [ ] Each slicer filters every visual on every page (select a category: delivery and review cards change too)
- [ ] Clearing all slicers returns the cards to the expected values

## Interview talking points

- **Star schema:** two fact tables at different grains (orders, and items in `fact_sales`) plus small dimensions. This avoids the double counting you get from a single wide table.
- **Business logic upstream:** flags like `is_sale`, `is_first_order` and `delivery_outcome` are computed once in SQL and Python and tested, so DAX stays simple and the dashboard matches the SQL report exactly.
- **One bidirectional relationship,** justified by the category slicer, with no ambiguous filter paths.
- **Time intelligence** needs a dedicated, marked date table: DATEADD for month over month, SAMEPERIODLASTYEAR for year over year.
- **Verification:** the expected-values file is a test for the dashboard, the same idea as unit tests for the Python code.
