from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "accounting_events.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _start_payload(session="s1"):
    return {
        "status_type": "Start",
        "username": "ali",
        "acct_session_id": session,
        "nas_ip_address": "198.51.100.10",
        "calling_station_id": "AA:BB:CC",
        "framed_ip_address": "10.0.0.9",
    }


def test_start_creates_online_session(app_db):
    from app.radius.services.accounting_events import AccountingEventsService

    result = AccountingEventsService().ingest(tenant_id=1, payload=_start_payload())

    assert result["status"] == "started"
    assert result["session"]["username"] == "ali"
    assert len(AccountingEventsService().list_online(tenant_id=1)) == 1


def test_interim_updates_counters(app_db):
    from app.radius.services.accounting_events import AccountingEventsService

    service = AccountingEventsService()
    service.ingest(tenant_id=1, payload=_start_payload())
    service.ingest(
        tenant_id=1,
        payload={
            **_start_payload(),
            "status_type": "Interim-Update",
            "input_octets": 123,
            "output_octets": 456,
            "session_time": 60,
        },
    )

    session = service.session_detail(tenant_id=1, session_id="s1")
    assert session["acctinputoctets"] == 123
    assert session["acctoutputoctets"] == 456
    assert session["acctsessiontime"] == 60


def test_stop_closes_session(app_db):
    from app.radius.services.accounting_events import AccountingEventsService

    service = AccountingEventsService()
    service.ingest(tenant_id=1, payload=_start_payload())
    result = service.ingest(tenant_id=1, payload={**_start_payload(), "status_type": "Stop"})

    assert result["status"] == "stopped"
    assert result["session"]["acctstoptime"]
    assert service.list_online(tenant_id=1) == []


def test_duplicate_start_idempotent(app_db):
    from app.radius.services.accounting_events import AccountingEventsService

    service = AccountingEventsService()
    first = service.ingest(tenant_id=1, payload=_start_payload())
    second = service.ingest(tenant_id=1, payload=_start_payload())

    assert first["session"]["radacctid"] == second["session"]["radacctid"]
    assert second["status"] == "idempotent"


def test_stale_cleanup_marks_stale_not_delete(app_db):
    from app.radius.db.connection import db
    from app.radius.services.accounting_events import AccountingEventsService

    service = AccountingEventsService()
    service.ingest(tenant_id=1, payload=_start_payload())
    db().execute("UPDATE radacct SET acctupdatetime='2020-01-01T00:00:00Z' WHERE acctsessionid='s1'")

    result = service.mark_stale(tenant_id=1, older_than_seconds=3600)
    row = service.session_detail(tenant_id=1, session_id="s1")

    assert result["closed"] == 1
    assert row["acctterminatecause"] == "Stale-Session-Timeout"
    assert row["acctstoptime"]


def test_accounting_event_api_and_read_routes(client):
    res = client.post("/api/v1/accounting/events", json=_start_payload("api-s1"), headers=AUTH)
    assert res.status_code == 200

    online = client.get("/api/v1/accounting/online", headers=AUTH)
    assert online.get_json()["data"]["count"] == 1

    detail = client.get("/api/v1/accounting/sessions/api-s1", headers=AUTH)
    assert detail.get_json()["data"]["item"]["username"] == "ali"

    history = client.get("/api/v1/accounting/sessions", headers=AUTH)
    assert history.get_json()["data"]["count"] == 1
