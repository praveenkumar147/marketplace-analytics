# RFM Customer Segmentation

**RFM** scores each customer on three behaviours that predict future value:

| Letter | Measures | Here | Why it matters |
|---|---|---|---|
| **R**ecency | How recently they bought | Days from last purchase to the snapshot date | Recent buyers are the most likely to buy again |
| **F**requency | How often they buy | Number of revenue orders | Repeat buyers are proven loyal |
| **M**onetary | How much they spend | Total price + freight across their orders | Who is worth the most |

Run it:

```bash
python -m src.analysis.rfm              # segments, charts, reports/rfm_segmentation.md
python -m src.analysis.rfm --to-mysql   # also (re)loads MySQL table customer_rfm for Power BI
```

Outputs:
- `data/processed/customer_rfm.csv`: one row per customer
- MySQL table `customer_rfm`
- `reports/rfm_segmentation.md`
- the charts `reports/charts/10_rfm_*.png`
- the SQL version, `sql/analysis/05_rfm.sql` (query `q19_rfm_segments`)

## 1. Method

1. **Customer = `customer_unique_id`.** Only revenue orders that have items count, which gives 94,983 customers and R$15,735,527.03, exactly the total revenue from Module D.
2. **Snapshot date = 2018-09-04**, the day after the last revenue purchase. "Today" in the analysis is the end of the data, not the real current date, which would make everyone look inactive.
3. **R and M are quintiles (scores 1-5)** with `NTILE(5)` semantics: customers are sorted and cut into five equal-sized groups.
   - R sorts oldest first, so 5 = most recent.
   - M sorts lowest spend first, so 5 = highest spend.
   - Ties are broken by `customer_unique_id`, so Python (`ntile()` in `rfm.py`) and MySQL produce **identical** scores. A test checks this.
4. **F is rule-based, not quintiles.** 96.96% of customers have exactly one order. Quintiles would give four of the five groups the same value (1 order), which carries no information. F is therefore 1 for one order, 4 for two orders and 5 for three or more, and the segment rules use it as **one-time vs repeat buyer**.

What the scores mean in practice:

| Score | Recency (days since last purchase) | Monetary (total spent) |
|---|---|---|
| 5 | 1-99 | R$209.11 - R$13,664.08 |
| 4 | 99-183 | R$133.05 - R$209.11 |
| 3 | 183-274 | R$87.51 - R$133.05 |
| 2 | 274-389 | R$55.36 - R$87.50 |
| 1 | 389-730 | R$9.59 - R$55.36 |

(A boundary value can appear in two neighbouring groups because NTILE splits tied values to keep the groups equal in size.)

## 2. Segment rules

The rules are evaluated top to bottom. Every customer falls into exactly one segment, and a test enumerates all score combinations to confirm it.

| Segment | Frequency | R | M | Meaning and recommended action |
|---|---|---|---|---|
| Champions | 2+ orders | 4-5 | any | Bought more than once and recently. Reward; ask for reviews and referrals. |
| Loyal Customers | 2+ orders | 1-3 | any | Proven repeat buyers who aren't recent. Win-back offer before they lapse. |
| New Customers | 1 order | 5 | any | First purchase in the most recent fifth. Onboarding; drive a second order. |
| Potential Loyalists | 1 order | 4 | any | Recent one-time buyers: the best conversion target. |
| Need Attention | 1 order | 3 | any | Middle-aged one-time buyers. Reminders and recommendations. |
| At Risk | 1 order | 1-2 | 4-5 | Big spenders who never came back. Personal win-back offer. |
| Hibernating | 1 order | 1-2 | 1-3 | Low-value, long-inactive buyers. Low-cost mass campaigns only. |

## 3. Results

| Segment | Customers | % customers | % revenue | Avg spend | Avg recency (days) |
|---|---|---|---|---|---|
| Champions | 1,235 | 1.30% | 2.53% | R$321.84 | 94.4 |
| Loyal Customers | 1,652 | 1.74% | 3.13% | R$298.27 | 326.9 |
| New Customers | 18,354 | 19.32% | 19.10% | R$163.73 | 51.2 |
| Potential Loyalists | 18,403 | 19.38% | 19.37% | R$165.61 | 141.6 |
| Need Attention | 18,374 | 19.34% | 17.75% | R$152.01 | 226.2 |
| At Risk | 14,001 | 14.74% | **27.51%** | R$309.14 | 400.8 |
| Hibernating | 22,964 | 24.18% | 10.62% | R$72.77 | 402.2 |

Python and SQL return exactly these counts.

**What the data says**
- **At Risk is the most valuable segment.** 14.74% of customers hold 27.51% of revenue, spending R$309.14 on average, yet none has bought in the last 274 days. They are the clearest win-back target.
- **Repeat buyers are worth about twice as much,** R$308.35 on average against R$161.19 for one-time buyers. They are only 3.04% of customers (Champions + Loyal = 5.66% of revenue).
- **The conversion opportunity is New Customers + Potential Loyalists:** 36,757 recent one-time buyers (R$6.05M of revenue). Converting even a small share into second purchases would raise the repeat rate meaningfully.
- **Recency and spend are independent** (Spearman correlation −0.015). The grid chart shows one-time buyers spread almost evenly across all 25 R×M cells, so there is no sign that recent customers spend more or less than older ones.

## 4. Limitations

- **Frequency barely varies:** with 97% single-purchase customers, this is in practice an RM segmentation plus a repeat flag. That's stated openly rather than forcing quintiles that would mislead.
- **Quintiles are relative:** "R = 5" means the most recent fifth of *this* dataset, not a fixed number of days. The table in section 1 translates the scores into days and R$.
- **No profit data:** Monetary is revenue, not margin.
- **Same-day repeat orders:** 31% of second orders were placed the same day as the first (see the EDA notebook). Some "repeat" customers may have split one basket, which slightly flatters the Champions and Loyal counts.
- **Clustering (e.g. k-means) was deliberately not used.** The project's goal is transparent analytics, and rule-based RFM segments can be explained to a business owner in one sentence each.
