# 00 — Audit index (Phase 0)

Date: 2026-11-11. Repo: `ikenlo/m-d` (local clone, unshallowed; working branch `feat/ext-api-md-manager` @ `2d25bce`, which is `main` @ `f834c01` + 1 commit).
Phase 0 rule respected: **only files under `audit/` were created**. No source, DB, docs, config or deployment was changed. Nothing was committed or pushed.

## Inspected
- [x] git log (91 commits), 57 tags, 90 remote branches, revert #31 contents
- [x] `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `docs/*` (12 files)
- [x] `server/mds/models.py` (all 33 tables), migrations list 0001-0025
- [x] services: finance, money, orders (full), common, users, analytics (dashboard, my_earnings, period), stock, expenses, utilities (signatures + charges), notifications, backup, gdrive, bookings (signatures), card_terminal router, integration doc
- [x] API: app.py (middleware, background loops), deps.py, auth router, ext router; full endpoint list (151)
- [x] bot: staff login (code/PIN), order close; runner outbox interval
- [x] frontend: NAV, VIEWS list, dashboard/earnings sections, calendar CSS, token storage
- [x] CI workflows, Dockerfile, requirements, config
- [x] test suite run: 207 passed; ruff clean; JS syntax OK
- [x] MDs Manager (`md-managment`) only where it touches Connect (session role mapping, .env git-ignore)
- [x] previous Claude transcripts on this container (user prompts only)
- [ ] **not accessible**: production DB/Railway, GitHub PR bodies/reviews for `ikenlo/m-d` (not in session scope), GitHub secrets, original Connect dev sessions
- [ ] not done: coverage % (tool not installed), runtime UI review on a device, Mermaid size check (no network fetch)

## Files
| File | Topic |
|---|---|
| `SUMMARY.md` | checkpoint summary + decisions needed |
| `01_USER_INSTRUCTIONS_HISTORY.md` | request history |
| `02_TASK_RECONCILIATION.md` | status per request / batch |
| `03_UNFINISHED_WORK.md` | forgotten / unfinished |
| `04_CONFLICTING_REQUIREMENTS.md` | conflicts + decisions |
| `05_PROJECT_ARCHITECTURE.md` | architecture |
| `06_BUSINESS_LOGIC_CATALOG.md` | function catalogue |
| `07_FINANCIAL_FORMULAS.md` | financial audit |
| `08_DATABASE_AUDIT.md` | DB |
| `09_SECURITY_AUDIT.md` | security (2 HIGH: S-1, S-3) |
| `10_ROLES_AUDIT.md` | roles |
| `11_CLAUDE_INSTRUCTIONS_AUDIT.md` | Claude instructions |
| `12_SKILLS_INVENTORY.md` | skills |
| `13_UNUSED_FILES_REPORT.md` | unused |
| `14_TEST_COVERAGE_REPORT.md` | tests |
| `15_CURRENT_PROJECT_STATE.md` | verified state |
| `16_V1.0_RECOMMENDATIONS.md` | readiness |
| `17_BACKUP_AUDIT.md` | backups |
| `18_REPORTS_AUDIT.md` | dashboard metrics |
| `19_SECURITY_EXTRAS.md` | archive/logout/encryption |
| `20_STOCK_LOG_AUDIT.md` | stock log, materials flag |
| `21_CANON_COMPLIANCE.md` | **canon matrix (start here)** |
| `22_UI_FINANCE_AUDIT.md` | finance UI |
| `23_CALENDAR_AUDIT.md` | calendar |
| `24_IDEMPOTENCY_AUDIT.md` | idempotency, statuses, version lock |
