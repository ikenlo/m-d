# 15 — Current project state (verified)

Verified = read in code **and** covered by a passing test (207/207 on 2026-11-11), unless marked "code only".

## Works (verified)
- Booking from staff panel, client bot and ext API, with overlap prevention, workplace limits, off-hours rules, anti-spam, pending confirmation and suggestions (`test_bookings`, `test_booking_rules`, `test_bot`, `test_ext_api`, `test_antispam`).
- Order close with idempotency, atomic status change, materials and stock write-off, ledger and audit (`test_orders`, `test_concurrency`, `test_stock_schedule`).
- Corrections by storno + new + reason, materials re-applied to stock (`test_orders`, `test_invariants`).
- Shift open/close, report snapshot, cash take, payout due, payouts with no double payment (`test_orders`, `test_late_shift`).
- Dashboard numbers consistent with orders and ledger **under the current model** (`test_invariants`).
- Utilities readings and generator with param snapshots (`test_utilities`).
- Taxes, payroll, FOP declaration XML validated against XSD (`test_tax_*`).
- Card terminal with bank verification (fake provider) (`test_card_terminal`).
- 2FA, password lockout (`test_twofa`, `test_api`).
- Backups with integrity check, Drive upload mocked (`test_gdrive`).
- Bot client and staff flows through the real aiogram dispatcher (`test_bot`).
- Migrations match the models (`test_migrations_cli`).
- UI renders all pages in CI smoke (Playwright) — CI config only, not run here.

## Works, code only (no dedicated test)
- Role restrictions beyond the few permission tests; Drive real upload; Telegram real delivery; reminders scheduling loop; SMS real sending (not configured).

## Not per canon / missing
See `21_CANON_COMPLIANCE.md`. In short: split formula, rounding, Admin, materials modes, utility deductions, statuses, version lock, products, vacation, PIN, login journal, AI abstraction, exports, alerts set, backup SA/encryption.

## Not verifiable here
Production data (how many directors/admins, any `master_charges` rows, whether orders with discounts exist), Railway settings and env vars, GitHub secrets, PR discussions.
