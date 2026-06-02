from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


TOKEN = "router-metrics-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_router_metrics_api_")
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


def test_router_metrics_route_is_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/routers/<int:router_id>/metrics/ingest" in routes


def test_router_metrics_ingest_records_sample_and_resolves_offline(app, client):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import alerts_repo

        alerts_repo.open(
            tenant_id=1,
            router_id=17,
            rule="auto.router.offline",
            dedup_key="auto.router.offline:17",
            severity="critical",
            title_ar="الراوتر مفصول",
        )

    res = client.post(
        "/api/v1/routers/17/metrics/ingest",
        headers=AUTH,
        json={
            "reported_at": "2026-06-02T10:00:00Z",
            "uptime_seconds": 3600,
            "interfaces": [
                {"name": "ether1", "rx_bytes": "100", "tx-byte": "250"},
                {"name": 7, "rx_bytes": "bad", "tx_bytes": 12},
                {"rx_bytes": 1, "tx_bytes": 2},
            ],
        },
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["router_id"] == 17
    assert data["sample_id"] > 0
    assert data["interfaces_recorded"] == 2

    with app.app_context():
        from app.radius.db.repos import alerts_repo, router_metrics_repo

        latest = router_metrics_repo.latest_two(1, 17)
        assert len(latest) == 1
        assert latest[0]["interfaces"][0]["name"] == "ether1"
        assert latest[0]["interfaces"][0]["rx_bytes"] == 100
        assert latest[0]["interfaces"][0]["tx_bytes"] == 250
        assert latest[0]["interfaces"][1]["name"] == "7"
        assert latest[0]["interfaces"][1]["rx_bytes"] is None
        assert latest[0]["interfaces"][1]["tx_bytes"] == 12
        assert alerts_repo.list_open(1, router_id=17) == []
        assert alerts_repo.list_resolved(1, router_id=17)


def test_router_metrics_validation_messages_are_arabic(app, client):
    _seed_router(app)

    missing = client.post("/api/v1/routers/999/metrics/ingest", headers=AUTH, json={})
    assert missing.status_code == 404
    assert missing.get_json()["error"]["message"] == "الراوتر غير موجود."

    empty = client.post(
        "/api/v1/routers/17/metrics/ingest",
        headers=AUTH,
        data="",
        content_type="text/plain",
    )
    assert empty.status_code == 400
    assert empty.get_json()["error"]["message"] == "بيانات المقاييس مطلوبة."

    invalid = client.post(
        "/api/v1/routers/17/metrics/ingest",
        headers=AUTH,
        data="not valid",
        content_type="text/plain",
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["message"] == "بيانات الطلب ليست بصيغة صحيحة."

    wrong_shape = client.post(
        "/api/v1/routers/17/metrics/ingest",
        headers=AUTH,
        data="[]",
        content_type="text/plain",
    )
    assert wrong_shape.status_code == 400
    assert wrong_shape.get_json()["error"]["message"] == "أرسل كائنًا يحتوي قائمة الواجهات."


def test_sweep_offline_opens_alert_after_silent_router(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import alerts_repo, router_metrics_repo, tenants_repo
        from app.radius.services import smart_alerts

        tenants_repo.set_setting(1, "network.alerts.telegram", "0")
        router_metrics_repo.record_sample(
            tenant_id=1,
            router_id=17,
            reported_at="2026-06-02T10:00:00Z",
            uptime_seconds=1,
            interfaces=[{"name": "ether1", "rx_bytes": 1, "tx_bytes": 2}],
        )
        old = (datetime.utcnow() - timedelta(minutes=30)).isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                "UPDATE router_metric_state SET last_push_at=? "
                "WHERE tenant_id=1 AND router_id=17",
                (old,),
            )

        result = smart_alerts.sweep_offline(1)
        assert result["opened"] == 1
        rows = alerts_repo.list_open(1, router_id=17)
        assert len(rows) == 1
        assert rows[0]["title_ar"] == "الراوتر «راوتر الفرع» مفصول"
        assert "لم يصل أي تحديث" in rows[0]["explanation_ar"]
