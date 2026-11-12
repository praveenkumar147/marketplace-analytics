-- =============================================================================
-- 02_create_tables.sql
-- Relational schema for the cleaned Olist data (see docs/data_model.md).
--
-- Design notes
--   * IDs are CHAR(32): every Olist id is a fixed-length 32-char hex string.
--   * Money is DECIMAL(10,2), never FLOAT, so sums are exact to the cent.
--   * Zip prefixes are CHAR(5) text to keep leading zeros ('01037').
--   * Flags from Module B are BOOLEAN (TINYINT(1)) so SQL can filter on them.
--   * Tables are dropped child-first and created parent-first so foreign keys
--     always point at a table that already exists. Re-running rebuilds cleanly.
--   * customers/sellers -> geolocation is NOT a foreign key: 0.28% of customer
--     zip prefixes are missing from the geolocation source. It is a soft link
--     (indexed join column) instead, documented in docs/database.md.
-- =============================================================================

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS product_categories;
DROP TABLE IF EXISTS sellers;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS geolocation;
SET FOREIGN_KEY_CHECKS = 1;

-- -----------------------------------------------------------------------------
-- Dimension / reference tables
-- -----------------------------------------------------------------------------
CREATE TABLE geolocation (
    zip_code_prefix   CHAR(5)       NOT NULL,
    latitude          DECIMAL(9,6)  NOT NULL,
    longitude         DECIMAL(9,6)  NOT NULL,
    city              VARCHAR(64)   NULL,
    state             CHAR(2)       NULL,
    sample_count      SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (zip_code_prefix)
) ENGINE = InnoDB COMMENT = 'One representative GPS point per zip prefix (median of raw samples)';

CREATE TABLE customers (
    customer_id         CHAR(32)    NOT NULL COMMENT 'Generated per order by Olist',
    customer_unique_id  CHAR(32)    NOT NULL COMMENT 'The real person - use for retention/RFM',
    zip_code_prefix     CHAR(5)     NOT NULL,
    city                VARCHAR(64) NULL,
    state               CHAR(2)     NOT NULL,
    PRIMARY KEY (customer_id),
    INDEX idx_customers_unique (customer_unique_id),
    INDEX idx_customers_state (state),
    INDEX idx_customers_zip (zip_code_prefix)
) ENGINE = InnoDB;

CREATE TABLE sellers (
    seller_id        CHAR(32)    NOT NULL,
    zip_code_prefix  CHAR(5)     NOT NULL,
    city             VARCHAR(64) NULL,
    state            CHAR(2)     NOT NULL,
    PRIMARY KEY (seller_id),
    INDEX idx_sellers_state (state),
    INDEX idx_sellers_zip (zip_code_prefix)
) ENGINE = InnoDB;

CREATE TABLE product_categories (
    category_name     VARCHAR(64) NOT NULL COMMENT 'Original Portuguese name',
    category_name_en  VARCHAR(64) NOT NULL,
    PRIMARY KEY (category_name)
) ENGINE = InnoDB;

CREATE TABLE products (
    product_id                  CHAR(32)    NOT NULL,
    category_name               VARCHAR(64) NOT NULL,
    product_name_length         SMALLINT UNSIGNED NULL,
    product_description_length  SMALLINT UNSIGNED NULL,
    product_photos_qty          TINYINT UNSIGNED  NULL,
    product_weight_g            INT UNSIGNED      NULL,
    product_length_cm           SMALLINT UNSIGNED NULL,
    product_height_cm           SMALLINT UNSIGNED NULL,
    product_width_cm            SMALLINT UNSIGNED NULL,
    PRIMARY KEY (product_id),
    CONSTRAINT fk_products_category
        FOREIGN KEY (category_name) REFERENCES product_categories (category_name)
) ENGINE = InnoDB;

-- -----------------------------------------------------------------------------
-- Fact tables
-- -----------------------------------------------------------------------------
CREATE TABLE orders (
    order_id                       CHAR(32)    NOT NULL,
    customer_id                    CHAR(32)    NOT NULL,
    order_status                   VARCHAR(16) NOT NULL,
    order_purchase_timestamp       DATETIME    NOT NULL,
    order_approved_at              DATETIME    NULL,
    order_delivered_carrier_date   DATETIME    NULL,
    order_delivered_customer_date  DATETIME    NULL,
    order_estimated_delivery_date  DATETIME    NULL,
    -- derived in Module B
    purchase_date            DATE          NOT NULL,
    purchase_year_month      CHAR(7)       NOT NULL COMMENT 'YYYY-MM',
    in_analysis_window       BOOLEAN       NOT NULL COMMENT '2017-01..2018-08 complete months',
    is_revenue_order         BOOLEAN       NOT NULL COMMENT 'FALSE for canceled/unavailable',
    is_valid_delivery        BOOLEAN       NOT NULL,
    delivery_days            DECIMAL(6,2)  NULL,
    estimated_delivery_days  DECIMAL(6,2)  NULL,
    delivery_delay_days      SMALLINT      NULL COMMENT 'delivered date - estimated date',
    is_late                  BOOLEAN       NULL COMMENT 'NULL when not a valid delivery',
    PRIMARY KEY (order_id),
    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT chk_orders_status CHECK (order_status IN
        ('created','approved','invoiced','processing','shipped','delivered','canceled','unavailable')),
    CONSTRAINT chk_orders_delivery_days CHECK (delivery_days IS NULL OR delivery_days >= 0),
    INDEX idx_orders_customer (customer_id),
    INDEX idx_orders_purchase (order_purchase_timestamp),
    INDEX idx_orders_year_month (purchase_year_month),
    INDEX idx_orders_status (order_status)
) ENGINE = InnoDB;

CREATE TABLE order_items (
    order_id             CHAR(32)       NOT NULL,
    order_item_id        TINYINT UNSIGNED NOT NULL COMMENT '1, 2, 3 ... within the order',
    product_id           CHAR(32)       NOT NULL,
    seller_id            CHAR(32)       NOT NULL,
    shipping_limit_date  DATETIME       NOT NULL,
    price                DECIMAL(10,2)  NOT NULL,
    freight_value        DECIMAL(10,2)  NOT NULL,
    line_total           DECIMAL(10,2)  NOT NULL COMMENT 'price + freight_value',
    is_price_outlier     BOOLEAN        NOT NULL,
    PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_items_order   FOREIGN KEY (order_id)   REFERENCES orders (order_id),
    CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT fk_items_seller  FOREIGN KEY (seller_id)  REFERENCES sellers (seller_id),
    CONSTRAINT chk_items_price   CHECK (price > 0),
    CONSTRAINT chk_items_freight CHECK (freight_value >= 0),
    INDEX idx_items_product (product_id),
    INDEX idx_items_seller (seller_id)
) ENGINE = InnoDB;

CREATE TABLE payments (
    order_id              CHAR(32)       NOT NULL,
    payment_sequential    TINYINT UNSIGNED NOT NULL,
    payment_type          VARCHAR(16)    NOT NULL,
    payment_installments  TINYINT UNSIGNED NOT NULL,
    payment_value         DECIMAL(10,2)  NOT NULL,
    PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT chk_payments_type CHECK (payment_type IN ('credit_card','boleto','voucher','debit_card')),
    CONSTRAINT chk_payments_installments CHECK (payment_installments >= 1),
    CONSTRAINT chk_payments_value CHECK (payment_value >= 0),
    INDEX idx_payments_type (payment_type)
) ENGINE = InnoDB;

CREATE TABLE reviews (
    review_id                CHAR(32)     NOT NULL,
    order_id                 CHAR(32)     NOT NULL,
    review_score             TINYINT UNSIGNED NOT NULL,
    review_comment_title     VARCHAR(100) NULL,
    review_comment_message   TEXT         NULL,
    review_creation_date     DATETIME     NOT NULL,
    review_answer_timestamp  DATETIME     NOT NULL,
    has_comment              BOOLEAN      NOT NULL,
    is_latest_for_order      BOOLEAN      NOT NULL COMMENT 'Use = 1 for one score per order',
    PRIMARY KEY (review_id, order_id),
    CONSTRAINT fk_reviews_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT chk_reviews_score CHECK (review_score BETWEEN 1 AND 5),
    INDEX idx_reviews_order (order_id),
    INDEX idx_reviews_score (review_score)
) ENGINE = InnoDB;
