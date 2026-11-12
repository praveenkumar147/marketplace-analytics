-- =============================================================================
-- 05_rfm.sql  -  RFM customer segmentation in pure SQL
--
-- Same method as src/analysis/rfm.py (see docs/rfm.md):
--   R = days since last purchase -> NTILE(5), 5 = most recent
--   M = total spent              -> NTILE(5), 5 = highest
--   F = rule-based (1 order / 2 orders / 3+), because 97% of customers buy once
-- Ties are broken by customer_unique_id in both SQL and Python, so the two
-- implementations produce identical scores (checked in tests/test_rfm.py).
-- =============================================================================


-- @query q19_rfm_segments
-- @title Q19. RFM customer segments
-- @purpose Groups customers by how recently they bought, how often, and how much
--          they spent, then summarises each segment's size and revenue share.
--          Shows a CTE pipeline: customer facts -> scores (NTILE) -> segment (CASE).
WITH revenue_orders AS (
    SELECT o.order_id,
           c.customer_unique_id,
           DATE(o.order_purchase_timestamp) AS purchase_day,
           SUM(i.line_total)                AS order_value
    FROM orders AS o
    JOIN customers   AS c ON c.customer_id = o.customer_id
    JOIN order_items AS i ON i.order_id = o.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY o.order_id, c.customer_unique_id, DATE(o.order_purchase_timestamp)
),
snapshot AS (
    SELECT DATE_ADD(MAX(purchase_day), INTERVAL 1 DAY) AS snapshot_day FROM revenue_orders
),
customer_facts AS (
    SELECT r.customer_unique_id,
           DATEDIFF(s.snapshot_day, MAX(r.purchase_day)) AS recency_days,
           COUNT(*)                                      AS frequency,
           SUM(r.order_value)                            AS monetary
    FROM revenue_orders AS r
    CROSS JOIN snapshot AS s
    GROUP BY r.customer_unique_id, s.snapshot_day
),
scored AS (
    SELECT customer_unique_id, recency_days, frequency, monetary,
           NTILE(5) OVER (ORDER BY recency_days DESC, customer_unique_id) AS r_score,
           NTILE(5) OVER (ORDER BY monetary ASC,     customer_unique_id) AS m_score
    FROM customer_facts
),
segmented AS (
    SELECT *,
           CASE
               WHEN frequency >= 2 AND r_score >= 4 THEN 'Champions'
               WHEN frequency >= 2                  THEN 'Loyal Customers'
               WHEN r_score = 5                     THEN 'New Customers'
               WHEN r_score = 4                     THEN 'Potential Loyalists'
               WHEN r_score = 3                     THEN 'Need Attention'
               WHEN m_score >= 4                    THEN 'At Risk'
               ELSE                                      'Hibernating'
           END AS segment
    FROM scored
)
SELECT
    segment,
    COUNT(*)                                                    AS customers,
    ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS customer_pct,
    ROUND(SUM(monetary), 2)                                     AS revenue,
    ROUND(100 * SUM(monetary) / SUM(SUM(monetary)) OVER (), 2)  AS revenue_pct,
    ROUND(AVG(recency_days), 1)                                 AS avg_recency_days,
    ROUND(AVG(frequency), 2)                                    AS avg_orders,
    ROUND(AVG(monetary), 2)                                     AS avg_monetary
FROM segmented
GROUP BY segment
ORDER BY FIELD(segment, 'Champions', 'Loyal Customers', 'New Customers',
               'Potential Loyalists', 'Need Attention', 'At Risk', 'Hibernating');
