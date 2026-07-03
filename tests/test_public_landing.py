# -*- coding: utf-8 -*-
"""Public landing page at the site root «/».

Standard front door for every customer instance (owner: «الواجهة تكون لكل
الريدياسات بالمستقبل»): a public, no-auth, no-data landing that presents three
entry points linking to the REAL existing login pages —

    دخول الإدارة        → /admin/radius/login        (radius.auth_login)
    دخول المشتركين      → /portal/subscriber/login   (portal.subscriber_login)
    سوق البطاقات        → /portal/card/login          (portal.card_login)

Before this, «/» redirected straight to the admin dashboard (which bounced
anonymous visitors to the admin login) — subscribers and card buyers had no
front door. The page must render fully anonymous, with no licensing linked and
no branding configured (graceful fallbacks), because it is the first thing a
fresh instance serves.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_landing_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def test_root_returns_200_without_auth(app, client):
    """Anonymous GET / must be 200 (not a redirect to admin login)."""
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in (res.content_type or "")


def test_root_contains_three_entry_links(app, client):
    """The three entry points must link to the real, registered login routes."""
    html = client.get("/").get_data(as_text=True)
    assert 'href="/admin/radius/login"' in html
    assert 'href="/portal/subscriber/login"' in html
    assert 'href="/portal/card/login"' in html
    # And the Arabic labels the owner asked for:
    assert "دخول الإدارة" in html
    assert "دخول المشتركين" in html
    assert "سوق البطاقات الإلكتروني" in html


def test_root_is_public_no_data(app, client):
    """No auth cookie, no licensing, no branding configured — the page still
    renders with the default brand (graceful fallback) and RTL Arabic shell."""
    html = client.get("/").get_data(as_text=True)
    assert "HobeRadius" in html            # default system_name fallback
    assert 'dir="rtl"' in html
    # It is a marketing front door, not a data page: no session/kpi widgets.
    assert "csrf" not in html.lower()      # no forms — pure links


def test_linked_login_routes_actually_exist(app, client):
    """Guard against link rot: every linked entry must itself respond (200 for
    the login pages — none of them may 404)."""
    for path in ("/admin/radius/login", "/portal/subscriber/login",
                 "/portal/card/login"):
        res = client.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"


def test_branding_from_settings_reflected(app, client):
    """system.name + branding.primary_color set in tenant settings must show up
    on the landing (the per-instance brand, not hardcoded)."""
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        tenants_repo.set_setting(1, "system.name", "شبكة النور", by=0)
        tenants_repo.set_setting(1, "branding.primary_color", "#7C3AED", by=0)
    html = client.get("/").get_data(as_text=True)
    assert "شبكة النور" in html
    assert "#7C3AED" in html
