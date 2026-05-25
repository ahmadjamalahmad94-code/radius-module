from __future__ import annotations

import json
import os

import pytest

from app.radius.db.connection import db, reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "capacity_status.db"))
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


def _insert_subscriber(username: str = "existing") -> None:
    db().execute(
        """
        INSERT INTO subscribers (tenant_id, username, password, status, created_at)
        VALUES (1, ?, 'secret', 'enabled', '2026-05-01T00:00:00Z')
        """,
        (username,),
    )


def _get_capacity_status(client):
    return client.get(
        "/api/v1/system/admin-bridge/capacity-status",
        headers=AUTH,
    )


def test_no_contract_returns_degraded_non_blocking_status(client):
    res = _get_capacity_status(client)

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["status"] == "degraded"
    assert data["contract"]["stale"] is True
    assert "no_capacity_contract" in data["warnings"]
    assert data["features"]["subscribers"]["state"] == "unknown"
    assert data["upgrade_intent"]["dry_run_only"] is True


def test_valid_contract_returns_usage_limits_and_features(client):
    _insert_subscriber()
    _capacity_contract(
        {
            "plan": {"name": "Pilot"},
            "limits": {
                "subscribers": {"max_total": 5},
                "cards": {"generate_per_batch": 50, "monthly_generated": 100},
            },
            "features": {
                "subscribers": {"state": "limited"},
                "cards": "enabled",
            },
        }
    )

    res = _get_capacity_status(client)

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["status"] == "active"
    assert data["usage"]["subscribers_total"] == 1
    assert data["features"]["subscribers"]["state"] == "limited"
    assert data["features"]["subscribers"]["limits"]["max_total"] == 5
    assert data["features"]["subscribers"]["remaining"] == 4
    assert data["features"]["cards"]["limits"]["generate_per_batch"] == 50


def test_stale_contract_returns_warning(client):
    _capacity_contract(
        {"limits": {"subscribers": {"max_total": 5}}},
        fetched_at="2000-01-01T00:00:00Z",
    )

    res = _get_capacity_status(client)

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["status"] == "stale"
    assert data["contract"]["stale"] is True
    assert "stale_contract" in data["warnings"]


def test_locked_feature_status_is_clear(client):
    _capacity_contract({"features": {"subscribers": {"state": "locked"}}})

    res = _get_capacity_status(client)

    assert res.status_code == 200
    feature = res.get_json()["data"]["features"]["subscribers"]
    assert feature["state"] == "locked"
    assert feature["blocked"] is True
    assert feature["block_code"] == "feature_locked"
    assert "ترقية" in feature["upgrade_hint_ar"]


def test_capacity_status_response_does_not_leak_secrets(client):
    _capacity_contract(
        {
            "license_key": "lic_secret_123456789",
            "shared_secret": "admin_shared_secret",
            "limits": {"subscribers": {"max_total": 5}},
            "features": {"subscribers": {"state": "enabled"}},
            "nested": {"private_key": "wg-private-secret"},
        }
    )

    res = _get_capacity_status(client)

    assert res.status_code == 200
    raw = json.dumps(res.get_json(), ensure_ascii=False)
    assert "lic_secret_123456789" not in raw
    assert "admin_shared_secret" not in raw
    assert "wg-private-secret" not in raw


def test_capacity_status_endpoint_is_json_only(client):
    res = _get_capacity_status(client)

    assert res.status_code == 200
    assert res.content_type.startswith("application/json")
    body = res.get_json()
    assert body["ok"] is True
    assert set(body["data"].keys()) >= {
        "status",
        "contract",
        "usage",
        "features",
        "warnings",
        "upgrade_intent",
    }
