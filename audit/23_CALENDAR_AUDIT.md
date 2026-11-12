# 23 — Calendar audit

Code: `VIEWS.calendar` (`app.js:1777`), CSS `.cal-ev*` (`style.css:348-360`), services `bookings.free_slots`, `free_windows` (:499), `suggest_slots` (:553), `_check_slot` (:215), `booking_options` (:122).

| Canon §0.17 | Current | Status |
|---|---|---|
| Booked slots dark-red semi-transparent | colour **by status**: pending=warn, confirmed=info, in_progress=prog, completed=ok (`style.css:354-357`) | MISMATCH |
| Time range text `13:40–15:25` | event cards show time; grid/list modes (0.1.22, 0.1.50); exact range format not verified on device | PARTIAL |
| Service icon + name + duration `Фарбування · 2:30` | name ✅; icons exist for quick-booking templates (0.1.39); duration label on calendar card not confirmed | PARTIAL |
| Overlap prevention: block | ✅ `_check_slot` under `_lock_master`, place limits (2 workplaces) | MATCHES |
| Suggest nearest fitting windows | ✅ `suggest_slots`, free windows for any duration (0.2.2 stage D) | MATCHES |
| Mobile-first, touch | ✅ many iterations (#43, #45, #62-#67, #74) | MATCHES |
| Collapsed empty hours, iPhone-like order | EXTRA | — |

**Decision for Phase 2:** status colour coding is useful operationally (pending vs confirmed). Replacing it with uniform dark red loses information. Options: dark-red base with a status accent stripe. **DECISION** (UI).
