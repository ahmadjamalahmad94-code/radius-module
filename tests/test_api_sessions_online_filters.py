from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sessions_api_")
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
                    (session_id, f"{session_id}-{username}", username,
                     ip_addr, mac, started, updated),
                )


def test_sessions_online_can_filter_subscribers_and_cards(app, client):
    _seed_online_sessions(app)

    all_res = client.get("/api/v1/sessions/online", headers=AUTH)
    assert all_res.status_code == 200, all_res.get_json()
    all_data = all_res.get_json()["data"]
    assert all_data["count"] == 2
    assert all_data["types"] == {"subscriber": 1, "card": 1}

    sub_res = client.get("/api/v1/sessions/online?type=subscriber", headers=AUTH)
    assert sub_res.status_code == 200
    sub_items = sub_res.get_json()["data"]["items"]
    assert [item["username"] for item in sub_items] == ["sub-online"]
    assert sub_items[0]["user_type"] == "subscriber"

    card_res = client.get("/api/v1/sessions/online?type=card", headers=AUTH)
    assert card_res.status_code == 200
    card_items = card_res.get_json()["data"]["items"]
    assert [item["username"] for item in card_items] == ["card-online"]
    card = card_items[0]
    assert card["user_type"] == "card"
    assert card["card_id"] == 901
    assert card["card_batch_id"] == 901
    assert card["nas_ip_address"] == "10.20.30.1"
    assert card["framed_ip_address"] == "192.168.10.11"
    assert card["calling_station_id"] == "AA:BB:CC:00:00:02"
    assert card["session_time"] > 0


def test_sessions_online_rejects_unknown_type(client):
    res = client.get("/api/v1/sessions/online?type=bad", headers=AUTH)
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_sessions_disconnect_uses_real_service(app, client, monkeypatch):
    calls = []

    class FakeSessions:
        def disconnect(self, *, actor: str, username: str, session_id: str | None = None):
            calls.append({"actor": actor, "username": username, "session_id": session_id})

    import app.api.v1.sessions as sessions_api

    monkeypatch.setattr(sessions_api, "_svc", lambda: FakeSessions())
    res = client.post(
        "/api/v1/sessions/disconnect",
        headers=AUTH,
        json={"username": "sub-online", "session_id": "s-sub"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["disconnect_requested"] is True
    assert calls == [{
        "actor": "api-token:None",
        "username": "sub-online",
        "session_id": "s-sub",
    }]
