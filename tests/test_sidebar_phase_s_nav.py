"""Phase S nav — sidebar + per-router links for the new
operations-center surfaces (topology, alerts, audit, backups).
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
    tmp = tempfile.mkdtemp(prefix="hr_navS_")
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
    u = f"navS_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="navS-pass", full_name="NavS",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "navS-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id=1):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"navS-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


# ─── Sidebar entries ──────────────────────────────────────────


def test_sidebar_lists_topology_link(app, client):
    _login(client)
    html = client.get("/admin/radius/").get_data(as_text=True)
    assert 'href="/admin/radius/topology"' in html
    assert "خريطة الشبكة" in html


def test_sidebar_lists_alerts_link(app, client):
    _login(client)
    html = client.get("/admin/radius/").get_data(as_text=True)
    assert 'href="/admin/radius/alerts"' in html
    assert "التنبيهات الذكيّة" in html


def test_sidebar_lists_audit_log_link(app, client):
    _login(client)
    html = client.get("/admin/radius/").get_data(as_text=True)
    assert 'href="/admin/radius/audit"' in html
    assert "سجل العمليات" in html


def test_sidebar_network_section_auto_opens_on_topology(app, client):
    """Visiting /topology should put the network section in
    `is-open has-active` state via sec_network_active."""
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    # The network section's outer div carries `is-open has-active`
    # when sec_network_active is True. Pin the marker.
    assert "is-open has-active" in html


def test_sidebar_network_section_auto_opens_on_alerts(app, client):
    _login(client)
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert "is-open has-active" in html


def test_sidebar_network_section_auto_opens_on_audit(app, client):
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    assert "is-open has-active" in html


# ─── Per-router quicknav strip ────────────────────────────────


def test_dashboard_quicknav_links_to_backups(app, client):
    _seed_nas(app, nas_id=42)
    _login(client)
    html = client.get(
        "/admin/radius/mt/42/dashboard").get_data(as_text=True)
    assert 'data-mt-router-link="backups"' in html
    assert "/admin/radius/mt/42/backups" in html
    assert "النسخ الاحتياطية" in html


# ─── Operations Center row action ─────────────────────────────


def test_operations_row_links_to_backups(app, client):
    _seed_nas(app, nas_id=13)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert 'data-mt-row-link="backups"' in html
    assert "/admin/radius/mt/13/backups" in html
