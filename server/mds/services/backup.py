from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def make_backup(database_url: str, backup_dir: Path, keep_days: int = 30) -> Path:
    src = sqlite_path(database_url)
    if src is None:
        raise RuntimeError("Автоматичний бекап реалізовано для SQLite; для PostgreSQL використовуйте pg_dump")
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"mds-{datetime.now():%Y%m%d-%H%M%S}.db"
    i = 1
    while dst.exists():
        dst = backup_dir / f"mds-{datetime.now():%Y%m%d-%H%M%S}-{i}.db"
        i += 1
    with closing(sqlite3.connect(src)) as source, closing(sqlite3.connect(dst)) as target:
        source.backup(target)
    with closing(sqlite3.connect(dst)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Резервна копія не пройшла перевірку цілісності")
    rotate(backup_dir, keep_days)
    return dst


def rotate(backup_dir: Path, keep_days: int) -> int:
    files = sorted(backup_dir.glob("mds-*.db"))
    if keep_days <= 0 or len(files) <= 1:
        return 0
    cut = datetime.now().timestamp() - keep_days * 86400
    removed = 0
    for old in files[:-1]:
        if old.stat().st_mtime < cut:
            old.unlink(missing_ok=True)
            removed += 1
    return removed


NAME_RE = re.compile(r"^mds-\d{8}-\d{6}(-\d+)?\.db$")


def backup_file(backup_dir: Path, name: str) -> Path:
    from ..errors import NotFound

    if not NAME_RE.match(name or ""):
        raise NotFound("Копію не знайдено")
    path = backup_dir / name
    if not path.is_file():
        raise NotFound("Копію не знайдено")
    return path


def check_backup(path: Path) -> dict:
    """Перевірка відновлюваності: цілісність і кількість рядків ключових таблиць."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # noqa: S608
                  for t in ("users", "orders", "ledger", "audit_log", "payouts", "expenses") if t in tables}
        rev = c.execute("SELECT version_num FROM alembic_version").fetchone()[0] if "alembic_version" in tables else None
    return {"ok": ok and "orders" in counts, "integrity": ok, "counts": counts, "revision": rev}


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.exists():
        return []
    return [
        {"name": p.name, "size": p.stat().st_size, "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
        for p in sorted(backup_dir.glob("mds-*.db"), reverse=True)
    ]


def backup_cycle(cfg, db) -> dict:
    from . import gdrive, notifications

    out: dict = {"local": None, "drive": None}
    if sqlite_path(cfg.database_url) is None:
        return out
    path = make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep_days)
    out["local"] = path.name
    if gdrive.ready(cfg.data_dir):
        try:
            out["drive"] = gdrive.upload(cfg.data_dir, path)
        except Exception as e:  # noqa: BLE001
            out["drive_error"] = str(e)
            if gdrive.record_error(cfg.data_dir, str(e)):
                with db.session() as s:
                    notifications.notify_directors(
                        s, "⚠️ Не вдалося зберегти резервну копію в Google Drive.\n"
                           f"Причина: {str(e)[:300]}\nЛокальна копія на сервері є. Перевірте: Налаштування → Резервні копії.")
    return out


def alert_local_failure(db, err: str) -> None:
    from . import notifications

    try:
        with db.session() as s:
            notifications.notify_directors(
                s, "🔴 Не вдалося створити резервну копію на сервері.\n"
                   f"Причина: {err[:300]}\nПеревірте диск сервера і «Налаштування → Резервні копії».")
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("mds").exception("Не вдалося надіслати сповіщення про помилку бекапу")
