from __future__ import annotations

from datetime import datetime

import pytest

from mds import clock
from mds.db import Database
from mds.models import Role
from mds.services import catalog, users
from mds.services.common import Actor

NOW = datetime(2026, 11, 2, 8, 0)


@pytest.fixture(autouse=True)
def fixed_clock():
    clock.set_now(NOW)
    yield
    clock.set_now(None)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.create_all()
    return database


@pytest.fixture
def seed(db):
    sysa = Actor.system()
    with db.session() as s:
        d = users.create_user(s, sysa, username="director", password="Director123", full_name="Марина Д.", role=Role.DIRECTOR)
        ad = users.create_user(s, sysa, username="admin", password="Admin1234", full_name="Адмін", role=Role.ADMIN)
        m1 = users.create_user(s, sysa, username="olena", password="Master123", full_name="Олена", role=Role.MASTER)
        m2 = users.create_user(s, sysa, username="iryna", password="Master123", full_name="Ірина", role=Role.MASTER, share_bp=4000)
        da = Actor.of(d)
        manicure = catalog.create_service(s, da, {"name": "Манікюр", "duration_min": 90, "price": 60000})
        haircut = catalog.create_service(s, da, {"name": "Стрижка", "duration_min": 60, "price": 50000})
        gel = catalog.create_material(s, da, {"name": "Гель-лак", "unit_cost": 3000})
        ids = dict(director=d.id, admin=ad.id, m1=m1.id, m2=m2.id, manicure=manicure.id, haircut=haircut.id, gel=gel.id)
    with db.session() as s:
        from mds.models import User
        ids["actors"] = {k: Actor.of(s.get(User, ids[k])) for k in ("director", "admin", "m1", "m2")}
    return ids


@pytest.fixture
def client_api(db, seed, tmp_path):
    from fastapi.testclient import TestClient

    from mds.api.app import create_app
    from mds.config import Config

    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    with TestClient(create_app(cfg, db)) as c:
        r = c.post("/api/auth/login", json={"username": "director", "password": "Director123"})
        yield c, seed, {"Authorization": f"Bearer {r.json()['token']}"}
