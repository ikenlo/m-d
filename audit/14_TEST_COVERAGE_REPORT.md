# 14 — Test coverage report (tests not modified)

Run on 2026-11-11 in this container: `python -m pytest -q` → **207 passed, 0 failed** (190 `def test_` + parametrized cases), ~3 min. `ruff check mds tests`: clean. `node --check app.js`: OK. Line coverage was not measured (`pytest-cov` is not installed and not in CI).

## Inventory (`server/tests/`, 32 files)
| Area | Files (tests) | Notes |
|---|---|---|
| Finance core | `test_finance.py` (7), `test_invariants.py` (2: 5 000 random splits; ledger ↔ orders ↔ dashboard over 40 orders + corrections) | encodes **old** model incl. half-up rounding |
| Orders/shifts/payouts | `test_orders.py` (12), `test_master_orders.py` (5), `test_late_shift.py` (3), `test_concurrency.py` (2) | double close, double payout, idempotency key |
| Bookings | `test_bookings.py` (9), `test_booking_rules.py` (6), `test_no_phone.py` (3), `test_antispam.py` (2), `test_edge_inputs.py` (1) | overlap, places, off-hours |
| API | `test_api.py` (15), `test_ext_api.py` (10), `test_permissions.py` (2) | TestClient |
| Bot | `test_bot.py` (29) | real aiogram dispatcher harness |
| Stock/schedule | `test_stock_schedule.py` (11) | |
| Utilities | `test_utilities.py` (9) | incl. distribution (to be removed) |
| Card terminal | `test_card_terminal.py` (9) | fake provider |
| Taxes | `test_taxes_payroll.py` (8), `test_tax_report.py` (6), `test_tax_declaration.py` (6, XSD validation) | |
| Other | `test_twofa.py` (5), `test_reminders.py` (5), `test_gdrive.py` (4), `test_client_base.py` (4), `test_insights.py` (3), `test_journal_feed.py` (3), `test_migrations_cli.py` (3), `test_sms_center.py` (2), `test_demo_fop1.py` (2), `test_wipe.py` (1), `test_version.py` (1) | |
| Frontend | `ui-tests/smoke.js` (Playwright: pages render, screenshots) | CI job `ui` |
| E2E business | none (no browser test that closes an order and checks the dashboard) | |

## Critical flows without tests (or weakly tested)
1. **Role matrix**: only 2 dedicated permission tests; admin rights are not systematically tested.
2. **Concurrent corrections** of the same order (ledger desync, 07 §3). Untested.
3. **Backup restore**: only creation/rotation/Drive mocked; no restore test.
4. **Bot PIN lockout across restarts / per target user**: the in-memory per-chat lock is tested (`test_bot.py:274`), but persistence and per-user locking are not (and not implemented).
5. **Dashboard by payment method / cash-card separation**: indirectly tested.
6. **Net profit composition** (equipment/household not subtracted). No test pins the intended behaviour.
7. **Wipe on start** (`MDS_WIPE_DATA`): `test_wipe.py` covers the function, not the start-up guard.
8. **Frontend logic** (money input parsing `app.js:118-126`, kop conversion): no unit tests.

## Tests that Phase 2 must rewrite (they encode non-canon behaviour)
`test_finance.py` (all modes, `test_rounding_half_up`), `test_invariants.py::test_compute_split_invariant_random` (modes) and the `split` mode in `test_ledger_orders_dashboard_consistent`, distribution tests in `test_utilities.py`, any test using role `admin`, `test_orders.py` expectations of `master_earning`, `test_ext_api.py` if C-5 drops writes.

## Prioritised strategy
1. **P0 money**: canon split (3 examples, odd kopeck, property test P/A/M/rate, negative-salon alert), status transitions, mark-paid, refund, correction after payout → next payout, version 409, idempotency TTL.
2. **P0 security**: role × endpoint matrix (parametrized from the router list), bot PIN lockout.
3. **P1**: dashboard = Σ of single transactions (cash/card split, discounts, unpaid excluded/included as defined), products bonus, vacation flow.
4. **P1**: restore rehearsal test (backup → restore to temp → integrity + counts).
5. **P2**: Playwright business E2E (close order → shift close → payout → dashboard); add `pytest-cov` to CI with a floor for `services/`.
