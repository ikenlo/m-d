"""Публічний API для сайту салону (онлайн-запис). Без авторизації, тому з захистом від спаму:

- вмикається налаштуванням `public_booking_enabled` (до відкриття — вимкнено, 503);
- обмеження частоти з однієї IP-адреси (слоти, записи) — 429;
- «пастка для ботів» (приховане поле `website`) і мінімальний час заповнення форми (`elapsed_ms` ≥ 3 с);
- перевірка імені/телефону (лише український номер), без посилань у тексті;
- правила `services/antispam.py` (ліміти на клієнта, черга непідтверджених, блокування);
- записи з сайту завжди «очікують підтвердження» адміністратором.
Контракт описано в site/docs/api.md.
"""

from __future__ import annotations

import re
import threading
import time as _time
from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ... import clock
from ...errors import DomainError, ValidationError
from ...models import ApptStatus, Service
from ...services import bookings, users
from ...services.antispam import TooMany
from ...services.catalog import normalize_phone
from ...services.common import Actor, get_setting
from ..deps import get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api/public", tags=["public"])

SITE = Actor(None, "site", "client", None)
MIN_FILL_MS = 3000
URL_RE = re.compile(r"(https?://|www\.|\.(com|ru|net|org|ua|xyz|top)\b|t\.me/|@\w{4,})", re.I)


class Unavailable(DomainError):
    status = 503
    code = "unavailable"


class _Limiter:
    """Ковзне вікно в пам'яті процесу: не більше `limit` подій за `window` секунд на ключ."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = _time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 5000:  # не розростаємось від сканерів
                for k in [k for k, v in self.hits.items() if not v][:2500]:
                    self.hits.pop(k, None)
            return True

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


READ_LIMIT = _Limiter(120, 600)  # перегляд слотів/послуг: 120 запитів за 10 хв з однієї IP
BOOK_LIMIT = _Limiter(5, 3600)  # спроби запису: 5 за годину з однієї IP


def client_ip(request: Request) -> str:
    # за проксі Railway реальна адреса — остання в X-Forwarded-For (додає сам проксі)
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _enabled(s: Session) -> None:
    if not get_setting(s, "public_booking_enabled"):
        raise Unavailable("Онлайн-запис ще не відкрито. Запишіться через Telegram або зателефонуйте.")


def _read(request: Request) -> None:
    if not READ_LIMIT.hit(client_ip(request)):
        raise TooMany("Забагато запитів. Спробуйте за кілька хвилин.")


def _short(full_name: str) -> str:
    return (full_name or "").split()[0] if full_name else ""


def _horizon(s: Session) -> tuple[date, date]:
    today = clock.now().date()
    return today, today + timedelta(days=int(get_setting(s, "booking_horizon_days") or 14))


@router.get("/info")
def info(request: Request, s: Session = Depends(get_session)):
    _read(request)
    a, b = _horizon(s)
    return {"enabled": bool(get_setting(s, "public_booking_enabled")), "salon_name": get_setting(s, "salon_name"),
            "slot_step_min": int(get_setting(s, "slot_step_min")), "date_from": a.isoformat(), "date_to": b.isoformat(),
            "now": clock.now().isoformat(timespec="minutes")}


@router.get("/services")
def services(request: Request, s: Session = Depends(get_session)):
    _read(request)
    _enabled(s)
    from ...services import catalog

    out = []
    for x in catalog.list_services(s):
        if not x.is_active:
            continue
        out.append({"id": x.id, "name": x.name, "section": x.section, "category": x.category, "subcategory": x.subcategory,
                    "price": x.price, "price_from": bool(x.price_from), "duration_min": x.duration_min})
    return out


@router.get("/masters")
def masters(request: Request, service: int | None = None, s: Session = Depends(get_session)):
    _read(request)
    _enabled(s)
    ms = users.masters_for_service(s, service) if service else users.list_masters(s)
    return [{"id": m.id, "name": _short(m.full_name)} for m in ms]


@router.get("/slots")
def slots(request: Request, date: date, service: int, master: int | None = None, s: Session = Depends(get_session)):
    """Вільні часи початку на дату (час салону, Europe/Kyiv). Минулий час і час ближче за мінімальний
    відступ не показуються. master=0 або без нього — будь-який майстер."""
    _read(request)
    _enabled(s)
    a, b = _horizon(s)
    if not (a <= date <= b):
        return {"date": date.isoformat(), "slots": []}
    svc = s.get(Service, service)
    if svc is None or not svc.is_active:
        raise ValidationError("Послугу не знайдено")
    if master:
        rows = [(t, [master]) for t in bookings.free_slots(s, master, service, date, for_client=True)]
    else:
        rows = sorted(bookings.free_slots_any(s, service, date, for_client=True).items())
    return {"date": date.isoformat(), "slots": [{"time": t.strftime("%H:%M"), "masters": ids} for t, ids in rows]}


class BookingIn(BaseModel):
    name: str = Field(max_length=80)
    phone: str = Field(max_length=32)
    service_id: int
    master_id: int | None = None  # None / 0 — будь-який вільний
    date: date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    comment: str | None = Field(default=None, max_length=300)
    website: str | None = None  # пастка для ботів: людина це поле не бачить
    elapsed_ms: int | None = None  # скільки часу заповнювали форму


@router.post("/bookings", status_code=201)
def create_booking(body: BookingIn, request: Request, s: Session = Depends(get_session)):
    _enabled(s)
    from ...services.antispam import whitelisted

    try:
        trusted = whitelisted(s, normalize_phone(body.phone))
    except DomainError:
        trusted = False
    if not trusted and not BOOK_LIMIT.hit(client_ip(request)):
        raise TooMany("Забагато спроб запису. Спробуйте пізніше або зателефонуйте.")
    if body.website or (body.elapsed_ms is not None and body.elapsed_ms < MIN_FILL_MS) or body.elapsed_ms is None:
        raise ValidationError("Не вдалося надіслати запис. Оновіть сторінку й спробуйте ще раз.")
    name = " ".join(body.name.split())
    if not (2 <= len(name) <= 60) or URL_RE.search(name) or not re.search(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]", name):
        raise ValidationError("Вкажіть ім'я (лише ім'я, без посилань)")
    if body.comment and URL_RE.search(body.comment):
        raise ValidationError("Коментар не може містити посилань")
    phone = normalize_phone(body.phone)
    if not re.fullmatch(r"\+380\d{9}", phone):
        raise ValidationError("Вкажіть український номер телефону, напр. 067 123 45 67")
    hh, mm = (int(x) for x in body.time.split(":"))
    try:
        start = datetime.combine(body.date, time(hh, mm))
    except ValueError:
        raise ValidationError("Невірний час") from None
    a, b = _horizon(s)
    if not (a <= body.date <= b):
        raise ValidationError("На цю дату запис недоступний")
    master_id = body.master_id or None
    if master_id is None:
        free = bookings.free_slots_any(s, body.service_id, body.date, for_client=True).get(start)
        if not free:
            from ...errors import Conflict

            raise Conflict("Цей час щойно зайняли. Оберіть інший, будь ласка.")
        master_id = free[0]
    elif start not in bookings.free_slots(s, master_id, body.service_id, body.date, for_client=True):
        from ...errors import Conflict

        raise Conflict("Цей час щойно зайняли. Оберіть інший, будь ласка.")
    appt = bookings.create_appointment(s, SITE, master_id=master_id, service_id=body.service_id, starts_at=start,
                                       client_name=name, client_phone=phone, comment=(body.comment or "").strip() or None,
                                       status=ApptStatus.PENDING)
    return {"id": appt.id, "status": appt.status, "starts_at": appt.starts_at.isoformat(timespec="minutes"),
            "service": appt.service.name, "master": _short(appt.master.full_name),
            "message": "Дякуємо! Запис отримано — адміністратор підтвердить його найближчим часом."}
