# 06 — Business logic catalogue (money, appointments, orders, stock first)

Format: **name** (location) — purpose · inputs → outputs · DB R/W · permissions · side effects · errors · tests · criticality.

## Money & orders (`services/orders.py`, `services/finance.py`)
- **compute_split** (`finance.py:21`): split A into master/materials/salon · (amount, materials, share_bp, mode) → `Split` · no DB · — · — · ValidationError on negatives/bad bp/mode · `test_finance`, `test_invariants` · **critical, CANON_MISMATCH**.
- **percent_of** (`money.py:6`): bp percentage, half-up · ints → int · — · `test_finance` · critical.
- **close_order** (`orders.py:152`): appointment → order · appt_id, amount, payment_method, materials[], idempotency_key, client_name → Order · R appointments, users, settings, materials; W appointments (status), orders, order_materials, shifts (auto-open), stock_moves, materials.stock, ledger, audit, clients.name · master own / staff manager · opens shift, writes off stock · NotFound/Forbidden/Validation/Conflict(already_closed) · `test_orders`, `test_concurrency`, `test_api`, `test_bot`, `test_card_terminal` · **critical**.
- **correct_order** (`orders.py:259`): fix an order · order_id, data{amount, payment_method, share_bp, materials_mode, materials}, reason → Order · W orders, order_materials, stock, ledger ×2, audit, notifications · director; master: materials only, own, shift open · stock reverse/apply, notify master · Forbidden/Validation · `test_orders`, `test_invariants`, `test_master_orders` · **critical**.
- **set_order_phone** (`orders.py:314`): add the client phone from an order; may re-link the order to an existing client · W clients, orders, appointments, audit · own while open / staff manager · `test_no_phone`, `test_master_orders` · medium.
- **open_shift** (`orders.py:135`) · W shifts, users.booking_lock, audit · own / staff manager · medium.
- **shift_report** (`orders.py:378`): totals, cash take, payout due · R orders, appointments, settings, master_charges · — · high.
- **close_shift** (`orders.py:472`): close, snapshot, cash take payout, notify · W shifts, payouts, ledger ×2, audit, notifications, master_charges (fixed mode) · own / director (force) · Conflict(incomplete_orders) · `test_orders`, `test_late_shift` · **critical**.
- **confirm_payout** (`orders.py:558`) · W shifts (status), payouts, ledger, audit, notifications · director · Conflict(already_paid) · `test_orders`, `test_concurrency` · **critical**.
- **master_balance** (`orders.py:597`): accrued − paid − charged · R orders, payouts, master_charges · — · high, CANON_MISMATCH (charges).
- **list_orders / list_shifts / list_payouts / get_shift** · scoped to the master for masters · medium.
- **needing_materials / incomplete_orders / order_missing** · materials/phone flags · medium.
- **order_history** (`orders.py:345`) · audit rows for an order · director or own master · low.

## Analytics (`services/analytics.py`)
- **dashboard** (:58) — §0.5 metrics (see 18) · director · high.
- **by_master / by_service / by_day** (:123-170) · director · medium.
- **my_earnings** (:171) · master own / director · medium.
- **forecast_month / insights** (:207, :248) · comparisons with previous period, trend, forecast · director · `test_insights` · low.

## Appointments (`services/bookings.py`)
- **create_appointment** (:231): validates client, service, master, slot, anti-spam (clients), places, off-hours; locks master · W clients, appointments, audit, notifications · staff/client · Conflict on overlap · `test_bookings`, `test_booking_rules`, `test_antispam`, `test_bot`, `test_ext_api` · **critical**.
- **reschedule** (:323), **set_status** (:399), **decline_busy/propose** (:577) · same guards · high.
- **free_slots / free_windows / suggest_slots / booking_options / free_slots_any** (:179-570) · slot algorithms · R schedule_days, users.schedule, appointments · high.
- **_lock_master / _check_slot** (:175, :215) · concurrency + overlap · critical.

## Stock (`services/stock.py`)
- **receive** (:64) · W materials.stock, stock_moves, audit, (expense) · director/admin · medium.
- **adjust** (:87) inventory · **writeoff** (:101) · director/admin.
- **apply_order / reverse_order** (:133, :144) · consumption by order · called from orders.
- **_auto_writeoff** (:122) · crumbs < 0.1 pack.

## Expenses (`services/expenses.py`)
- **create/update/delete_expense** · director · W expenses, ledger, audit · delete = soft + reversal · medium.

## Utilities (`services/utilities.py`)
- Readings: **create/update/confirm_reading** (:96-171) · director · confirm → expense "utilities" · `test_utilities`.
- Generator: **start/stop/add_run/record_hours/correct_run/set_motohours/forecast/totals** (:216-413) · start/stop: all staff; corrections: director.
- Charges (to remove): **charges_total, _charge, shift_charge, charge_on_shift_close, allocation_preview, allocate_month, reverse_month, charges_list** (:414-578).

## Users & auth (`services/users.py`, `twofa.py`, `security.py`)
- **authenticate** (lockout 5/15), **create_user/update_user** (share_bp, materials_mode, role), **reset_password**, **change_password** (token_version++), **check_staff_code**, **verify_bot_pin** (no lockout), **effective_terms**.
- 2FA: **applies/start/verify/remember_device/device_ok**.

## Notifications (`services/notifications.py`)
- **enqueue / notify_directors / notify_appointment / pending / mark_sent / mark_failed** · outbox, max 5 attempts.

## Other services (EXTRA)
`taxes.py` (rates, FOP accruals, payroll), `tax_report.py`, `tax_declaration.py` (XML), `card_terminal*.py`, `client_base.py`, `callbacks.py`, `antispam.py`, `reminders.py`, `sms.py`, `sms_center.py`, `gdrive.py`, `backup.py`, `bugreport.py`, `journal.py`, `journal_feed.py`, `integration.py` (ext API), `schedule.py`, `catalog.py`, `sync.py` (live refresh), `common.py` (Actor, audit, ledger, settings).

## API ↔ service map
Routers are thin (verified samples: `money.py`, `calendar.py`, `utilities.py`). Exceptions where the router does some logic: `routers/system.py` (backups, gdrive OAuth callback, health), `routers/card_terminal.py::mock_complete`, `routers/ext.py` (maps payloads). Acceptable.
