"""Запуск бота (long polling) і доставка черги сповіщень."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from ..db import Database
from ..services import notifications
from .handlers import build_router

log = logging.getLogger("mds.bot")


def build_dispatcher(db: Database) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), db=db)
    dp.include_router(build_router())
    return dp


def make_bot(token: str, **kw) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), **kw)


async def deliver_outbox(bot: Bot, db: Database) -> int:
    """Надіслати накопичені сповіщення. Повертає кількість надісланих."""
    sent = 0
    with db.session() as s:
        for n in notifications.pending(s):
            try:
                await bot.send_message(n.chat_id, n.text, parse_mode=None)
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


async def run_bot(token: str, db: Database) -> None:
    bot = make_bot(token)
    dp = build_dispatcher(db)
    sender = asyncio.create_task(outbox_loop(bot, db))
    try:
        log.info("Telegram-бот запущено")
        await dp.start_polling(bot, handle_signals=False)
    finally:
        sender.cancel()
        await bot.session.close()
