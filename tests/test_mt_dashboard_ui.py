"""K9 — MikroTik dashboard UI smoke tests.

The dashboard must:
- Be login-guarded (the global guard installed by the radius
  blueprint redirects anon visitors to the login page).
- Render the shell without requiring a live MikroTik connection.
- Carry every stable `data-mt-*` marker the JS + future tests rely
  on.
- 404 when the nas_id doesn't exist.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mt_dash_")
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

    username = f"mt_dash_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username,
        password="dash-pass",
        full_name="Dashboard Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "dash-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_router(app, *, nas_id: int, name: str = "rtr-test",
                 address: str = "203.0.113.50") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot', 1,
                        ?, 'direct')
                """,
                (nas_id, name, address, now),
            )


def test_dashboard_route_is_login_guarded(client):
    res = client.get("/admin/radius/mt/1/dashboard", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_dashboard_renders_shell_and_markers(app, client):
    _seed_router(app, nas_id=1, name="main-gw", address="203.0.113.10")
    _login(client)

    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Stable markers — every later K9.x commit + every external
    # automated test depends on these strings being literally
    # present.
    assert "data-mt-dashboard" in html
    assert 'data-mt-router-id="1"' in html
    assert 'data-mt-api-base="/api/v1"' in html
    # Token comes from the dev env; non-empty in test mode.
    assert "data-mt-api-token=" in html

    assert "data-mt-kpi-strip" in html
    assert "data-mt-status" in html

    # KPI cards each carry their kind. JS fills them later from
    # /system/overview — the page itself just renders the shell.
    for kind in ("uptime", "cpu", "memory", "temperature",
                 "version", "dialed"):
        assert f'data-mt-kpi="{kind}"' in html

    # K9.2 panels — markers must be in place from this commit on.
    assert "data-mt-live-traffic" in html
    assert "data-mt-interface-select" in html
    assert "data-mt-traffic-rx" in html
    assert "data-mt-traffic-tx" in html
    assert "data-mt-spark" in html
    assert "data-mt-active-users" in html
    assert "data-mt-hotspot-count" in html
    assert "data-mt-ppp-count" in html
    assert "data-mt-active-users-rows" in html

    # K9.3 quick-actions: every required marker is live.
    assert "data-mt-quick-actions" in html
    assert "data-mt-action-backup" in html
    assert "data-mt-action-reboot" in html
    assert "data-mt-action-ping" in html
    assert "data-mt-action-identity" in html
    assert "data-mt-action-result" in html
    assert "data-mt-action-form" in html
    assert "data-rh-loop-tile" in html
    assert "تتبّع اللوب" in html
    assert "كشف اللوب عبر مجس DHCP على منافذ الزبائن" in html

    # The router name lands in the title + meta strip.
    assert "main-gw" in html
    assert "203.0.113.10" in html


def test_dashboard_loop_tile_stays_renderable_with_probe(app, client):
    _seed_router(app, nas_id=3, name="loop-rtr", address="203.0.113.30")
    with app.app_context():
        from app.radius.db.repos import router_loop_probes_repo
        router_loop_probes_repo.upsert_reading(
            tenant_id=1,
            router_id=3,
            interface="ether2",
            status="bound",
            lease_ip="10.0.0.8/24",
            server_ip="10.0.0.1",
        )
    _login(client)

    res = client.get("/admin/radius/mt/3/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-rh-loop-tile" in html
    assert "مفعّل" in html


def test_dashboard_returns_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/dashboard")
    assert res.status_code == 404


def test_dashboard_does_NOT_require_live_mikrotik_to_render(app, client):
    """The page shell must come up even when the wire client is
    completely unable to reach the router — JS is what does the
    fetch, and a failed fetch only paints an in-page error chip."""
    _seed_router(app, nas_id=2, name="offline-rtr", address="10.0.0.1")
    _login(client)

    # Note: we do NOT monkeypatch any pool here. The page render
    # path must not touch the router at all.
    res = client.get("/admin/radius/mt/2/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "offline-rtr" in html
    # JS will report the error to the operator; the shell still
    # shows the pending status pill.
    assert "جارٍ الاتصال" in html
