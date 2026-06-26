# -*- coding: utf-8 -*-
"""WireGuard details page reaching parity with the SSTP details page:

  1) WinBox remote-access enable (any-IP, not IP-locked) — same endpoint.
  2) «شرح الكود» explanation panel — ALWAYS shown (not gated on a preview).
  3) sections sit in .uds-main so the uniform section gap applies.

Per-file isolation (fresh app/db)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


def _make_app(monkeypatch, *, wg_server=True):
    tmp = tempfile.mkdtemp(prefix="hr_wgp_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_REMOTE_ACCESS_ENABLED", "1")
    if wg_server:
        monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "U" * 43 + "=")
        monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
    else:
        monkeypatch.delenv("HOBERADIUS_WG_SERVER_PUBKEY", raising=False)
        monkeypatch.delenv("HOBERADIUS_WG_SERVER_ENDPOINT", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


@pytest.fixture
def app(monkeypatch):
    yield _make_app(monkeypatch, wg_server=True)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"a_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw", full_name="A",
                             is_super_admin=True)
    res = client.post("/admin/radius/login", data={"username": u, "password": "pw"})
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/wg-peers"):
    client.get(url)
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _make_wg_router(name="CCR-WG", ip="10.10.0.7",
                    pub="ABCdef0123456789ABCdef0123456789ABCdef01234="):
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction
    dev = NasDevice(
        id=None, name=name, address=ip, secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True,
    )
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version='7', connection_mode='vpn', "
            "       management_tunnel_type='', vpn_public_key=?, "
            "       vpn_peer_address=?, vpn_interface='wg0' WHERE id=?",
            (pub, ip, saved.id),
        )
    return int(saved.id)


def _make_v6_router(name="CCR-SSTP", ip="203.0.113.9"):
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction
    dev = NasDevice(
        id=None, name=name, address=ip, secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True,
    )
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version='6', connection_mode='vpn', "
            "       management_tunnel_type='sstp_mgmt', vpn_peer_address=? WHERE id=?",
            (ip, saved.id),
        )
    return int(saved.id)


# ════════ Defect 1 — WinBox remote access ════════

def test_wg_details_has_winbox_section_and_modal(app, client):
    with app.app_context():
        _login(client)
        nid = _make_wg_router()
        html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
    assert "الوصول البعيد — WinBox" in html
    assert 'data-uds-modal-open="winbox-open-modal"' in html
    # posts to the SAME endpoint the SSTP page uses
    assert f"/admin/radius/mt/{nid}/remote/winbox/open" in html
    # default is "any" (not IP-locked) per the owner's standing requirement
    assert 'name="source_mode" value="any"' in html and "من أي مكان" in html


def test_winbox_open_accepts_wg_router_and_returns_to_wg(app, client):
    """The remote endpoint must accept a WG router (not 404) and redirect back
    to the WG details page — not the SSTP page."""
    with app.app_context():
        _login(client)
        nid = _make_wg_router(ip="10.10.0.21")
        token = _csrf(client)
        res = client.post(f"/admin/radius/mt/{nid}/remote/winbox/open",
                          data={"_csrf_token": token, "source_mode": "any"})
    assert res.status_code in {302, 303}
    assert f"/mt/{nid}/wg" in res.headers["Location"]
    assert "/sstp" not in res.headers["Location"]


def test_winbox_open_v6_still_returns_to_sstp(app, client):
    """Regression: a v6 router must still round-trip to the SSTP page."""
    with app.app_context():
        _login(client)
        nid = _make_v6_router(ip="203.0.113.21")
        token = _csrf(client)
        res = client.post(f"/admin/radius/mt/{nid}/remote/winbox/open",
                          data={"_csrf_token": token, "source_mode": "any"})
    assert res.status_code in {302, 303}
    assert f"/mt/{nid}/sstp" in res.headers["Location"]


def test_remote_return_url_helper(app):
    with app.app_context():
        from app.radius.routes import mt_setup as m
        from flask import url_for
        wg = _make_wg_router(ip="10.10.0.31")
        v6 = _make_v6_router(ip="203.0.113.31")
        with app.test_request_context():
            assert f"/mt/{wg}/wg" in m._remote_return_url(wg)
            assert f"/mt/{v6}/sstp" in m._remote_return_url(v6)


# ════════ Defect 2 — شرح always shown ════════

def test_explanation_panel_always_shown_when_wg_server_configured(app, client):
    with app.app_context():
        _login(client)
        nid = _make_wg_router(ip="10.10.0.41")
        html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
    assert "hcode-explain" in html and "شرح الكود" in html
    assert "ربط خادم اللوحة كـ peer" in html          # a real explanation line


def test_explanation_panel_shown_even_without_wg_server(monkeypatch):
    """The owner's scenario: WG server env NOT configured → the config preview
    can't be built, but the «شرح الكود» must STILL render (it was gated before)."""
    app = _make_app(monkeypatch, wg_server=False)
    try:
        with app.app_context():
            client = app.test_client()
            _login(client)
            nid = _make_wg_router(ip="10.10.0.51")
            html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
        assert "hcode-explain" in html and "شرح الكود" in html
        assert "واجهة WireGuard" in html               # explanation present
        # no live preview card, but a link to the full setup script instead
        assert 'data-cc-card="wgcfg"' not in html
        assert f"/admin/radius/mt/{nid}/script" in html
    finally:
        for k in list(sys.modules):
            if k.startswith("app."):
                del sys.modules[k]


# ════════ Defect 3 — sections in .uds-main ════════

def test_sections_live_in_uds_main(app, client):
    """The page must use the worklayout/.uds-main structure so the shared
    section-gap (--uds-block-gap) applies, like diagnostics/sstp."""
    with app.app_context():
        _login(client)
        nid = _make_wg_router(ip="10.10.0.61")
        html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
    assert "uds-main" in html and "uds-worklayout" in html
    # 5 sections now (status / settings / peer / WinBox / config+شرح)
    assert html.count("hub-section ") + html.count('hub-section"') >= 4
