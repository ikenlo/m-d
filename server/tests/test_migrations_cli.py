from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from mds import models  # noqa: F401
from mds.__main__ import main
from mds.db import Base
from mds.migrate import upgrade


def test_migrations_match_models(tmp_path):
    """Схема після міграцій має збігатися з моделями (нагадування створити міграцію)."""
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"Моделі змінено без міграції: {diff}"


def test_cli_init_and_backup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MDS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MDS_DATABASE_URL", raising=False)
    main(["init", "--username", "boss", "--full-name", "Бос", "--password", "Secret123"])
    main(["init"])  # повторно — нічого не ламає
    main(["create-user", "anna", "--full-name", "Анна", "--role", "master", "--password", "Master123"])
    main(["backup"])
    out = capsys.readouterr().out
    assert "директор уже існує" in out and "Створено: anna" in out
    assert list((tmp_path / "backups").glob("mds-*.db"))
