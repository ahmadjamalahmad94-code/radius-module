"""«بطاقات اليوم/الشهر» لأيّ يومٍ أو شهر، بتوقيت المالك (فلسطين) لا UTC.

قرار المالك 2026-10-02: «أغيّر اليوم … ويكتب كم العدد والسعر، والشهر زيّ هيك».
وكان «اليوم» يُقارَن بتاريخ UTC فتُنسب بطاقاتُ ما بعد منتصف الليل حتى الثالثة
فجرًا (غزّة +3) لليوم السابق. شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_totals_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo
        now = datetime.utcnow().isoformat() + "Z"
        db().execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t','T',?)", (now,))
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        db().execute("INSERT INTO access_plans(id, tenant_id, name, code, speed_down_kbps, speed_up_kbps,"
                     " created_at) VALUES (901,1,'باقة-مجاميع','tot',1024,512,?)", (now,))
        db().execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, plan_id, count,"
                     " generated, price_per_card, status, created_at) VALUES (901,1,'B-TOT','حزمة',901,4,4,"
                     " 5,'active',?)", (now,))
        # 2026-09-10 23:30 بتوقيت غزّة (+3) = 2026-09-10 20:30 UTC → يوم 10 محلّيًّا
        # 2026-09-11 01:30 بتوقيت غزّة = 2026-09-10 22:30 UTC → يوم 11 محلّيًّا (كان يُحسب 10)
        # 2026-08-20 12:00 UTC → شهر 8
        rows = [(9011, "c1", "2026-09-10T20:30:00Z"), (9012, "c2", "2026-09-10T22:30:00Z"),
                (9013, "c3", "2026-08-20 12:00:00"), (9014, "c4", None)]
        for cid, u, fu in rows:
            db().execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, plan_id, used,"
                         " first_used_at, created_at) VALUES (?,1,901,?,'p',901,?,?,?)",
                         (cid, u, 1 if fu else 0, fu, now))
        db().commit()
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _totals(client, qs):
    r = client.get("/api/v1/cards/batches" + qs, headers=AUTH)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["totals"]


def test_any_day_in_local_time(app):
    c = app.test_client()
    t = _totals(c, "?status=all&day=2026-09-10&month=2026-09")
    assert (t["used_today"], t["value_today"]) == (1, 5.0), t
    t = _totals(c, "?status=all&day=2026-09-11&month=2026-09")
    assert (t["used_today"], t["value_today"]) == (1, 5.0), "بطاقة 01:30 فجرًا محلّيًّا نُسبت لليوم السابق"
    assert t["day"] == "2026-09-11"


def test_any_month(app):
    c = app.test_client()
    t = _totals(c, "?status=all&day=2026-09-10&month=2026-09")
    assert (t["used_month"], t["value_month"]) == (2, 10.0), t
    t = _totals(c, "?status=all&day=2026-08-20&month=2026-08")
    assert (t["used_month"], t["value_month"]) == (1, 5.0), t
    assert t["month"] == "2026-08"


def test_bad_values_fall_back_to_current(app):
    c = app.test_client()
    t = _totals(c, "?status=all&day=yesterday&month=13")
    assert len(t["day"]) == 10 and len(t["month"]) == 7
