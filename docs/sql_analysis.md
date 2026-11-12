# SQL Business Analysis Guide

The 18 required business questions (plus 6 supporting queries) live in
`sql/analysis/`. Run them all and save the results with:

```bash
python -m src.analysis.sql_runner          # all queries
python -m src.analysis.sql_runner q08 q16  # just some
```

Results: [`reports/sql_analysis_results.md`](../reports/sql_analysis_results.md)
(every table) and `reports/sql_results/<query>.csv` (for charts and Power BI checks).
Every figure in this document is copied from those outputs.

## 1. Shared definitions and assumptions

| Term | Definition | Why |
|---|---|---|
| Revenue | `SUM(order_items.price + freight_value)` | Item level, so it can be split by product/category/seller. Agrees with payments within R$1 for 99.75% of orders. |
| Product revenue | `SUM(price)` | Used for product, category and seller rankings, so freight doesn't skew them. |
| Revenue order | `is_revenue_order = 1` (status is not canceled/unavailable) | Lost orders aren't sales. |
| Trend window | `in_analysis_window = 1` (2017-01 to 2018-08) | The months before and after are almost empty (5 orders in 2016-09 and 2016-12; 20 in 2018-09/10). |
| Customer | `customer_unique_id` | `customer_id` is new for every order. |
| Delivery metrics | `is_valid_delivery = 1` | Delivered, has a date, no impossible timestamps. |
| One review per order | `is_latest_for_order = 1` | 551 orders have more than one review. |

**Limitations**
- The dataset has no product names, so products are shown by `product_id` and category.
- There is no cost data, so profit and margin can't be calculated; revenue is the measure.
- With 3% repeat customers, retention metrics describe a mostly single-purchase marketplace.
- Payments include canceled orders (the payment was recorded), so Q12 covers all paid orders.

## 2. Query map

| # | Question | File / query | Main techniques |
|---|---|---|---|
| 1 | Total revenue | `01_sales_kpis.sql` / `q01` | JOIN, aggregate |
| 2 | Total orders | `01` / `q02` | CASE (conditional count), EXISTS subquery |
| 3 | Average order value | `01` / `q03` | CTE (order grain first, then average) |
| 4 | Monthly revenue | `01` / `q04` | CTE, `SUM() OVER (ORDER BY)` running total |
| 5 | Top 10 products | `02_products_sellers.sql` / `q05` | CTE, `RANK()` |
| 6 | Best categories | `02` / `q06` | Two CTEs at different grains, HAVING, `DENSE_RANK()`, `SUM() OVER ()` share |
| 7 | Revenue by seller | `02` / `q07`, `q07b` | `ROW_NUMBER()`, running share (Pareto), `NTILE(10)` deciles |
| 8 | Revenue by state / region | `03_customers_regions.sql` / `q08`, `q08b` | Two CTEs joined, CASE state-to-region mapping |
| 9 | Repeat customer % | `03` / `q09` | Subquery in FROM, CASE |
| 10 | New vs returning | `03` / `q10` | 3 chained CTEs, first-purchase month |
| 11 | Monthly active customers | `03` / `q11` | `COUNT(DISTINCT)`, `LAG()` |
| 12 | Payment methods | `04_operations.sql` / `q12`, `q12b` | Scalar subquery, CASE buckets |
| 13 | Cancellation % | `04` / `q13`, `q13b` | Boolean aggregation `AVG(condition)` |
| 14 | Average delivery time | `04` / `q14` | Median using `ROW_NUMBER()` + `COUNT() OVER ()` |
| 15 | Late delivery % | `04` / `q15`, `q15b` | `AVG(is_late)` with NULLs ignored |
| 16 | Review scores | `04` / `q16`, `q16b` | CASE delay buckets, JOIN reviews to orders |
| 17 | Purchase frequency | `03` / `q17` | CTE, CASE buckets, nested window `SUM(SUM()) OVER ()` |
| 18 | MoM revenue growth | `01` / `q18` | `LAG(1)` and `LAG(12)` |

## 3. Headline results

**Sales**
- Total revenue: **R$15,735,527.03**, of which freight is R$2,241,126.29 (14.24%).
- Orders: 99,441 placed; 98,199 revenue orders with items; 96,478 delivered.
- AOV: **R$160.24**, with 1.142 items per order.
- Jan–Aug 2018 revenue was **R$8.59M**, up **140.37%** on Jan–Aug 2017 (R$3.57M).
- Best month: **2017-11 at R$1,172,191.68**, up 53.28% month over month (Black Friday month). 2017-12 then fell 26.5%.
- 2018 levelled off: monthly revenue stayed between R$0.98M and R$1.16M from January to August.

**Products and sellers**
- Top categories by product revenue: health_beauty (9.31%), watches_gifts (8.88%), bed_bath_table (7.68%), sports_leisure (7.26%), computers_accessories (6.70%). The top 10 of 74 categories make up 62.4% of product revenue.
  - *Correction made in Module E:* the first version of Q6 took shares of the filtered categories only, because a window `SUM() OVER ()` is evaluated after `HAVING`. It now divides by a separate grand-total CTE, and the notebook's pandas figures confirm the result.
- Seller concentration: the top 10% of sellers (306) generate **67.5%** of product revenue; the bottom 50% generate 3.24%.

**Customers**
- **3.04%** of 94,990 customers bought more than once; 96.96% bought exactly once.
- Returning customers are at most 3.05% of monthly active customers. The share rises from 0.13% in 2017-01, when almost nobody had bought before, to 2.68% by 2018-08.

**Regions**
- Southeast: **64.57%** of revenue. SP alone is 37.36%.
- North and Northeast customers have higher AOV (R$223.41 and R$201.24) and much higher freight per item (R$36.85 and R$32.22 vs R$17.37 in the Southeast).
- Delivery: SP averages 8.8 days and 4.5% late. AL (21.41% late), MA (17.43%) and SE (15.22%) are the worst; RR, AP and AM take 26–29 days on average.

**Operations**
- Payments: credit card is used on 76.94% of orders and carries 78.34% of value; boleto 19.90%. 66.85% of card payments are split into installments.
- Cancellation: **0.63%** canceled; 1.24% including unavailable.
- Delivery: average **12.56 days**, median 10.22, versus 23.73 days promised (deliveries arrive 11.18 days early on average).
- **6.77%** of deliveries were late, by 10.62 days on average when late. The worst month was 2018-03 (18.96% late), then 2018-02 (14.13%) and 2017-11 (12.40%).
- Reviews: 57.77% five-star and 11.52% one-star.
- **Late delivery drives bad reviews:** the average score falls from 4.31 (7+ days early) to 3.29 (1–3 days late), 2.10 (4–7 days late) and 1.70 (8+ days late). 79.29% of reviews for orders 8+ days late are 1–2 stars, against 8.99% for orders 7+ days early.

## 4. Techniques worth explaining in an interview

- **Grain before aggregation (Q3, Q6, Q8):** aggregate items up to orders before averaging, or join reviews at order level, so multi-item orders aren't double counted.
- **Window vs GROUP BY:** `GROUP BY` collapses rows. A window function (`OVER`) keeps every row and adds a calculation across them: running totals, ranks, previous month, share of total.
- **RANK vs DENSE_RANK vs ROW_NUMBER:** after a tie at rank 1, RANK gives 1, 1, 3; DENSE_RANK gives 1, 1, 2; ROW_NUMBER gives 1, 2, 3 (always unique).
- **WHERE vs HAVING:** WHERE filters rows before grouping; HAVING filters groups after (Q6 keeps categories with ≥100 orders).
- **Median without MEDIAN() (Q14):** number the sorted rows and average the middle one or two.
- **NULL-aware averages (Q15):** `AVG(is_late)` ignores NULLs, so orders that were never delivered are left out automatically.
