# 13 — Unused / obsolete files (nothing deleted)

| Item | Why unnecessary | Referenced by |
|---|---|---|
| `ExpenseFile` model + table `expense_files` (migration 0015) | the upload feature was reverted in #31; no service or router uses it | `models.py:649`, `wipe.py:10` |
| `MasterCharge` model, `master_charges` table, utilities charge functions, 4 routes, UI blocks in `VIEWS.utilities`, settings `utilities_charge_*` | removed by canon | `utilities.py:414-608`, `routers/utilities.py:159-177`, `analytics.py:88-116`, `orders.py:401,486,597`, `journal_feed.py:359` |
| `MaterialsMode`, `users.materials_mode`, `orders.materials_mode`, setting `default_materials_mode`, labels in `app.js:154` | removed by canon (keep the column until 0.2.1-equivalent) | 42 refs |
| `Role.ADMIN`, `NAV.admin`, `MOBILE_TABS.admin`, `ROLE_UA.admin` | removed by canon | 30 + JS |
| `Split.salon_gross` | used only in tests | `finance.py:17` |
| `mds/demo_fop1.py`, CLI `demo-fop1`, env `MDS_DEMO_FOP1` | test-environment only, but shipped in the prod entrypoint | `__main__.py:116,137` |
| `mds/wipe.py`, `wipe-data`, env `MDS_WIPE_DATA` | test/reset tool; dangerous in prod | `__main__.py:110,131` |
| `routers/card_terminal.py::mock_complete` | test-only route in prod router | — |
| `docs/DEPLOY_RAILWAY.md` vs `docs/OPERATIONS.md` | overlapping deploy/ops content; Railway is "temporary" per canon | README |
| `docs/ROADMAP.md` stage 2 (Flutter) | superseded by WebView/Electron shells | — |
| Remote branches (≈88 besides `main`) | most are merged feature branches (`feature/*`, `design/*`, `fix/*`, `perf/*`, `release/*`, `revert/to-0.1.14`) | git; `website` and `site/*` are intentionally separate |
| `__pycache__/` dirs in the working copy | build artefacts, git-ignored, **not** committed | — |
| Dependencies | none found unused in `requirements.txt` (7 packages, all imported) | — |
| Conflicting migrations | none on `main` (linear 0001-0024); 0025 only on the ext-API branch, which creates a possible head conflict with Phase 2 migrations | 08 |
| Redundant docs | `README.md` repeats CLAUDE.md commands | — |
