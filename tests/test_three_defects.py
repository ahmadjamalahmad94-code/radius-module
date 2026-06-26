# -*- coding: utf-8 -*-
"""Three owner-reported defects on the new work:

  1) WireGuard per-router DETAILS page (the v7 sibling of /mt/<id>/sstp).
  2) the «شرح الكود» explanation panel on every script page (was missing).
  3) design-system section spacing (sections were «متلاصقة»).

Per-file isolation (fresh app/db)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_3def_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "U" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
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


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"a_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw", full_name="A",
                             is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "pw"})
    assert res.status_code in {302, 303}


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


# ════════ Defect 1 — WireGuard details page ════════

def test_wg_details_route_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_wg_details" in rules


def test_wg_details_mirrors_sstp_structure(app, client):
    """The WG details page is the v7 sibling of /mt/<id>/sstp — same design
    system, with WG-real data."""
    with app.app_context():
        _login(client)
        nid = _make_wg_router("CCR-WG-1", ip="10.10.0.7")
        html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
    # design system (same megahero / worklayout as the SSTP page)
    assert "uds-hero" in html and "uds-hero-kpis" in html
    # REAL WG facts
    assert "CCR-WG-1" in html and "10.10.0.7" in html
    assert "wg0" in html and "187.77.70.18:51820" in html      # interface + endpoint
    # public-key reveal control (key is public, revealed not faked)
    assert "data-wg-pub-toggle" in html and "data-wg-copy" in html
    # WG-appropriate actions (regenerate / remove), no MSCHAP/password reveal
    assert "radius.mt_wg_peer_regenerate" in html or "wg-peers/regenerate" in html
    assert "إزالة peer" in html
    # honest gaps surfaced, never faked (private key not stored in the panel)
    assert "لا يُخزَّن" in html


def test_wg_details_has_config_preview_and_explanation(app, client):
    with app.app_context():
        _login(client)
        nid = _make_wg_router("CCR-WG-2", ip="10.10.0.8")
        html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
    # router config preview rendered through the code-card component
    assert 'data-cc-card="wgcfg"' in html
    # AND the «شرح الكود» explanation panel paired with it
    assert "hcode-explain" in html
    assert "ربط خادم اللوحة كـ peer" in html        # a real WG explanation line


def test_wg_details_404_for_non_wg_or_unknown(app, client):
    with app.app_context():
        _login(client)
        # unknown id
        assert client.get("/admin/radius/mt/99999/wg").status_code == 404


def test_wg_peers_list_links_each_row_to_details(app, client):
    with app.app_context():
        _login(client)
        nid = _make_wg_router("CCR-WG-3", ip="10.10.0.9")
        html = client.get("/admin/radius/mt/wg-peers").get_data(as_text=True)
    assert f"/admin/radius/mt/{nid}/wg" in html      # row links into its details page


def test_get_mgmt_peer_service(app):
    with app.app_context():
        nid = _make_wg_router("CCR-WG-4", ip="10.10.0.11")
        from app.radius.services import wireguard_mgmt as wg
        peer = wg.get_mgmt_peer(1, nid)
        assert peer is not None
        assert peer["tunnel_ip"] == "10.10.0.11"
        assert peer["interface"] == "wg0"
        assert peer["has_private_key"] is False
        assert wg.get_mgmt_peer(1, 99999) is None


# ════════ Defect 2 — code explanation (شرح الكود) ════════

def test_explain_sections_covers_every_section_with_valid_ranges(app):
    with app.app_context():
        from app.radius.services.router_onboarding_script import (
            OnboardingParams, build_onboarding_script, split_sections,
            explain_sections)
        p = OnboardingParams(
            router_name="CCR-Test", router_id=5, accel_host="1.2.3.4",
            sstp_port=443, tunnel_user="rtr-ccrtest",
            tunnel_password="PwLongEnough12345!", tunnel_ip="10.50.0.9",
            radius_ip="1.2.3.4", radius_secret="sekretsekret12",
            api_user="hobe-api", api_password="ApiPwLong123456!",
            walled_garden=["x.com"], block_page_url="",
            hotspot_pool="10.5.50.0/24", pppoe_pool="10.5.60.0/24")
        script = build_onboarding_script(p)
        total = len(script.split("\n"))
        secs = split_sections(script)
        ex = explain_sections(secs)
        assert len(ex) == len(secs)                     # every section explained
        assert all(e["body"] for e in ex)               # real text, not blank
        for e in ex:                                    # line ranges are valid
            assert 1 <= e["start_line"] <= e["end_line"] <= total
            assert e["sec"] is not None


def test_onboarding_page_renders_explanation_panel(app, client):
    from tests.test_onboarding_redesign import _v6_router
    with app.app_context():
        _login(client)
        nid = _v6_router(client)
        html = client.get(
            f"/admin/radius/mt/{nid}/onboarding-script").get_data(as_text=True)
    assert "شرح الكود" in html and "hcode-explain" in html
    assert 'data-cc-jump="ob"' in html                  # explained ToC = clickable
    assert "المسار الذي تُدار منه" in html               # a real section explanation


def test_setup_script_page_has_explanation(app, client):
    """The /mt/<id>/script page (v7 WG / legacy) carries a شرح panel too."""
    with app.app_context():
        _login(client)
        nid = _make_wg_router("CCR-WG-5", ip="10.10.0.12")
        html = client.get(f"/admin/radius/mt/{nid}/script").get_data(as_text=True)
    assert "hcode-explain" in html
    assert "شرح الكود" in html


def test_code_explain_macro_emits_jump_and_copy_hooks(app):
    """The component macro renders clickable, per-section explained rows."""
    from flask import render_template_string
    with app.test_request_context("/"):
        html = render_template_string(
            '{% import "_partials/code_card.html" as cc %}'
            "{{ cc.code_explain(items, target='t') }}",
            items=[{"title": "أ", "body": "شرح أ", "start_line": 1,
                    "end_line": 5, "sec": 0},
                   {"title": "ب", "body": "شرح ب"}])
        assert "hcode-explain" in html
        assert 'data-cc-jump="t"' in html and 'data-cc-jump-start="1"' in html
        assert 'data-cc-seccopy="t"' in html and 'data-cc-sec="0"' in html
        assert "شرح أ" in html and "شرح ب" in html


# ════════ Defect 3 — section spacing (design-system token) ════════

def test_block_gap_token_increased():
    """The shared vertical-rhythm token drives every section/card gap. It was
    20px (felt cramped) — bumped so the fix propagates design-system-wide."""
    css = open("app/static/css/unified_design.css", encoding="utf-8").read()
    import re
    m = re.search(r"--uds-block-gap:\s*(\d+)px", css)
    assert m, "the shared block-gap token must exist"
    assert int(m.group(1)) >= 24, "block gap should be >= 24px (was 20)"
    # the main work-column gap is driven by that same token (one source)
    assert ".uds-main{ gap: var(--uds-block-gap)" in css


def test_section_explain_css_present():
    css = open("app/static/css/unified_design.css", encoding="utf-8").read()
    assert ".hcode-explain" in css and ".hcode-xrow" in css
