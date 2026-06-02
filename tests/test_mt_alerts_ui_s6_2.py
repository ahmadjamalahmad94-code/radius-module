"""S6.2 — Alerts UI."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s6_2_")
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
    u = f"s6_2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s6-pass", full_name="S6.2",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s6-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _open(rule="router_offline", dedup="x:1",
          severity="critical", router_id=42, title="الراوتر مفصول"):
    from app.radius.db.repos import alerts_repo
    return alerts_repo.open(
        tenant_id=1, rule=rule, dedup_key=dedup,
        title_ar=title, severity=severity,
        router_id=router_id,
        explanation_ar="لم نستلم استجابة",
        recommended_action_ar="افحص الـ uplink",
        evidence={"last_seen_minutes_ago": 5},
    )


def test_alerts_index_login_guarded(client):
    res = client.get("/admin/radius/alerts", follow_redirects=False)
    assert res.status_code in {302, 303}


def test_alerts_index_renders_shell(app, client):
    _login(client)
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert "data-mt-alerts-page" in html
    assert "data-mt-alerts-filters" in html


def test_alerts_index_renders_empty_state(app, client):
    _login(client)
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert "data-mt-alerts-empty" in html


def test_alerts_index_lists_open_rows(app, client):
    with app.app_context():
        _open(dedup="r1")
        _open(dedup="r2", router_id=99, title="جلسات RADIUS فاشلة",
              severity="warning")
    _login(client)
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert "data-mt-alerts-rows" in html
    assert "الراوتر مفصول" in html
    assert "جلسات RADIUS فاشلة" in html


def test_alerts_index_filter_by_severity(app, client):
    with app.app_context():
        _open(dedup="c1", severity="critical")
        _open(dedup="w1", severity="warning",
              title="انتفاضة ترافيك")
    _login(client)
    html = client.get(
        "/admin/radius/alerts?severity=critical").get_data(as_text=True)
    assert "الراوتر مفصول" in html
    assert "انتفاضة ترافيك" not in html


def test_alerts_index_resolved_view(app, client):
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        _open(dedup="rsv:1", title="منتهية")
        alerts_repo.resolve(1, "rsv:1")
    _login(client)
    html = client.get(
        "/admin/radius/alerts?status=resolved").get_data(as_text=True)
    assert "منتهية" in html


def test_alerts_detail_renders(app, client):
    with app.app_context():
        aid = _open()
    _login(client)
    html = client.get(
        f"/admin/radius/alerts/{aid}").get_data(as_text=True)
    assert "data-mt-alert-detail-page" in html
    assert f'data-mt-alert-id="{aid}"' in html
    assert "الإجراء المُقترح" in html


def test_alerts_detail_404_for_unknown(app, client):
    _login(client)
    res = client.get("/admin/radius/alerts/99999")
    assert res.status_code == 404


def _seed_router_for_metrics(app, router_id: int = 17) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, 'راوتر الفرع', '10.0.0.17', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw',
                           '2026-06-02T10:00:00Z')""",
                (router_id,),
            )


def test_metrics_setup_page_renders_arabic_script_helper(app, client):
    _seed_router_for_metrics(app)
    with app.app_context():
        from app.radius.db.repos import api_tokens_repo

        api_tokens_repo.create_token(
            tenant_id=1,
            name="رمز الراوترات",
            scopes=["admin:full"],
        )
    _login(client)
    html = client.get("/admin/radius/alerts/agent-setup").get_data(as_text=True)
    assert "إعداد دفع مقاييس الراوتر" in html
    assert "راوتر الفرع" in html
    assert "رمز الراوترات" in html
    assert "الصق رمز الربط" in html
    assert "Ø" not in html


def test_alert_settings_save_persists_global_and_router_thresholds(app, client):
    _seed_router_for_metrics(app)
    _login(client)
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "alerts-csrf"
    res = client.post(
        "/admin/radius/alerts/settings",
        data={
            "_csrf_token": "alerts-csrf",
            "enabled": "1",
            "offline": "1",
            "offline_after_min": "9",
            "default_speed_mbps": "150",
            "default_usage_gb": "300",
            "usage_window": "month",
            "r_17_present": "1",
            "r_17_enabled": "1",
            "r_17_offline": "5",
            "r_17_speed": "80",
            "r_17_usage": "120",
            "r_17_window": "day",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import router_alert_settings_repo, tenants_repo

        assert tenants_repo.get_setting(1, "network.alerts.offline_after_min") == "9"
        assert tenants_repo.get_setting(1, "network.alerts.default_speed_mbps") == "150"
        row = router_alert_settings_repo.get(1, 17)
        assert row is not None
        assert row["offline_after_min"] == 5
        assert row["normal_speed_mbps"] == 80
        assert row["normal_usage_gb"] == 120
        assert row["usage_window"] == "day"
