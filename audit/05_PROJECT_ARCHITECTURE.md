# 05 — Project architecture

## Components

| Layer | Location | Notes |
|---|---|---|
| Entry / CLI | `server/mds/__main__.py` | `init`, `start` (migrate → bootstrap director/catalog → optional wipe/demo → serve + bot), `serve`, `bot`, `migrate`, `backup`, `create-user`, `wipe-data`, `demo-fop1` |
| Config | `server/mds/config.py` | env `MDS_*`; SQLite in `data/mds.db` by default; `RAILWAY_VOLUME_MOUNT_PATH` |
| DB | `server/mds/db.py` | SQLAlchemy 2 engine; SQLite pragmas `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000`; `Database.session()` commits/rolls back |
| Models | `server/mds/models.py` (729 lines, 33 tables + `master_services` association) | see 08 |
| Migrations | `server/migrations/versions/0001…0025` | Alembic; `test_migrations_match_models` guards drift |
| Services | `server/mds/services/*.py` (32 modules, ~8 k lines) | all business logic, `(session, actor, …)` |
| API | `server/mds/api/app.py` + 15 routers | FastAPI; 151 endpoints; `DomainError` → JSON |
| Auth | `security.py`, `api/deps.py`, `services/users.py`, `services/twofa.py` | JWT HS256 + token_version; 2FA via Telegram code |
| Bot | `server/mds/bot/` (handlers = clients, staff = staff, runner = polling + outbox 3 s) | aiogram 3 |
| Background | `app.py` lifespan: `_backup_loop` (6 h), `_reminders_loop` (5 min: reminders, unclosed shifts, SMS automations/queue, tax reminders) | in-process asyncio, so a single replica is required |
| Frontend | `server/mds/web/static/` — `app.js` (4 358 lines, single file), `style.css`, `sw.js`, `index.html`, fonts | vanilla JS SPA/PWA, `VIEWS` + `NAV`, DOM via `h()` |
| Apps | `apps/android` (WebView, Java), `apps/windows` (Electron) | shells only |
| UI tests | `ui-tests/smoke.js`, `seed.sh` | Playwright smoke + screenshots in CI |
| CI/CD | `.github/workflows/ci.yml` (ruff, node --check, pytest, Playwright), `apps.yml`, `release.yml` (tag + release from CHANGELOG) | |
| Deploy | `Dockerfile` (python, `CMD python -m mds start`), `railway.json`, `docs/DEPLOY_RAILWAY.md` | Railway volume |
| External | Telegram Bot API; Google Drive (OAuth); PrivatBank Terminal; TurboSMS (not configured); MDs Manager via `/api/ext/v1` | |

## Critical flows
1. **Booking**: bot/web/ext → `bookings.create_appointment` → `_lock_master` (UPDATE users.booking_lock) → `_check_slot` → insert → notifications.
2. **Close order**: `POST /api/appointments/{id}/close` → `orders.close_order`: idempotency check → atomic UPDATE appointment → `compute_split` → `open_shift` → Order + materials → stock moves → ledger → audit.
3. **Close shift**: `orders.close_shift`: incomplete-orders check → utilities charge → report snapshot → cash-take payout → status closed/paid → notify directors.
4. **Payout**: `orders.confirm_payout`: atomic shift status → Payout → ledger → audit → notify master.
5. **Correction**: `orders.correct_order`: storno + new + stock reversal + audit + notify.
6. **Dashboard**: `analytics.dashboard` (aggregates on the fly from orders/expenses/payouts/generator/charges).
7. **Backup**: `backup.backup_cycle` → SQLite online backup → integrity check → rotate → Drive upload.

## Weaknesses
- **Single-process design**: background loops and the bot live in the web process, so no horizontal scaling (fine for one salon, but a crash loses all three).
- **`app.js` is a 4.4 k-line monolith.** Adding the canon UI (finance, analytics, my finance, vacation, security, logic) grows it further. Consider splitting into modules (no bundler needed; ES modules).
- **Ledger is a convention, not enforced**; `shift_closed` summary rows mix with transaction rows.
- **No optimistic locking**: concurrent director edits can desync ledger vs orders (07 §3).
- **Settings as JSON key/value** (`settings` table) validated in `common.update_settings` (a long if-chain, `common.py:225-290`). New canon toggles (reports, alerts, vacation) will lengthen it, so a schema dict is recommended.
- **Finance computed in several places**: `analytics._order_totals`, `orders.shift_report`, `orders.master_balance`, `integration.visits`, `journal_feed`. The formula is single (compute_split), but the aggregates are re-derived. When orders get a `status`, every one of them must filter it (risk of inconsistent reports).
- **Duplication**: `notify_directors` vs per-feature messages; three "materials missing" mechanisms (`order_missing`, `needing_materials`, red highlight in UI).
- **Failure points**: Drive OAuth token expiry (alerts exist); Telegram outages (outbox retries 5×, then the message is dropped silently); the SQLite file on a single volume.
