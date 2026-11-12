# 03 — Forgotten / unfinished work

| ID | Kind | Item | Evidence | Note |
|---|---|---|---|---|
| U-1 | requested, not implemented | Dependency lock file | `docs/ROADMAP.md` (post-0.1 priorities); `requirements.txt` ranges | also canon §0.14 |
| U-2 | requested, not implemented | Content-Security-Policy | ROADMAP; `api/app.py` | |
| U-3 | requested, then reverted | Excel export / accountant pack, refunds, payment status, expense files, owner screen | #26-#29 reverted by #31 | refunds and exports return via canon |
| U-4 | started, abandoned | `expense_files` table: model + migration 0015 remain, no code uses it | `models.py:649`; only `wipe.py` references it | backend without feature |
| U-5 | planned, never connected | TurboSMS real sending | `services/sms.py` (provider code), ROADMAP 1.0.0 | conflicts canon (C-6) |
| U-6 | planned, not done | Android signing key in GitHub secrets and first signed Release | ROADMAP "Термін" | owner action |
| U-7 | planned, superseded | Flutter native apps (ROADMAP stage 2) | `apps/` are shells | remove in Phase 3 |
| U-8 | decision never taken | Zero-floor for master in `master`/`split` modes | ROADMAP | moot under canon |
| U-9 | implemented, unmerged | `/api/ext/v1` (branch `feat/ext-api-md-manager`, migration 0025) | `git log origin/main..HEAD` | the working tree for this audit **is** this branch |
| U-10 | unreleased | 0.2.2 stages A-F on `main`, CHANGELOG "[Невипущене]" | CHANGELOG | version decision C-15 |
| U-11 | old requirement in code | materials modes, individual share, utility distribution, Admin | 21 §0.4 | canon removal |
| U-12 | likely bug / undocumented rule | Expense categories `equipment` and `household` are not subtracted in net profit (only rent, utilities, ads, other, generator) | `analytics.py:91` | **DECISION**: treat as capex/cash-flow (like materials) or "other" |
| U-13 | workaround that became permanent | `shift_closed` summary rows in the ledger alongside transaction rows | `orders.py:510` | readers must filter by kind |
| U-14 | duplicate functionality | Materials-missing detection in three places (`order_missing`, `needing_materials`, UI red marking) | `orders.py:34,618` | consolidate when adding the stored flag |
| U-15 | dev-only feature in prod entrypoint | `MDS_WIPE_DATA`, `MDS_DEMO_FOP1` on `start`; `mock-complete` route | `__main__.py:110-118`, `routers/card_terminal.py:48` | 09 S-3, S-11 |
| U-16 | undocumented | `Split.salon_gross` property used only in tests | `finance.py:17` | dead code |
| U-17 | button/back-end gap | Local backup failure is not surfaced to directors; only Drive failure | `app.py:39` | |
| U-18 | doc drift | `docs/FINANCE.md` §3 says "Директор може закрити примусово" for incomplete shift; canon says materials required first | C-14 | |
| U-19 | doc drift | CLAUDE.md "Захист від подвійних записів: `_lock_master`…" ✅ accurate; CLAUDE.md invariant 3 "Формула поділу — тільки в compute_split" ✅ accurate but describes the old formula indirectly via FINANCE.md | — | Phase 1 rewrite |
| U-20 | MDs Manager dependency on Connect admin role | `md-managment/app/web/session.py:61` | C-4 | |
| U-21 | outbox drop | Notifications stop after 5 failed attempts with no alert | `notifications.py:9,43` | relevant for "critical, cannot be disabled" alerts |
| U-22 | missing guard | No `M ≤ A` validation; salon can go negative silently | `finance.py` | canon wants alert, not block |
| U-23 | perf | No index on `ledger(kind)` / `ledger(master_id)` | 08 | grows with time |
