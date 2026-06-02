"""O5 — diagnostics repair script + connection-mode badge are
connection-mode aware.

VPN routers get a WG-subnet rule (so HobeRadius can reach them
via 10.10.0.1 inside the tunnel); direct routers get the public
IP rule. Mixing them up was the EOF/'unknown client' incident
in the postmortem.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # WG subnet → drives the VPN-branch repair script.
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
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
    u = f"o5_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="o5-pass", full_name="O5 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o5-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int, host: str, mode: str = "direct") -> None:
    """Seed a nas_devices row in the requested connection_mode."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     connection_mode, vpn_peer_address, created_at)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, 'hr-test', 'pw', ?, ?, ?)""",
                (nas_id, f"rt-{nas_id}", host, mode,
                 host if mode == "vpn" else "", now),
            )


def _stub_tcp_failed(monkeypatch):
    """Force every router into the tcp_failed verdict so the
    repair-script block actually renders."""
    from app.radius.services import mt_diagnostics
    monkeypatch.setattr(
        mt_diagnostics, "_tcp_probe",
        lambda host, port, timeout=5.0: {
            "ok": False, "latency_ms": None,
            "error": "timed_out", "hint": "test stub",
        },
    )


# ─── Service-layer plumbing ──────────────────────────────────────


def test_collect_routers_includes_connection_mode(app):
    """_collect_routers must surface connection_mode so the
    diagnose loop can copy it into each entry."""
    _seed(app, nas_id=100, host="10.10.0.7", mode="vpn")
    _seed(app, nas_id=101, host="203.0.113.20", mode="direct")
    with app.app_context():
        from app.radius.services import mt_diagnostics
        rows = mt_diagnostics._collect_routers(1)
    by_host = {r["host"]: r for r in rows}
    assert by_host["10.10.0.7"]["connection_mode"]    == "vpn"
    assert by_host["203.0.113.20"]["connection_mode"] == "direct"


def test_diagnose_tenant_propagates_connection_mode(app, monkeypatch):
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=110, host="10.10.0.99", mode="vpn")
    with app.app_context():
        from app.radius.services import mt_diagnostics
        report = mt_diagnostics.diagnose_tenant(1)
    assert len(report["routers"]) == 1
    assert report["routers"][0]["connection_mode"] == "vpn"


# ─── Template rendering ──────────────────────────────────────────


def test_vpn_row_shows_wireguard_badge(app, client, monkeypatch):
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=120, host="10.10.0.50", mode="vpn")
    _login(client)
    html = client.get("/admin/radius/diagnostics").get_data(as_text=True)
    assert 'data-mt-conn-mode="vpn"' in html
    assert "نفق الإدارة" in html


def test_direct_row_shows_direct_badge(app, client, monkeypatch):
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=121, host="198.51.100.10", mode="direct")
    _login(client)
    html = client.get("/admin/radius/diagnostics").get_data(as_text=True)
    assert 'data-mt-conn-mode="direct"' in html
    assert "اتصال مباشر" in html


def test_vpn_repair_script_uses_wg_subnet(app, client, monkeypatch):
    """The killer test: a VPN-mode router that fails TCP must
    emit a fix script with address=10.10.0.0/24 — NOT the
    public IP. This is what would have prevented postmortem #9."""
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=130, host="10.10.0.8", mode="vpn")
    _login(client)
    html = client.get("/admin/radius/diagnostics").get_data(as_text=True)

    # The VPN fix block:
    assert 'data-mt-repair-mode="vpn"' in html
    assert "address=10.10.0.0/24 disabled=no" in html
    assert "src-address=10.10.0.0/24" in html
    # Public-IP placeholder MUST NOT appear in a VPN fix block —
    # that's the bug O5 prevents.
    assert "YOUR_VPS_IP/32" not in html
    # The label tells the operator it's the management tunnel branch.
    assert "نفق الإدارة" in html


def test_direct_repair_script_uses_public_ip(app, client, monkeypatch):
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=131, host="198.51.100.11", mode="direct")
    _login(client)
    res = client.get(
        "/admin/radius/diagnostics",
        headers={"X-Real-IP": "203.0.113.55"},
    )
    html = res.get_data(as_text=True)
    assert 'data-mt-repair-mode="direct"' in html
    assert "address=203.0.113.55/32" in html
    assert "src-address=203.0.113.55" in html
    # WG subnet MUST NOT appear in a direct fix block.
    assert "10.10.0.0/24" not in html


def test_legacy_mikrotik_configs_no_longer_appears(app, client, monkeypatch):
    """N3 dropped the table; the diagnostics page must not even
    try to read it. Sanity check tied to the postmortem #14
    follow-up."""
    _stub_tcp_failed(monkeypatch)
    _seed(app, nas_id=140, host="10.10.0.15", mode="vpn")
    _login(client)
    res = client.get("/admin/radius/diagnostics")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # No legacy "mikrotik_configs" source tag on any row.
    assert "mikrotik_configs" not in html
