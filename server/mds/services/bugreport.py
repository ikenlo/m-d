"""Баг-репорти з програми: опис + необов'язковий знімок екрана → директору в Telegram."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from sqlalchemy.orm import Session

from .. import clock
from ..errors import ValidationError
from ..models import User
from . import notifications
from .common import Actor, audit

ROLE_UA = {"director": "директор", "admin": "адміністратор", "master": "майстер", "client": "клієнт"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT = 2000


def _save_image(data_dir: Path, image_b64: str) -> str:
    raw = image_b64.split(",", 1)[1] if image_b64.startswith("data:") else image_b64
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise ValidationError("Не вдалося прочитати знімок екрана") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("Знімок завеликий (максимум 5 МБ)")
    if data[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    else:
        raise ValidationError("Знімок має бути JPG, PNG або WEBP")
    folder = data_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"bug-{clock.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.{ext}"
    path.write_bytes(data)
    return str(path)


def create(s: Session, actor: Actor, data_dir: Path, *, text: str, page: str | None = None,
           context: dict | None = None, image_b64: str | None = None) -> dict:
    text = (text or "").strip()
    if len(text) < 3:
        raise ValidationError("Опишіть, будь ласка, що сталося")
    text = text[:MAX_TEXT]
    user = s.get(User, actor.user_id) if actor.user_id else None
    photo = _save_image(data_dir, image_b64) if image_b64 else None
    ctx = context or {}
    lines = [
        "🐞 Повідомлення про помилку",
        f"Від: {user.full_name if user else actor.name} ({ROLE_UA.get(actor.role, actor.role)})",
        f"Сторінка: {page or '—'}",
        f"Час: {clock.now():%d.%m.%Y %H:%M}",
        "",
        text,
    ]
    tech = []
    if ctx.get("device"):
        tech.append(f"Пристрій: {str(ctx['device'])[:200]}")
    if ctx.get("screen"):
        tech.append(f"Екран: {str(ctx['screen'])[:40]}")
    if ctx.get("version"):
        tech.append(f"Версія: {str(ctx['version'])[:40]}")
    for err in (ctx.get("errors") or [])[:5]:
        tech.append(f"⚠️ {str(err)[:300]}")
    if tech:
        lines += ["", "— технічні дані —", *tech]
    message = "\n".join(lines)[:3900]
    sent = notifications.notify_directors(s, message, photo_path=photo)
    audit(s, actor, "bug_report", 0, "create", None, {"page": page, "text": text, "photo": bool(photo), "sent": sent})
    return {"ok": True, "delivered": sent}
