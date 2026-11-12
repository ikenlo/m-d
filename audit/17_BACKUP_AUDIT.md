# 17 — Backup & recovery audit

| Aspect | Current | Canon §0.14 | Status |
|---|---|---|---|
| Mechanism | SQLite online backup API → `data/backups/mds-YYYYMMDD-HHMMSS.db` (`services/backup.py:18-34`) | — | OK |
| Frequency | every `MDS_BACKUP_INTERVAL_HOURS` = **6 h** in-process loop (`api/app.py:34-42`); manual `POST /api/backups`, CLI `python -m mds backup`; before wipe/demo | daily + manual | MATCHES (stricter) |
| Remote | Google Drive via **OAuth** (`services/gdrive.py`, scope `drive.file`, refresh token in `data/gdrive.json`), director connects in UI | **Service Account** | MISMATCH |
| Encryption | none (plain `.db` uploaded) | encrypted remote | MISSING |
| Copies | server volume + Drive | 3 (prod, encrypted Drive, local Windows PC) | PARTIAL (no PC copy / export button) |
| Integrity | `PRAGMA integrity_check` on the copy; fails → exception | yes | MATCHES |
| Retention | local: keep **40 files** (`MDS_BACKUP_KEEP`) ≈ 10 days; Drive: keep **180 files** (`MDS_GDRIVE_KEEP`, `gdrive.py:63`) ≈ 45 days | 30 days | MISMATCH |
| Failure alert | Drive failure → Telegram to directors (de-duplicated via `record_error`); **local** failure → log only | Telegram alert | PARTIAL |
| PostgreSQL | not supported (raises; docs say use pg_dump) | — | gap if PG is ever used |
| Restore procedure | `docs/OPERATIONS.md:136` (stop → copy file → start); env-based restore notes at :174 | documented + test script | PARTIAL (no `scripts/test_restore.sh`, never rehearsed in CI) |
| Backup of uploaded files | n/a (no uploads now) | — | — |
| Secret key | `data/secret.key` not backed up; if lost, all sessions are invalid (harmless) | — | INFO |

## Gaps to close (Phase 2 §2.10)
1. Service Account upload (`MDS_GDRIVE_SA_JSON`, `MDS_GDRIVE_FOLDER_ID`). Remember that a Service Account has **no storage quota of its own** on personal Google accounts: upload to a **Shared Drive**, or share a folder owned by a Workspace user. Otherwise uploads fail with `storageQuotaExceeded`. This needs a decision on the Google account type.
2. Client-side encryption (e.g. age/AES-GCM with a key from env) before upload; document key custody.
3. Age-based retention (30 days) on both sides.
4. Alert on local backup failure.
5. Restore rehearsal script plus a CI job that restores the latest test backup and runs `integrity_check` plus a row-count sanity check.
6. Third copy: "Download backup" button (director) or a scheduled pull from the Windows PC.
