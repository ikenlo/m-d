# 08 — Database audit (no changes made)

## Engine
- Default SQLite file `data/mds.db` (Railway volume). PostgreSQL is supported by SQLAlchemy, but backups are SQLite-only (`backup.py:20-22` raises for PG).
- Pragmas per connection (`db.py:44-48`): `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000`. The SQLite connect timeout is 30 s.
- Transactions: one `Database.session()` per request (commit on success, rollback on exception). Services never commit (CLAUDE.md rule; spot-checked). Concurrency for booking and closing relies on `UPDATE users SET booking_lock = booking_lock + 1` (row lock in PG, write lock in SQLite) plus atomic conditional `UPDATE`s.
- `create_all()` exists (`db.py:26`) for tests; production uses Alembic `upgrade head` at `start`.

## Schema (33 tables + `master_services`)
Core: `users`, `services`, `master_services`, `clients`, `appointments`, `appointment_attribution` (0025, unmerged branch), `orders`, `order_materials`, `shifts`, `payouts`, `expenses`, `ledger`, `audit_log`, `settings`, `notifications`.
Stock: `materials`, `stock_moves`. Schedule: `schedule_days`.
Utilities: `meter_readings`, `generator_runs`, `master_charges`.
Payments: `card_payments`. Taxes/HR: `tax_rates`, `tax_accruals`, `tax_payments`, `payroll_accruals`, `payroll_payments`, `employee_profiles`.
Auth: `login_challenges`, `trusted_devices`. CRM: `callback_requests`, `sms_campaigns`, `sms_messages`. Orphan: `expense_files`.

## Keys and constraints (selected)
- `orders.appointment_id` UNIQUE, `orders.idempotency_key` UNIQUE, `payouts.shift_id` UNIQUE: double-posting guards. Good.
- `clients.phone`, `telegram_user_id`, `instagram_user_id` UNIQUE.
- `generator_runs.running` UNIQUE (only one running; NULL when stopped). A neat trick.
- `master_charges.dedupe_key`, `tax_accruals.dedupe_key`, `payroll_accruals.dedupe_key` UNIQUE.
- **No CHECK constraints** (status enums, payment_method, amounts ≥ 0) — validated only in services.
- FKs: most relations have FKs; 7 `ondelete` rules (CASCADE on `master_services`, `order_materials`, `schedule_days`, auth tables; SET NULL on `sms_messages.client_id`). `ledger.ref_id`, `ledger.master_id`, `audit_log.user_id`, `stock_moves.user_id`, `meter_readings.created_by` are plain ints without FKs (intentional for append-only logs).
- Indexes: added in 0023 (`ix_appt_*`, `ix_order_*`, `ix_audit_*`, `ix_notif_pending`, `ix_expense_spent_on`, `ix_stock_material`, …). **No index on `ledger(kind, ts)` or `ledger(master_id)`**; journal/ledger views will slow down as it grows.

## Canon gaps (schema)
| Needed by canon | Present? |
|---|---|
| `orders.price_snapshot`, `orders.master_rate_bp` | no (`share_bp` exists) |
| `orders.status` (completed/unpaid/cancelled/refunded) | no |
| `version` on appointments/orders/shifts/payouts/expenses | no (`orders.revision` = counter) |
| `idempotency_keys` table | no (column on orders) |
| `payouts.tag` | no |
| `materials.markup_bp` | no |
| `products`, `order_products`, `bonus_accruals` | no |
| unified `stock_movements` (item_type, reason, price) | partial: `stock_moves` (materials only) |
| `orders.materials_not_entered` | computed, not stored |
| `permission_requests`, vacation settings | no |
| `login_events` | no |
| `users.finance_pin_hash` | no |
| `expenses.archived_at` | `is_deleted` (bool) |
| drop `master_charges`, Admin, `materials_mode` | all present |

## Deletion / archival
- Financial rows are never deleted by services: expenses soft-delete; orders are corrected, not deleted; ledger and audit are only inserted to.
- **Exceptions:** `wipe.py` (CLI + `MDS_WIPE_DATA` on start) deletes financial tables (09 S-3); `clients/purge-no-phone` and `clients/{id}/anonymize` remove or blank client PII (a reasonable GDPR-style feature, audited); `ON DELETE CASCADE` on `order_materials` would remove material lines if an order were ever deleted (not done in code).

## Backups / restore — see 17.

## Migrations
- Linear chain 0001 → 0024 on `main`; 0025 only on `feat/ext-api-md-manager`. Phase 2 migrations must be numbered after 0025 **only if that branch is merged first**, otherwise there will be a conflicting head. Decide C-5 before writing Phase 2 migrations.
- `test_migrations_match_models` compares autogenerate against the models. Good.
- SQLite ALTER limitations: renaming `share_bp` → `master_rate_bp` and adding NOT NULL columns need `batch_alter_table` (table rebuild). The table is locked during the rebuild; it's small, so this is fine. Backfill `price_snapshot` for historical orders: **decision C-1/C-3** (recommend `appointments.price`, falling back to `amount`).
- Risk: 0019 made `clients.phone` nullable. Check that any data migration handles NULL phones.

## Sensitive data in DB
Client names/phones/birthdays/notes, staff phones, `employee_profiles.tin` (tax ID), password and PIN hashes (scrypt), 2FA code hashes, trusted device token hashes, card payment RRN/approval code/masked PAN (no full PAN). **No encryption at rest** (canon §0.14 → MISSING).

## Corruption risks
SQLite on a network volume with WAL: OK on Railway (local block storage). Backups use the SQLite online-backup API, which is consistent. Integrity is checked on every backup. Risk: the 6-hourly cadence loses up to 6 h of orders on volume loss.
