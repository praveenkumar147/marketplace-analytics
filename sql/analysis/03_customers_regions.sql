-- =============================================================================
-- 03_customers_regions.sql  -  Customer behaviour, retention and geography
--
-- IMPORTANT: a real customer is customers.customer_unique_id. Olist creates a new
-- customer_id for every order, so counting customer_id would show 0% repeat buyers.
-- Customer metrics use revenue orders (canceled/unavailable excluded).
-- =============================================================================


-- @query q09_repeat_customer_rate
-- @title Q9. Repeat customer percentage
-- @purpose Share of customers who bought more than once - the core retention KPI.
--          Inner query: orders per person. Outer query: classify with CASE and count.
SELECT
    COUNT(*)                                                    AS customers,
    SUM(CASE WHEN order_count >= 2 THEN 1 ELSE 0 END)           AS repeat_customers,
    ROUND(100 * AVG(CASE WHEN order_count >= 2 THEN 1 ELSE 0 END), 2) AS repeat_customer_pct,
    ROUND(AVG(order_count), 4)                                  AS avg_orders_per_customer
FROM (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS order_count
    FROM orders AS o
    JOIN customers AS c ON c.customer_id = o.customer_id
    WHERE o.is_revenue_order = 1
    GROUP BY c.customer_unique_id
) AS per_customer;


-- @query q10_new_vs_returning
-- @title Q10. New vs returning customers per month
-- @purpose For each month: how many active customers were buying for the first time
--          vs had bought before. First-purchase month is found over the FULL history
--          (so a 2016 buyer returning in 2017 counts as returning).
WITH customer_orders AS (
    SELECT c.customer_unique_id,
           o.purchase_year_month,
           o.in_analysis_window
    FROM orders AS o
    JOIN customers AS c ON c.customer_id = o.customer_id
    WHERE o.is_revenue_order = 1
),
first_purchase AS (
    SELECT customer_unique_id, MIN(purchase_year_month) AS first_month
    FROM customer_orders
    GROUP BY customer_unique_id
),
active_by_month AS (
    SELECT DISTINCT co.purchase_year_month AS month, co.customer_unique_id, fp.first_month
    FROM customer_orders AS co
    JOIN first_purchase AS fp ON fp.customer_unique_id = co.customer_unique_id
    WHERE co.in_analysis_window = 1
)
SELECT
    month,
    COUNT(*)                                                      AS active_customers,
    SUM(CASE WHEN first_month = month THEN 1 ELSE 0 END)          AS new_customers,
    SUM(CASE WHEN first_month < month THEN 1 ELSE 0 END)          AS returning_customers,
    ROUND(100 * AVG(CASE WHEN first_month < month THEN 1 ELSE 0 END), 2) AS returning_pct
FROM active_by_month
GROUP BY month
ORDER BY month;


-- @query q11_monthly_active_customers
-- @title Q11. Monthly active customers
-- @purpose Distinct people buying each month, and how that changes vs the previous
--          month (LAG). Orders per active customer shows whether growth comes from
--          more people or from people buying more often.
WITH monthly AS (
    SELECT o.purchase_year_month                AS month,
           COUNT(DISTINCT c.customer_unique_id) AS active_customers,
           COUNT(DISTINCT o.order_id)           AS orders
    FROM orders AS o
    JOIN customers AS c ON c.customer_id = o.customer_id
    WHERE o.is_revenue_order = 1
      AND o.in_analysis_window = 1
    GROUP BY o.purchase_year_month
)
SELECT
    month,
    active_customers,
    orders,
    ROUND(orders / active_customers, 3)                         AS orders_per_active_customer,
    active_customers - LAG(active_customers) OVER (ORDER BY month) AS change_vs_prev_month
FROM monthly
ORDER BY month;


-- @query q17_purchase_frequency
-- @title Q17. Customer purchase frequency distribution
-- @purpose How many customers bought once, twice, three times, 4+ times, and how much
--          revenue each group brings. CASE buckets the counts; a window total gives %.
WITH per_customer AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS order_count,
           SUM(i.line_total)          AS revenue
    FROM orders AS o
    JOIN customers   AS c ON c.customer_id = o.customer_id
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY c.customer_unique_id
),
bucketed AS (
    SELECT CASE WHEN order_count = 1 THEN '1 order'
                WHEN order_count = 2 THEN '2 orders'
                WHEN order_count = 3 THEN '3 orders'
                ELSE '4+ orders' END   AS frequency_bucket,
           order_count, revenue
    FROM per_customer
)
SELECT
    frequency_bucket,
    COUNT(*)                                                 AS customers,
    ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)         AS customer_pct,
    ROUND(SUM(revenue), 2)                                   AS revenue,
    ROUND(100 * SUM(revenue) / SUM(SUM(revenue)) OVER (), 2) AS revenue_pct,
    ROUND(AVG(revenue), 2)                                   AS avg_revenue_per_customer
FROM bucketed
GROUP BY frequency_bucket
ORDER BY MIN(order_count);


-- @query q08_revenue_by_state
-- @title Q8. Revenue by customer state
-- @purpose Where revenue comes from, with delivery performance per state, so sales
--          and logistics can be compared side by side. Two CTEs aggregate at their
--          own grain (items for revenue, orders for delivery) before joining.
WITH state_revenue AS (
    SELECT c.state,
           SUM(i.line_total)                    AS revenue,
           COUNT(DISTINCT o.order_id)           AS orders,
           COUNT(DISTINCT c.customer_unique_id) AS customers
    FROM orders AS o
    JOIN customers   AS c ON c.customer_id = o.customer_id
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY c.state
),
state_delivery AS (
    SELECT c.state,
           AVG(o.delivery_days) AS avg_delivery_days,
           AVG(o.is_late)       AS late_rate          -- AVG of 0/1 = share late; NULLs ignored
    FROM orders AS o
    JOIN customers AS c ON c.customer_id = o.customer_id
    WHERE o.is_valid_delivery = 1
    GROUP BY c.state
)
SELECT
    RANK() OVER (ORDER BY sr.revenue DESC)              AS revenue_rank,
    sr.state,
    ROUND(sr.revenue, 2)                                AS revenue,
    ROUND(100 * sr.revenue / SUM(sr.revenue) OVER (), 2) AS revenue_share_pct,
    sr.orders,
    sr.customers,
    ROUND(sr.revenue / sr.orders, 2)                    AS aov,
    ROUND(sd.avg_delivery_days, 1)                      AS avg_delivery_days,
    ROUND(100 * sd.late_rate, 2)                        AS late_delivery_pct
FROM state_revenue AS sr
LEFT JOIN state_delivery AS sd ON sd.state = sr.state
ORDER BY sr.revenue DESC;


-- @query q08b_revenue_by_region
-- @title Q8b. Revenue by Brazilian region
-- @purpose Rolls the 27 states up into Brazil's 5 official regions with CASE,
--          which is easier to read on an executive dashboard.
WITH state_region AS (
    SELECT c.customer_id,
           c.customer_unique_id,
           CASE
               WHEN c.state IN ('SP','RJ','MG','ES')                          THEN 'Southeast'
               WHEN c.state IN ('PR','SC','RS')                               THEN 'South'
               WHEN c.state IN ('DF','GO','MT','MS')                          THEN 'Central-West'
               WHEN c.state IN ('BA','PE','CE','MA','PB','RN','AL','PI','SE') THEN 'Northeast'
               WHEN c.state IN ('AM','PA','RO','AC','AP','RR','TO')           THEN 'North'
           END AS region
    FROM customers AS c
)
SELECT
    sr.region,
    ROUND(SUM(i.line_total), 2)                                   AS revenue,
    ROUND(100 * SUM(i.line_total) / SUM(SUM(i.line_total)) OVER (), 2) AS revenue_share_pct,
    COUNT(DISTINCT o.order_id)                                    AS orders,
    COUNT(DISTINCT sr.customer_unique_id)                         AS customers,
    ROUND(SUM(i.line_total) / COUNT(DISTINCT o.order_id), 2)      AS aov,
    ROUND(AVG(i.freight_value), 2)                                AS avg_freight_per_item
FROM orders AS o
JOIN state_region AS sr ON sr.customer_id = o.customer_id
JOIN order_items  AS i  ON i.order_id = o.order_id
WHERE o.is_revenue_order = 1
GROUP BY sr.region
ORDER BY revenue DESC;
