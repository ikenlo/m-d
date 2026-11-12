from __future__ import annotations

from datetime import timedelta

import pytest

from mds import clock
from mds.models import AuditLog, AuthThrottle
from mds.services import users


@pytest.fixture
def pins(db, seed):
    from mds.services.common import Actor

    with db.session() as s:
        users.set_bot_pin(s, Actor.system(), seed["m1"], "1111")
        users.set_bot_pin(s, Actor.system(), seed["director"], "9999")
    return seed


def test_pin_lock_is_per_user_across_chats(db, pins):
    with db.session() as s:
        for chat in range(100, 105):
            assert users.verify_bot_pin(s, pins["director"], "0000", chat_id=chat) is None
        assert users.bot_locked(s, 999, pins["director"])
        assert users.verify_bot_pin(s, pins["director"], "9999", chat_id=999) is None
        assert s.query(AuditLog).filter_by(action="bot_pin_failed").count() == 5


def test_pin_lock_survives_restart_and_expires(db, pins):
    with db.session() as s:
        for _ in range(5):
            users.verify_bot_pin(s, pins["m1"], "0000", chat_id=7)
    with db.session() as s:
        assert s.query(AuthThrottle).filter_by(scope="bot_user").one().locked_until is not None
        assert users.bot_locked(s, 7, pins["m1"])
    clock.set_now(clock.now() + timedelta(minutes=16))
    with db.session() as s:
        assert not users.bot_locked(s, 7, pins["m1"])
        assert users.verify_bot_pin(s, pins["m1"], "1111", chat_id=7) is not None


def test_staff_code_failures_lock_chat(db, pins):
    with db.session() as s:
        for _ in range(5):
            assert not users.check_staff_code(s, "1234", chat_id=42)
        assert users.bot_locked(s, 42)
        assert not users.bot_locked(s, 43)


def test_success_resets_counter(db, pins):
    with db.session() as s:
        for _ in range(4):
            users.verify_bot_pin(s, pins["m1"], "0000", chat_id=8)
        assert users.verify_bot_pin(s, pins["m1"], "1111", chat_id=8) is not None
        for _ in range(4):
            users.verify_bot_pin(s, pins["m1"], "0000", chat_id=8)
        assert not users.bot_locked(s, 8, pins["m1"])


def test_bot_lock_survives_new_dispatcher(db, pins):
    from tests.test_bot import BotHarness

    h1 = BotHarness(db)
    h1.send("👩‍🎨 Для майстрів")
    h1.send("0000")
    for _ in range(5):
        h1.click(f"s:who:{pins['m1']}")
        h1.send("0001")
    h2 = BotHarness(db)
    h2.send("👩‍🎨 Для майстрів")
    assert "Забагато" in h2.session.texts()[-1]


@pytest.mark.parametrize("env", [None, "production", "garbage"])
def test_destructive_tools_refuse_in_production(monkeypatch, db, env):
    from mds.config import is_production
    from mds.demo_fop1 import purge_demo, seed_fop1
    from mds.wipe import wipe_data

    if env is None:
        monkeypatch.delenv("MDS_ENV", raising=False)
    else:
        monkeypatch.setenv("MDS_ENV", env)
    assert is_production()
    for fn in (lambda: wipe_data(db, "x"), lambda: purge_demo(db), lambda: seed_fop1(db)):
        with pytest.raises(RuntimeError, match="MDS_ENV"):
            fn()


@pytest.mark.parametrize("var", ["MDS_WIPE_DATA", "MDS_DEMO_FOP1"])
def test_start_refuses_destructive_env_in_production(monkeypatch, tmp_path, var):
    from mds.__main__ import cmd_start
    from mds.config import Config

    monkeypatch.setenv("MDS_ENV", "production")
    monkeypatch.setenv(var, "yes")
    cfg = Config(data_dir=tmp_path / "d", secret_key="k" * 40)
    with pytest.raises(RuntimeError, match=var):
        cmd_start(cfg, None)
    assert not (tmp_path / "d" / "mds.db").exists()


def test_mock_terminal_disabled_in_production(monkeypatch, db, seed):
    from mds.services import card_terminal
    from mds.services.common import update_settings

    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "mock"})
        assert card_terminal.mode(s) == "mock"
        monkeypatch.setenv("MDS_ENV", "production")
        assert card_terminal.mode(s) == "off"
        from mds.errors import ValidationError

        with pytest.raises(ValidationError, match="0.4.0"):
            update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "live"})
