# 09 — Security audit (OWASP-based, read-only, no active testing)

## ⚠️ Report immediately (per Final Note 11)

### S-1 · HIGH · Live secrets pasted into chat transcripts
- Evidence: Claude Code transcripts on this container (`~/.claude/projects/-home-user*/00b6d989…jsonl`) contain a **Telegram bot token** (MDs Manager bot, pasted 2026-11-05 19:49) and a **Google AI Studio API key** (2026-11-06 07:16). The same values sit in `md-managment/.env`.
- Checked: `.env` is git-ignored (`.gitignore:1`) and appears in **no commit** of `ikenlo/md-managment` or `ikenlo/m-d` (searched all revisions).
- Impact: anyone with access to the chat history or transcript storage can take over the bot or spend the AI quota.
- Mitigation: **rotate both** (BotFather `/revoke`, regenerate the AI Studio key) and pass them only through environment settings, never through chat. Not fixed by this audit.
- The values are deliberately **not** reproduced in these reports.

### S-2 · MEDIUM (corrected from HIGH in Phase 1) · Bot PIN lockout is per chat and in memory only
- Evidence: `bot/staff.py:23-61` `StaffSessions.fail/locked` locks a **chat** for 15 min after 5 wrong codes or PINs (tested: `tests/test_bot.py:274 test_staff_pin_bruteforce_lock`). The service `services/users.py:269` `verify_bot_pin` has no counter. The lock lives in **process memory**, keyed by `chat_id`, not by the target user. Default staff code is `"0000"` (`services/common.py:135`). PINs are 4+ digits.
- Correction: the Phase 0 audit said there was no limit at all. That was wrong; the in-memory per-chat limit was missed.
- Impact: a restart (each deploy) resets the counters, and every new Telegram account gets 5 fresh attempts at the same director PIN. With the default `0000` code, a slow distributed guess of a 4-digit PIN is feasible.
- Mitigation (director decision 2026-11-11): persistent lockout **per target user** (and per chat) in the DB, 5 attempts / 15 min; staff code must be changed from the default.

### S-3 · HIGH · Destructive env switches in the production entrypoint
- Evidence: `mds/__main__.py:106-119` `cmd_start`. `MDS_WIPE_DATA=<mark>` deletes orders, payouts, ledger, audit, shifts, expenses… once per mark (`mds/wipe.py:10-12`). `MDS_DEMO_FOP1=yes` replaces data with demo data. Both run on **every** start of the production container.
- A backup is taken first, but this contradicts the "financial data never deleted" and append-only ledger/audit rules (§0.14, CLAUDE.md invariant 4).
- Impact: one wrong Railway variable wipes production.
- Mitigation: remove from `start`, or refuse unless `MDS_ENV=test`. A decision is needed.

## Findings

| ID | Sev | Area | Evidence | Impact | Mitigation |
|---|---|---|---|---|---|
| S-4 | MEDIUM | Session | JWT HS256, absolute TTL 12 h (`config.py:19`, `security.py:45`), stored in `localStorage` (`app.js:12-15`) | an XSS could steal a token valid for 12 h; no idle logout (canon 30 min) | idle timeout + shorter TTL / refresh; CSP |
| S-5 | MEDIUM | Headers | no `Content-Security-Policy`, no HSTS (`app.py:136-150` sets nosniff, X-Frame-Options DENY, Referrer-Policy) | XSS defence-in-depth missing | add CSP (`script-src 'self'`); HSTS at the proxy |
| S-6 | MEDIUM | Brute force | web login lockout is **per user** (5/15 min) with no IP throttle | anyone can lock out both directors by hammering their usernames | IP-based rate limit plus per-user limit |
| S-7 | MEDIUM | Backups | Drive copies are plain SQLite files (whole DB: clients' phones, finances, HR data such as tax numbers in `employee_profiles.tin`) | a Drive account compromise leaks everything | encrypt before upload (canon §0.14) |
| S-8 | MEDIUM | Dependencies | ranges in `requirements.txt`; no hashes; no `pip-audit` in CI | supply-chain drift | lock file with hashes (R-1) |
| S-9 | MEDIUM | Integration | `/api/ext/v1` single static token, no rotation, no scope, no IP allowlist (`routers/ext.py:17-23`); returns client names and phones in lookup; `visits` has financial data | token leak gives booking and cancelling as any client | scoped tokens, rotation, audit `actor=ext` (exists) |
| S-10 | LOW | Bot authz | staff bot can close orders (`bot/staff.py:456`) | canon says view + confirm only | C-7 |
| S-11 | LOW | Card terminal | `POST /card-payments/{id}/mock-complete` is in the prod router; guarded by `p.mode == "mock"` (`routers/card_terminal.py:48-58`) | fake payments only if mock mode can be chosen in prod | hide the route unless test env |
| S-12 | LOW | Container | `Dockerfile` has no `USER` (runs as root) | larger blast radius if exploited | non-root user |
| S-13 | LOW | Secret key | if `MDS_SECRET_KEY` is unset, `data/secret.key` is generated and kept in the data volume (`config.py:38-48`). Backups copy only the `.db`, so the key is not in Drive | anyone with volume access can mint JWTs | require the env var in prod |
| S-14 | LOW | 2FA scope | `twofa_roles` default `["director"]` | admin/master without 2FA | fine for masters |
| S-15 | LOW | Admin role | `is_staff_manager` gives admins client data, schedule, stock, generator and other masters' orders (`orders.close_order` allows staff manager) | over-privilege vs canon | remove Admin (§2.2) |
| S-16 | LOW | Master sees salon share | `shift_report` returns `salon_earning` and the full order list to the master (`orders.py:418-437`) | canon: master never sees others' earnings; salon share is director info | split views (§2.14) |
| S-17 | INFO | SQLi | all DB access through the SQLAlchemy ORM / `select()`; no raw SQL with user input found | — | — |
| S-18 | INFO | XSS | DOM built via `h()`; the single `innerHTML` is the static icon map (`app.js:230`). Bot uses `html.escape` (CLAUDE.md rule) | low risk | Mermaid (2.16.1) will add SVG injection, so feed it only numbers |
| S-19 | INFO | CSRF | Bearer token in header, not cookies, so classic CSRF doesn't apply | — | — |
| S-20 | INFO | CORS | no CORS middleware, so same-origin only | — | — |
| S-21 | INFO | Error disclosure | `DomainError` → `{error, detail}`; validation errors show the field name; unhandled exceptions → generic 500 | — | — |
| S-22 | INFO | Passwords | scrypt N=2^14 r=8 p=1, 16-byte salt, constant-time compare; strength ≥ 8 chars, letters + digits (`security.py`) | fine | — |
| S-23 | INFO | Logging | logs perf and backup status; phones are not logged in `app.py`. Not fully verified across the bot | — | grep in Phase 2 |
| S-24 | INFO | Uploads | `expense_files` table exists, but upload code was reverted (#31), so there is no upload surface now | — | drop or reuse |
| S-25 | INFO | Login journal | absent (canon §0.14) | no forensic trail of web logins | §2.7 |

## Telegram bot
- Clients: anti-spam limits (`services/antispam.py`).
- Staff: staff code + PIN (scrypt), in-memory per-chat lockout. See S-2.
- Role taken from the user record. Masters see only their own data (verified in `bot/staff.py` via services, which check `actor`).

## Admin access paths
- Director bootstrap from `MDS_DIRECTOR_USERNAME/PASSWORD` only when no director exists (`__main__.py:55-80`). Fine.
- `python -m mds create-user` CLI (shell access only).
