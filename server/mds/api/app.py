"""Фабрика FastAPI-застосунку: REST API + веб-панель (PWA) зі статики."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .. import __version__, clock, perf
from ..config import Config
from ..db import Database
from ..errors import DomainError
from ..services import backup
from .routers import auth, calendar, card_terminal, callbacks, catalog, clients, money, sms, staff, stock_schedule, system, tax_report, taxes, utilities

log = logging.getLogger("mds")
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


def _asset_version() -> str:
    """Мітка для ?v= у index.html: версія + хеш app.js/style.css — новий файл = нова адреса,
    тож браузер може кешувати статику «назавжди» (immutable) і не перевіряти її щоразу."""
    import hashlib

    digest = hashlib.sha256()
    for name in ("app.js", "style.css"):
        digest.update((STATIC / name).read_bytes())
    return f"{__version__}-{digest.hexdigest()[:10]}"


async def _backup_loop(cfg: Config, db: Database) -> None:
    """Автоматичний бекап кожні N годин (за замовч. 6; перший — одразу при старті):
    локальна копія + Google Drive, якщо підключено."""
    while True:
        try:
            res = await asyncio.to_thread(backup.backup_cycle, cfg, db)
            log.info("Резервна копія: %s; Google Drive: %s", res.get("local"),
                     (res.get("drive") or {}).get("name") or res.get("drive_error") or "не підключено")
        except Exception:  # noqa: BLE001
            log.exception("Помилка резервного копіювання")
        await asyncio.sleep(max(cfg.backup_interval_hours, 1) * 3600)


async def _reminders_loop(db: Database) -> None:
    """Нагадування клієнтам (за добу і за 2 год) — кожні 5 хвилин; строки податків ФОП — щогодини."""
    from ..services import reminders, taxes

    tick = 0
    while True:
        try:
            with db.session() as s:
                n = reminders.send_due(s) + reminders.remind_unclosed(s)
                from ..services import sms_center  # SMS: автоматизація, черга, статуси доставки

                sms_center.run_automations(s)
                sms_center.process_queue(s)
                if tick % 3 == 0:
                    sms_center.refresh_statuses(s)
                if tick % 12 == 0:  # щогодини — строки сплати податків ФОП (директору)
                    taxes.tax_reminders(s)
            if n:
                log.info("Нагадувань поставлено в чергу: %s", n)
        except Exception:  # noqa: BLE001
            log.exception("Помилка нагадувань")
        tick += 1
        await asyncio.sleep(300)


def create_app(
    cfg: Config | None = None,
    db: Database | None = None,
    *,
    background: bool = False,
    with_bot: bool = False,
) -> FastAPI:
    """background — авто-бекапи; with_bot — запустити Telegram-бот у тому ж процесі."""
    cfg = cfg or Config()
    clock.configure(cfg.timezone)
    db = db or Database(cfg.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if background:
            tasks.append(asyncio.create_task(_backup_loop(cfg, db)))
            tasks.append(asyncio.create_task(_reminders_loop(db)))
        if with_bot:
            if cfg.telegram_token:
                from ..bot.runner import run_bot

                tasks.append(asyncio.create_task(run_bot(cfg.telegram_token, db)))
            else:
                log.warning("MDS_TELEGRAM_TOKEN не задано — Telegram-бот не запущено")
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="MDS Connect", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.db = db

    @app.exception_handler(DomainError)
    async def domain_error(_req: Request, exc: DomainError):
        return JSONResponse({"error": exc.code, "detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(x) for x in first.get("loc", [])[1:])
        return JSONResponse(
            {"error": "validation", "detail": f"Невірні дані: {field} — {first.get('msg', '')}"}, status_code=422
        )

    for r in (auth, card_terminal, staff, clients, catalog, calendar, callbacks, money, sms, stock_schedule, system, tax_report, taxes, utilities):
        app.include_router(r.router)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    index_html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__V__", _asset_version())

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def timing(request: Request, call_next):
        """Час відповіді API: заголовок Server-Timing (видно в DevTools), статистика для «Налаштування →
        Швидкодія», повільні запити — у журнал сервера (лише шаблон маршруту, без параметрів і даних)."""
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        t0 = time.perf_counter()
        resp = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        route = request.scope.get("route")
        name = f"{request.method} {getattr(route, 'path', None) or 'other'}"
        perf.record("server", name, ms, perf.SLOW_API_MS)
        if ms >= perf.SLOW_API_MS:
            log.warning("повільний запит %s: %.0f мс", name, ms)
        resp.headers["Server-Timing"] = f"app;dur={ms:.1f}"
        return resp

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        path = request.url.path
        if path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith("/static/") and resp.status_code in (200, 304):
            # версійовані файли (?v=…) і шрифти не змінюються — кешуємо на рік; решта — з перевіркою ETag
            if request.query_params.get("v") or path.startswith("/static/fonts/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    # стиснення відповідей (JS/CSS/JSON) — у 3–5 разів менше трафіку на мобільному інтернеті
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    return app
