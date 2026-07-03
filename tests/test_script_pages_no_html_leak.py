# -*- coding: utf-8 -*-
"""Script pages must render real inline-<code> chips — never leaked raw tags.

Owner-reported bug on /admin/radius/mt/<id>/onboarding-script: step 3 of
«ماذا تفعل بهذا السكربت؟» showed the LITERAL text
`<code dir="ltr">hr: Onboarding done</code>` instead of a styled code chip
(«في أكواد كثير متسربة داخل النص»).

Root cause (the Markup-concat trap): `_()` returns Markup here, and
concatenating it with a plain string (`_('انتظر سطر') ~ ' <code>…</code> '`)
escapes the plain side at concat time — so by the time the macro's `|safe`
runs, the string already contains `&lt;code…`. Fix: static tags are authored
as real template markup captured via `{% set x %}…{% endset %}` blocks (which
produce Markup: tags real, `{{ dynamic }}` values still escaped — no |safe on
anything user/router-derived). The same class was fixed in mt_setup_script,
wg_details, and the `footer_html='<button…' ~ _()` modal footers
(card_pricing / finance_billing / hotspot_errors / recharge_panel).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_leak_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
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
    username = f"leak_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=username, password="leak-pass",
                             full_name="Leak Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "leak-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/setup"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _make_v6_sstp_router(client) -> int:
    token = _csrf(client)
    client.post("/admin/radius/mt/setup",
                data={"_csrf_token": token, "name": "CafeNoor",
                      "ros_version": "6", "v6_mode": "sstp_mgmt",
                      "server_ip": "187.77.70.18"}, follow_redirects=False)
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT id FROM nas_devices WHERE name='CafeNoor'").fetchone()
    assert row, "v6 router was not created"
    return int(row["id"])


def _make_legacy_v7_router() -> int:
    """Direct-service creation (same pattern as test_script_explain_interactive)
    — renders the /script page's legacy path with api_user in the callout."""
    from app.radius.core.types import NasDevice
    from app.radius.db.connection import transaction
    from app.radius.services.devices import get_nas_devices_service
    dev = NasDevice(
        id=None, name="WizSeven", address="203.0.113.77",
        secret="s3cr3t-radius", vendor="mikrotik", nas_type="hotspot",
        api_port=8728, api_user="hobe-api", api_password="x",
        api_use_tls=False, enabled=True, monitoring_enabled=True)
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute("UPDATE nas_devices SET ros_version='7', connection_mode='' "
                  "WHERE id=?", (saved.id,))
    return int(saved.id)


LEAK_MARKERS = ("&lt;code", "&lt;b&gt;", "&lt;/code&gt;", "&lt;br",
                "&lt;span", "&lt;button")


def test_onboarding_instructions_render_real_code_chip(app, client):
    """The reported leak verbatim: step 3 must contain a REAL <code> element
    with the marker line, and the page must not show the escaped tag text."""
    _login(client)
    with app.app_context():
        nas_id = _make_v6_sstp_router(client)
    res = client.get(f"/admin/radius/mt/{nas_id}/onboarding-script")
    assert res.status_code == 200, res.get_data(as_text=True)[:400]
    html = res.get_data(as_text=True)
    assert '<code dir="ltr">hr: Onboarding done</code>' in html
    for marker in LEAK_MARKERS:
        assert marker not in html, f"leaked escaped tag on onboarding page: {marker}"
    # The firewall callout's <b> is real markup too.
    assert "<b>" in html


def test_setup_script_page_has_no_leaked_tags(app, client):
    """Sibling page (mt/<id>/script) used the same concat pattern in its
    steps, security callout (with the dynamic api_user inside <code>), and
    explain bodies — all must render as real markup now. (Legacy v7 router —
    the v6 tunnel router redirects off this page.)"""
    _login(client)
    with app.app_context():
        nas_id = _make_legacy_v7_router()
        res = client.get(f"/admin/radius/mt/{nas_id}/script")
    assert res.status_code == 200, res.get_data(as_text=True)[:400]
    html = res.get_data(as_text=True)
    assert '<code dir="ltr">HobeRadius provisioning done</code>' in html
    for marker in LEAK_MARKERS:
        assert marker not in html, f"leaked escaped tag on setup page: {marker}"


def test_dynamic_values_stay_escaped_inside_code_chip(app):
    """XSS posture of the fix: the set-block keeps DYNAMIC values escaped —
    only the static tag structure is markup. A hostile value must not become
    live HTML inside the <code> chip."""
    with app.test_request_context():
        from flask import render_template_string
        out = render_template_string(
            "{% set u = '<img src=x onerror=alert(1)>' %}"
            "{% set s %}{{ _('المستخدم') }} <code dir=\"ltr\">{{ u }}</code>{% endset %}"
            "{{ s }}")
        assert "<code" in out                       # real chip
        assert "<img" not in out                    # payload not live
        assert "&lt;img" in out                     # payload escaped
