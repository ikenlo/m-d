from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.getenv(f"MDS_{name}", default)


ENVIRONMENTS = ("production", "test", "dev")


def current_env() -> str:
    v = (os.getenv("MDS_ENV") or "production").strip().lower()
    return v if v in ENVIRONMENTS else "production"


def is_production() -> bool:
    return current_env() == "production"


def require_non_production(what: str) -> None:
    if is_production():
        raise RuntimeError(
            f"{what}: заборонено в робочому середовищі (MDS_ENV=production). "
            "Для тестового середовища задайте MDS_ENV=test."
        )


@dataclass
class Config:
    data_dir: Path = field(
        default_factory=lambda: Path(_env("DATA_DIR", os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "./data")).resolve()
    )
    database_url: str = ""
    secret_key: str = ""
    token_ttl_hours: int = field(default_factory=lambda: int(_env("TOKEN_TTL_HOURS", "12")))
    idle_minutes: int = field(default_factory=lambda: int(_env("IDLE_MINUTES", "30")))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Kyiv"))
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_TOKEN", ""))
    manager_api_key: str = field(default_factory=lambda: _env("MANAGER_API_KEY", ""))
    meta_verify_token: str = field(default_factory=lambda: _env("META_VERIFY_TOKEN", ""))
    checkbox_api_url: str = field(default_factory=lambda: _env("CHECKBOX_API_URL", "https://api.checkbox.ua"))
    checkbox_login: str = field(default_factory=lambda: _env("CHECKBOX_LOGIN", ""))
    checkbox_password: str = field(default_factory=lambda: _env("CHECKBOX_PASSWORD", ""))
    checkbox_license_key: str = field(default_factory=lambda: _env("CHECKBOX_LICENSE_KEY", ""))
    meta_app_secret: str = field(default_factory=lambda: _env("META_APP_SECRET", ""))
    webhook_processing_timeout_minutes: int = field(default_factory=lambda: int(_env("WEBHOOK_PROCESSING_TIMEOUT_MINUTES", "5")))
    backup_dir: Path | None = None
    backup_interval_hours: int = field(default_factory=lambda: int(_env("BACKUP_INTERVAL_HOURS", "6")))
    backup_keep_days: int = field(default_factory=lambda: int(_env("BACKUP_KEEP_DAYS", "30")))
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
