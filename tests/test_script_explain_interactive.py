# -*- coding: utf-8 -*-
"""Every «شرح الكود» row on a script page must be a LIVE jump control.

The onboarding-script page's praised treatment makes each explanation row a
clickable table-of-contents entry: clicking «اقفز إلى هذا القسم في السكربت»
scrolls + flashes that section's lines in the code card (data-cc-jump +
data-cc-jump-start/end, handled by code_card.js). The other script pages used to
render the SAME panel but WITHOUT line ranges, so the rows were dead.

This file guards that they are no longer dead:
  * the line-range helpers in mt_provisioner map markers → (start, end);
  * the cc.code_explain_ranged macro injects those ranges per row;
  * the three pages (mt_setup_script / sstp_credentials / wg_details) actually
    emit data-cc-jump-start/end on their explanation rows (no dead rows).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest


# ───────────────────────── unit: line-range helpers ─────────────────────────

def test_script_section_lines_maps_markers_to_ranges():
    from app.radius.services import mt_provisioner as mp
    wg = mp.render_wg_block(
        nas_name="r1", router_private_key="PRIV", server_pubkey="PUB",
        server_endpoint="1.2.3.4:13231", allowed_subnet="10.10.0.0/24",
        router_tunnel_ip="10.10.0.5/24", mgmt_server_ip="10.10.0.1")
    main = mp.render_routeros_script(
        nas_name="r1", api_user="hr-x", api_password="p", radius_secret="s",
        server_ip="10.10.0.1", ros_version="7")
    full = wg.rstrip() + "\n\n" + main

    ranges = mp.script_section_lines(full)
    # every WG sub-block + every numbered RADIUS step is found
    for key in ("0a", "0b", "0c", "0d", "0e", "1", "2", "3", "4", "5"):
        assert key in ranges, f"marker {key} not detected"
        start, end = ranges[key]
        assert 1 <= start <= end, (key, start, end)
    # markers are strictly ordered down the file (no overlap drift)
    starts = [ranges[k][0] for k in ("0a", "0b", "0c", "0d", "0e", "1", "2", "3", "4", "5")]
    assert starts == sorted(starts)
    # the marker line actually starts that section in the rendered text
    lines = full.split("\n")
    assert lines[ranges["0a"][0] - 1].lstrip().startswith("# 0a)")
    assert lines[ranges["1"][0] - 1].lstrip().startswith("# 1)")


def test_block_command_line_finds_the_single_command():
    from app.radius.services import mt_provisioner as mp
    block = mp.render_sstp_mgmt_block(
        nas_name="r1", accel_host="h", username="rtr-x", password="pw",
        port=443, iface="hr-sstp-mgmt")
    cmd = mp.block_command_line(block)
    assert block.split("\n")[cmd - 1].lstrip().startswith("/interface sstp-client")
    # the line count helper matches how code_card splits on \n
    assert mp.block_line_count(block) == len(block.split("\n"))


def test_block_command_line_falls_back_to_one_when_no_command():
    from app.radius.services import mt_provisioner as mp
    assert mp.block_command_line("# only comments\n# no command") == 1
    assert mp.block_command_line("") == 1
    assert mp.script_section_lines("") == {}


# ───────────────────────── unit: the macro ─────────────────────────

@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_explain_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _render(app, body):
    from flask import render_template_string
    with app.test_request_context("/"):
        return render_template_string(
            '{% import "_partials/code_card.html" as cc %}' + body)


def test_code_explain_ranged_injects_jump_attrs(app):
    items = "[{'title':'A','body':'x'},{'title':'B','body':'y'}]"
    ranges = "[(1, 4), (7, 9)]"
    html = _render(app, "{{ cc.code_explain_ranged(" + items + ", " + ranges + ", target='t') }}")
    assert 'data-cc-jump="t"' in html
    assert 'data-cc-jump-start="1"' in html and 'data-cc-jump-end="4"' in html
    assert 'data-cc-jump-start="7"' in html and 'data-cc-jump-end="9"' in html
    # role=button → keyboard-activatable (same hooks as the onboarding chips)
    assert html.count('role="button"') >= 2


def test_code_explain_ranged_no_ranges_is_non_interactive(app):
    """When the page has no code card to jump to (ranges falsy), the rows render
    but stay non-interactive — never a clickable row that jumps to nothing."""
    items = "[{'title':'A','body':'x'}]"
    html = _render(app, "{{ cc.code_explain_ranged(" + items + ", None, target='t') }}")
    assert "hcode-explain" in html and "hcode-xrow" in html
    assert "data-cc-jump" not in html


def test_code_explain_ranged_partial_ranges(app):
    """A None entry leaves just that row non-interactive; the others still jump."""
    items = "[{'title':'A','body':'x'},{'title':'B','body':'y'}]"
    ranges = "[None, (3, 5)]"
    html = _render(app, "{{ cc.code_explain_ranged(" + items + ", " + ranges + ", target='t') }}")
    assert html.count("data-cc-jump=") == 1
    assert 'data-cc-jump-start="3"' in html and 'data-cc-jump-end="5"' in html


# ───────────────────────── routes: the three pages ─────────────────────────

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


def _csrf(client, url):
    client.get(url)
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _jump_rows(html: str) -> int:
    """Count explanation rows that carry a working jump range (not dead)."""
    return len(re.findall(r'class="hcode-xrow"[^>]*data-cc-jump-start="\d+"', html))


def _make_legacy_v7_router(name="LEGACY7", ip="198.51.100.7"):
    """A direct (non-VPN) v7 router — mt_setup_script renders the main template
    with the 3-row legacy explanation (markers 1..5 always present)."""
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction
    dev = NasDevice(
        id=None, name=name, address=ip, secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True)
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute("UPDATE nas_devices SET ros_version='7', connection_mode='' "
                  "WHERE id=?", (saved.id,))
    return int(saved.id)


def test_setup_script_legacy_explain_rows_are_live(app, client):
    with app.app_context():
        _login(client)
        nid = _make_legacy_v7_router()
        html = client.get(f"/admin/radius/mt/{nid}/script").get_data(as_text=True)
    assert "شرح الكود" in html and "hcode-explain" in html
    # the 3 legacy rows each jump to a real line range in the #setup card
    assert 'data-cc-jump="setup"' in html
    assert _jump_rows(html) == 3
    # the ranges fall inside the rendered script (line count badge ≥ max end)
    ends = [int(m) for m in re.findall(r'data-cc-jump-end="(\d+)"', html)]
    assert ends and max(ends) >= 5


def _make_v6_sstp_router(client):
    token = _csrf(client, "/admin/radius/mt/setup")
    res = client.post("/admin/radius/mt/setup",
                      data={"_csrf_token": token, "name": "CCR4",
                            "ros_version": "6", "v6_mode": "sstp_mgmt",
                            "server_ip": "187.77.70.18"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:400]
    from app.radius.db.connection import db
    row = db().execute("SELECT id FROM nas_devices WHERE name='CCR4'").fetchone()
    return int(row["id"])


def test_sstp_mikrotik_and_accel_explain_rows_are_live(app, client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
    with app.app_context():
        _login(client)
        nid = _make_v6_sstp_router(client)
        # reset reveals the MikroTik block → the sstp-mt explanation becomes live
        token = _csrf(client, f"/admin/radius/mt/{nid}/sstp")
        html = client.post(f"/admin/radius/mt/{nid}/sstp/reset",
                           data={"_csrf_token": token, "password": "Chosen-Pw-77"},
                           follow_redirects=True).get_data(as_text=True)
    assert "/interface sstp-client add" in html       # block really rendered
    # the MikroTik-block explanation rows jump into the #sstp-mt card
    assert 'data-cc-jump="sstp-mt"' in html
    assert _jump_rows(html) >= 3
    # the accel-ppp explanation (if its card rendered) also jumps
    if 'data-cc-card="sstp-accel"' in html:
        assert 'data-cc-jump="sstp-accel"' in html


def test_sstp_no_block_means_no_dead_jump_rows(app, client, monkeypatch):
    """Before reveal there is no MikroTik card; the sstp-mt rows must NOT pretend
    to be clickable (no jump-to-nothing)."""
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
    with app.app_context():
        _login(client)
        nid = _make_v6_sstp_router(client)
        html = client.get(f"/admin/radius/mt/{nid}/sstp").get_data(as_text=True)
    assert 'data-cc-jump="sstp-mt"' not in html        # no block → no jump rows


def _make_wg_router(name="CCR-WG", ip="10.10.0.7",
                    pub="ABCdef0123456789ABCdef0123456789ABCdef01234="):
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction
    dev = NasDevice(
        id=None, name=name, address=ip, secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True)
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version='7', connection_mode='vpn', "
            "       management_tunnel_type='', vpn_public_key=?, "
            "       vpn_peer_address=?, vpn_interface='wg0' WHERE id=?",
            (pub, ip, saved.id))
    return int(saved.id)


def test_wg_details_explain_rows_are_live_with_preview(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_explain_wg_")
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
    app = create_app()
    try:
        with app.app_context():
            client = app.test_client()
            _login(client)
            nid = _make_wg_router(ip="10.10.0.81")
            html = client.get(f"/admin/radius/mt/{nid}/wg").get_data(as_text=True)
        # preview card present → the 4 explanation rows jump into #wgcfg
        assert 'data-cc-card="wgcfg"' in html
        assert 'data-cc-jump="wgcfg"' in html
        assert _jump_rows(html) == 4
    finally:
        for k in list(sys.modules):
            if k.startswith("app."):
                del sys.modules[k]
