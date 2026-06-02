from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


TOKEN = "router-alerts-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_router_alerts_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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


def _seed_router(app, router_id: int = 17) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, 'راوتر الفرع', '10.0.0.17', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw', ?)""",
                (router_id, now),
            )


def test_router_alerts_settings_route_is_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/router-alerts/settings" in routes


def test_router_alerts_settings_get_returns_settings_and_routers(app, client):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import router_loop_probes_repo, router_metrics_repo

        router_metrics_repo.record_sample(
            tenant_id=1,
            router_id=17,
            interfaces=[{"name": "ether1", "rx_bytes": 10, "tx_bytes": 20}],
        )
        router_loop_probes_repo.upsert_reading(
            tenant_id=1,
            router_id=17,
            interface="ether2",
            status="bound",
            lease_ip="10.0.0.7/24",
            server_ip="10.0.0.1",
        )

    res = client.get("/api/v1/router-alerts/settings", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["settings"]["enabled"] is True
    assert data["counts"]["routers"] == 1
    assert data["counts"]["pushing"] == 1
    assert data["counts"]["loop_probes"] == 1
    assert data["counts"]["loop_detected"] == 1
    assert data["routers"][0]["name"] == "راوتر الفرع"
    assert data["routers"][0]["last_push_at"]
    assert data["loop_probes"][0]["router_name"] == "راوتر الفرع"
    assert data["loop_probes"][0]["interface"] == "ether2"
    assert data["loop_probes"][0]["loop_detected"] is True


def test_router_alerts_settings_patch_persists_global_and_router(app, client):
    _seed_router(app)
    res = client.patch(
        "/api/v1/router-alerts/settings",
        headers=AUTH,
        json={
            "settings": {
                "enabled": True,
                "telegram": False,
                "offline": True,
                "high_traffic": True,
                "high_usage": True,
                "loop": False,
                "offline_after_min": 11,
                "default_speed_mbps": 120,
                "default_usage_gb": 400,
                "usage_window": "month",
            },
            "routers": [
                {
                    "id": 17,
                    "enabled": True,
                    "offline_after_min": 5,
                    "normal_speed_mbps": 80,
                    "normal_usage_gb": 140,
                    "usage_window": "day",
                }
            ],
        },
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["settings"]["offline_after_min"] == 11
    assert data["settings"]["loop"] is False
    assert data["routers"][0]["normal_speed_mbps"] == 80
    assert data["counts"]["overrides"] == 1

    with app.app_context():
        from app.radius.db.repos import audit_repo, router_alert_settings_repo
        from app.radius.services import smart_alerts

        row = router_alert_settings_repo.get(1, 17)
        assert row and row["normal_usage_gb"] == 140
        assert smart_alerts.global_settings(1)["default_speed_mbps"] == 120
        audits = audit_repo.recent(1, action="router_alerts_settings_update")
        assert audits


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"settings": {"offline_after_min": 1}}, "offline_after_min"),
        ({"settings": {"default_speed_mbps": 0}}, "default_speed_mbps"),
        ({"settings": {"usage_window": "year"}}, "usage_window"),
        ({"routers": [{"id": 17, "normal_usage_gb": -1}]}, "normal_usage_gb"),
    ],
)
def test_router_alerts_settings_patch_rejects_invalid_values(app, client, payload, field):
    _seed_router(app)
    res = client.patch("/api/v1/router-alerts/settings", headers=AUTH, json=payload)
    assert res.status_code == 422, res.get_json()
    assert field in res.get_json()["error"]["details"]


def test_router_alerts_settings_patch_rejects_unknown_router(client):
    res = client.patch(
        "/api/v1/router-alerts/settings",
        headers=AUTH,
        json={"routers": [{"id": 9999, "normal_speed_mbps": 10}]},
    )
    assert res.status_code == 404, res.get_json()
    assert res.get_json()["error"]["message"] == "الراوتر غير موجود."
