-- =============================================================================
-- 01_sales_kpis.sql  -  Headline sales KPIs and growth
--
-- Shared definitions (see docs/data_model.md section 5):
--   Revenue        = SUM(order_items.line_total)  (price + freight)
--   Revenue order  = orders.is_revenue_order = 1  (excludes canceled/unavailable)
--   Trend window   = orders.in_analysis_window = 1 (2017-01..2018-08, complete months)
--
-- Each query starts with an "@query" marker so src/analysis/sql_runner.py can run
-- them one by one and save the results. Lines starting with "--" are comments.
-- =============================================================================


-- @query q01_total_revenue
-- @title Q1. Total revenue
-- @purpose The single most important KPI. Split into product value and freight
--          so we can see how much of what customers pay is shipping.
SELECT
    ROUND(SUM(i.price), 2)                       AS product_revenue,
    ROUND(SUM(i.freight_value), 2)               AS freight_revenue,
    ROUND(SUM(i.line_total), 2)                  AS total_revenue,
    ROUND(100 * SUM(i.freight_value) / SUM(i.line_total), 2) AS freight_share_pct
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.order_id
WHERE o.is_revenue_order = 1;


-- @query q02_total_orders
-- @title Q2. Total orders
-- @purpose Order volume at each stage, so "total orders" is never ambiguous:
--          all orders placed, orders that count toward revenue, and delivered orders.
--          Conditional aggregation (SUM of a CASE) counts several groups in one pass.
SELECT
    COUNT(*)                                                     AS all_orders,
    SUM(CASE WHEN o.is_revenue_order = 1 THEN 1 ELSE 0 END)      AS revenue_orders,
    SUM(CASE WHEN o.is_revenue_order = 1
              AND EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)
             THEN 1 ELSE 0 END)                                  AS revenue_orders_with_items,
    SUM(CASE WHEN o.order_status = 'delivered' THEN 1 ELSE 0 END) AS delivered_orders
FROM orders AS o;


-- @query q03_average_order_value
-- @title Q3. Average order value (AOV)
-- @purpose How much a customer spends per order. A CTE first builds one row per
--          order (its total), then we average those totals. Averaging item rows
--          directly would give average *item* value instead - a classic mistake.
WITH order_totals AS (
    SELECT o.order_id,
           SUM(i.line_total) AS order_value,
           COUNT(*)          AS items_in_order
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY o.order_id
)
SELECT
    COUNT(*)                          AS orders,
    ROUND(AVG(order_value), 2)        AS average_order_value,
    ROUND(MIN(order_value), 2)        AS min_order_value,
    ROUND(MAX(order_value), 2)        AS max_order_value,
    ROUND(AVG(items_in_order), 3)     AS avg_items_per_order
FROM order_totals;


-- @query q04_monthly_revenue
-- @title Q4. Monthly revenue trend
-- @purpose Revenue, orders and AOV per month, plus a running (cumulative) total
--          using a window function: SUM(...) OVER (ORDER BY month).
--          Restricted to complete months so partial months don't look like a crash.
WITH monthly AS (
    SELECT o.purchase_year_month          AS month,
           COUNT(DISTINCT o.order_id)     AS orders,
           SUM(i.line_total)              AS revenue
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
      AND o.in_analysis_window = 1
    GROUP BY o.purchase_year_month
)
SELECT
    month,
    orders,
    ROUND(revenue, 2)                                   AS revenue,
    ROUND(revenue / orders, 2)                          AS aov,
    ROUND(SUM(revenue) OVER (ORDER BY month), 2)        AS cumulative_revenue
FROM monthly
ORDER BY month;


-- @query q18_revenue_growth_mom
-- @title Q18. Month-over-month revenue growth
-- @purpose LAG() reads the previous month's revenue onto the current row so we can
--          compute growth %. Also compares each month with the same month last
--          year (LAG 12) where both exist - removes seasonality from the comparison.
WITH monthly AS (
    SELECT o.purchase_year_month AS month,
           SUM(i.line_total)     AS revenue
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
      AND o.in_analysis_window = 1
    GROUP BY o.purchase_year_month
),
with_lag AS (
    SELECT month,
           revenue,
           LAG(revenue, 1)  OVER (ORDER BY month) AS prev_month_revenue,
           LAG(revenue, 12) OVER (ORDER BY month) AS same_month_last_year
    FROM monthly
)
SELECT
    month,
    ROUND(revenue, 2)                                                  AS revenue,
    ROUND(prev_month_revenue, 2)                                       AS prev_month_revenue,
    ROUND(100 * (revenue - prev_month_revenue) / prev_month_revenue, 2) AS mom_growth_pct,
    ROUND(100 * (revenue - same_month_last_year) / same_month_last_year, 2) AS yoy_growth_pct
FROM with_lag
ORDER BY month;
