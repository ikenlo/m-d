"""Захист від спаму записів з Telegram-бота і сайту (онлайн-запис).

Правила (числа — у налаштуваннях, «Налаштування → Захист онлайн-запису»):
1. Заблокований клієнт (картка клієнта → «Заблокувати онлайн-запис») не може записатися ні з бота, ні з сайту.
2. Не більше N активних майбутніх записів на одного клієнта (за номером телефону / Telegram).
3. Не більше N нових записів від одного клієнта за добу (скасування теж рахуються — захист від «записав/скасував»).
4. Черга непідтверджених записів з бота й сайту не більше N: поки адміністратор не розбере — нові не приймаються.
5. Сайт: не більше N нових записів за годину загалом (масова атака); записи з сайту завжди «очікують»
   підтвердження; часті неявки клієнта — запис лише через підтвердження.
Персонал (директор/адміністратор/майстер) обмежень не має.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import DomainError
from ..models import Appointment, ApptStatus, CallbackRequest, Client
from .common import get_setting

ACTIVE = (ApptStatus.PENDING, ApptStatus.CONFIRMED, ApptStatus.IN_PROGRESS)
ONLINE = ("telegram",)


class TooMany(DomainError):
    status = 429
    code = "too_many"


def _n(s: Session, key: str) -> int:
    return int(get_setting(s, key) or 0)


def whitelisted(s: Session, phone_n: str | None) -> bool:
    """Номери з білого списку (власниця, персонал) — без обмежень захисту від спаму."""
    return bool(phone_n) and phone_n in (get_setting(s, "antispam_whitelist") or [])


def check_booking(s: Session, client: Client, source: str) -> None:
    """Перевірка перед створенням запису клієнтом (бот або сайт). Кидає TooMany з людським поясненням."""
    if whitelisted(s, client.phone):
        return
    now = clock.now()
    if client.blocked:
        raise TooMany("Онлайн-запис для цього номера недоступний. Зателефонуйте, будь ласка, в салон.")
    lim = _n(s, "antispam_max_active")
    if lim:
        active = s.scalar(select(func.count(Appointment.id)).where(
            Appointment.client_id == client.id, Appointment.status.in_(ACTIVE), Appointment.starts_at >= now)) or 0
        if active >= lim:
            raise TooMany(f"У вас уже є {active} активні записи. Новий можна зробити після візиту "
                          "або перенесіть/скасуйте наявний.")
    lim = _n(s, "antispam_max_per_day")
    if lim:
        day = s.scalar(select(func.count(Appointment.id)).where(
            Appointment.client_id == client.id, Appointment.source.in_(ONLINE),
            Appointment.created_at >= now - timedelta(days=1))) or 0
        if day >= lim:
            raise TooMany("Забагато записів за добу з цього номера. Зателефонуйте, будь ласка, в салон.")
    lim = _n(s, "antispam_max_pending_total")
    if lim:
        pending = s.scalar(select(func.count(Appointment.id)).where(
            Appointment.status == ApptStatus.PENDING, Appointment.source.in_(ONLINE), Appointment.starts_at >= now)) or 0
        if pending >= lim:
            raise TooMany("Зараз багато нових записів — адміністратор їх опрацьовує. Спробуйте пізніше або зателефонуйте.")


def needs_confirmation(s: Session, client: Client) -> bool:
    """Часті неявки (2+ за 90 днів) — запис лише з підтвердженням адміністратора, навіть з автопідтвердженням."""
    since = clock.now() - timedelta(days=90)
    n = s.scalar(select(func.count(Appointment.id)).where(
        Appointment.client_id == client.id, Appointment.status == ApptStatus.NO_SHOW, Appointment.starts_at >= since)) or 0
    return n >= 2


def check_callback(s: Session, phone_n: str, telegram_user_id: int | None) -> None:
    """Заявки «передзвоніть»: не більше N за добу з одного номера / Telegram і N на годину загалом."""
    if whitelisted(s, phone_n):
        return
    now = clock.now()
    c = s.scalar(select(Client).where(Client.phone == phone_n))
    if c is not None and c.blocked:
        raise TooMany("Заявку прийняти не вдалося. Зателефонуйте, будь ласка, в салон.")
    lim = _n(s, "antispam_callbacks_per_day")
    if lim:
        q = select(func.count(CallbackRequest.id)).where(CallbackRequest.created_at >= now - timedelta(days=1))
        q = q.where((CallbackRequest.phone == phone_n) | (CallbackRequest.telegram_user_id == telegram_user_id)) \
            if telegram_user_id else q.where(CallbackRequest.phone == phone_n)
        if (s.scalar(q) or 0) >= lim:
            raise TooMany("Ми вже отримали вашу заявку — адміністратор передзвонить.")
    lim = _n(s, "antispam_callbacks_per_hour")
    if lim and (s.scalar(select(func.count(CallbackRequest.id)).where(
            CallbackRequest.created_at >= now - timedelta(hours=1))) or 0) >= lim:
        raise TooMany("Зараз багато заявок. Спробуйте пізніше або зателефонуйте.")
