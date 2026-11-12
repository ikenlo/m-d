# 07 — Financial formulas audit

Scope: every operation that creates or changes money. Code ref = `server/mds/...`.

## 1. Order split — `services/finance.py::compute_split` (lines 21-36)

```python
compute_split(amount, materials, share_bp, mode)
  salon  mode: master = percent_of(A, s)            # DEFAULT
  master mode: master = percent_of(A, s) - M
  split  mode: master = percent_of(A - M, s)
  salon = A - M - master
```
- `percent_of` (`money.py:6`): `sign * ((|x|·bp + 5000) // 10000)`, i.e. **half-up** rounding.
- Inputs: A = `amount` entered at close (paid amount), M = Σ `order_materials.total`, s = `users.share_bp` or setting `default_share_bp` (5000), mode = `users.materials_mode` or `default_materials_mode` (`salon`). Resolved by `users.effective_terms` (`users.py:37`).
- Validation: A ≥ 0, M ≥ 0, 0 ≤ s ≤ 10000. There is **no** M ≤ A check. In `master` mode the master can go negative; in `salon` mode the salon goes negative if M > A − master.
- Single source: yes. `compute_split` is called only from `orders.close_order` (`orders.py:202`) and `orders.correct_order` (`orders.py:283`). Grep found no duplicate split math.

### Canon vs code with the canon examples (rate 5000, P = 1000 ₴ = 100 000 kop)

| Example | Canon master / salon | Code (`salon` mode, A-based) master / salon | Diff |
|---|---|---|---|
| 1 no discount: A=1000, M=200 | 500 / 300 | 500 / 300 | — |
| 2 discount 20 %: A=800, M=200 | **500 / 100** | **400 / 200** | master −100 |
| 3 salon < 0: A=600, M=200 | **500 / −100 + alert** | **300 / 100**, no alert | master −200 |
| odd kopeck: P=A=1 kop, M=0 | 0 / 1 (floor) | **1 / 0** (half-up) | rounding favours the master |

**Verdict: CANON_MISMATCH.** The two models agree only when there is no discount and the price is even.

### Missing inputs
- `P` (price snapshot) is not on `orders`. `appointments.price` (`models.py:215`) is the booked price. It can be changed via `PATCH /api/appointments/{id}`, and for `price_from` services ("від") it is a minimum, not the real price. Phase 2 needs a decision on what P is for "від" services (see 04 C-3).
- `master_rate_bp` on orders: today it is `orders.share_bp` (a snapshot).

## 2. Ledger — `services/common.py::ledger`
- Table `ledger` (`models.py:413`). Kinds seen: `order_closed`, `order_reversal`, `order_corrected`, `payout`, `shift_closed`, `expense*`, `expense_reversal`, `master_charge*`, tax/payroll kinds.
- Append-only: **by convention only**. No DB trigger or permission prevents UPDATE/DELETE. The `wipe-data` CLI (`mds/wipe.py:10-12`) deletes ledger, orders, payouts and audit rows. It is a test-environment tool, but it can run against production (it makes a backup first).
- `shift_closed` writes a *summary* row with revenue and earnings (`orders.py:510`). It duplicates order rows in the same table, so anyone summing the ledger must filter by kind (`test_invariants` does).

## 3. Correction — `orders.correct_order` (`orders.py:259-320`)
- Director: any field (amount, payment_method, share_bp, materials_mode, materials) + reason. Non-director: materials only, only own order, only while the shift is open.
- Writes `order_reversal` (negatives of the old values) and `order_corrected` (new values) to the ledger, plus an `audit` row with before/after/reason, and bumps `revision`.
- Stock: `reverse_order` + `apply_order` when materials change.
- Paid shift: the difference shows up in `master_balance` (accrued − paid − charged). There is no explicit next-payout entry and the shift's `payout_due` is not updated.
- No concurrency guard. Two directors correcting the same order at once: both read the old state and both write reversals of the same values, so the ledger is double-reversed while `orders` holds the last write. **Risk: ledger ≠ orders after concurrent corrections.** It is rare in practice, but the canon's version lock exists for exactly this case.

## 4. Shift close and cash — `orders.close_shift` / `shift_report` (`orders.py:378-540`)
```
master_earning = Σ order.master_earning (shift)
charge         = utilities fixed charge (if utilities_charge_mode == "fixed")
net_earning    = master_earning − charge
take           = min(max(net_earning,0), cash)   if setting master_takes_cash (default True) else 0
leave_in_till  = cash − take
director_pays  = max(net_earning − take, 0)       → shift.payout_due
```
- Matches canon §0.8 when `charge = 0` (the default). Under the canon, `charge` must disappear.
- `take > 0` → creates a `Payout(cash_shift_id=…)` and a ledger `payout` row.
- `payout_due ≤ 0` → shift goes straight to `paid`.
- Canon example check (7×500 cash, 5×500 card, earnings 3000) → take 3000, left 500, director pays 0. **MATCHES.** (cash 500, earnings 1500) → 500 / 0 / 1000. **MATCHES.**
- Unrecorded: when the report snapshot is taken, later corrections don't change `payout_due`. That is intended (canon: the report is a snapshot).

## 5. Payout — `orders.confirm_payout` (`orders.py:558-595`)
- Director only; amount > 0; method validated.
- With `shift_id`: atomic `UPDATE shifts SET status='paid' WHERE status='closed'` plus UNIQUE `payouts.shift_id`, so a double payout is impossible.
- Without `shift_id`: arbitrary payout (advance/bonus), recorded by master. No tag.
- Ledger `payout` (`master_amount = −amount`) and audit.
- Amount isn't checked against `payout_due` (the director may change it, as documented).

## 6. Balance — `orders.master_balance` (`orders.py:597`)
`balance = Σ orders.master_earning − Σ payouts.amount − Σ master_charges.amount`
Canon: owed = accrued − paid, **no deductions**. **MISMATCH** while the charges code exists (the value is 0 when the mode is `none`).

## 7. Dashboard — `analytics.dashboard` (`analytics.py:58`)
- `gross = revenue − masters_share − materials`. The formula matches the canon, but `masters_share` comes from the wrong master formula.
- `net = gross − (rent + utilities + advertising + other) − generator + master_charges`. Taxes aren't subtracted and charges are added (**MISMATCH**). Expense categories `equipment` and `household` are **not** in `operating`, so they don't reduce net profit either. That looks like a bug or an undocumented decision (see 03 U-12).
- `materials_purchases` and `fuel_purchases` are shown as cash flow and correctly not subtracted again.
- `by_payment` splits revenue by cash/card/transfer. **Cash vs card distinguished: MATCHES.**
- `average_check = revenue // orders_count` (floor, int).

## 8. Utilities — `services/utilities.py`
- Electricity `cost = used_milli × tariff / 1000` via `_r` (integer rounding); kWh stored in thousandths.
- Generator `hour_cost = lph × fuel_price + amort`; run cost = hour_cost × hours (milli, ints).
- Charges: `_largest_remainder` allocation (sums exactly). All ints.
- Note: `routers/utilities.py` accepts `float` for readings/hours. They are converted with `round(x*1000)`, so a float only touches *quantities*, never money.

## 9. Taxes and payroll — `services/taxes.py`
- Rates in `tax_rates` with dates and bp. Accruals use the `reverses_id` storno pattern. ints throughout. Display strings use `/100` formatting (`taxes.py:241,355`), only in messages.
- Not part of the canon; kept as EXTRA.

## 10. Card terminal — `services/card_terminal.py`
- Closes the order only after `check_payment` gets response_code "00" from the bank. It calls `close_order` with `idempotency_key=f"card-{p.id}"`. Amount comes from the payment record (int kop).

## Grep checks (requested in Phase 0 §0.6.3)

| Pattern | Hits in `mds/` | Finding |
|---|---|---|
| `float(` / `: float` / `Decimal` | 26 | input parsing of quantities, timeouts, perf ms, SMS balance. **No float in a money formula** |
| `/ 100` | 29 | display/formatting only (CSV, XML, messages, JS `money()`) |
| `round(` | 46 | quantities, stats, JS UI. Not in split math |
| `0.5` / `* 0.5` / `/ 2` in money | 0 in services | — |
| `materials_mode` | 42 | **CANON_MISMATCH** |
| `share_bp` | 54 | **CANON_MISMATCH** (rename to master_rate_bp) |
| `Role.ADMIN` / `"admin"` | 30 / several in JS | **CANON_MISMATCH** |
| `MasterCharge` / allocate | 45 | **CANON_MISMATCH** |
| `version` (optimistic lock) | 0 | MISSING |
| `idempotency` | 11 | orders only, no TTL |

## Tests on money (see 14 for full list)
`test_finance.py` (7 tests: 3 modes, balance, half-up rounding, fmt), `test_invariants.py` (random 5 000 splits; ledger ↔ orders ↔ dashboard consistency over 40 orders with corrections), `test_orders.py` (12), `test_concurrency.py` (2), `test_utilities.py` (9), `test_card_terminal.py` (9). Every one of them encodes the **current** (non-canon) model. Phase 2 has to rewrite `test_finance.py` and parts of `test_invariants.py`, `test_orders.py` and `test_utilities.py`.
