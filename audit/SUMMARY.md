# Phase 0 — Audit SUMMARY (checkpoint)

Repo `ikenlo/m-d` @ `2d25bce` (branch `feat/ext-api-md-manager`; `main` = `f834c01`). Code version 0.2.1; tags v0.2.0 and v0.2.1 already exist. Tests 207/207 green, ruff clean. Details: `00_AUDIT_INDEX.md`; canon matrix: `21_CANON_COMPLIANCE.md`.

## ⚠️ Urgent (reported, not fixed)
- **S-1 HIGH**: a Telegram bot token and a Google AI key for MDs Manager were pasted into chat and are stored in transcripts on the container. They are not in git. **Rotate both.**
- **S-2 HIGH**: staff bot PIN has no attempt limit; default staff code `0000`. Brute-force could give director access in Telegram.
- **S-3 HIGH**: env vars `MDS_WIPE_DATA` / `MDS_DEMO_FOP1` on production start delete or replace financial data.

## 1. What we built
MDS Connect, an internal system for one salon: FastAPI + SQLAlchemy/Alembic (33 tables, 25 migrations), 151 API endpoints, vanilla-JS PWA, aiogram client and staff bots, Android/Windows shells, Railway deploy, CI with Playwright. Domains: booking, orders and split, shifts and cash, payouts, expenses, stock, utilities and generator, taxes and payroll, FOP declaration, card terminal, client base, SMS, 2FA, backups to Drive, ext API for MDs Manager.

## 2. What works
Booking with overlap protection and suggestions; idempotent atomic order close; storno corrections with audit; shift cash take; double-payout protection; dashboard consistent with the ledger; utilities and generator snapshots; 2FA; integrity-checked backups. All covered by passing tests (15).

## 3. What's incomplete
Idempotency (orders only, no TTL); materials flag (computed, not stored; force-close bypasses it); stock log (materials only, no unit price or canon reasons); backups (no encryption, no 3rd copy, no restore test); session revocation (no logout-all); alerts (subset); master My Finance; profitability (no scale).

## 4. Requested but not implemented
Lock file, CSP, Excel export (reverted), Android signing/release, TurboSMS sending; plus all **new** canon items: products and 10 % bonus, order statuses unpaid/refunded and mark-paid, version lock, vacation mode, finance PIN, login journal, AI provider abstraction and `/api/analytics/recommend`, report toggles, PDF/Excel, separate Analytics tab, 2.16.1 Mermaid "Логіка системи".

## 5. Started but abandoned
Expense files (table left, code reverted); refunds/payment status and accountant Excel (reverted #31); Flutter apps plan; public site (moved to the `website` branch).

## 6. Broken / wrong (vs canon)
- **Split formula**: master = 50 % of **paid amount** with half-up rounding. The canon wants 50 % of **price**, floored. With a 20 % discount the master currently gets 400 instead of 500 on a 1000 ₴ service.
- Three materials modes (one is `(A−M)×50 %`), individual share, **Admin role**, **utility deductions** (code present, off by default), net profit adds deductions back, owed amount subtracts them.
- No salon < 0 alert. No master-negative guard in `master` mode.
- Net profit ignores taxes and the `equipment`/`household` expenses.
- Concurrent corrections can desync ledger vs orders (no version lock).

## 7. Implemented without request in canon (EXTRA)
Taxes and payroll, FOP XML, card terminal auto-close, SMS centre, client base, 2FA, anti-spam, demo/wipe tools, perf RUM, bug reports, journal feed, ext API with booking writes.

## 8. Conflicting requirements → `04_CONFLICTING_REQUIREMENTS.md` (27 items)

## 9. Technical / security risks
3 HIGH (above); MEDIUM: 12 h token in localStorage without CSP, per-user lockout enables director lockout, unencrypted Drive backups with HR/PII, unpinned deps, static ext token; single-process background jobs; 4.4 k-line `app.js`; migration head conflict if the ext-API branch merges after Phase 2 migrations.

## 10. Production ready?
**No.** The money rules don't match the approved canon, there are 3 HIGH security issues, and there is no proven restore or encrypted off-site copy.

## 11. Must-do before v1.0 → `16_V1.0_RECOMMENDATIONS.md`

## 12. Safe to postpone
FEFO, local LLM activation, VPS move, native apps, Manager hourly sync extras, calendar restyle.

## 13. Decisions needed from you
1. **Version**: v0.2.0 is already tagged. Use **0.3.0** for the canon release? (C-15)
2. **Retroactivity**: past orders keep their old split (no recalculation); the canon applies from the release date? (C-1)
3. **P for "від" services**: is P the list minimum or the agreed price entered at booking? May P change at close? (C-3)
4. **Admin accounts**: do any exist in production, and what do they become? MDs Manager admin login depends on them. (C-4)
5. **MDs Manager booking into Connect** (ext API writes, unmerged branch): keep (amend canon) or drop? Merge the branch before Phase 2? (C-5)
6. **SMS in Connect**: keep, or move to Manager? (C-6)
7. **Bot closing orders**: keep, or view/confirm only? (C-7)
8. **Card terminal auto-close**: keep (amend canon)? (C-8)
9. **Historic utility charges in production**: any? How to archive? (C-11)
10. **Net profit**: which taxes count; are equipment/household "other"? (C-12, U-12)
11. **Master edit rights while shift open**: amount and payment method too? (C-13)
12. **Force-close**: materials required; may a missing phone still be forced? (C-14)
13. **Auto-logout 30 min**: for masters too? (C-22)
14. **Finance PIN** on top of the existing 2FA: still wanted? (C-21)
15. **Google account** for the Service Account backup (Workspace/Shared Drive needed). (17)
16. **`unpaid` orders in revenue**: count in "real revenue" or not? (18)
17. **Calendar**: uniform dark-red, or keep status colours as an accent? (23)
18. **Wipe/demo env switches**: remove from production start? (S-3)
19. Confirm this plan counts as the director's explicit request to change the financial rules (CLAUDE.md). (C-16)
20. Mermaid view: data source `/api/finance/logic-stats` (director only), lazy-loaded, built after the canon split? (C-23, C-24)

## 14. Priorities
- **First**: security HIGHs (S-1 rotate now; S-2/S-3 in Phase 2 batch 0), then canon split + legacy removal + order statuses (2.1, 2.2, 2.17) with tests.
- **Second**: reports/dashboard per §0.5 (2.11), version lock + idempotency (2.15), backups (2.10), alerts (2.4), security extras (2.12).
- **Third**: products (2.3), vacation (2.5), PIN/login journal (2.6, 2.7), AI abstraction (2.8), UI (2.9, 2.16, 2.16.1).

---
**STOP.** Waiting for "GO for Phase 1".
