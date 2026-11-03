"""Резервне копіювання БД.

SQLite: онлайн-копія через sqlite3 backup API (безпечно під час роботи).
PostgreSQL: використовуйте pg_dump за розкладом (див. docs/OPERATIONS.md).
"""

from __future__ import annotations

import sqlite3
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
    with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
        source.backup(target)
    # перевірка цілісності копії
    with sqlite3.connect(dst) as check:
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
