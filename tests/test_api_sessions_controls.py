from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sessions_controls_")
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


def _seed_online_sessions(app):
    now = datetime.utcnow()
    started = (now - timedelta(minutes=7)).isoformat() + "Z"
    updated = now.isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1, 't1', 'T1', ?)",
                (updated,),
            )
            conn.execute(
                """
                INSERT INTO access_plans(id, tenant_id, name, code, created_at)
                VALUES (901, 1, 'Sessions API Plan', 'sessions_api_plan', ?)
                """,
                (updated,),
            )
            conn.execute(
                """
                INSERT INTO subscribers(tenant_id, username, password, plan_id, status, created_at)
                VALUES (1, 'sub-online', 'x', 901, 'enabled', ?)
                """,
                (updated,),
            )
            conn.execute(
                """
                INSERT INTO card_batches(id, tenant_id, batch_code, package_name, plan_id,
                                         count, generated, created_at)
                VALUES (901, 1, 'B-SESSIONS-API', 'Cards', 901, 1, 1, ?)
                """,
                (updated,),
            )
            conn.execute(
                """
                INSERT INTO cards(id, tenant_id, batch_id, username, password, plan_id,
                                  used, created_at)
                VALUES (901, 1, 901, 'card-online', 'secret', 901, 0, ?)
                """,
                (updated,),
            )
            for username, session_id, mac, ip_addr in (
                ("sub-online", "s-sub", "AA:BB:CC:00:00:01", "192.168.10.10"),
                ("card-online", "s-card", "AA:BB:CC:00:00:02", "192.168.10.11"),
            ):
                conn.execute(
                    """
                    INSERT INTO radacct
                        (tenant_id, acctsessionid, acctuniqueid, username,
                         nasipaddress, framedipaddress, callingstationid,
                         acctstarttime, acctupdatetime, acctinputoctets,
                         acctoutputoctets, acctstoptime)
                    VALUES (1, ?, ?, ?, '10.20.30.1', ?, ?, ?, ?, 100, 200, NULL)
                    """,
                    (
                        session_id,
                        f"{session_id}-{username}",
                        username,
                        ip_addr,
                        mac,
                        started,
                        updated,
                    ),
                )


def test_sessions_api_locks_mac_and_ip_from_live_row(app, client):
    _seed_online_sessions(app)

    res = client.post(
        "/api/v1/sessions/lock-mac",
        headers=AUTH,
        json={"username": "sub-online", "session_id": "s-sub"},
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["mac_address"] == "AA:BB:CC:00:00:01"
    assert data["target_type"] == "subscriber"

    res = client.post(
        "/api/v1/sessions/lock-ip",
        headers=AUTH,
        json={"username": "sub-online", "session_id": "s-sub"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["ip_address"] == "192.168.10.10"

    res = client.post(
        "/api/v1/sessions/lock-mac",
        headers=AUTH,
        json={"username": "card-online", "session_id": "s-card"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["target_type"] == "card"

    with app.app_context():
        from app.radius.db.connection import db

        sub = db().execute(
            "SELECT mac_lock, allowed_macs, static_ip FROM subscribers WHERE username = ?",
            ("sub-online",),
        ).fetchone()
        assert sub["mac_lock"] == "AA:BB:CC:00:00:01"
        assert sub["allowed_macs"] == "AA:BB:CC:00:00:01"
        assert sub["static_ip"] == "192.168.10.10"

        card = db().execute(
            "SELECT locked_mac FROM cards WHERE username = ?",
            ("card-online",),
        ).fetchone()
        assert card["locked_mac"] == "AA:BB:CC:00:00:02"


def test_sessions_api_temporary_speed_and_cancel(app, client, monkeypatch):
    _seed_online_sessions(app)
    calls = []

    import app.radius.services.temp_speed as temp_speed

    def fake_apply_temp_speed(**kwargs):
        calls.append(("apply", kwargs))
        return {
            "ends_at": "2026-06-05T12:30:00",
            "rate": "1024k/2048k",
            "coa": {"ok": True, "code": "ok"},
        }

    def fake_cancel_temp_speed(**kwargs):
        calls.append(("cancel", kwargs))
        return {"reverted": True}

    monkeypatch.setattr(temp_speed, "apply_temp_speed", fake_apply_temp_speed)
    monkeypatch.setattr(temp_speed, "cancel_temp_speed", fake_cancel_temp_speed)

    res = client.post(
        "/api/v1/sessions/temp-speed",
        headers=AUTH,
        json={
            "username": "sub-online",
            "session_id": "s-sub",
            "down_kbps": 2048,
            "up_kbps": 1024,
            "duration_minutes": 30,
        },
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["temporary_speed"]["rate"] == "1024k/2048k"
    assert calls[0][0] == "apply"
    assert calls[0][1]["username"] == "sub-online"
    assert calls[0][1]["down_kbps"] == 2048
    assert calls[0][1]["up_kbps"] == 1024

    res = client.post(
        "/api/v1/sessions/temp-speed/cancel",
        headers=AUTH,
        json={"username": "sub-online", "session_id": "s-sub"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["temporary_speed"]["reverted"] is True
    assert calls[1][0] == "cancel"


def test_sessions_api_temp_speed_rejects_cards(app, client):
    _seed_online_sessions(app)
    res = client.post(
        "/api/v1/sessions/temp-speed",
        headers=AUTH,
        json={
            "username": "card-online",
            "session_id": "s-card",
            "down_kbps": 2048,
            "up_kbps": 1024,
            "duration_minutes": 30,
        },
    )
    assert res.status_code == 422
    assert "للمشتركين فقط" in res.get_json()["error"]["message"]
