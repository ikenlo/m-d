# 22 — UI: Finance / Analytics / My Finance

Frontend: `server/mds/web/static/app.js` (single file). Director NAV (`app.js:545-553`): Огляд, Календар, Графік, Клієнти, Склад, Замовлення, Зміни й виплати, Витрати, Комунальні платежі, Звіт для податкової, Податки і зарплата, SMS-розсилки, Персонал, Послуги, Налаштування, Журнал, Профіль. Master NAV (`:539-541`): Сьогодні, Календар, Мої замовлення, Заробіток, Зміна, Профіль.

## Finance tab (director) — `VIEWS.dashboard` (`app.js:2515`)
Present: period picker; KPI tiles (revenue, net profit, gross profit with sparkline, average check) with previous-period chips; "Куди йдуть гроші" (revenue → masters → materials → gross → expenses → net); salary block (paid, debt now); by-master table (orders, revenue, accrued, paid, debt); by-service; insights and forecast cards (`insightsBlock`, `:2652`); profit sheet with profitability as text (`:2635`); average-check sheet.

| Canon §0.17 order | State |
|---|---|
| 1 Revenue / Discounts | revenue ✅, discounts ❌ |
| 2 Master / Salon / Materials | ✅ (values non-canon) |
| 3 Gross (clickable sub-items) | clickable ✅ → breakdown sheet; margin benefit and material categories ❌ |
| 4 Rent/Utilities/Ads/Taxes/Generator/Other | partial (no taxes) |
| 5 Net | ✅ (formula non-canon) |
| 6 Profitability + scale | text only ❌ scale |
| 7 Paid / Owed / Product bonus | paid ✅ owed ✅ (with deductions) bonus ❌ |
| 8 Unpaid orders | ❌ |
| Period day/week/month | ✅ |
| Per-report toggles | ❌ |
| PDF / Excel export | ❌ |

## Analytics tab — ❌ not separate
Insights and forecast live in "Огляд". Pie chart ❌ (bars/sum-lines instead). Recommendations ❌ (insights are comparisons, not recommendations). Average check per service: partly (`checkSheet`).

## My Finance (master) — `VIEWS.earnings` (`app.js:2037`) + `VIEWS.shift` (`:2099`)
| Canon | State |
|---|---|
| Current shift: orders, earnings, to pay | ✅ in "Зміна" (report incl. cash take) |
| Month: earnings, paid, debt, product bonus | partial: day/week/month earnings and balance; product bonus ❌ |
| Last-7-days bar chart | ❌ (sparkline exists on some tiles) |
| Top-3 services by count with earnings | ❌ |
| History (last 20) | ❌ (payouts list available via API) |

## Logic view (2.16.1) — ❌ not present
Not in NAV/VIEWS; no `static/vendor/`. Mermaid v11 minified is about 2.5–3 MB (unverified size, network not used). It must be lazy-loaded only on that view (not in `index.html` globally) to keep the startup budget from `docs/PERFORMANCE.md`, and excluded from or lazily added to the service-worker precache. Recommendation for Phase 2 (C-23/C-24).

## UI language
All visible strings checked in NAV and dashboard are Ukrainian. ✅
