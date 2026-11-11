# 10 — Roles audit (vs §0.9)

## Roles in code
`models.py:28-32`: `director`, `admin`, `master` (+ internal `system`, `client` actors in `services/common.py:16-56`).
- `Actor.is_director` → director or system.
- `Actor.is_staff_manager` → director, **admin** or system (12 uses).
- `actor.require(...)` checks in services. **Authorization is enforced on the backend**: services raise `Forbidden`, and routers don't decide permissions. Verified on orders, payouts, analytics, stock, expenses, utilities.

**Admin exists → CANON_MISMATCH.** Where admin gets rights:

| Area | Code | Admin can |
|---|---|---|
| Calendar | `bookings._check_access` (staff manager) | book/edit for any master |
| Close order | `orders.close_order:176` (`is_staff_manager`) | close **any** master's order |
| Phone on order | `orders.set_order_phone` | change a client's phone |
| Edit order | `orders._check_edit:255` | edit orders < 14 days (code path; `correct_order` still needs director for non-materials) |
| Open shift | `orders.open_shift:136` | open any master's shift |
| Needing materials | `orders.needing_materials:626` | list all |
| Stock | `stock.receive/adjust/writeoff` (`stock.py:65,88,102`) | receipts, inventory, write-off (**canon: director only**) |
| Schedule | `schedule.py:42,76` | edit all |
| Clients base | `client_base.py:65,152,197` | segments, merge, duplicates |
| 2FA | `twofa.ROLES` | eligible |
| UI | `app.js:543-555` NAV/MOBILE_TABS `admin` | calendar, schedule, clients, stock, generator |
| MDs Manager | `md-managment/app/web/session.py:61` | maps to Manager `admin` |

## Matrix vs canon

| Action | Canon M / D | Code M / D | Status |
|---|---|---|---|
| Create order | ✅ / ✅ | ✅ own / ✅ | MATCHES |
| Edit order (shift open) | ✅ / ✅ | materials + phone only / ✅ | PARTIAL (C-13) |
| Edit order (shift closed) | ❌ / ✅ | ❌ / ✅ | MATCHES |
| Close order without materials | ✅ / ✅ | ✅ (computed flag) | MATCHES |
| Mark no-show | ✅ / ✅ | ✅ own / ✅ | MATCHES |
| Force-close shift | ❌ / ✅ (materials first) | ❌ / ✅ (no materials needed) | MISMATCH (C-14) |
| Confirm payout | ❌ / ✅ | ❌ / ✅ | MATCHES |
| Arbitrary payout | ❌ / ✅ | ❌ / ✅ | MATCHES |
| Cancel order / refund / reversal | ❌ / ✅ | correction director only; cancel/refund absent | PARTIAL |
| Add product | ✅ / ✅ | absent | MISSING |
| Pay product bonus | ❌ / ✅ | absent | MISSING |
| Inventory / receipt | ❌ / ✅ | ❌ / ✅ (+ admin) | MISMATCH (admin) |
| Financial reports, others' earnings | ❌ / ✅ | ❌ / ✅ (master sees `salon_earning` in own shift report) | PARTIAL (S-16) |
| View own earnings | ✅ / ✅ | ✅ | MATCHES |
| Settings | ❌ / ✅ | ❌ / ✅ | MATCHES |
| Open / close shift | ✅ / ✅ | ✅ own / ✅ | MATCHES |
| Generator ON/OFF | not in canon | all staff | EXTRA |

## Two directors
No limit on the number of directors and no hierarchy. `notify_directors` sends to every active director (minus the actor). Equal access: MATCHES. A "first processed" marker for shared alerts is missing.

## Vacation mode
**NOT IMPLEMENTED.** No setting, table, endpoints or UI.

## Tests
`test_permissions.py` (2 tests) and permission asserts scattered in `test_api.py` / `test_orders.py`. There is no full role × endpoint matrix test. Recommend a parametrized matrix in Phase 2.
