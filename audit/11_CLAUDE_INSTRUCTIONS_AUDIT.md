# 11 — Claude Code instructions audit

## Inventory

| Item | Location | Purpose | Activation | Used | Issues |
|---|---|---|---|---|---|
| `CLAUDE.md` (m-d) | repo root, 88 lines | dev rules for MDS Connect: commands, architecture, 11 invariants, how to add a feature, change process, apps, versions, terminal, taxes, website, anti-spam | auto-loaded in m-d | yes | **Outdated vs canon**: invariant 2 is fine; invariant 3 points to the old formula (via `FINANCE.md`); no mention of the canon; "1.0.0 = TurboSMS + FOP" conflicts with canon §0.15 / C-15 |
| `CLAUDE.md` (md-managment) | `ikenlo/md-managment` root | MDs Manager rules; references `docs/integration/README.md` and Connect admin mapping | auto-loaded there | yes | depends on the Connect admin role (C-4) |
| `docs/*.md` (12 files) | m-d `docs/` | ARCHITECTURE, FINANCE, API, OPERATIONS, DEPLOY_RAILWAY, ROADMAP, APPS, CLIENTS, INTEGRATION, PAYROLL, PERFORMANCE, TERMINAL | referenced from CLAUDE.md/README | yes | FINANCE.md describes the non-canon model; ROADMAP mixes done checklists with plans; DEPLOY_RAILWAY + OPERATIONS overlap |
| `CHANGELOG.md` | root | history + version rule | release workflow reads it | yes | fine |
| `README.md` | root | overview | — | — | duplicates the CLAUDE.md commands |
| `ui-tests/README.md` | | UI tests | — | — | — |
| `.claude/` (settings, commands, agents, hooks, skills) | — | — | — | **none in either repo** | — |
| MCP config | none in repos | — | — | — | session-level MCP only (GitHub, Docs, etc.) |
| Memory | none found (`~/.claude/projects/*/memory` absent) | — | — | — | — |
| Container-level: `~/.claude/skills/session-start-hook`, `stop-hook-git-check.sh` | `/root/.claude` | harness-provided | harness | — | not project-owned |
| Transcripts of previous sessions | `~/.claude/projects/-home-user*` | history | — | read for 01 | contain secrets (09 S-1) |

## Hierarchy today (effective)
1. CLAUDE.md (m-d) → 2. docs/FINANCE.md, ARCHITECTURE.md → 3. code comments (mostly removed in #70) → 4. this plan (session prompt, not in repo).

**Problem:** Section 0 exists only in the chat prompt. Unless it lands in the repo (Phase 1), a future session will follow the old FINANCE.md.

## Recommendations (not applied)
- Phase 1: add a canon file (`docs/FINANCE.md` rewritten, or `docs/CANON.md`) and point CLAUDE.md at it as the only source of truth.
- Keep CLAUDE.md ≤ 200 lines; move the terminal/taxes/website sections into their docs and keep one-line pointers.
- Add a project hook or CI check that greps for forbidden symbols (`materials_mode`, `Role.ADMIN`, `share_bp`, `utilities/allocat`) after Phase 2, so regressions fail CI. (Phase 2+ work, not now.)
- Remove transcripts with secrets / rotate secrets (09 S-1).
