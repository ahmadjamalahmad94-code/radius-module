"""feat/api-first-parity — bandwidth schedules sr_days + copy-from (group 9).

يتحقّق أن POST /api/v1/bandwidth-schedules يقبل أيام القاعدة (sr_days كمرادف
لـdays_csv) ويدعم النسخ من جدول محفوظ (source_schedule_id) مطابقًا لصفحة
قواعد السرعة. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_bw_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_plan(app, pid=930):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, created_at) VALUES (?,1,'P','p',?)", (pid, now))
    return pid


def _create(client, **fields):
    base = {"target_type": "plan", "name": "Rule", "starts_at_time": "16:00",
            "ends_at_time": "20:00", "speed_down_kbps": 2048, "speed_up_kbps": 1024}
    base.update(fields)
    return client.post("/api/v1/bandwidth-schedules", headers=AUTH, json=base)


def _list(client):
    return client.get("/api/v1/bandwidth-schedules", headers=AUTH).get_json()["data"]["items"]


def test_days_csv_round_trip(app, client):
    pid = _seed_plan(app)
    res = _create(client, plan_id=pid, name="DaysCsv", days_csv="sat,sun,mon")
    assert res.status_code == 201, res.get_json()
    sched = next(s for s in _list(client) if s["name"] == "DaysCsv")
    assert sched["days_csv"] == "sat,sun,mon"


def test_sr_days_list_alias(app, client):
    pid = _seed_plan(app)
    # sr_days كقائمة (مثل صفحة قواعد السرعة) → يُطبَّع إلى days_csv
    res = _create(client, plan_id=pid, name="SrDaysList", sr_days=["sat", "wed", "fri"])
    assert res.status_code == 201, res.get_json()
    sched = next(s for s in _list(client) if s["name"] == "SrDaysList")
    assert sched["days_csv"] == "sat,wed,fri"


def test_sr_days_csv_alias(app, client):
    pid = _seed_plan(app)
    res = _create(client, plan_id=pid, name="SrDaysCsv", sr_days="tue,thu")
    assert res.status_code == 201, res.get_json()
    sched = next(s for s in _list(client) if s["name"] == "SrDaysCsv")
    assert sched["days_csv"] == "tue,thu"


def test_explicit_days_csv_wins_over_sr_days(app, client):
    pid = _seed_plan(app)
    res = _create(client, plan_id=pid, name="Both", days_csv="mon", sr_days=["sat", "sun"])
    assert res.status_code == 201
    sched = next(s for s in _list(client) if s["name"] == "Both")
    assert sched["days_csv"] == "mon"


def test_copy_from_source_schedule(app, client):
    pid = _seed_plan(app)
    src = _create(client, plan_id=pid, name="Source", days_csv="sat,sun",
                  speed_down_kbps=4096, speed_up_kbps=2048, cir_down_kbps=512)
    assert src.status_code == 201, src.get_json()
    src_id = src.get_json()["data"]["schedule"]["id"]
    # نسخ: نمرّر فقط source_schedule_id (+ plan_id للهدف) بلا سرعات → تُنسخ من المصدر
    copy = client.post("/api/v1/bandwidth-schedules", headers=AUTH,
                       json={"target_type": "plan", "plan_id": pid, "name": "",
                             "source_schedule_id": src_id})
    assert copy.status_code == 201, copy.get_json()
    new = copy.get_json()["data"]["schedule"]
    assert int(new["speed_down_kbps"]) == 4096 and int(new["speed_up_kbps"]) == 2048
    assert new["days_csv"] == "sat,sun"
    assert "نسخة من" in new["name"]


def test_copy_with_body_override(app, client):
    pid = _seed_plan(app)
    src_id = _create(client, plan_id=pid, name="Src2", days_csv="mon",
                     speed_down_kbps=1000, speed_up_kbps=500).get_json()["data"]["schedule"]["id"]
    # تجاوز السرعة صراحةً مع النسخ (بلا سرعة رفع في الجسم → تُؤخذ من المصدر)
    copy = client.post("/api/v1/bandwidth-schedules", headers=AUTH,
                       json={"target_type": "plan", "plan_id": pid, "name": "Override",
                             "source_schedule_id": src_id, "speed_down_kbps": 9999})
    new = copy.get_json()["data"]["schedule"]
    assert int(new["speed_down_kbps"]) == 9999      # تجاوز الجسم
    assert new["days_csv"] == "mon"                  # مأخوذ من المصدر
    assert new["name"] == "Override"


def test_missing_source_falls_back(app, client):
    pid = _seed_plan(app)
    # مصدر غير موجود → يُتجاهل ويُستخدم الجسم (يطابق الويب)
    res = _create(client, plan_id=pid, name="NoSrc", source_schedule_id=999999)
    assert res.status_code == 201, res.get_json()
