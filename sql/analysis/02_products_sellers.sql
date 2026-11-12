-- =============================================================================
-- 02_products_sellers.sql  -  Product, category and seller performance
-- Revenue order = orders.is_revenue_order = 1. Full date range (not only window).
-- Limitation: the dataset has no product names, only product_id + category.
-- =============================================================================


-- @query q05_top10_products
-- @title Q5. Top 10 products by revenue
-- @purpose Which individual products earn the most. RANK() is a window function
--          that numbers rows by revenue; ties would share a rank.
WITH product_sales AS (
    SELECT i.product_id,
           SUM(i.price)               AS product_revenue,
           COUNT(*)                   AS units_sold,
           COUNT(DISTINCT i.order_id) AS orders,
           AVG(i.price)               AS avg_price
    FROM order_items AS i
    JOIN orders AS o ON o.order_id = i.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY i.product_id
)
SELECT
    RANK() OVER (ORDER BY ps.product_revenue DESC) AS revenue_rank,
    ps.product_id,
    c.category_name_en                             AS category,
    ROUND(ps.product_revenue, 2)                   AS product_revenue,
    ps.units_sold,
    ps.orders,
    ROUND(ps.avg_price, 2)                         AS avg_price
FROM product_sales AS ps
JOIN products AS p            ON p.product_id = ps.product_id
JOIN product_categories AS c  ON c.category_name = p.category_name
ORDER BY ps.product_revenue DESC
LIMIT 10;


-- @query q06_category_performance
-- @title Q6. Best-performing product categories
-- @purpose Revenue, volume, share of total and average review per category.
--          HAVING drops tiny categories (< 100 orders) whose averages would be
--          unreliable. The share % divides by the total of ALL categories (CTE
--          grand_total): a window SUM() OVER () would run AFTER HAVING and give a
--          share of the filtered categories only - a subtle but real bug.
--          Reviews are joined at order level (one official review per order) in a
--          separate CTE to avoid double counting when an order has many items.
WITH grand_total AS (
    SELECT SUM(i.price) AS total_product_revenue
    FROM order_items AS i
    JOIN orders AS o ON o.order_id = i.order_id
    WHERE o.is_revenue_order = 1
),
category_sales AS (
    SELECT p.category_name,
           SUM(i.price)               AS product_revenue,
           COUNT(*)                   AS units_sold,
           COUNT(DISTINCT i.order_id) AS orders
    FROM order_items AS i
    JOIN orders   AS o ON o.order_id = i.order_id
    JOIN products AS p ON p.product_id = i.product_id
    WHERE o.is_revenue_order = 1
    GROUP BY p.category_name
    HAVING COUNT(DISTINCT i.order_id) >= 100
),
category_reviews AS (
    SELECT p.category_name,
           AVG(r.review_score) AS avg_review_score
    FROM (SELECT DISTINCT order_id, product_id FROM order_items) AS op
    JOIN products AS p ON p.product_id = op.product_id
    JOIN reviews  AS r ON r.order_id = op.order_id AND r.is_latest_for_order = 1
    GROUP BY p.category_name
)
SELECT
    DENSE_RANK() OVER (ORDER BY cs.product_revenue DESC)              AS revenue_rank,
    c.category_name_en                                               AS category,
    ROUND(cs.product_revenue, 2)                                     AS product_revenue,
    ROUND(100 * cs.product_revenue / gt.total_product_revenue, 2)    AS revenue_share_pct,
    cs.orders,
    cs.units_sold,
    ROUND(cs.product_revenue / cs.units_sold, 2)                     AS avg_item_price,
    ROUND(cr.avg_review_score, 2)                                    AS avg_review_score
FROM category_sales AS cs
JOIN product_categories AS c ON c.category_name = cs.category_name
LEFT JOIN category_reviews AS cr ON cr.category_name = cs.category_name
CROSS JOIN grand_total AS gt
ORDER BY cs.product_revenue DESC;


-- @query q07_revenue_by_seller
-- @title Q7. Revenue by seller (top 15, with Pareto share)
-- @purpose Who the most valuable sellers are, and how concentrated revenue is.
--          The running SUM() OVER (ORDER BY revenue DESC) divided by the grand total
--          gives the cumulative share - the basis of an 80/20 (Pareto) analysis.
WITH seller_sales AS (
    SELECT i.seller_id,
           SUM(i.price)               AS product_revenue,
           COUNT(DISTINCT i.order_id) AS orders,
           COUNT(*)                   AS units_sold
    FROM order_items AS i
    JOIN orders AS o ON o.order_id = i.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY i.seller_id
),
ranked AS (
    SELECT ss.*,
           ROW_NUMBER() OVER (ORDER BY product_revenue DESC)                        AS revenue_rank,
           SUM(product_revenue) OVER (ORDER BY product_revenue DESC
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_revenue,
           SUM(product_revenue) OVER ()                                              AS total_revenue
    FROM seller_sales AS ss
)
SELECT
    r.revenue_rank,
    r.seller_id,
    s.city                                              AS seller_city,
    s.state                                             AS seller_state,
    ROUND(r.product_revenue, 2)                         AS product_revenue,
    r.orders,
    ROUND(100 * r.product_revenue / r.total_revenue, 2) AS revenue_share_pct,
    ROUND(100 * r.running_revenue / r.total_revenue, 2) AS cumulative_share_pct
FROM ranked AS r
JOIN sellers AS s ON s.seller_id = r.seller_id
WHERE r.revenue_rank <= 15
ORDER BY r.revenue_rank;


-- @query q07b_seller_concentration
-- @title Q7b. Seller revenue concentration by decile
-- @purpose NTILE(10) splits sellers into 10 equal-sized groups by revenue.
--          Shows what share of revenue the top 10% of sellers generate.
WITH seller_sales AS (
    SELECT i.seller_id, SUM(i.price) AS product_revenue
    FROM order_items AS i
    JOIN orders AS o ON o.order_id = i.order_id
    WHERE o.is_revenue_order = 1
    GROUP BY i.seller_id
),
deciles AS (
    SELECT seller_id, product_revenue,
           NTILE(10) OVER (ORDER BY product_revenue DESC) AS decile
    FROM seller_sales
)
SELECT
    decile                                                         AS seller_decile,
    COUNT(*)                                                       AS sellers,
    ROUND(SUM(product_revenue), 2)                                 AS product_revenue,
    ROUND(100 * SUM(product_revenue) / SUM(SUM(product_revenue)) OVER (), 2) AS revenue_share_pct,
    ROUND(AVG(product_revenue), 2)                                 AS avg_revenue_per_seller
FROM deciles
GROUP BY decile
ORDER BY decile;
