from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.getenv(f"MDS_{name}", default)


@dataclass
class Config:
    data_dir: Path = field(
        default_factory=lambda: Path(_env("DATA_DIR", os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "./data")).resolve()
    )
    database_url: str = ""
    secret_key: str = ""
    token_ttl_hours: int = field(default_factory=lambda: int(_env("TOKEN_TTL_HOURS", "12")))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Kyiv"))
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_TOKEN", ""))
    backup_dir: Path | None = None
    backup_interval_hours: int = field(default_factory=lambda: int(_env("BACKUP_INTERVAL_HOURS", "6")))
    backup_keep: int = field(default_factory=lambda: int(_env("BACKUP_KEEP", "40")))
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", os.getenv("PORT") or "8000")))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = _env("DATABASE_URL", f"sqlite:///{self.data_dir / 'mds.db'}")
        if self.backup_dir is None:
            self.backup_dir = Path(_env("BACKUP_DIR", str(self.data_dir / "backups"))).resolve()
        if not self.secret_key:
            self.secret_key = _env("SECRET_KEY", "") or self._load_or_create_secret()

    def _load_or_create_secret(self) -> str:
        path = self.data_dir / "secret.key"
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_urlsafe(48)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key
