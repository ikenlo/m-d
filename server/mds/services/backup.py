from __future__ import annotations

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


def make_backup(database_url: str, backup_dir: Path, keep: int = 30) -> Path:
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
    rotate(backup_dir, keep)
    return dst


def rotate(backup_dir: Path, keep: int) -> None:
    files = sorted(backup_dir.glob("mds-*.db"))
    for old in files[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


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
    path = make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep)
    out["local"] = path.name
    if gdrive.configured() and gdrive.load_state(cfg.data_dir).get("refresh_token"):
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
