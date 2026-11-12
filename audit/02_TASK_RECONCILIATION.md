# 02 — Task reconciliation

Status values: COMPLETED · PARTIALLY COMPLETED · NOT IMPLEMENTED · IMPLEMENTED BUT UNVERIFIED · FAILED · SUPERSEDED · CANCELLED · CANON_MISMATCH · CANON_PARTIAL · UNCLEAR.
Each item was checked against the code (file:line). Dependencies refer to other IDs.

## A. Historical requests (from 01)

| ID | Instruction | Status | Evidence | Remaining | Action |
|---|---|---|---|---|---|
| H-1 | Core v0.1 (orders, split, shifts, payouts) | CANON_MISMATCH | `finance.py:21`, `orders.py:152` | rewrite split | Phase 2 §2.1 |
| H-5 | Cash after shift | COMPLETED (matches §0.8) | `orders.py:404-433` | remove the utilities-charge coupling | §2.2 |
| H-6 | Drive backups every 6 h | CANON_PARTIAL | `backup.py:52`, `gdrive.py` (OAuth) | SA, encryption, 3rd copy, age retention | §2.10 |
| H-7 | Analytics & forecast | COMPLETED | `analytics.insights`, `forecast_month`; `test_insights.py` (3) | — | keep |
| H-10 | Utility distribution to masters | SUPERSEDED (code still present) | `utilities.py:414-608` | delete | §2.2 |
| H-15 | Terminal auto-close | COMPLETED / conflict C-8 | `card_terminal.py:114`; tests (9) | decision | — |
| H-17 | Taxes & payroll | COMPLETED (EXTRA) | `taxes.py`; `test_taxes_payroll.py` (8) | — | keep |
| H-18 | Excel pack, refunds, payment status, expense files, owner screen | CANCELLED (reverted #31) | `git show 330a470`. The `expense_files` **table remains** (`models.py:649`), unused | refunds/unpaid are back in the canon | §2.17 / §2.11 |
| H-19 | Wipe data | COMPLETED (dev tool) | `wipe.py`, `test_wipe.py` | guard against prod (see 09) | report |
| H-22 | 2FA | COMPLETED | `twofa.py`; `test_twofa.py` (5) | — | keep |
| H-24 | Public site + public booking API | CANCELLED in main (#70) | branch `website` | — | — |
| H-26 | Lateness shift via SMS/Viber | IMPLEMENTED BUT UNVERIFIED (real SMS sending) | `sms.py`; TurboSMS token not configured (ROADMAP 1.0.0) | decision C-6 | — |
| H-30 | SMS campaigns | PARTIALLY COMPLETED (no real sending) | `sms_center.py`; `test_sms_center.py` (2) | decision C-6 | — |
| H-34 | Version 0.2.0 | COMPLETED (tag exists) | `git tag v0.2.0` | — | C-15 |
| H-36 | My orders / authorship (0.2.1) | COMPLETED | migration 0024, `test_master_orders.py` (5) | — | — |
| H-38 | 0.2.2 stages A-F | COMPLETED, unreleased | CHANGELOG "[Невипущене]" | release | fold into the next release |
| H-39 | ext API for MDs Manager | IMPLEMENTED, unmerged; CANON_MISMATCH (writes) | `routers/ext.py`, `test_ext_api.py` (10) | decision C-5 | — |
| R-1 | ROADMAP: lock file for deps | NOT IMPLEMENTED | `requirements.txt` ranges | — | §2.12 |
| R-2 | ROADMAP: CSP for the panel | NOT IMPLEMENTED | `app.py:136-150` sets no CSP | — | §2.12 |
| R-3 | ROADMAP: Excel export | NOT IMPLEMENTED (reverted) | — | — | §2.11 |
| R-4 | ROADMAP: Android signing key + first Release | NOT IMPLEMENTED / UNCLEAR | `apps.yml` — GitHub secrets not checked | — | owner action |
| R-5 | ROADMAP: native Flutter apps | NOT IMPLEMENTED; likely SUPERSEDED by the shells | `apps/` | — | Phase 3 |
| R-6 | ROADMAP: master zero-floor decision | SUPERSEDED (canon removes modes) | — | — | close |
| R-7 | ROADMAP 1.0.0: TurboSMS, FOP data | NOT IMPLEMENTED | — | C-6 | Phase 3 |

## B. Canon / Phase 2 batches

| ID | Canon item | Status | Evidence | Remaining work | Depends | Risk |
|---|---|---|---|---|---|---|
| 2.1 | Canon split, `price_snapshot`, `master_rate_bp`, floor | CANON_MISMATCH | `finance.py:21-36`, `money.py:6` | new columns + migration; rewrite `compute_split`, `close_order`, `correct_order`; tests | C-1, C-3 | **high** (money) |
| 2.1 | `payment_method` on orders | COMPLETED (no DB enum) | `models.py:280` | optional CHECK constraint | — | low |
| 2.1 | `unit_price_snapshot` | COMPLETED (as `order_materials.unit_cost`) | `models.py:311` | rename or alias only | — | low |
| 2.1 | `version` columns + `idempotency_keys` table | NOT IMPLEMENTED | — | migration, service guard, 409 body | — | medium |
| 2.2 | Remove utility distribution, endpoints, table | CANON_MISMATCH | `routers/utilities.py:159-177`, `models.py:392` | delete code, migration drop, UI section in `VIEWS.utilities` | C-11 | medium (prod data?) |
| 2.2 | Remove Admin | CANON_MISMATCH | 30 refs + JS NAV | role migration, MDs Manager mapping | C-4 | **high** (lockout) |
| 2.2 | Remove materials_mode | CANON_MISMATCH | 42 refs | stop reading; keep column | 2.1 | medium |
| 2.3 | Products, order_products, bonus_accruals, stock_movements | NOT IMPLEMENTED | — | full feature | C-20 | medium |
| 2.4 | 10 alert types, hourly cron, in-app bell, de-dup | CANON_PARTIAL | `notifications.py`, `reminders.py` | most types | 2.1, 2.17 | low |
| 2.5 | Vacation mode + permission requests | NOT IMPLEMENTED | — | full feature | 2.17 | medium |
| 2.6 | Finance PIN | NOT IMPLEMENTED | — | — | C-21 | low |
| 2.7 | Login journal + Security tab | NOT IMPLEMENTED | — | — | — | low |
| 2.8 | AI abstraction + `/api/analytics/recommend` | NOT IMPLEMENTED (insights exists) | `analytics.insights` | — | — | low |
| 2.9 | Calendar dark-red booked slots, overlap + suggest | CANON_PARTIAL | `style.css:354`; `bookings.suggest_slots:553` | styling, labels | — | low |
| 2.10 | Backups SA/encrypted/3 copies/30 d/restore script | CANON_PARTIAL | `backup.py`, `gdrive.py` | see 17 | C-9 | medium |
| 2.11 | Dashboard per §0.5, my-finance, exports, toggles | CANON_PARTIAL | `analytics.py:58-188` | discounts, taxes, unpaid, bonus, sub-items, exports | 2.1, 2.3, 2.17 | medium |
| 2.12 | Archive expenses | COMPLETED (as `is_deleted`) | `expenses.py:60` | rename optional | — | low |
| 2.12 | Auto-logout 30 min, revocation, encryption doc, pinned deps | CANON_PARTIAL | `config.py:19`, `deps.py:31` | — | C-22 | medium |
| 2.13 | stock_movements, materials flag, force-close requires materials, inventory | CANON_PARTIAL | `stock.py`, `orders.py:480,618` | unify log; force-close rule | C-14 | low |
| 2.14 | Cash formulas | COMPLETED (minus coupling) | `orders.py:404` | — | 2.2 | low |
| 2.14 | Payout tags, nullable shift_id | CANON_PARTIAL (shift_id already nullable) | `models.py:321` | `tag` column | — | low |
| 2.14 | Shift report master vs director view | NOT IMPLEMENTED | `shift_public` | — | — | low (master currently sees salon share) |
| 2.14 | `mark-paid` | NOT IMPLEMENTED | — | — | 2.17 | medium |
| 2.14 | Order card shows last 5 changes | CANON_PARTIAL | `order_history` (`orders.py:345`) returns all | UI limit | — | low |
| 2.15 | Correction overpayment entries, cancellation after paid | CANON_PARTIAL | balance-based | explicit entries | 2.14 | medium |
| 2.15 | Version conflict 409, idempotency TTL | NOT IMPLEMENTED | — | — | 2.1 | medium |
| 2.16 | Finance / Analytics / My Finance UI | CANON_PARTIAL | `app.js:2037,2515` | rebuild per §0.17 | 2.11 | medium |
| 2.16.1 | "Логіка системи" Mermaid view, `/api/finance/logic-stats`, vendored mermaid v11 | NOT IMPLEMENTED | — | — | 2.1 (numbers must be canon), C-23, C-24 | low; mermaid.min.js ≈ 2.5–3 MB affects PWA cache and the perf budget in `docs/PERFORMANCE.md` |
| 2.17 | `orders.status` unpaid/refunded/cancelled | NOT IMPLEMENTED | — | — | — | **high** (touches every report query) |
| 2.18 | FINANCE.md = canon | CANON_MISMATCH (doc describes old model) | `docs/FINANCE.md` | Phase 1 | — | — |
| 0.15 | Bot PIN lockout | PARTIALLY COMPLETED (in-memory, per chat) | `bot/staff.py:23-61`, `test_bot.py:274` | persist per user in DB | — | medium (security) |
| 0.15 | Bot view-only | CANON_MISMATCH | `bot/staff.py:456` | C-7 | — | — |
| 0.15 | Manager pull: purchase prices, expenses, consumption, stock | NOT IMPLEMENTED | `integration.py` | new ext endpoints | C-5 | low |
