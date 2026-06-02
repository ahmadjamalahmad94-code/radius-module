from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_smart_alerts_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app

    yield create_app()

    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"sa_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=username,
        password="sa-pass",
        full_name="اختبار التنبيهات",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "sa-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_router(router_id: int = 77, name: str = "راوتر الاختبار") -> int:
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO nas_devices(id, tenant_id, name, address, secret,
                vendor, nas_type, enabled, created_at, connection_mode)
            VALUES(?,1,?,?,'sek','mikrotik','hotspot',1,?,'direct')
            """,
            (
                router_id,
                name,
                f"10.0.0.{router_id}",
                datetime.utcnow().isoformat(),
            ),
        )
    return router_id


def _set_last_push(router_id: int, minutes_ago: float) -> None:
    from app.radius.db.connection import transaction

    ts = (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO router_metric_state(tenant_id, router_id, last_push_at, last_sample_id)
            VALUES(1,?,?,NULL)
            ON CONFLICT(tenant_id, router_id) DO UPDATE SET last_push_at=excluded.last_push_at
            """,
            (router_id, ts),
        )


def _csrf(client) -> str:
    token = f"csrf-{uuid4().hex}"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    return token


def test_metrics_ingest_stores_sample_and_heartbeat(app, client):
    with app.app_context():
        _seed_router(77)
    res = client.post(
        "/api/v1/routers/77/metrics/ingest",
        json={
            "uptime_seconds": 1000,
            "interfaces": [{"name": "ether1", "rx_bytes": 10, "tx_bytes": 20}],
        },
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["interfaces_recorded"] == 1
    with app.app_context():
        from app.radius.db.repos import router_metrics_repo

        assert 77 in router_metrics_repo.last_push_map(1)
        samples = router_metrics_repo.latest_two(1, 77)
        assert samples
        assert samples[0]["interfaces"][0]["name"] == "ether1"


def test_metrics_ingest_unknown_router_is_404(app, client):
    res = client.post(
        "/api/v1/routers/9999/metrics/ingest",
        json={"interfaces": []},
        headers=AUTH,
    )
    assert res.status_code == 404


def test_sweep_offline_opens_then_resolves(app, client):
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        from app.radius.services import smart_alerts

        _seed_router(77)
        _set_last_push(77, minutes_ago=30)
        out = smart_alerts.sweep_offline(1)
        assert out["opened"] == 1
        open_keys = {a["dedup_key"] for a in alerts_repo.list_open(1)}
        assert "auto.router.offline:77" in open_keys

        _set_last_push(77, minutes_ago=0)
        out2 = smart_alerts.sweep_offline(1)
        assert out2["resolved"] == 1
        open_keys2 = {a["dedup_key"] for a in alerts_repo.list_open(1)}
        assert "auto.router.offline:77" not in open_keys2


def test_ingest_clears_offline_alert(app, client):
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        from app.radius.services import smart_alerts

        _seed_router(77)
        _set_last_push(77, minutes_ago=30)
        smart_alerts.sweep_offline(1)
        assert "auto.router.offline:77" in {
            a["dedup_key"] for a in alerts_repo.list_open(1)
        }

    client.post(
        "/api/v1/routers/77/metrics/ingest",
        json={"interfaces": []},
        headers=AUTH,
    )

    with app.app_context():
        from app.radius.db.repos import alerts_repo

        assert "auto.router.offline:77" not in {
            a["dedup_key"] for a in alerts_repo.list_open(1)
        }


def test_settings_save_persists_global_and_per_router(app, client):
    with app.app_context():
        _seed_router(77)
    _login(client)
    csrf_token = _csrf(client)
    res = client.post(
        "/admin/radius/alerts/settings",
        data={
            "_csrf_token": csrf_token,
            "enabled": "1",
            "offline": "1",
            "telegram": "1",
            "offline_after_min": "10",
            "default_speed_mbps": "80",
            "default_usage_gb": "150",
            "usage_window": "month",
            "r_77_present": "1",
            "r_77_enabled": "1",
            "r_77_speed": "50",
            "r_77_usage": "30",
            "r_77_offline": "4",
            "r_77_window": "day",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import router_alert_settings_repo
        from app.radius.services import smart_alerts

        settings = smart_alerts.global_settings(1)
        assert settings["offline_after_min"] == 10
        assert settings["default_speed_mbps"] == 80
        assert settings["usage_window"] == "month"
        row = router_alert_settings_repo.get(1, 77)
        assert row
        assert row["normal_speed_mbps"] == 50
        assert row["normal_usage_gb"] == 30


def test_alerts_page_renders_gear_and_settings_modal(app, client):
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert "data-sa-open" in html
    assert "data-sa-modal" in html
    assert "إعدادات التنبيهات الذكية" in html
    assert "راوتر الفرع" in html


def test_metrics_agent_setup_page_renders(app, client):
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/alerts/agent-setup").get_data(as_text=True)
    assert "إعداد دفع مقاييس الراوتر" in html
    assert "/metrics/ingest" in html
    assert "راوتر الفرع" in html


# ── Phase 2: high traffic + high usage ──────────────────────────────

def _insert_sample(router_id: int, secs_ago: float, ifaces: list) -> None:
    import json

    from app.radius.db.connection import transaction
    ts = (datetime.utcnow() - timedelta(seconds=secs_ago)).isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO router_metric_samples(tenant_id, router_id, reported_at,
                uptime_seconds, interfaces_json, recorded_at)
            VALUES(1,?,?,?,?,?)
            """,
            (router_id, "", None, json.dumps(ifaces), ts),
        )


def test_high_traffic_opens_then_resolves(app, client):
    with app.app_context():
        from app.radius.db.repos import alerts_repo, router_alert_settings_repo
        from app.radius.services import smart_alerts

        _seed_router(77)
        router_alert_settings_repo.upsert(tenant_id=1, router_id=77,
                                          enabled=True, normal_speed_mbps=1)
        # 60 MB over 120 s on ether1 ≈ 4 Mbps > 1 Mbps threshold → open
        _insert_sample(77, 120, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 0}])
        _insert_sample(77, 0, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 60_000_000}])
        out = smart_alerts.evaluate_push(1, 77)
        assert out.get("high_traffic") == "opened"
        assert "auto.router.high_traffic:77" in {a["dedup_key"] for a in alerts_repo.list_open(1)}

        # a fresh 120s pair with no Δ (idle) → 0 Mbps < threshold → resolves
        _insert_sample(77, 120, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 60_000_000}])
        _insert_sample(77, 0, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 60_000_000}])
        smart_alerts.evaluate_push(1, 77)
        assert "auto.router.high_traffic:77" not in {a["dedup_key"] for a in alerts_repo.list_open(1)}


def test_high_usage_opens_when_window_usage_exceeds_threshold(app, client):
    with app.app_context():
        from app.radius.db.repos import alerts_repo, router_alert_settings_repo
        from app.radius.services import smart_alerts

        _seed_router(77)
        router_alert_settings_repo.upsert(tenant_id=1, router_id=77,
                                          enabled=True, normal_usage_gb=1,
                                          usage_window="day")
        # 2.5 GB consumed within the last 24h → over the 1 GB threshold
        _insert_sample(77, 6000, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 0}])
        _insert_sample(77, 60, [{"name": "ether1", "rx_bytes": 0, "tx_bytes": 2_500_000_000}])
        out = smart_alerts.evaluate_push(1, 77)
        assert out.get("high_usage") == "opened"
        assert "auto.router.high_usage:77" in {a["dedup_key"] for a in alerts_repo.list_open(1)}
