from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys

from . import clock
from .config import Config
from .db import Database
from .migrate import upgrade


def _db(cfg: Config) -> Database:
    clock.configure(cfg.timezone)
    return Database(cfg.database_url)


def cmd_migrate(cfg: Config, _args) -> None:
    upgrade(cfg.database_url)
    print("Міграції застосовано.")


def _ask_password(prompt: str = "Пароль: ") -> str:
    p1 = getpass.getpass(prompt)
    p2 = getpass.getpass("Повторіть пароль: ")
    if p1 != p2:
        sys.exit("Паролі не збігаються")
    return p1


def cmd_init(cfg: Config, args) -> None:
    from sqlalchemy import select

    from .models import Role, User
    from .services import users
    from .services.common import Actor

    upgrade(cfg.database_url)
    db = _db(cfg)
    with db.session() as s:
        if s.scalar(select(User).where(User.role == Role.DIRECTOR)):
            print("База готова, директор уже існує.")
            return
    username = args.username or input("Логін директора: ").strip()
    full_name = args.full_name or input("Ім'я директора: ").strip()
    password = args.password or _ask_password()
    with db.session() as s:
        users.create_user(s, Actor.system(), username=username, password=password, full_name=full_name, role=Role.DIRECTOR)
    print(f"Готово. Директор '{username}' створений. Запустіть: python -m mds run")


def bootstrap_director(cfg: Config) -> str:
    import os

    from sqlalchemy import select

    from .models import Role, User
    from .services import users
    from .services.common import Actor

    db = _db(cfg)
    with db.session() as s:
        if s.scalar(select(User).where(User.role == Role.DIRECTOR)):
            return "exists"
        username = os.getenv("MDS_DIRECTOR_USERNAME", "").strip()
        password = os.getenv("MDS_DIRECTOR_PASSWORD", "")
        if not username or not password:
            logging.getLogger("mds").warning(
                "Директора ще немає. Задайте MDS_DIRECTOR_USERNAME і MDS_DIRECTOR_PASSWORD та перезапустіть сервіс."
            )
            return "missing"
        users.create_user(s, Actor.system(), username=username, password=password,
                          full_name=os.getenv("MDS_DIRECTOR_NAME", "Директор").strip() or "Директор", role=Role.DIRECTOR)
    logging.getLogger("mds").info("Створено директора '%s'. Змінну MDS_DIRECTOR_PASSWORD тепер можна видалити.", username)
    return "created"


def bootstrap_catalog(cfg: Config) -> int:
    import json
    from pathlib import Path

    from sqlalchemy import func, select

    from .models import Service
    from .services import catalog
    from .services.common import Actor

    db = _db(cfg)
    with db.session() as s:
        if s.scalar(select(func.count(Service.id))):
            return 0
        data = json.loads((Path(__file__).parent / "seed" / "services.json").read_text(encoding="utf-8"))
        for i, x in enumerate(data["services"]):
            catalog.create_service(s, Actor.system(), {
                "name": x["name"], "section": x.get("section"), "category": x.get("category"),
                "subcategory": x.get("subcategory"), "price": int(x["price_uah"]) * 100,
                "price_from": bool(x.get("price_from")), "duration_min": int(x["duration_min"]), "sort_order": i,
            })
    logging.getLogger("mds").info("Завантажено прайс: %d послуг", len(data["services"]))
    return len(data["services"])


def cmd_start(cfg: Config, args) -> None:
    from .config import require_non_production

    mark = os.environ.get("MDS_WIPE_DATA", "").strip()
    if mark:
        require_non_production("MDS_WIPE_DATA")
    if os.environ.get("MDS_DEMO_FOP1", "").strip():
        require_non_production("MDS_DEMO_FOP1")
    upgrade(cfg.database_url)
    bootstrap_director(cfg)
    bootstrap_catalog(cfg)
    if mark:
        from .wipe import already_wiped

        if not already_wiped(_db(cfg), mark):
            _wipe(cfg, mark)
    if os.environ.get("MDS_DEMO_FOP1", "").strip().lower() == "yes":
        import subprocess

        subprocess.Popen([sys.executable, "-m", "mds", "demo-fop1"])
    cmd_serve(cfg, args, with_bot=True)


def _wipe(cfg: Config, mark: str | None) -> None:
    from .services.backup import make_backup
    from .wipe import wipe_data

    print("Резервна копія перед очищенням даних:", make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep), flush=True)
    print("Очищено:", wipe_data(_db(cfg), mark), flush=True)


def cmd_wipe_data(cfg: Config, args) -> None:
    from .config import require_non_production

    require_non_production("wipe-data")
    if not args.yes:
        raise SystemExit("Незворотна дія. Додайте --yes, щоб підтвердити (резервна копія буде зроблена).")
    _wipe(cfg, None)


def cmd_demo_fop1(cfg: Config, _args) -> None:
    from .config import require_non_production

    require_non_production("demo-fop1")
    from sqlalchemy import select

    from .demo_fop1 import FOP_LOGIN, purge_demo, seed_fop1
    from .models import User
    from .services.backup import make_backup

    db = _db(cfg)
    with db.session() as s:
        if s.scalar(select(User.id).where(User.username == FOP_LOGIN)):
            print("Демо «ФОП 1 група» вже є — пропуск", flush=True)
            return
    print("Резервна копія перед заміною демо-даних:", make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep), flush=True)
    print(purge_demo(db), flush=True)
    print(seed_fop1(db), flush=True)


def cmd_create_user(cfg: Config, args) -> None:
    from .services import users
    from .services.common import Actor

    password = args.password or _ask_password()
    with _db(cfg).session() as s:
        u = users.create_user(s, Actor.system(), username=args.username, password=password,
                              full_name=args.full_name, role=args.role)
        print(f"Створено: {u.username} ({u.role})")


def cmd_backup(cfg: Config, _args) -> None:
    from .services.backup import make_backup

    print(make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep))


def cmd_serve(cfg: Config, args, *, with_bot: bool = False) -> None:
    import uvicorn

    from .api.app import create_app

    app = create_app(cfg, _db(cfg), background=True, with_bot=with_bot)
    uvicorn.run(app, host=args.host or cfg.host, port=args.port or cfg.port, proxy_headers=True, forwarded_allow_ips="*")


def cmd_bot(cfg: Config, _args) -> None:
    from .bot.runner import run_bot

    if not cfg.telegram_token:
        sys.exit("Задайте MDS_TELEGRAM_TOKEN")
    asyncio.run(run_bot(cfg.telegram_token, _db(cfg)))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="mds", description="MDS Connect — система салону M&D")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="створити БД і першого директора")
    i.add_argument("--username")
    i.add_argument("--full-name")
    i.add_argument("--password")

    sub.add_parser("migrate", help="застосувати міграції БД")

    cu = sub.add_parser("create-user", help="додати співробітника")
    cu.add_argument("username")
    cu.add_argument("--full-name", required=True)
    cu.add_argument("--role", choices=["director", "admin", "master"], required=True)
    cu.add_argument("--password")

    for name in ("run", "serve", "start"):
        sp = sub.add_parser(name)
        sp.add_argument("--host")
        sp.add_argument("--port", type=int)

    sub.add_parser("bot", help="лише Telegram-бот")
    sub.add_parser("backup", help="резервна копія зараз")
    sub.add_parser("demo-fop1", help="ЗАМІНИТИ демо-дані на сценарій «ФОП 1 група» (лише тестове середовище)")

    wd = sub.add_parser("wipe-data", help="ОЧИСТИТИ робочі дані (лишаються акаунти, послуги, налаштування)")
    wd.add_argument("--yes", action="store_true", help="підтвердити незворотну дію")

    args = p.parse_args(argv)
    cfg = Config()
    {
        "init": cmd_init,
        "migrate": cmd_migrate,
        "create-user": cmd_create_user,
        "backup": cmd_backup,
        "serve": cmd_serve,
        "run": lambda c, a: cmd_serve(c, a, with_bot=True),
        "bot": cmd_bot,
        "start": cmd_start,
        "demo-fop1": cmd_demo_fop1,
        "wipe-data": cmd_wipe_data,
    }[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
