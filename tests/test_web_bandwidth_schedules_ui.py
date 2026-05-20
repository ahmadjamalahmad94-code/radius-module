"""Web UI smoke tests for bandwidth schedule screen."""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_plans(
                    id, tenant_id, name, code, plan_type, service_type,
                    duration_minutes, validity_days, speed_down_kbps,
                    speed_up_kbps, price, currency, enabled, created_at
                )
                VALUES(1,1,'Bandwidth Schedule Test Plan','BWSCHED','time','Hotspot',
                       1440,1,4000,2000,1,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"bandwidth_web_{uuid4().hex[:10]}"
    password = "bandwidth-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Bandwidth Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_bandwidth_schedules_web_route_is_login_guarded(client):
    res = client.get("/admin/radius/bandwidth-schedules", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_bandwidth_schedules_create_and_apply_dry_run(client):
    _web_login(client)
    page = client.get("/admin/radius/bandwidth-schedules")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "applied_to_radius = false" in html

    token = _csrf(client, "/admin/radius/bandwidth-schedules")
    created = client.post(
        "/admin/radius/bandwidth-schedules",
        data={
            "_csrf_token": token,
            "name": "Night speed window",
            "plan_id": "1",
            "starts_at_time": "22:00",
            "ends_at_time": "06:00",
            "speed_down_kbps": "3000",
            "speed_up_kbps": "1000",
            "cir_down_kbps": "0",
            "cir_up_kbps": "0",
            "restore_mode": "profile_default",
            "enabled": "1",
            "notes": "test schedule",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    created_html = created.get_data(as_text=True)
    assert "Night speed window" in created_html
    assert "3000" in created_html

    from app.radius.db.repos import operations_repo

    schedules = operations_repo.list_bandwidth_schedules(1, limit=1000)
    schedule = next(item for item in schedules if item["name"] == "Night speed window")

    applied = client.post(
        f"/admin/radius/bandwidth-schedules/{schedule['id']}/apply",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert applied.status_code == 200
    applied_html = applied.get_data(as_text=True)
    assert "Night speed window" in applied_html
    assert "applied_to_radius = false" in applied_html
