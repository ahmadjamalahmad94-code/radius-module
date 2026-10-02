"""«المبيعات» على اللوحة لأيّ فترة (يوم/أسبوع/شهر/من–إلى) — المالك 2026-10-02.

الافتراض اليوم، وبتوقيت فلسطين، وبنفس قواعد ``sales_today``. شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dash_sales_")
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
                     " created_at) VALUES (902,1,'باقة-لوحة','dash',1024,512,?)", (now,))
        db().execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, plan_id, count,"
                     " generated, price_per_card, status, created_at) VALUES (902,1,'B-DASH','حزمة',902,3,3,"
                     " 4,'active',?)", (now,))
        rows = [(9021, "d1", "2026-09-10T20:30:00Z"),   # 23:30 غزّة → 10 سبتمبر
                (9022, "d2", "2026-09-10T22:30:00Z"),   # 01:30 غزّة → 11 سبتمبر
                (9023, "d3", "2026-09-20 12:00:00")]
        for cid, u, fu in rows:
            db().execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, plan_id, used,"
                         " first_used_at, created_at) VALUES (?,1,902,?,'p',902,1,?,?)", (cid, u, fu, now))
        db().commit()
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _sales(c, qs=""):
    r = c.get("/api/v1/dashboard/sales" + qs, headers=AUTH)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def test_single_local_day(app):
    d = _sales(app.test_client(), "?from=2026-09-11&to=2026-09-11")
    assert d["cards_count"] == 1 and d["date"] == "2026-09-11"


def test_range_counts_and_value(app):
    d = _sales(app.test_client(), "?from=2026-09-01&to=2026-09-30")
    assert d["cards_count"] == 3
    total = sum(float(x["total"]) for x in d.get("by_currency") or [])
    assert total == 12.0


def test_default_is_today(app):
    d = _sales(app.test_client())
    assert d["date"] == d["date_to"] and len(d["date"]) == 10
