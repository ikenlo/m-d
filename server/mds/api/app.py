"""Фабрика FastAPI-застосунку: REST API + веб-панель (PWA) зі статики."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, clock
from ..config import Config
from ..db import Database
from ..errors import DomainError
from ..services import backup
from .routers import auth, calendar, card_terminal, callbacks, catalog, money, public, staff, stock_schedule, system, tax_report, taxes, utilities

log = logging.getLogger("mds")
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


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
                n = reminders.send_due(s)
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

    for r in (auth, card_terminal, staff, catalog, calendar, callbacks, money, public, stock_schedule, system, tax_report, taxes, utilities):
        app.include_router(r.router)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    # сайт салону (інший домен, напр. GitHub Pages) звертається лише до /api/public/* — CORS тільки для нього,
    # і лише для доменів зі змінної MDS_PUBLIC_ORIGINS (через кому)
    import os

    public_origins = {o.strip().rstrip("/") for o in os.environ.get("MDS_PUBLIC_ORIGINS", "").split(",") if o.strip()}

    @app.middleware("http")
    async def public_cors(request: Request, call_next):
        origin = request.headers.get("origin", "").rstrip("/")
        is_public = request.url.path.startswith("/api/public/")
        allowed = is_public and origin and (origin in public_origins or "*" in public_origins)
        if allowed and request.method == "OPTIONS":
            from fastapi.responses import Response

            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST",
                "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600", "Vary": "Origin"})
        resp = await call_next(request)
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        return resp

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app
