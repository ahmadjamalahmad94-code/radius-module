"""S5.2+S5.3 — Topology UI + filter contracts."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s5_2_")
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
    u = f"s5_2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s5-pass", full_name="S5.2",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s5-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id, name, mode="direct", enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, ?)""",
                (nas_id, name, f"203.0.113.{nas_id}",
                 1 if enabled else 0, now, mode),
            )


def test_route_is_login_guarded(client):
    res = client.get("/admin/radius/topology",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_page_renders_with_server_card(app, client):
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    assert "data-mt-topology-page" in html
    assert "data-mt-topology-server" in html


def test_page_lists_each_router(app, client):
    _seed_nas(app, nas_id=10, name="alpha")
    _seed_nas(app, nas_id=20, name="beta", mode="vpn")
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    assert 'data-mt-topology-node="10"' in html
    assert 'data-mt-topology-node="20"' in html
    assert "alpha" in html
    assert "beta" in html


def test_filter_vpn_only(app, client):
    _seed_nas(app, nas_id=10, name="direct-one",  mode="direct")
    _seed_nas(app, nas_id=20, name="vpn-one",     mode="vpn")
    _login(client)
    html = client.get(
        "/admin/radius/topology?show=vpn").get_data(as_text=True)
    assert "vpn-one" in html
    assert "direct-one" not in html


def test_search_filter(app, client):
    _seed_nas(app, nas_id=10, name="alpha")
    _seed_nas(app, nas_id=20, name="beta")
    _login(client)
    html = client.get(
        "/admin/radius/topology?q=alpha").get_data(as_text=True)
    assert "alpha" in html
    assert "beta" not in html


def test_empty_filter_state_renders(app, client):
    _seed_nas(app, nas_id=10, name="only-direct")
    _login(client)
    html = client.get(
        "/admin/radius/topology?show=vpn").get_data(as_text=True)
    assert "data-mt-topology-empty" in html
    assert "لا يوجد راوتر" in html


def test_disabled_router_shows_disabled_status(app, client):
    _seed_nas(app, nas_id=30, name="offline-rtr", enabled=False)
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    assert 'data-mt-topology-status="disabled"' in html
