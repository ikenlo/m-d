"""Alembic: URL бази береться з MDS_DATABASE_URL / конфігурації MDS."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from mds import models  # noqa: F401
from mds.config import Config
from mds.db import Base

config = context.config
url = config.attributes.get("database_url") or Config().database_url
target_metadata = Base.metadata


def run_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online():
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as conn:
            _run(conn)
    else:
        _run(connectable)


def _run(conn):
    # render_as_batch — потрібно для ALTER TABLE у SQLite
    context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
