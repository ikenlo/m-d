# 21 — Canon compliance (Section 0 vs code)

Audited code: `ikenlo/m-d`, branch `feat/ext-api-md-manager` @ `2d25bce` (= `main` @ `f834c01` + 1 unmerged commit with `/api/ext/v1`).
Code version: `0.2.1` (`server/mds/__init__.py`). Tests: 207 passed, 0 failed; `ruff check` clean; `node --check app.js` OK (run 2026-11-11).
Verified by reading the code, not comments or docs.

Legend: `MATCHES` · `PARTIAL` · `MISSING` · `MISMATCH` (contradicts the canon, usually legacy) · `EXTRA` (in code, not in the canon).

## Summary

| Canon section | M | P | Miss | MisM |
|---|---|---|---|---|
| 0.1 Money/rates/methods | 3 | 1 | 0 | 0 |
| 0.2–0.3 Split formula & rules | 2 | 0 | 1 | 5 |
| 0.4 Removed items | 0 | 0 | 2 | 6 |
| 0.5 Dashboard | 7 | 2 | 6 | 2 |
| 0.6 Products | 0 | 0 | 9 | 0 |
| 0.7 Markup | 0 | 0 | 1 | 0 |
| 0.8 Lifecycle/idempotency/cash | 7 | 4 | 8 | 2 |
| 0.9 Roles | 9 | 2 | 1 | 3 |
| 0.10 Vacation mode | 0 | 0 | 1 | 0 |
| 0.11 Alerts | 2 | 4 | 7 | 0 |
| 0.12 Stock | 6 | 4 | 4 | 0 |
| 0.13 Utilities/generator | 5 | 0 | 0 | 1 |
| 0.14 Security/reliability | 4 | 5 | 5 | 3 |
| 0.15 Integrations | 2 | 2 | 1 | 4 |
| 0.16 AI | 1 | 0 | 6 | 0 |
| 0.17 UI | 2 | 4 | 6 | 1 |

Bottom line: **the financial core contradicts the canon** (master gets 50 % of the *paid amount*, not the *price*; three materials modes; utility deductions; Admin role). Most of the new canon features (products, vacation mode, PIN, login journal, version lock, order statuses, AI abstraction, exports) **do not exist**.

---

## 0.1 Identity, money, rates, payment methods

| Item | Status | Evidence |
|---|---|---|
| One salon, no multi-tenancy | MATCHES | no tenant columns in `server/mds/models.py` |
| Money = int kopecks | MATCHES | every money column is `Integer`/`BigInteger` (`models.py`); `money.fmt` is display-only |
| Rates in bp | MATCHES | `money.py:3` `BP_100 = 10_000`; `share_bp` columns are ints |
| `orders.payment_method` ∈ {cash, card, transfer} | PARTIAL | `models.py:280` `String(16)` not null; checked in the service (`orders.py:178`), **no DB CHECK/enum** |
| float in money math | MATCHES (low risk) | `float` appears only when parsing quantities and meter readings (`stock.py:16`, `routers/utilities.py:21`), which are converted straight to milli-ints; `sms.py:50` balance is display-only |

## 0.2 / 0.3 Split formula and rules

| Item | Status | Evidence |
|---|---|---|
| `master = P × rate // 10000` | **MISMATCH** | `services/finance.py:21-36`: `compute_split(amount, materials, share_bp, mode)`; default mode `salon` → `master = percent_of(amount, share_bp)`, so it uses **A (paid)**, not P (price) |
| `P = orders.price_snapshot` | MISSING | there is no `price_snapshot` column. `appointments.price` (`models.py:215`) holds the booked price and is the nearest candidate |
| `salon = A − master − M` | MATCHES (in `salon` mode) | `finance.py:35` `salon = amount - materials - master` |
| Invariant `A == master + M + salon` | MATCHES | holds by construction; `tests/test_invariants.py:12` (5 000 random cases) |
| Master protected from discount | **MISMATCH** | master = A × 50 %, so a discount cuts the master's pay |
| Materials on salon | PARTIAL → **MISMATCH** | true only in the default `salon` mode. `master` mode subtracts M from the master (`finance.py:30`); `split` mode uses `(A−M)×s` (`finance.py:32`) |
| Rounding in favour of salon (floor) | **MISMATCH** | `money.percent_of` rounds **half-up** (`money.py:6-8`), so an odd kopeck goes to the master. `tests/test_finance.py:test_rounding_half_up` locks this behaviour in |
| Salon < 0 → alert to both directors, order still closes | MISSING | `close_order` (`orders.py:152`) has no check or alert |
| Master earnings never negative | **MISMATCH** | in `master` mode `A×s − M` can go negative; the open roadmap question "обмежувати нулем…" (`docs/ROADMAP.md`, last section) is still undecided |

## 0.4 Items that must not exist

| Item | Status | Evidence |
|---|---|---|
| `materials_mode` salon/master/split | **MISMATCH** | `models.py:35-40` `MaterialsMode`; `users.materials_mode`, `orders.materials_mode`; setting `default_materials_mode` (`common.py:126`); 42 references |
| Distribution of utilities to masters | **MISMATCH** (exists, **off by default**) | `services/utilities.py:414-608` (`charges_total`, `shift_charge`, `allocate_month`, `reverse_month`); setting `utilities_charge_mode` default `"none"` (`common.py:163`) |
| Admin role | **MISMATCH** | `models.py:30` `Role.ADMIN`; `Actor.is_staff_manager` includes admin (`common.py:45`); 30 `Role.ADMIN` uses; admin `NAV` and mobile tabs in `app.js:543,555`; 2FA roles accept admin |
| Individual `share_bp` | **MISMATCH** | `users.share_bp` editable (`users.py:128` UPDATABLE); `orders.share_bp` snapshot |
| `(A − M) × 50 %` split | **MISMATCH** | `finance.py:32` (`split` mode) |
| Endpoints `/api/utilities/allocation`, `/allocate`, `/reverse`, `/charges` | **MISMATCH** | `routers/utilities.py:159,164,169,174` |
| Tables `master_charge`, `master_charge_reversal` | **MISMATCH** (different name) | the real table is `master_charges` (`models.py:392`); reversal is a row with `reverses_id`, so there is **no** `master_charge_reversal` table |
| `version` on appointments/orders/shifts/payouts/expenses | MISSING | none. `orders.revision` (`models.py:287`) is a counter that `UPDATE … WHERE` never checks |
| `appointments.version` | MISSING | — |

> Note: Section 0.4 lists `version` under "REMOVED", but the text and 2.15 say it must be **added**. This audit reads it as "must be added" (see 04_CONFLICTING_REQUIREMENTS C-17).

## 0.5 Dashboard metrics (`services/analytics.py:58-120`)

| Metric | Status | Evidence / gap |
|---|---|---|
| Real revenue Σ A | MATCHES | `_order_totals` → `revenue` |
| Discounts Σ (P − A) | MISSING | P is not stored on orders |
| Master share Σ (P × 50 %) | MISMATCH | Σ `master_earning`, which is A-based |
| Salon share (intermediate) Σ (A − master) | MATCHES (formula) | `salon_share = revenue - masters_share` (`analytics.py:101`) |
| Material costs Σ M | MATCHES | `materials` |
| Gross profit Σ (A − master − M) | MATCHES (formula) | `analytics.py:90` |
| Gross profit sub-items (margin benefit, by material category) | MISSING | — |
| Rent / Utilities / Ads / Other | MATCHES | `analytics.py:103-106` |
| Generator | MATCHES | `generator_totals` |
| Taxes in net profit | MISSING | the taxes module (`services/taxes.py`) is not subtracted in `net_profit` |
| Net profit | **MISMATCH** | `gross - operating - generator + charged` (`analytics.py:114`), so master charges are **added** to profit |
| Paid to masters | MATCHES | `payouts` |
| Owed = accrued − paid (no deductions) | **MISMATCH** | `salary_debt_total = accrued - paid - charged` (`analytics.py:116`); `master_balance` too (`orders.py:597`) |
| Product bonus line | MISSING | — |
| Unpaid orders (count, sum, >3 d) | MISSING | no `unpaid` status |
| Profitability net / revenue + 5-level scale | PARTIAL | only as text in the profit sheet (`app.js:2635`), no scale |
| Period switch day/week/month | MATCHES | `period_range` (`analytics.py:16`) also has `year` |
| Per-report on/off toggle | MISSING | — |

## 0.6 Products — **MISSING entirely**
No `products`, `order_products` or `bonus_accruals` tables; no 10 % bonus, product refund, product-only order or monthly bonus payout. Blocker: `orders.appointment_id` is NOT NULL + UNIQUE (`models.py:272`), so a product-only order needs a schema decision.

## 0.7 Markup — MISSING
`materials` has no `markup_bp` column.

## 0.8 Lifecycle, statuses, corrections, idempotency

| Item | Status | Evidence |
|---|---|---|
| `appointments.status` pending/confirmed/in_progress/completed/cancelled/no_show | MATCHES | `models.py:43-53` |
| `orders.status` completed/unpaid/cancelled/refunded | MISSING | `orders` has no status column; an order exists only if it is completed |
| 1:1 appointment↔order, order created on close | MATCHES | `orders.appointment_id` unique; `close_order` |
| `unpaid` flow + `POST /api/orders/{id}/mark-paid` | MISSING | — |
| `refunded` (services + products) | MISSING | a refund/payment status was added in 0.1.16 (#27) and removed by the revert to 0.1.14 (#31) |
| `no_show` has no financial effect | MATCHES | `set_status` touches only the appointment |
| Cancel order after close | MISSING | no service for it; an order can only be *corrected* |
| Correction = storno + new + audit, reason required, no time limit for director | MATCHES | `orders.py:259-320` (`order_reversal` + `order_corrected` ledger rows, `audit(...,'correct')`) |
| Difference after paid shift flows to next payout | PARTIAL | it happens implicitly through `balance = accrued − paid`. There is no explicit `correction_overpayment` entry, and the shift's `payout_due` snapshot doesn't change |
| Optimistic lock `version` → 409 with current state | MISSING | — |
| Idempotency: same key → same result | PARTIAL | `orders.idempotency_key` unique column (`orders.py:163-168`) returns the existing order. There is **no** `idempotency_keys` table, `response_json` or 30-day TTL, and it covers only order creation |
| Different key on completed → 409 | MATCHES | atomic `UPDATE … WHERE status IN CLOSABLE` → `Conflict(code="already_closed")` (`orders.py:189-196`) |
| Arbitrary payout tied to master | MATCHES | `confirm_payout(shift_id=None)` (`orders.py:558`) |
| Payout tags advance/bonus/product_bonus/correction_overpayment | MISSING | free-text `note` only |
| Cash at close: taken = min(earn, cash), left, extra | PARTIAL | `shift_report` (`orders.py:404-433`) uses `net_earning = master_earning − utilities charge` (wrong base under canon) and an on/off setting `master_takes_cash` (default on) |
| Shift report = snapshot | MATCHES | `close_shift` stores `sh.report` (`orders.py:493`) |
| Separate master vs director shift view | MISSING | one report shape for both roles (it shows salon earning to the master too) |

## 0.9 Roles

| Action | Status | Evidence |
|---|---|---|
| Only Master + Director | **MISMATCH** | Admin exists (see 0.4) |
| Two equal directors | MATCHES | `notify_directors` loops over all active directors (`notifications.py:19`); there is no director hierarchy |
| Master edits own order while shift open | PARTIAL | non-directors may change **only materials** (`orders.py:261-264`) and the phone (`set_order_phone`), not amount or method |
| Edit after shift closed — director only | MATCHES | `_check_edit` (`orders.py:247`) |
| Close order without materials (flag) | MATCHES (functionally) | `order_missing` (`orders.py:34`) computes it on the fly; there is no stored flag |
| Mark no-show — master | MATCHES | `bookings.set_status` with `_check_access` |
| Force-close shift — director, must enter materials first | **MISMATCH** | `close_shift(force=True)` skips the materials check (`orders.py:480`) |
| Confirm payout / arbitrary payout — director | MATCHES | `actor.require(Role.DIRECTOR)` (`orders.py:568`) |
| Cancel/refund/reversal — director | PARTIAL | correction is director-only; cancel/refund don't exist |
| Inventory / receipt — director | **MISMATCH** | `stock.receive/adjust/writeoff` allow **DIRECTOR + ADMIN** (`stock.py:65,88`) |
| Financial reports — director | MATCHES | `analytics.dashboard` requires director |
| Master sees only own earnings | MATCHES | `list_orders`, `list_payouts`, `my_earnings` force `master_id = actor.user_id` |
| Settings — director | MATCHES | `PUT /api/settings` → service requires director |
| Open/close shift — master and director | MATCHES | `open_shift`/`close_shift` |
| Master history: current shift + month, last 20 | MISSING | `my_earnings` returns day/week/month totals plus the balance |

## 0.10 Vacation mode — **MISSING**
No setting, `permission_requests` table, endpoints or bot flow.

## 0.11 Alerts

| Item | Status | Evidence |
|---|---|---|
| Outbox in same transaction, bot delivers every 3 s | MATCHES | `services/notifications.py`; `bot/runner.py:104` `interval=3.0` |
| Both directors receive | MATCHES | `notify_directors` |
| No duplication / first-processed marker | MISSING | each director gets their own row; there is no shared "handled by" marker |
| Salon < 0 | MISSING | — |
| Master negative balance | MISSING | — |
| Permission request | MISSING | — |
| Earnings change > 50 ₴ | PARTIAL | a correction notifies the master on **any** change (`orders.py:313`); directors aren't told and there is no threshold |
| Unpaid > 3 days | MISSING | — |
| Shift not closed > 12 h | PARTIAL | `reminders.remind_unclosed` (late shift), with a different threshold |
| Profitability < 18 %, negative stock (daily digest) | MISSING | — |
| No-show / new order / cancellation to master | PARTIAL | `notify_appointment` covers new/changed bookings, not orders |
| Disable "important" alerts | MISSING | — |
| In-app bell | MISSING | Telegram only |

## 0.12 Stock

| Item | Status | Evidence |
|---|---|---|
| Materials g/ml/pcs, categories | MATCHES | `Material.unit`, `category` |
| Products stock type | MISSING | — |
| Master enters materials during/after close while shift open | MATCHES | `correct_order` materials-only path |
| After shift closed — director only | MATCHES | `_check_edit` |
| `materials_not_entered` flag | PARTIAL | computed (`order_missing`, `needing_materials` `orders.py:618`), not stored; no `GET /api/orders?materials_not_entered=true` (`/api/orders/needs-materials` exists) |
| Negative stock allowed | MATCHES | `_move` doesn't check |
| Average cost | **MISSING** | `receive` doesn't recompute `unit_cost`; cost comes from the card's pack price |
| Movement log | PARTIAL | `stock_moves` (`models.py:153`): ts, material_id, kind, qty_milli, cost, order_id, user_id, note. Materials only; kinds differ (`receipt/adjust/writeoff/…`); no `item_type`; no unit price column |
| `order_materials.unit_price_snapshot` | MATCHES (renamed) | `order_materials.unit_cost` + `total` stored at close |
| Inventory: actual qty → diff movement | MATCHES | `stock.adjust` (`stock.py:87`) |
| Inventory/receipt director only | MISMATCH | admin is allowed (see 0.9) |

## 0.13 Utilities and generator

| Item | Status | Evidence |
|---|---|---|
| Electricity monthly reading, cost, confirm → one expense | MATCHES | `utilities.create_reading/confirm_reading` |
| Paid amount separate, accrual basis | MATCHES | `meter_readings.paid_amount` |
| Generator snapshot of params, motohours | MATCHES | `generator_runs` fuel_lph/fuel_price/amort snapshot |
| "No power" flag report-only, forecast info | MATCHES | `power_outage`, `generator_forecast` |
| Corrections by reversal + reason | MATCHES | `update_reading`/`correct_run` require a reason |
| No deductions from masters | **MISMATCH** | the charges machinery exists (off by default) |

## 0.14 Security and reliability

| Item | Status | Evidence |
|---|---|---|
| Audit log for changes | MATCHES | `common.audit` called from services |
| Login journal (90 d, Security tab) | MISSING | only `audit` rows `bot_login`/`bot_pin_failed`; web logins are not journalled |
| Daily + manual backup | PARTIAL | local every **6 h** (`config.backup_interval_hours=6`), manual `POST /api/backups` |
| Google Drive **Service Account** | **MISMATCH** | OAuth refresh token (`services/gdrive.py:20-60`, `MDS_GDRIVE_CLIENT_ID/SECRET`) |
| 3 independent copies (prod, encrypted Drive, local PC) | MISSING | 2 copies (server + Drive); Drive copy **not encrypted**; no local-PC export |
| Keep 30 days | **MISMATCH** | local keep = 40 files (`BACKUP_KEEP=40`, ≈10 days at 6 h); Drive keeps a count |
| Integrity check | MATCHES | `PRAGMA integrity_check` (`backup.py:30`) |
| Failure → Telegram | PARTIAL | Drive upload failure alerts directors; a **local** backup failure is only logged (`app.py:39`) |
| Restore test procedure | PARTIAL | manual steps in `docs/OPERATIONS.md:136`; no test script |
| PIN on Finance tab | MISSING | — |
| Archive instead of delete | MATCHES (expenses) | `expenses.is_deleted` + ledger reversal (`expenses.py:60`); the field is `is_deleted`, not `archived_at` |
| Auto-logout 30 min idle | **MISMATCH** | JWT absolute TTL **12 h** (`config.py:19`); no idle timer in `app.js` |
| Session revocation | PARTIAL | `users.token_version` checked in `deps.get_user`; incremented on password change/reset/deactivate; `DELETE /api/auth/devices` revokes trusted devices. No "log out everywhere" button |
| Encryption at rest | MISSING | not implemented, not documented |
| HTTPS | MATCHES (platform) | Railway TLS |
| Secrets via env | MATCHES | `config.py` `MDS_*`; `secret.key` is auto-generated in the data dir if `MDS_SECRET_KEY` is unset |
| Pinned deps | **MISMATCH** | ranges in `server/requirements.txt` (e.g. `fastapi>=0.115,<1`); there is a roadmap item for a lock file |

## 0.15 Integrations

| Item | Status | Evidence |
|---|---|---|
| Manager **pulls** from Connect, hourly | PARTIAL | `/api/ext/v1/visits`, `/ad-spend`, `/catalog`, `/slots` are pull endpoints. Scheduling is on the Manager side |
| Manager → Connect: **recommendations only** | **MISMATCH** | `POST /api/ext/v1/appointments` and `/cancel` let the Manager **create and cancel appointments** (`routers/ext.py:76,95`). The user asked for this on 2026-11-05 ("хай ділиться даними… і навпаки") |
| Auth: API key + HTTPS + OAuth2/JWT + encryption | PARTIAL | static header token `X-MDS-Integration-Token`, constant-time compare; no OAuth2/JWT |
| Data Connect → Manager: purchase prices, expenses by service/master, consumption, stock | MISSING | only visits and ad-spend |
| Bot: role auto, master views earnings/history | MATCHES | `bot/staff.py` |
| Bot login: staff code + personal PIN (scrypt) | MATCHES | `users.check_staff_code`, `verify_bot_pin` (scrypt via `hash_password`) |
| Bot PIN lockout 5 attempts / 15 min | PARTIAL (corrected) | per chat, in memory: `bot/staff.py:23-61`, tested `test_bot.py:274`; resets on restart, not per target user |
| Bot: view + confirm only, no create/edit | **MISMATCH** | staff bot can **close orders** (`bot/staff.py:456`, `idempotency_key=f"tg-close-…"`) |
| PrivatBank POS manual now, auto later | EXTRA / conflict | auto-closing card payment via "Термінал" already exists (`services/card_terminal*.py`, `docs/TERMINAL.md`) |
| Connect does not send SMS (Manager does) | **MISMATCH** | `services/sms.py`, `sms_center.py` (TurboSMS, campaigns, automations), Director "SMS-розсилки" tab |

## 0.16 AI analytics

| Item | Status |
|---|---|
| `GET /api/finance/insights` exists, keep | MATCHES (`analytics.insights`, `routers/money.py:113`) |
| `AIProvider`, `LocalLlamaProvider`, `RulesProvider` | MISSING |
| `POST /api/analytics/recommend`, 1 h cache, fallback chain | MISSING |
| `mds/prompts/financial_analysis.md` | MISSING |
| Default provider `rules` config | MISSING |

## 0.17 UI

| Item | Status | Evidence |
|---|---|---|
| Finance tab order (8 blocks) | PARTIAL | `VIEWS.dashboard` (`app.js:2515`) shows net/gross/average check, "Куди йдуть гроші", debt; no discounts, product bonus, unpaid or scale; order differs |
| Gross profit expandable | PARTIAL | `profitSheet` opens a breakdown, but not margin benefit or material categories |
| Separate Analytics tab | MISSING | insights/forecast are embedded in the dashboard |
| Pie chart, expenses-up, average check per service | PARTIAL | "Куди гроші" and `checkSheet` exist; no pie |
| My Finance (master) | PARTIAL | `VIEWS.earnings` (`app.js:2037`): day/week/month and balance; no 7-day bar chart, top-3 or last-20 history |
| Calendar booked slots dark-red with time range | **MISMATCH** | colour by status (`style.css:354-357`: warn/info/prog/ok) |
| Overlap prevention + suggest nearest windows | MATCHES | `bookings._check_slot`, `suggest_slots`, `free_windows` |
| Service icon + name + duration | PARTIAL | quick-booking templates have icons; calendar cards show the name |
| Mobile-first | MATCHES | extensive mobile work 0.1.31–0.2.2 |
| Export PDF + Excel | MISSING | CSV only (tax report, quarterly payroll, clients). The Excel accountant pack (0.1.15–0.1.16) was reverted in #31 |
| Report on/off toggles | MISSING | — |

## EXTRA (in code, not in canon)
Taxes & payroll, FOP declaration XML (F0103407), tax income report; SMS centre; card terminal (NFC); client base (segments, merge, anonymize, CSV); callback requests; 2FA via Telegram + trusted devices; anti-spam; reminders to clients; demo data generator (`demo_fop1.py`) and `wipe-data`; performance RUM `/api/perf`; bug report to Telegram; human-readable journal feed; ext API for MDs Manager; Android/Windows shells; `website` branch. None of these are forbidden by the canon, but they are outside it. See `04_CONFLICTING_REQUIREMENTS.md` for the ones that clash with it (SMS, terminal, bot actions, ext writes).
