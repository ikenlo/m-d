# 20 — Stock log and materials flag

| Check | Result | Evidence |
|---|---|---|
| `stock_movements` table | **PARTIAL**: exists as `stock_moves` | `models.py:153-166` |
| Columns: date | ✅ `ts` | |
| item_type (material/product) | ❌ materials only (`material_id` FK) | |
| item_id | ✅ `material_id` | |
| qty (+/−) | ✅ `qty_milli` (thousandths of g/ml/pcs) | |
| price at moment | ⚠️ `cost` = **total** cost of the move, not the unit price | |
| reason | ⚠️ `kind`: `receipt`, `adjust` (inventory), `writeoff`, `order` (consumption), plus auto write-off/return. Canon vocabulary: receipt/consumption/sale/return/inventory/correction | `stock.py:52-160` |
| ref_order_id | ✅ `order_id` | |
| user_id | ✅ (plain int, no FK) | |
| note | ✅ | |
| Every stock change writes a row | ✅ all changes go through `stock._move` | `stock.py:52` |
| Movement list endpoint | ✅ `GET /api/stock/moves` | `routers/stock_schedule.py:68` |
| `unit_price_snapshot` in `order_materials` | ✅ as `unit_cost` + `total`, frozen at close | `models.py:303-312`, `orders._build_materials` |
| Cost method average | ❌ the material card's `unit_cost` (pack price) is set manually; receipts don't recompute a weighted average | `stock.receive` |
| `materials_not_entered` flag | ⚠️ computed: `order_missing()` → `"materials"` when `service.needs_materials` and no lines | `orders.py:34-40` |
| Director list of flagged orders | ⚠️ `GET /api/orders/needs-materials` (last 14 days) instead of `?materials_not_entered=true` | `routers/money.py:31`, `orders.needing_materials` |
| Master can add materials after close while shift open | ✅ | `correct_order` materials path |
| Shift close blocked by incomplete orders | ✅ (`incomplete_orders`, also covers missing phone) | `orders.py:477-482` |
| Force-close requires materials | ❌ `force=True` by director bypasses | `orders.py:480` |
| Inventory: actual → diff movement | ✅ `stock.adjust` (but director **and admin**) | `stock.py:87-99` |
| Negative stock allowed | ✅ | |
| Negative stock alert / inventory task | ❌ | |
| Auto write-off of crumbs (< 0.1 pack) | EXTRA (0.1.27) | `stock._auto_writeoff` |
| Products stock | ❌ | |
| Markup `markup_bp` | ❌ | |

**Phase 2 notes:** either extend `stock_moves` with `item_type`, `unit_price` and a canon `reason` (rename `kind` values via data migration), or create `stock_movements` and migrate. The canon names the new table, but extending avoids a second log. **DECISION** (technical; recommend extend plus a view).
