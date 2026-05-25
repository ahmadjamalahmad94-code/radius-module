from __future__ import annotations

import os
from datetime import datetime

import pytest

from app.radius.db.connection import db, reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "capacity.db"))
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


def _capacity_contract(payload: dict, *, fetched_at: str | None = None) -> None:
    from app.radius.services.admin_panel_client import (
        SNAPSHOT_CAPACITY,
        LicenseAdminSnapshotStore,
    )

    LicenseAdminSnapshotStore().save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_CAPACITY,
        normalized_status="active",
        source_url="mock://capacity-contract",
        payload={"status": "active", **payload},
        fetched_at=fetched_at,
        stale_after_seconds=60,
    )


def _insert_plan(name: str = "Plan A") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans (tenant_id, name, created_at)
        VALUES (1, ?, ?)
        """,
        (name, datetime.utcnow().isoformat() + "Z"),
    )
    return int(cur.lastrowid)


def _insert_subscriber(username: str = "existing") -> None:
    db().execute(
        """
        INSERT INTO subscribers (tenant_id, username, password, status, created_at)
        VALUES (1, ?, 'secret', 'enabled', ?)
        """,
        (username, datetime.utcnow().isoformat() + "Z"),
    )


def _insert_nas(name: str = "router-1") -> None:
    db().execute(
        """
        INSERT INTO nas_devices (tenant_id, name, address, created_at)
        VALUES (1, ?, '10.0.0.1', ?)
        """,
        (name, datetime.utcnow().isoformat() + "Z"),
    )


def test_subscriber_over_limit_is_blocked(client):
    _insert_subscriber()
    _capacity_contract({"limits": {"subscribers": {"max_total": 1}}})

    res = client.post(
        "/api/v1/accounts",
        json={"username": "new-sub", "password": "pw"},
        headers=AUTH,
    )

    assert res.status_code == 403
    body = res.get_json()
    assert body["error"]["code"] == "capacity_limit_exceeded"
    assert body["error"]["details"]["feature_key"] == "subscribers"
    assert body["error"]["details"]["current_usage"] == 1
    assert body["error"]["details"]["limit"] == 1


def test_cards_batch_limit_is_blocked(client):
    plan_id = _insert_plan()
    _capacity_contract({"limits": {"cards": {"generate_per_batch": 1}}})

    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": plan_id, "count": 2},
        headers=AUTH,
    )

    assert res.status_code == 403
    body = res.get_json()
    assert body["error"]["code"] == "capacity_limit_exceeded"
    assert body["error"]["details"]["feature_key"] == "cards"
    assert body["error"]["details"]["limit"] == 1


def test_nas_over_limit_is_blocked(client):
    _insert_nas()
    _capacity_contract({"limits": {"nas": {"max_total": 1}}})

    res = client.post(
        "/api/v1/nas",
        json={"name": "router-2", "address": "10.0.0.2"},
        headers=AUTH,
    )

    assert res.status_code == 403
    body = res.get_json()
    assert body["error"]["code"] == "capacity_limit_exceeded"
    assert body["error"]["details"]["feature_key"] == "nas"


def test_locked_feature_blocks_create(client):
    _capacity_contract({"features": {"subscribers": {"state": "locked"}}})

    res = client.post(
        "/api/v1/accounts",
        json={"username": "locked-sub", "password": "pw"},
        headers=AUTH,
    )

    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "feature_locked"


def test_readonly_feature_blocks_create(client):
    _capacity_contract({"features": {"profiles": {"state": "readonly"}}})

    res = client.post(
        "/api/v1/profiles",
        json={"name": "Read Only Plan"},
        headers=AUTH,
    )

    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "feature_readonly"


def test_missing_capacity_contract_does_not_crash_or_block(client):
    res = client.post(
        "/api/v1/accounts",
        json={"username": "no-contract-sub", "password": "pw"},
        headers=AUTH,
    )

    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["username"] == "no-contract-sub"


def test_stale_contract_is_still_enforced_with_warning(client):
    _insert_subscriber()
    _capacity_contract(
        {"limits": {"subscribers": {"max_total": 1}}},
        fetched_at="2000-01-01T00:00:00Z",
    )

    res = client.post(
        "/api/v1/accounts",
        json={"username": "stale-sub", "password": "pw"},
        headers=AUTH,
    )

    assert res.status_code == 403
    details = res.get_json()["error"]["details"]
    assert details["contract_status"] == "stale"
    assert "stale_contract" in details["warnings"]
