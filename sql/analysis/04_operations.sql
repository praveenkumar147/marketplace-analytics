-- =============================================================================
-- 04_operations.sql  -  Payments, order status, delivery and reviews
--
-- Delivery metrics use orders.is_valid_delivery = 1 only (status delivered, has a
-- delivery date, no impossible timestamp sequence). is_late is NULL otherwise.
-- Review metrics use reviews.is_latest_for_order = 1 (one official review per order).
-- =============================================================================


-- @query q12_payment_methods
-- @title Q12. Payment method distribution
-- @purpose Which payment methods customers use, by number of orders and by value,
--          and how many installments credit-card buyers choose. An order paid with
--          card + voucher counts once for each method, so order shares can exceed 100%.
SELECT
    p.payment_type,
    COUNT(*)                                                   AS payment_lines,
    COUNT(DISTINCT p.order_id)                                 AS orders_using_method,
    ROUND(100 * COUNT(DISTINCT p.order_id)
          / (SELECT COUNT(DISTINCT order_id) FROM payments), 2) AS pct_of_orders,
    ROUND(SUM(p.payment_value), 2)                             AS payment_value,
    ROUND(100 * SUM(p.payment_value) / SUM(SUM(p.payment_value)) OVER (), 2) AS value_share_pct,
    ROUND(AVG(p.payment_value), 2)                             AS avg_payment_value,
    ROUND(AVG(p.payment_installments), 2)                      AS avg_installments
FROM payments AS p
GROUP BY p.payment_type
ORDER BY payment_value DESC;


-- @query q12b_installments
-- @title Q12b. Credit-card installment behaviour
-- @purpose Brazilian shoppers often split card payments into installments.
--          Bucketing with CASE shows how common long installment plans are and
--          whether they are used for bigger purchases.
SELECT
    CASE WHEN payment_installments = 1             THEN '1 (single payment)'
         WHEN payment_installments BETWEEN 2 AND 3 THEN '2-3'
         WHEN payment_installments BETWEEN 4 AND 6 THEN '4-6'
         WHEN payment_installments BETWEEN 7 AND 10 THEN '7-10'
         ELSE '11+' END                                     AS installments,
    COUNT(*)                                                AS payments,
    ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)        AS pct_of_card_payments,
    ROUND(AVG(payment_value), 2)                            AS avg_payment_value
FROM payments
WHERE payment_type = 'credit_card'
GROUP BY installments
ORDER BY MIN(payment_installments);


-- @query q13_cancellation_rate
-- @title Q13. Order cancellation percentage
-- @purpose Share of orders that never turned into a sale. 'canceled' is an explicit
--          cancellation; 'unavailable' means the item could not be supplied.
--          Both are lost revenue, so both rates are shown.
SELECT
    COUNT(*)                                                         AS all_orders,
    SUM(o.order_status = 'canceled')                                 AS canceled_orders,
    ROUND(100 * AVG(o.order_status = 'canceled'), 2)                 AS cancellation_pct,
    SUM(o.order_status = 'unavailable')                              AS unavailable_orders,
    ROUND(100 * AVG(o.order_status = 'unavailable'), 2)              AS unavailable_pct,
    ROUND(100 * AVG(o.order_status IN ('canceled','unavailable')), 2) AS lost_order_pct
FROM orders AS o;


-- @query q13b_order_status
-- @title Q13b. Order status breakdown
-- @purpose Full status distribution (feeds the Order Status visual in Power BI).
SELECT
    order_status,
    COUNT(*)                                         AS orders,
    ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_orders
FROM orders
GROUP BY order_status
ORDER BY orders DESC;


-- @query q14_delivery_time
-- @title Q14. Average delivery time
-- @purpose Actual vs promised delivery time. MySQL has no MEDIAN(), so the median
--          is computed with ROW_NUMBER() and COUNT() OVER (): pick the middle row(s).
--          The median matters because a few very slow deliveries pull the mean up.
WITH valid AS (
    SELECT delivery_days,
           estimated_delivery_days,
           ROW_NUMBER() OVER (ORDER BY delivery_days) AS rn,
           COUNT(*)     OVER ()                       AS n
    FROM orders
    WHERE is_valid_delivery = 1
),
median_row AS (
    SELECT AVG(delivery_days) AS median_delivery_days
    FROM valid
    WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
)
SELECT
    COUNT(*)                                        AS delivered_orders,
    ROUND(AVG(v.delivery_days), 2)                  AS avg_delivery_days,
    ROUND(MAX(m.median_delivery_days), 2)           AS median_delivery_days,
    ROUND(AVG(v.estimated_delivery_days), 2)        AS avg_promised_days,
    ROUND(AVG(v.estimated_delivery_days - v.delivery_days), 2) AS avg_days_early,
    ROUND(MAX(v.delivery_days), 2)                  AS slowest_delivery_days
FROM valid AS v
CROSS JOIN median_row AS m;


-- @query q15_late_delivery_rate
-- @title Q15. Late delivery percentage
-- @purpose Share of delivered orders that arrived after the promised date, overall
--          and by how late. AVG(is_late) works because is_late is 1/0 (NULL ignored).
SELECT
    COUNT(*)                                                   AS delivered_orders,
    SUM(is_late)                                               AS late_orders,
    ROUND(100 * AVG(is_late), 2)                               AS late_delivery_pct,
    ROUND(AVG(CASE WHEN is_late = 1 THEN delivery_delay_days END), 2) AS avg_days_late_when_late,
    SUM(delivery_delay_days > 7)                               AS orders_over_1_week_late
FROM orders
WHERE is_valid_delivery = 1;


-- @query q15b_late_rate_by_month
-- @title Q15b. Late delivery rate by month
-- @purpose Is logistics getting better or worse over time? Spikes point to
--          capacity problems (e.g. after peak sales periods).
SELECT
    purchase_year_month                AS month,
    COUNT(*)                           AS delivered_orders,
    ROUND(AVG(delivery_days), 2)       AS avg_delivery_days,
    ROUND(100 * AVG(is_late), 2)       AS late_delivery_pct
FROM orders
WHERE is_valid_delivery = 1
  AND in_analysis_window = 1
GROUP BY purchase_year_month
ORDER BY month;


-- @query q16_review_scores
-- @title Q16. Review score distribution
-- @purpose How satisfied customers are overall (one official review per order).
SELECT
    review_score,
    COUNT(*)                                         AS reviews,
    ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_reviews,
    ROUND(100 * AVG(has_comment), 2)                 AS pct_with_comment
FROM reviews
WHERE is_latest_for_order = 1
GROUP BY review_score
ORDER BY review_score DESC;


-- @query q16b_reviews_vs_delivery
-- @title Q16b. Review score vs delivery performance
-- @purpose The key operational question: does late delivery hurt satisfaction?
--          CASE turns delay days into readable buckets; the inner JOIN keeps only
--          reviewed orders. Orders never delivered get their own bucket.
WITH reviewed AS (
    SELECT r.review_score,
           CASE
               WHEN o.is_valid_delivery = 0                THEN '7. not delivered / no valid date'
               WHEN o.delivery_delay_days <= -7            THEN '1. 7+ days early'
               WHEN o.delivery_delay_days BETWEEN -6 AND -1 THEN '2. 1-6 days early'
               WHEN o.delivery_delay_days = 0              THEN '3. on promised day'
               WHEN o.delivery_delay_days BETWEEN 1 AND 3  THEN '4. 1-3 days late'
               WHEN o.delivery_delay_days BETWEEN 4 AND 7  THEN '5. 4-7 days late'
               ELSE                                             '6. 8+ days late'
           END AS delivery_outcome
    FROM reviews AS r
    JOIN orders  AS o ON o.order_id = r.order_id
    WHERE r.is_latest_for_order = 1
)
SELECT
    delivery_outcome,
    COUNT(*)                                          AS reviews,
    ROUND(AVG(review_score), 2)                       AS avg_review_score,
    ROUND(100 * AVG(review_score <= 2), 2)            AS pct_negative_1_2,
    ROUND(100 * AVG(review_score = 5), 2)             AS pct_five_star
FROM reviewed
GROUP BY delivery_outcome
ORDER BY delivery_outcome;
