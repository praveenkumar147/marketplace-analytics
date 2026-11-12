# Business Insights Report - Olist Marketplace

**Scope:** 99,441 orders, 96,096 customers and 3,095 sellers, purchases from 2016-09-04 to 2018-10-17.
Source: Brazilian E-Commerce Public Dataset by Olist (Kaggle, CC BY-NC-SA 4.0).

**How to read this report.** Every figure was calculated in this project. The
*Source* line under each section names the query, notebook section or report
it comes from, and the numbers were reproduced in both SQL and pandas.
Recommendations are judgements based on those figures. The one forward-looking
estimate (section 8) is labelled as an illustrative scenario.

**Definitions:**
- revenue = item price + freight on orders that were not canceled or unavailable
- customer = `customer_unique_id`
- trends use the complete months Jan 2017 – Aug 2018
- delivery metrics use valid deliveries only

---

## Executive summary

1. **Growth has stalled after a strong 2017.** Revenue for Jan–Aug 2018 (R$8.59M) was 140.4% higher than Jan–Aug 2017. But since January 2018, monthly revenue has stayed flat between R$0.98M and R$1.16M.
2. **Late delivery is the biggest controllable driver of dissatisfaction.** Late or undelivered orders are 9.38% of reviewed orders but produce 42.89% of all 1–2 star reviews. A late order is 6.7× as likely to get a negative review as an on-time one (62.42% vs 9.26%).
3. **Almost nobody comes back.** 97.0% of customers bought once; the repeat rate is 3.04%. Repeat customers spend about twice as much (R$308 vs R$161).
4. **Revenue is concentrated** in the Southeast (64.6%, with São Paulo alone 37.4%), in the top 10% of sellers (67.5% of revenue) and in the top 10 categories (62.4%).
5. **The most valuable lapsed group is "At Risk":** 14,001 one-time big spenders (14.7% of customers, 27.5% of revenue) who haven't bought in 274+ days.

---

## 1. Sales trends

| Metric | Value |
|---|---|
| Total revenue | **R$15,735,527.03** (product R$13,494,400.74 + freight R$2,241,126.29, 14.24% freight) |
| Orders | 99,441 placed, 98,199 with revenue |
| Average order value | R$160.24 (median R$105.28) |
| Jan–Aug 2018 vs Jan–Aug 2017 | R$8.59M vs R$3.57M (**+140.4%**) |
| Best month | Nov 2017, R$1,172,191.68 (+53.3% month on month) |
| Black Friday | 24 Nov 2017: 1,166 orders, about 7.8× the median day (150) |
| 2018 monthly range | R$0.98M – R$1.16M |

**Insights**
- The business roughly doubled in a year, but 2018 shows a **plateau**. August 2018 revenue (R$996,973.51) was 4.1% below July. Growth now needs new levers (retention, new regions) rather than the momentum of 2017.
- **Peak events are powerful but disruptive.** Black Friday drove the record month, then December fell 26.5%. Section 5 shows the logistics cost of the peaks.
- The median order (R$105) is well below the mean (R$160). A few large orders lift the average, so targets should track both.

*Source: SQL Q1, Q2, Q3, Q4, Q18; EDA notebook sections 3.1, 5.1, 5.2.*

## 2. Best-performing categories

| Rank | Category | Product revenue | Share | Avg review |
|---|---|---|---|---|
| 1 | health_beauty | R$1,255,695.13 | 9.31% | 4.17 |
| 2 | watches_gifts | R$1,198,185.21 | 8.88% | 4.05 |
| 3 | bed_bath_table | R$1,035,964.06 | 7.68% | 3.92 |
| 4 | sports_leisure | R$979,740.92 | 7.26% | 4.15 |
| 5 | computers_accessories | R$904,322.02 | 6.70% | 4.00 |

**Insights**
- The **top 10 of 74 categories generate 62.4%** of product revenue.
- **watches_gifts earns its rank on price, not volume:** 5,970 units against 11,097 for bed_bath_table. Its average item price is R$200.70, against R$93.36 for bed_bath_table.
- **Revenue and satisfaction are unrelated** (r = 0.00 across the 52 categories with 100+ orders). bed_bath_table is #3 by revenue but below-median on reviews (3.92). The lowest-rated categories are **office_furniture (3.62), fashion_male_clothing (3.73) and audio (3.84)**.

*Source: SQL Q6; EDA sections 6.1, 6.2.*

## 3. Regional performance

| Region | Revenue share | AOV | Avg freight per item |
|---|---|---|---|
| Southeast | **64.57%** | R$150.88 | R$17.37 |
| South | 14.49% | R$162.87 | R$21.22 |
| Northeast | 11.90% | R$201.24 | R$32.22 |
| Central-West | 6.44% | R$176.98 | R$23.05 |
| North | 2.60% | R$223.41 | R$36.85 |

**Insights**
- **São Paulo is 37.4% of revenue,** and SP + RJ + MG together are 62.5%. SP also gets the best service: 8.8-day average delivery and 4.5% late.
- **Northern and north-eastern customers spend more per order but pay about twice the freight** (R$32–37 per item vs R$17 in the Southeast). Freight is 22.2% of order value in RR and 20.8% in MA, against 12.2% in SP. High freight is a likely barrier to growth in these regions.
- **Two different delivery problems:**
  - RR, AP and AM are *slow* (26–29 days on average) but rarely late, because their promises are long.
  - AL (21.4%), MA (17.4%) and SE (15.2%) are *unreliable*: the promise is missed.
  - Slow and late correlate only weakly across states (r = 0.32), so each problem needs its own fix.
- **In absolute volume, Rio de Janeiro matters most:** 1,495 late deliveries (12.1% late) against 261 for AL, MA and SE combined.

*Source: SQL Q8, Q8b; EDA sections 8.1, 8.2.*

## 4. Customer retention

| Metric | Value |
|---|---|
| Customers | 94,983 with a purchase |
| Bought exactly once | 92,096 (**96.96%**) |
| Repeat customers | 2,887 (**3.04%**) |
| Avg spend, repeat vs one-time | R$308.35 vs R$161.19 |
| Returning share of monthly active customers | at most 3.05% (0.13% in Jan 2017 → 2.68% in Aug 2018) |
| Second order on the same day as the first | 31.0% of repeat customers |
| Median wait for a genuine second order | 77 days (excluding same-day) |

**RFM segments**

| Segment | Customers | % customers | % revenue | Avg spend |
|---|---|---|---|---|
| Champions | 1,235 | 1.30% | 2.53% | R$321.84 |
| Loyal Customers | 1,652 | 1.74% | 3.13% | R$298.27 |
| New Customers | 18,354 | 19.32% | 19.10% | R$163.73 |
| Potential Loyalists | 18,403 | 19.38% | 19.37% | R$165.61 |
| Need Attention | 18,374 | 19.34% | 17.75% | R$152.01 |
| **At Risk** | 14,001 | 14.74% | **27.51%** | R$309.14 |
| Hibernating | 22,964 | 24.18% | 10.62% | R$72.77 |

**Insights**
- **Retention is the largest untapped opportunity.** The marketplace acquires customers well but almost never sees them again. Even the 3.04% is flattering: about a third of "second orders" were placed the same day, which suggests split baskets rather than loyalty.
- **At Risk customers hold more revenue than any other segment** but have been inactive for 274–730 days.
- **36,757 recent one-time buyers** (New Customers + Potential Loyalists) are the best conversion target, because they bought within the last ~6 months.

*Source: SQL Q9, Q10, Q17, Q19; `docs/rfm.md`; EDA sections 7.1, 7.2.*

## 5. Delivery problems

| Metric | Value |
|---|---|
| Average / median delivery time | 12.56 / 10.22 days |
| Average promised time | 23.73 days (orders arrive 11.18 days early on average) |
| Late deliveries | 6,534 of 96,447 (**6.77%**), 10.62 days late on average when late |
| More than one week late | 2,862 orders |
| Worst months | Mar 2018 **18.96%**, Feb 2018 14.13%, Nov 2017 12.40% |
| Best month | Jun 2018 1.16% |

**Delivery outcome vs review score**

| Outcome | Avg score | 1–2 star share |
|---|---|---|
| 7+ days early | 4.31 | 8.99% |
| 1–6 days early | 4.18 | 10.59% |
| On the promised day | 4.03 | 12.42% |
| 1–3 days late | 3.29 | 32.13% |
| 4–7 days late | 2.10 | 67.68% |
| 8+ days late | 1.70 | 79.29% |
| Not delivered / no valid date | 1.76 | 77.75% |

**Insights**
- **Late delivery is the clearest driver of bad reviews in the data.** The score drops more than a full star as soon as an order is 1–3 days late. Late and undelivered orders cause **42.89% of all negative reviews** while making up only 9.38% of reviewed orders.
- **Logistics capacity lagged demand.** The late rate climbed after Black Friday (12.4% in Nov 2017) and peaked at 19.0% in Mar 2018. It recovered to 1.2% in Jun 2018, and average delivery time fell to 7.7 days by Aug 2018, so the system can work well; it broke under peak load. The late rate was rising again by Aug 2018 (3.4% in Jul, 6.2% in Aug) and should be watched.
- **Promises are heavily padded** (23.7 days promised vs 12.6 actual). This hides delays for most customers but likely hurts conversion: a shopper who sees "arrives in 24 days" may not buy at all.

*Source: SQL Q14, Q15, Q15b, Q16b; EDA sections 3.3, 4.1, 9.1.*

## 6. Payments and order status

- **Credit card is used on 76.94% of orders** and carries 78.34% of payment value. Boleto (bank slip) is used on 19.90% of orders, voucher on 3.89% and debit card on 1.54%.
- **Installments go with larger baskets:** the median card order is R$72 when paid at once, R$111 in 2–3 installments and R$206–214 in 7+ installments. 66.85% of card payments use installments.
- **Order loss is small:** 0.63% of orders are canceled and 0.61% unavailable (1.24% lost in total). 97.02% are delivered. The satisfaction cost of *late* orders is far larger than the cost of *lost* ones.

*Source: SQL Q12, Q12b, Q13, Q13b; EDA sections 4.3, 9.2.*

## 7. Seller concentration

- The **top 10% of sellers (306) generate 67.5%** of product revenue, and the top 20% generate 82.6%. 80% of revenue comes from 540 of 3,053 active sellers (17.7%).
- The bottom 50% of sellers together generate 3.24%.

*Source: SQL Q7, Q7b; EDA section 6.3.*

---

## 8. Recommendations

Ordered by expected impact relative to effort. Each is tied to the evidence above and to a KPI already on the Power BI dashboard.

| # | Recommendation | Evidence | KPI to track |
|---|---|---|---|
| 1 | **Make on-time delivery the top operational priority.** Monitor late rate by state and carrier; set alerts when a state passes 10% late. | Late orders: 62.4% negative reviews vs 9.3% on time; 42.9% of all negative reviews | Late Delivery %, Negative Review % |
| 2 | **Plan logistics capacity before peak events** (Black Friday, post-holiday). Pre-book carrier capacity; temporarily widen promises only in the peak weeks. | Late rate 12.4% (Nov 2017) rising to 19.0% (Mar 2018) after demand peaks | Late Delivery % by month |
| 3 | **Launch a second-purchase programme** for New Customers and Potential Loyalists: a post-delivery email with a time-limited voucher and recommendations from the same category. | 97% buy once; repeat customers spend ~2×; 36,757 recent one-time buyers | Repeat Purchase Rate %, Returning Customer % |
| 4 | **Run a win-back campaign for At Risk customers** (high spend, 274+ days inactive), personalised by their past category. | 14,001 customers holding 27.5% of revenue | Revenue from At Risk segment |
| 5 | **Fix region-specific delivery problems:** review carriers in AL, MA, SE (unreliable) and RJ (largest late volume); make promises more accurate in the North (slow but padded). | AL 21.4%, MA 17.4%, SE 15.2% late; RJ 1,495 late orders; RR/AP/AM 26–29 days | Late Delivery % and Avg Delivery Days by state |
| 6 | **Tighten the promised delivery date** where delivery is reliable (e.g. SP), testing whether shorter promises raise conversion. | 23.7 days promised vs 12.6 actual; SP 8.8 days and 4.5% late | Avg Promised Days vs Avg Delivery Days |
| 7 | **Reduce freight cost for the North and Northeast** (regional warehousing, freight subsidies on high-value orders). These customers already spend the most per order. | Freight R$32–37/item vs R$17 in the Southeast; AOV R$201–223 | Revenue share by region, Freight Share % |
| 8 | **Protect key sellers and grow the long tail:** account management for the top 10%; onboarding support for small sellers. | Top 10% of sellers = 67.5% of revenue | Seller revenue concentration |
| 9 | **Audit low-rated categories** (office_furniture, fashion_male_clothing, audio): check product quality, descriptions and seller performance. | Average review 3.62–3.84 vs median ~4.1 | Avg Review Score by category |
| 10 | **Keep installments prominent** at checkout, especially for higher-priced categories. | Median order R$72 (1 payment) vs R$206+ (7+ installments) | Average Order Value |

## 9. Sizing the retention opportunity (illustrative scenario, not a finding)

To show the scale of recommendation 3, here is one simple scenario built only from observed values:

> If **5%** of the 36,757 recent one-time buyers (≈ 1,838 people) placed one more order at the observed median order value of **R$105.28**, that would add about **R$193,505** in revenue. The repeat-customer rate would rise from **3.04% to about 4.97%** ((2,887 + 1,838) / 94,983).

The 5% conversion rate is an **assumption** chosen for illustration, not a measured result. A real target should come from an A/B test of the second-purchase campaign.

## 10. Limitations

- **No cost or margin data**, so recommendations are judged on revenue and satisfaction, not profit.
- **No product names, marketing or traffic data**, so conversion and campaign effects can't be measured here.
- **Correlation is not causation.** Late delivery is strongly *associated* with bad reviews; the gradient (worse the later the order) makes a causal link plausible, but it isn't proven. Other factors (product quality, seller behaviour) also matter.
- **Partial months** (2016, Sep–Oct 2018) are excluded from trends; totals include all data.
- **The data ends in 2018.** Patterns describe that period, not today's market.
