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
from .routers import auth, calendar, catalog, money, staff, system

log = logging.getLogger("mds")
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


async def _backup_loop(cfg: Config) -> None:
    """Автоматичний бекап SQLite кожні N годин (перший — одразу при старті)."""
    while True:
        try:
            if backup.sqlite_path(cfg.database_url):
                path = await asyncio.to_thread(backup.make_backup, cfg.database_url, cfg.backup_dir, cfg.backup_keep)
                log.info("Резервна копія: %s", path)
        except Exception:  # noqa: BLE001
            log.exception("Помилка резервного копіювання")
        await asyncio.sleep(max(cfg.backup_interval_hours, 1) * 3600)


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
            tasks.append(asyncio.create_task(_backup_loop(cfg)))
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

    for r in (auth, staff, catalog, calendar, money, system):
        app.include_router(r.router)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

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
