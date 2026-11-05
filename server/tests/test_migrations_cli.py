from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from mds import models  # noqa: F401
from mds.__main__ import main
from mds.db import Base
from mds.migrate import upgrade


def test_migrations_match_models(tmp_path):
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
    main(["init"])
    main(["create-user", "anna", "--full-name", "Анна", "--role", "master", "--password", "Master123"])
    main(["backup"])
    out = capsys.readouterr().out
    assert "директор уже існує" in out and "Створено: anna" in out
    assert list((tmp_path / "backups").glob("mds-*.db"))


def test_hosting_bootstrap_director_from_env(tmp_path, monkeypatch):
    from mds.__main__ import bootstrap_director
    from mds.config import Config
    from mds.db import Database
    from mds.models import User

    monkeypatch.delenv("MDS_DATA_DIR", raising=False)
    monkeypatch.delenv("MDS_DATABASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path / "vol"))
    monkeypatch.setenv("PORT", "4321")
    cfg = Config()
    assert cfg.data_dir == (tmp_path / "vol").resolve() and cfg.port == 4321
    upgrade(cfg.database_url)
    assert bootstrap_director(cfg) == "missing"
    monkeypatch.setenv("MDS_DIRECTOR_USERNAME", "margaryta")
    monkeypatch.setenv("MDS_DIRECTOR_PASSWORD", "Salon2026!")
    monkeypatch.setenv("MDS_DIRECTOR_NAME", "Маргарита")
    assert bootstrap_director(cfg) == "created"
    assert bootstrap_director(cfg) == "exists"
    from mds.__main__ import bootstrap_catalog
    n = bootstrap_catalog(cfg)
    assert n == 42 and bootstrap_catalog(cfg) == 0
    with Database(cfg.database_url).session() as s:
        u = s.query(User).one()
        assert (u.username, u.full_name, u.role) == ("margaryta", "Маргарита", "director")
