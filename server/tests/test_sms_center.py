"""SMS-розсилки: аудиторії лише зі згодою, розсилка й витрати, черга й статуси, автоматизація, аналіз бази."""

from datetime import date, datetime

import pytest

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.models import Expense, SmsMessage
from mds.services import catalog, client_base, sms, sms_center
from mds.services.common import update_settings


@pytest.fixture
def fake_sms(monkeypatch):
    sent = []
    monkeypatch.setenv("MDS_TURBOSMS_TOKEN", "x")
    monkeypatch.setenv("MDS_TURBOSMS_SENDER", "MD Salon")
    monkeypatch.setattr(sms, "send_one", lambda phone, text, channel="viber_sms": sent.append((phone, text)) or {"ok": True, "message_id": f"m{len(sent)}", "error": None})
    monkeypatch.setattr(sms, "statuses", lambda ids: {i: "delivered" for i in ids})
    monkeypatch.setattr(sms, "balance", lambda: 123.45)
    return sent


def _clients(s, seed):
    adm = seed["actors"]["admin"]
    a = catalog.get_or_create_client(s, adm, "Олена Коваль", "0671000001")
    b = catalog.get_or_create_client(s, adm, "Ірина Мельник", "0671000002")
    c = catalog.get_or_create_client(s, adm, "Без згоди", "0671000003")
    client_base.update_card(s, adm, a.id, {"marketing_consent": True, "birthday": date(1990, 11, 6)})
    client_base.update_card(s, adm, b.id, {"marketing_consent": True})
    return a, b, c


def test_campaign_only_consent_cost_to_expenses_and_statuses(db, seed, fake_sms):
    d = seed["actors"]["director"]
    clock.set_now(datetime(2026, 11, 5, 11))
    with db.session() as s:
        _clients(s, seed)
        with pytest.raises(ValidationError):  # не ввімкнено в налаштуваннях
            sms_center.create_campaign(s, d, title="Акція", text="{name}, привіт", audience_key="all")
        update_settings(s, d, {"sms_enabled": True, "sms_price": 150})
        pre = sms_center.create_campaign(s, d, title="Акція", text="{name}, знижка 10%", audience_key="all", dry_run=True)
        assert (pre["recipients"], pre["cost"], pre["preview"]) == (2, 300, "Олена, знижка 10%")
        with pytest.raises(Forbidden):
            sms_center.create_campaign(s, seed["actors"]["admin"], title="x", text="y", audience_key="all")
        r = sms_center.create_campaign(s, d, title="Акція", text="{name}, знижка 10%", audience_key="all")
        assert r["recipients"] == 2 and r["cost"] == 300
        e = s.query(Expense).one()
        assert e.category == "advertising" and e.amount == 300 and "SMS-розсилка" in e.description
        assert sms_center.process_queue(s) == 2
        assert sorted(t for _, t in fake_sms) == ["Ірина, знижка 10%", "Олена, знижка 10%"]
        sms_center.refresh_statuses(s)
        assert {m.status for m in s.query(SmsMessage)} == {"delivered"}
        sm = sms_center.summary(s, d)
        assert (sm["balance"], sm["spent_total"], sm["sent_total"], sm["consent_clients"]) == (123.45, 300, 2, 2)
        assert len(sms_center.messages(s, d)) == 2
    clock.set_now(None)


def test_not_configured_marks_history_and_automations(db, seed, monkeypatch):
    d = seed["actors"]["director"]
    monkeypatch.delenv("MDS_TURBOSMS_TOKEN", raising=False)
    clock.set_now(datetime(2026, 11, 6, 11))
    with db.session() as s:
        a, _, _ = _clients(s, seed)
        sms_center.enqueue(s, phone=a.phone, text="тест", kind="service", reason="test", client_id=a.id)
        sms_center.process_queue(s)
        assert s.query(SmsMessage).one().status == "not_configured"
    monkeypatch.setenv("MDS_TURBOSMS_TOKEN", "x")
    monkeypatch.setenv("MDS_TURBOSMS_SENDER", "MD")
    with db.session() as s:
        update_settings(s, d, {"sms_enabled": True, "sms_auto_birthday": True})
        assert sms_center.run_automations(s) == 1  # сьогодні 06.11 — день народження Олени
        assert sms_center.run_automations(s) == 0  # не двічі
        msg = s.query(SmsMessage).filter_by(reason="birthday").one()
        assert msg.text.startswith("Олена, вітаємо") and msg.kind == "marketing"
        ins = sms_center.insights(s, d)
        assert ins["best_hour"].endswith(":00") and any(r["audience"] == "birthdays" for r in ins["recommendations"])
    clock.set_now(None)
