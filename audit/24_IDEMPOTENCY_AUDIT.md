# 24 — Idempotency, statuses, version lock

## Idempotency
| Check | Result | Evidence |
|---|---|---|
| Client sends `idempotency_key` on order creation | ✅ web (`app.js:1122,1696`), bot (`tg-close-{appt}`), terminal (`card-{payment}`) | |
| Storage | `orders.idempotency_key` UNIQUE column, **not** a separate `idempotency_keys` table | `models.py:273` |
| Same key → existing result | ✅ returns the same `Order` (the response is rebuilt, not cached JSON) | `orders.py:163-168` |
| Same key, different appointment → 409 | ✅ `Conflict` | |
| Different key on already completed → 409 | ✅ via atomic appointment UPDATE (`code="already_closed"`) | `orders.py:189-196` |
| Unique violation race → 409 | ✅ `IntegrityError` → `Conflict` | `orders.py:226-229` (except at :228) |
| TTL 30 days + cleanup | ❌ keys live forever on orders (harmless, but not canon) | |
| Idempotency for other mutations (payouts, expenses, receipts) | ❌; payouts protected only per shift (`payouts.shift_id` UNIQUE); arbitrary payouts and expenses can be double-submitted | |
| ext API | ✅ `external_ref` UNIQUE in `appointment_attribution` (branch only) | 0025 |

## Statuses
| Check | Result |
|---|---|
| `appointments.status` six values | ✅ `models.py:43-53` |
| Transition rules | ✅ `CLOSABLE`, `EDITABLE`, `OCCUPYING` sets; `set_status` validates (`bookings.py:399`) |
| `orders.status` (`completed/unpaid/cancelled/refunded`) | ❌ absent |
| `unpaid` (accrues to master, appears in list, mark-paid later) | ❌ |
| `refunded` | ❌ (existed briefly in 0.1.16, reverted #31) |
| `POST /api/orders/{id}/mark-paid` | ❌ |
| `no_show` no financial effect | ✅ |

## Version lock
| Entity | `version` column | Conditional update | 409 with current state |
|---|---|---|---|
| appointments | ❌ (`updated_at` only) | ❌ | ❌ |
| orders | ❌ (`revision` counter, not checked) | ❌ | ❌ |
| shifts | ❌ (status-conditional UPDATE for close/pay only) | partial | ❌ (plain 409 message) |
| payouts | ❌ | — | — |
| expenses | ❌ | ❌ | ❌ |

`Conflict` responses today: `{"error": code, "detail": message}`. There is no entity body for client-side merge. The UI shows the message but has no "дані змінилися, оновіть" flow.

**Risk without version lock:** two directors editing the same order or expense at once means last write wins, and for orders the ledger can be double-reversed (07 §3).
