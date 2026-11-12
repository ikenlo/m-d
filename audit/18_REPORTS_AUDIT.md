# 18 — Financial reports alignment (§0.5)

Source: `services/analytics.py` (`dashboard` :58, `by_master` :123, `by_service` :138, `by_day` :159, `my_earnings` :171, `forecast_month` :207, `insights` :248), `orders.shift_report`, `orders.master_balance`.

| Metric | Correct per canon? | Notes |
|---|---|---|
| Real revenue Σ A | ✅ | from `orders.amount` by `closed_at` |
| Discounts Σ (P − A) | ❌ not computed | needs `price_snapshot` |
| Master share | ❌ | Σ A-based `master_earning` |
| Salon share (intermediate) Σ (A − master) | ✅ formula | wrong master input |
| Materials Σ M | ✅ | |
| Gross = Σ (A − master − M) | ✅ formula | equals Σ `salon_earning` per order (invariant) |
| Gross sub-items | ❌ | |
| Rent, utilities, ads, other | ✅ | categories `equipment`, `household` are **not** included anywhere in profit (03 U-12) |
| Taxes | ❌ | not in net |
| Generator | ✅ | from runs, not expenses |
| Net | ❌ | `+ master_charges` |
| Paid to masters | ✅ | payouts by `paid_at` in period (cash taken from till included) |
| Owed (no deductions) | ❌ | `accrued − paid − charged` |
| Product bonus | ❌ | |
| Unpaid orders | ❌ | |
| Profitability + scale | ⚠️ | text only |
| Cash vs card | ✅ `by_payment` in dashboard and shift report | other reports (by_master, by_service, by_day) don't split by method |
| Consistency with transactions | ✅ `test_invariants.test_ledger_orders_dashboard_consistent` checks Σ orders = Σ ledger (order kinds) = dashboard | only for the current model |
| Period switch | ✅ day/week/month/year + custom from/to | |
| Report toggles | ❌ | |
| Export PDF/Excel | ❌ (CSV: tax income, quarterly payroll, clients) | |

## Subtle points to carry into Phase 2
- Orders are attributed to the period by `closed_at`, expenses by `spent_on` and payouts by `paid_at`. Fine, but document it.
- When `orders.status` arrives, decide per metric whether `unpaid` counts in revenue (canon: master accrues, so revenue is probably "accrued" and cash is separate). **DECISION** (03/04).
- The `shift_closed` ledger summary rows must stay excluded from sums (they are today, by `kind` filter).
