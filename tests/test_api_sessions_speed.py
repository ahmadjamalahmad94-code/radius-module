"""feat/api-first-parity — sessions speed data + speed filter (group 2).

يتحقّق أن /api/v1/sessions/online يضيف لكل جلسة: السرعة الحالية/الخطة
(موجودة أصلًا من الـdataclass) + الحالة المشتقّة speed_state ونافذة السرعة
المؤقتة، وأنه يدعم فلتر speed=(special|temporary|normal) مطابقًا لصفحة
الجلسات المتصلة. شغّل الملف وحده.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sess_speed_api_")
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


def _seed(app):
    now = datetime.utcnow()
    started = (now - timedelta(minutes=5)).isoformat() + "Z"
    updated = now.isoformat() + "Z"
    # نافذة سرعة مؤقتة فعّالة (تنتهي بعد ساعة) للمشترك temp.
    temp_from = now.isoformat(timespec="seconds")
    temp_to = (now + timedelta(hours=1)).isoformat(timespec="seconds")
    meta = json.dumps({"temporary_speed_from": temp_from, "temporary_speed_to": temp_to,
                       "temporary_speed_duration_minutes": 60})
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (updated,))
            conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, created_at) VALUES (920,1,'P','p',?)", (updated,))
            # normal · custom-speed · temporary-speed subscribers
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, created_at) VALUES (1,'s-normal','x',920,'enabled',?)", (updated,))
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, custom_speed, created_at) VALUES (1,'s-custom','x',920,'enabled',1,?)", (updated,))
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, temporary_speed, metadata, created_at) VALUES (1,'s-temp','x',920,'enabled',1,?,?)", (meta, updated))
            for username, sid, mac, ip in (
                ("s-normal", "sn", "AA:BB:CC:00:00:11", "192.168.1.11"),
                ("s-custom", "sc", "AA:BB:CC:00:00:12", "192.168.1.12"),
                ("s-temp",   "st", "AA:BB:CC:00:00:13", "192.168.1.13"),
            ):
                conn.execute(
                    """INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username,
                        nasipaddress, framedipaddress, callingstationid, acctstarttime,
                        acctupdatetime, acctinputoctets, acctoutputoctets, acctstoptime)
                       VALUES (1,?,?,?,'10.0.0.1',?,?,?,?,100,200,NULL)""",
                    (sid, f"{sid}-{username}", username, ip, mac, started, updated),
                )


def _by_user(body):
    return {it["username"]: it for it in body["data"]["items"]}


def test_online_includes_speed_fields(app, client):
    _seed(app)
    res = client.get("/api/v1/sessions/online", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    rows = _by_user(res.get_json())
    # كل صف يحمل الحقول الجديدة + حقول السرعة الخام الموجودة أصلًا
    for u in ("s-normal", "s-custom", "s-temp"):
        assert u in rows, rows.keys()
        r = rows[u]
        for key in ("rate_down_kbps", "rate_up_kbps", "plan_down_kbps", "plan_up_kbps",
                    "speed_state", "has_special_speed", "has_active_temporary_speed",
                    "temporary_speed_window"):
            assert key in r, (u, key)
    assert rows["s-normal"]["speed_state"] == "normal"
    assert rows["s-custom"]["speed_state"] == "custom"
    assert rows["s-temp"]["speed_state"] == "temporary"
    # النافذة المؤقتة فعّالة للمشترك temp
    assert rows["s-temp"]["temporary_speed_window"]["active"] is True
    assert rows["s-temp"]["has_active_temporary_speed"] is True


def test_speed_filter_special(app, client):
    _seed(app)
    body = client.get("/api/v1/sessions/online?speed=special", headers=AUTH).get_json()
    users = set(_by_user(body).keys())
    assert users == {"s-custom", "s-temp"}            # خاصة أو مؤقتة فعّالة
    assert body["data"]["speed"] == "special"


def test_speed_filter_temporary(app, client):
    _seed(app)
    users = set(_by_user(client.get("/api/v1/sessions/online?speed=temporary", headers=AUTH).get_json()).keys())
    assert users == {"s-temp"}


def test_speed_filter_normal(app, client):
    _seed(app)
    users = set(_by_user(client.get("/api/v1/sessions/online?speed=normal", headers=AUTH).get_json()).keys())
    assert users == {"s-normal"}


def test_speeds_breakdown_counts(app, client):
    _seed(app)
    body = client.get("/api/v1/sessions/online", headers=AUTH).get_json()
    assert body["data"]["speeds"] == {"normal": 1, "custom": 1, "temporary": 1}


def test_bad_speed_param_422(client):
    assert client.get("/api/v1/sessions/online?speed=bogus", headers=AUTH).status_code == 422


def test_speed_filter_composes_with_type(app, client):
    _seed(app)
    # كل الجلسات مشتركون → speed=special + type=subscriber = نفس النتيجة
    body = client.get("/api/v1/sessions/online?speed=special&type=subscriber", headers=AUTH).get_json()
    assert set(_by_user(body).keys()) == {"s-custom", "s-temp"}
