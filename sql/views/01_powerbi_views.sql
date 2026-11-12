-- =============================================================================
-- 01_powerbi_views.sql  -  Reporting layer (star schema) for Power BI
--
-- Power BI reads ONLY these views, never the base tables. Business rules live in
-- one place (SQL + the tested Python pipeline), so the dashboard can't drift from
-- the SQL analysis.
--
--   dim_date (built in DAX) ---> fact_orders <--- fact_payments
--   dim_state    ------------->      ^ (both directions, see guide)
--   dim_customer ------------->      |
--                                fact_sales <--- dim_product
--                                           <--- dim_seller
--
-- Flags are returned as 0/1 integers, which Power BI handles more simply than
-- MySQL BOOLEAN. Re-runnable: CREATE OR REPLACE VIEW.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- fact_orders: one row per order (status, delivery, review, payment, customer)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_fact_orders AS
WITH order_values AS (
    SELECT order_id,
           SUM(line_total)    AS order_value,
           SUM(price)         AS product_value,
           SUM(freight_value) AS freight_value,
           COUNT(*)           AS items
    FROM order_items
    GROUP BY order_id
),
main_payment AS (            -- the payment line with the largest value per order
    SELECT order_id, payment_type, payment_installments
    FROM (
        SELECT order_id, payment_type, payment_installments,
               ROW_NUMBER() OVER (PARTITION BY order_id
                                  ORDER BY payment_value DESC, payment_sequential) AS rn
        FROM payments
    ) AS ranked
    WHERE rn = 1
),
latest_review AS (
    SELECT order_id, review_score
    FROM reviews
    WHERE is_latest_for_order = 1
),
sales_sequence AS (          -- order number per customer, counting sales only
    SELECT o.order_id,
           ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                              ORDER BY o.order_purchase_timestamp, o.order_id) AS customer_order_number
    FROM orders AS o
    JOIN customers AS c ON c.customer_id = o.customer_id
    WHERE o.is_revenue_order = 1
      AND EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)
)
SELECT
    o.order_id,
    c.customer_unique_id,
    c.state                                           AS customer_state,
    o.order_status,
    o.order_purchase_timestamp,
    o.purchase_date,
    o.purchase_year_month,
    HOUR(o.order_purchase_timestamp)                  AS purchase_hour,
    CAST(o.in_analysis_window AS UNSIGNED)            AS in_analysis_window,
    CAST(o.is_revenue_order AS UNSIGNED)              AS is_revenue_order,
    -- is_sale = revenue order that has items: the base of every revenue/customer KPI
    CASE WHEN o.is_revenue_order = 1 AND ov.order_id IS NOT NULL THEN 1 ELSE 0 END AS is_sale,
    CASE WHEN ss.customer_order_number = 1 THEN 1 ELSE 0 END                AS is_first_order,
    ss.customer_order_number,
    ov.order_value,
    ov.product_value,
    ov.freight_value,
    ov.items,
    COALESCE(mp.payment_type, 'no payment')           AS main_payment_type,
    mp.payment_installments                           AS main_payment_installments,
    CAST(o.is_valid_delivery AS UNSIGNED)             AS is_valid_delivery,
    o.delivery_days,
    o.estimated_delivery_days,
    o.delivery_delay_days,
    CAST(o.is_late AS UNSIGNED)                       AS is_late,
    CASE
        WHEN o.is_valid_delivery = 0                 THEN '7. Not delivered / no valid date'
        WHEN o.delivery_delay_days <= -7             THEN '1. 7+ days early'
        WHEN o.delivery_delay_days BETWEEN -6 AND -1 THEN '2. 1-6 days early'
        WHEN o.delivery_delay_days = 0               THEN '3. On promised day'
        WHEN o.delivery_delay_days BETWEEN 1 AND 3   THEN '4. 1-3 days late'
        WHEN o.delivery_delay_days BETWEEN 4 AND 7   THEN '5. 4-7 days late'
        ELSE                                              '6. 8+ days late'
    END                                               AS delivery_outcome,
    lr.review_score
FROM orders AS o
JOIN customers          AS c  ON c.customer_id = o.customer_id
LEFT JOIN order_values  AS ov ON ov.order_id = o.order_id
LEFT JOIN main_payment  AS mp ON mp.order_id = o.order_id
LEFT JOIN latest_review AS lr ON lr.order_id = o.order_id
LEFT JOIN sales_sequence AS ss ON ss.order_id = o.order_id;


-- -----------------------------------------------------------------------------
-- fact_sales: one row per item of a revenue order (revenue lives here)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_fact_sales AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    i.price,
    i.freight_value,
    i.line_total,
    CAST(i.is_price_outlier AS UNSIGNED) AS is_price_outlier
FROM order_items AS i
JOIN orders AS o ON o.order_id = i.order_id
WHERE o.is_revenue_order = 1;


-- -----------------------------------------------------------------------------
-- fact_payments: one row per payment line
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_fact_payments AS
SELECT order_id, payment_sequential, payment_type, payment_installments, payment_value,
       CASE WHEN payment_installments = 1              THEN '1'
            WHEN payment_installments BETWEEN 2 AND 3  THEN '2-3'
            WHEN payment_installments BETWEEN 4 AND 6  THEN '4-6'
            WHEN payment_installments BETWEEN 7 AND 10 THEN '7-10'
            ELSE '11+' END                                     AS installment_group
FROM payments;


-- -----------------------------------------------------------------------------
-- dim_customer: one row per real customer (customer_unique_id)
-- State = state on the customer's most recent order (a person can move).
-- RFM columns come from table customer_rfm (src/analysis/rfm.py); customers
-- with no sale (e.g. only canceled orders) get segment 'No purchase'.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dim_customer AS
WITH latest_state AS (
    SELECT customer_unique_id, state, city
    FROM (
        SELECT c.customer_unique_id, c.state, c.city,
               ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                                  ORDER BY o.order_purchase_timestamp DESC, o.order_id) AS rn
        FROM customers AS c
        JOIN orders AS o ON o.customer_id = c.customer_id
    ) AS ranked
    WHERE rn = 1
)
SELECT
    ls.customer_unique_id,
    ls.state                                    AS customer_state,
    ls.city                                     AS customer_city,
    COALESCE(r.frequency, 0)                    AS lifetime_orders,
    CASE WHEN r.frequency IS NULL THEN '0 orders'
         WHEN r.frequency = 1     THEN '1 order'
         WHEN r.frequency = 2     THEN '2 orders'
         WHEN r.frequency = 3     THEN '3 orders'
         ELSE '4+ orders' END                   AS frequency_bucket,
    r.monetary                                  AS lifetime_revenue,
    r.recency_days,
    r.r_score, r.f_score, r.m_score, r.rfm_code,
    COALESCE(r.segment, 'No purchase')          AS rfm_segment,
    CASE COALESCE(r.segment, 'No purchase')
        WHEN 'Champions'           THEN 1
        WHEN 'Loyal Customers'     THEN 2
        WHEN 'New Customers'       THEN 3
        WHEN 'Potential Loyalists' THEN 4
        WHEN 'Need Attention'      THEN 5
        WHEN 'At Risk'             THEN 6
        WHEN 'Hibernating'         THEN 7
        ELSE 8 END                              AS rfm_segment_order
FROM latest_state AS ls
LEFT JOIN customer_rfm AS r ON r.customer_unique_id = ls.customer_unique_id;


-- -----------------------------------------------------------------------------
-- dim_product: product with English category
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dim_product AS
SELECT p.product_id,
       pc.category_name_en AS category,
       pc.category_name    AS category_pt,
       p.product_weight_g,
       p.product_photos_qty
FROM products AS p
JOIN product_categories AS pc ON pc.category_name = p.category_name;


-- -----------------------------------------------------------------------------
-- dim_seller
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dim_seller AS
SELECT seller_id, city AS seller_city, state AS seller_state
FROM sellers;


-- -----------------------------------------------------------------------------
-- dim_state: 27 Brazilian states with full name and region (for maps/slicers)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dim_state AS
SELECT s.state,
       s.state_name,
       CASE
           WHEN s.state IN ('SP','RJ','MG','ES')                          THEN 'Southeast'
           WHEN s.state IN ('PR','SC','RS')                               THEN 'South'
           WHEN s.state IN ('DF','GO','MT','MS')                          THEN 'Central-West'
           WHEN s.state IN ('BA','PE','CE','MA','PB','RN','AL','PI','SE') THEN 'Northeast'
           ELSE 'North'
       END AS region,
       CONCAT(s.state_name, ', Brazil') AS map_location
FROM (
    SELECT 'AC' AS state, 'Acre' AS state_name UNION ALL SELECT 'AL', 'Alagoas'
    UNION ALL SELECT 'AP', 'Amapa'            UNION ALL SELECT 'AM', 'Amazonas'
    UNION ALL SELECT 'BA', 'Bahia'            UNION ALL SELECT 'CE', 'Ceara'
    UNION ALL SELECT 'DF', 'Distrito Federal' UNION ALL SELECT 'ES', 'Espirito Santo'
    UNION ALL SELECT 'GO', 'Goias'            UNION ALL SELECT 'MA', 'Maranhao'
    UNION ALL SELECT 'MT', 'Mato Grosso'      UNION ALL SELECT 'MS', 'Mato Grosso do Sul'
    UNION ALL SELECT 'MG', 'Minas Gerais'     UNION ALL SELECT 'PA', 'Para'
    UNION ALL SELECT 'PB', 'Paraiba'          UNION ALL SELECT 'PR', 'Parana'
    UNION ALL SELECT 'PE', 'Pernambuco'       UNION ALL SELECT 'PI', 'Piaui'
    UNION ALL SELECT 'RJ', 'Rio de Janeiro'   UNION ALL SELECT 'RN', 'Rio Grande do Norte'
    UNION ALL SELECT 'RS', 'Rio Grande do Sul' UNION ALL SELECT 'RO', 'Rondonia'
    UNION ALL SELECT 'RR', 'Roraima'          UNION ALL SELECT 'SC', 'Santa Catarina'
    UNION ALL SELECT 'SP', 'Sao Paulo'        UNION ALL SELECT 'SE', 'Sergipe'
    UNION ALL SELECT 'TO', 'Tocantins'
) AS s;
