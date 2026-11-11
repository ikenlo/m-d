# 01 — User instructions history

## Sources and access

| Source | Accessed | Notes |
|---|---|---|
| `ikenlo/m-d` git history (91 commits, 2026-11-03 → 11-05), 57 tags v0.1.0…v0.2.1, 90 remote branches | yes (unshallowed) | the main record. Each PR title names the request |
| `CHANGELOG.md`, `docs/ROADMAP.md`, `docs/*.md`, `CLAUDE.md`, `README.md` | yes | |
| Claude Code transcripts on this container (`~/.claude/projects/*`, 3 sessions, 2026-11-05 → 11-06) | yes | they cover **MDs Manager** (`ikenlo/md-managment`) work, not Connect |
| Original Connect development sessions (session ids in commit trailers, e.g. `session_017iAGzUHERipr5VSMpXyrrm`) | **no** | not on this container |
| GitHub PR descriptions / review comments | **no** | `ikenlo/m-d` is not in this session's GitHub scope; only PR titles via merge commits |
| Production DB / Railway config | **no** | |
| Claude memory files | none found | |

So "requested by user" vs "Claude suggestion" can be told apart only where a commit or doc says so. Elsewhere it is marked `UNCLEAR`.

## Timeline (Connect)

| # | Date | Request (from PR title / docs) | Origin | Status now | Matches canon? |
|---|---|---|---|---|---|
| H-1 | 11-03 | MDS Connect v0.1: server, PWA panel, Telegram bot (`f855a11`) | explicit (initial brief) | done | base |
| H-2 | 11-03 | Bot booking flow: section → type → service, 3-level catalogue, "від" prices, durations hidden | explicit | done | n/a |
| H-3 | 11-03 | Railway deploy (Dockerfile, railway.json) | explicit | done | canon: Railway is temporary |
| H-4 | 11-03 | Redesign: light theme, calendar, finance overview (#3) | explicit | done, later superseded by #56-#72 | — |
| H-5 | 11-03 | Stock with dye tones, schedule by dates, pending bookings, "cash after shift", favourite master, bug report (#4) | explicit | done | cash formula matches §0.8 |
| H-6 | 11-04 | Release v0.1 + backups to Google Drive every 6 h + client reminders (#8) | explicit | done | Drive OAuth ≠ SA |
| H-7 | 11-04 | Schedule grid, Back button, plain-language journal, analytics & forecast (#10, #11) | explicit | done | insights = keep |
| H-8 | 11-04 | PROJECT_STATE.md (#12) | Claude suggestion? | later **deleted** in #70 ("чистка документів") | — |
| H-9 | 11-04 | Versioning + auto GitHub releases (#13, #14) | explicit | done | — |
| H-10 | 11-04 | **Utilities & generator; distribution among masters** (0.1.4, #15) | explicit | done | **SUPERSEDED** by §0.13 (distribution removed) |
| H-11 | 11-04 | Android .apk / Windows .exe (#9) | explicit | done | EXTRA |
| H-12 | 11-04 | Tax report: income by month/quarter, CSV, group limit (#17) | explicit | done | EXTRA |
| H-13 | 11-04 | Demo year / demo data stage 2 (#18, #19) | explicit (test env) | done | EXTRA (dev-only) |
| H-14 | 11-04 | FOP declaration XML F0103309 → replaced by F0103407 (#20, #22) | explicit | done | EXTRA |
| H-15 | 11-04 | Card payment via PrivatBank "Термінал" (NFC) with auto-close (#21) | explicit | done | conflicts §0.15 (C-8) |
| H-16 | 11-04 | Demo "ФОП 1 група" (#23); cleanup old demo (#24) | explicit | done | dev-only |
| H-17 | 11-04 | Taxes & payroll (official salary, HR data, quarterly sheet, dated rates) (#25) | explicit | done | EXTRA |
| H-18 | 11-04 | Accountant Excel pack (#26), refunds + payment status + masters (#27), expenses payment method + files (#28), owner screen + tax calendar (#29) | explicit | **REVERTED** by #31 "Повернення до 0.1.14 (версія 0.1.19), база без змін" | refunds/unpaid status now required again by §0.8 |
| H-19 | 11-04 | Wipe working data (#32) | explicit | done | dev/ops tool, risky |
| H-20 | 11-04 | Bot: single free master → days (#33); design calendar grid/list (#34) | explicit | done | — |
| H-21 | 11-04 | Generator motohours, quarterly report, menu dedup, **no rent** (#35) | explicit | done | canon keeps "Rent" line (C-12 related) |
| H-22 | 11-04 | 2FA via Telegram + trusted devices (#36), login page colours (#37) | explicit | done | EXTRA (C-21) |
| H-23 | 11-04 | Stock fixes, auto write-off of tiny remainders (#38, #39) | explicit | done | — |
| H-24 | 11-04 | Anti-spam, public booking API, site with online booking (#40, #41) | explicit | the public site was **removed** from main in #70 (moved to branch `website`) | — |
| H-25 | 11-04 | "Now / Next" card, live refresh (#42); fast taps fixes (#43, #45) | explicit | done | — |
| H-26 | 11-04 | Unclosed orders, shifting bookings on lateness (Telegram/SMS/Viber), phone whitelist (#44) | explicit | done | SMS part conflicts §0.15 |
| H-27 | 11-04 | Booking without phone; delete clients without phone; drop "Client came" (#46) | explicit | done | — |
| H-28 | 11-04/05 | Quick booking for master, templates, round "+", colour orders red when dyeing without materials (#47-#51) | explicit | done | relates to materials flag |
| H-29 | 11-05 | Client base: card, stats, segments, duplicates, analytics, export (#52) | explicit | done | EXTRA |
| H-30 | 11-05 | SMS campaigns for director (#53) | explicit | done (no real sending yet) | conflicts §0.15 (C-6) |
| H-31 | 11-05 | Performance: gzip, cache, fonts, skeletons, N+1, indexes, RUM (#54, #55) | explicit / Claude suggestion | done | — |
| H-32 | 11-05 | Redesign steps 1-5, UI regression in CI (#56-#61) | explicit | done | — |
| H-33 | 11-05 | Calendar compact/iPhone order, schedule, orders filters (#62-#67) | explicit | done | calendar colours ≠ §0.17 |
| H-34 | 11-05 | Switch to version 0.2.0; TurboSMS & FOP → 1.0.0 (#69) | explicit | done (tag v0.2.0) | C-15 |
| H-35 | 11-05 | Optimisation: no business-card site, **no code comments**, docs cleanup (#70) | explicit | done | CLAUDE.md "comments minimal" |
| H-36 | 11-05 | Master quick booking, "My orders", order authorship (#71, 0.2.1) | explicit | done | partly §0.12 |
| H-37 | 11-05 | Visual refresh 0.2.1 (#72), login polish (#73) | explicit | done | — |
| H-38 | 11-05 | Plan 0.2.2 stages A-F (director's edits) (#74-#79) | explicit (ROADMAP "правки директора 05.11.2026") | done on main; **unreleased** (CHANGELOG "[Невипущене]") | — |
| H-39 | 11-05 | Service API `/api/ext/v1` for MDs Manager (`2d25bce`) | explicit ("хай ділиться даними з MDs connect і навпаки", transcript 11-05 20:05) | on branch `feat/ext-api-md-manager`, **not merged** | conflicts §0.15 (C-5) |
| H-40 | 11-11 | This plan: canon v0.2.0 rev 3 + 2.16.1 Mermaid "Логіка системи" | explicit | Phase 0 in progress | — |

## Open decisions in older docs
- `docs/ROADMAP.md` "Після версії 0.1": "Рішення директора: обмежувати нулем заробіток майстра в режимах «майстер»/«пропорційно»?" Still open, and **made moot by the canon** (modes removed).
- Lock file for deps, Content-Security-Policy: never done.
- "Export to Excel": requested in roadmap stage 3; the Excel pack was built in #26 and then reverted (#31).

## MDs Manager instructions touching Connect (from transcripts)
- 11-05: Manager is a product **for the owner only** (promotion, ads, content, analytics); share data both ways with Connect.
- 11-06: Manager UI based on the MDS Connect design; owner + admins log in (admins without money/ads). This depends on the Connect `admin` role (C-4).
- 11-06: local AI / Gemini for content. Matches the spirit of §0.16 (local LLM later) but on the Manager side.
- ⚠️ A Telegram bot token and a Google AI Studio key were pasted into chat (11-05 19:49, 11-06 07:16). See `09_SECURITY_AUDIT.md` S-1.

## Superseded by Section 0
H-10 (distribution), the materials modes (base model in H-1), the Admin role (H-1), the master-share formula (H-1, FINANCE.md), and parts of H-15 / H-26 / H-30 / H-39 (pending decisions C-5…C-8).
