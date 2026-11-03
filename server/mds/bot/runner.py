"""Запуск бота (long polling) і доставка черги сповіщень."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl

from aiohttp import ClientSession
from aiogram import Bot, Dispatcher
from aiogram.__meta__ import __version__ as aiogram_version
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from ..db import Database
from ..services import notifications
from .handlers import build_router
from .staff import StaffSessions

log = logging.getLogger("mds.bot")


def build_dispatcher(db: Database) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), db=db, staff=StaffSessions())
    dp.include_router(build_router())
    return dp


class EnvAwareSession(AiohttpSession):
    """HTTP-сесія бота, що враховує стандартні налаштування мережі сервера:
    HTTPS_PROXY / NO_PROXY (корпоративний проксі) і SSL_CERT_FILE (власний CA)."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        cafile = os.getenv("SSL_CERT_FILE")
        if cafile and os.path.exists(cafile):
            self._connector_init["ssl"] = ssl.create_default_context(cafile=cafile)

    async def create_session(self) -> ClientSession:
        if self._should_reset_connector:
            await self.close()
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                connector=self._connector_type(**self._connector_init),
                headers={"User-Agent": f"mds-connect aiogram/{aiogram_version}"},
                trust_env=True,
            )
            self._should_reset_connector = False
        return self._session


def make_bot(token: str, **kw) -> Bot:
    kw.setdefault("session", EnvAwareSession())
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), **kw)


async def deliver_outbox(bot: Bot, db: Database) -> int:
    """Надіслати накопичені сповіщення. Повертає кількість надісланих."""
    sent = 0
    with db.session() as s:
        for n in notifications.pending(s):
            try:
                markup = None
                if n.buttons:
                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in n.buttons
                    ])
                if n.photo_path and os.path.exists(n.photo_path):
                    # підпис до фото — до 1024 символів; повний текст окремим повідомленням
                    await bot.send_photo(n.chat_id, FSInputFile(n.photo_path), caption=n.text[:1000], parse_mode=None)
                    if len(n.text) > 1000:
                        await bot.send_message(n.chat_id, n.text, parse_mode=None, reply_markup=markup)
                else:
                    await bot.send_message(n.chat_id, n.text, parse_mode=None, reply_markup=markup)
                notifications.mark_sent(n)
                sent += 1
            except Exception as e:  # noqa: BLE001 — помилка одного повідомлення не зупиняє чергу
                notifications.mark_failed(n, str(e))
                log.warning("Не вдалося надіслати сповіщення #%s: %s", n.id, e)
    return sent


async def outbox_loop(bot: Bot, db: Database, interval: float = 3.0) -> None:
    while True:
        try:
            await deliver_outbox(bot, db)
        except Exception:  # noqa: BLE001
            log.exception("Помилка доставки сповіщень")
        await asyncio.sleep(interval)


async def run_bot(token: str, db: Database, *, retry_delay: float = 15.0) -> None:
    """Запуск бота з автоматичним перезапуском: помилка мережі не зупиняє бота назавжди
    і обов'язково потрапляє в журнал (раніше падіння фонової задачі було непомітним)."""
    while True:
        bot = make_bot(token)
        dp = build_dispatcher(db)
        sender = asyncio.create_task(outbox_loop(bot, db))
        try:
            me = await bot.get_me()
            log.info("Telegram-бот @%s запущено", me.username)
            await dp.start_polling(bot, handle_signals=False)
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Telegram-бот зупинився з помилкою; перезапуск через %.0f с", retry_delay)
        finally:
            sender.cancel()
            await bot.session.close()
        await asyncio.sleep(retry_delay)
