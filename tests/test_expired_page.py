# -*- coding: utf-8 -*-
"""«انتهى اشتراكك» public page (/p/expired) + the opt-in captive auto-redirect.

The page is PUBLIC (no admin login), HTTP-reachable by blocked subscribers, and
fully panel-configurable. The captive redirect is OFF by default and must never
hijack panel traffic (own host / panel prefixes are excluded).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


def _make_app(monkeypatch, **env):
    tmp = tempfile.mkdtemp(prefix="hr_exppage_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


# ════════════ the page ════════════
def test_page_public_and_renders_defaults(monkeypatch):
    app = _make_app(monkeypatch)
    c = app.test_client()
    res = c.get("/p/expired")          # NO admin login
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "انتهى اشتراكك" in html
    assert res.headers.get("Cache-Control", "").startswith("no-store")


def test_page_reflects_settings_and_username(monkeypatch):
    app = _make_app(
        monkeypatch,
        HOBERADIUS_BLOCK_PAGE_TITLE="اشتراكك خلص",
        HOBERADIUS_BLOCK_PAGE_MESSAGE="جدّد من فضلك يا غالي",
        HOBERADIUS_BLOCK_PAGE_RENEWAL_LINK="http://pay.example/x",
        HOBERADIUS_BLOCK_PAGE_CONTACT_WHATSAPP="962799999999",
    )
    c = app.test_client()
    html = c.get("/p/expired?u=ahmad-pppoe").get_data(as_text=True)
    assert "اشتراكك خلص" in html
    assert "جدّد من فضلك يا غالي" in html
    assert "http://pay.example/x" in html
    assert "wa.me/962799999999" in html
    assert "ahmad-pppoe" in html        # best-effort identity from ?u


# ════════════ captive redirect — opt-in, fail-safe ════════════
def test_captive_redirect_off_by_default(monkeypatch):
    app = _make_app(monkeypatch,
                    HOBERADIUS_BLOCK_PAGE_URL="http://203.0.113.9/p/expired")
    c = app.test_client()
    # foreign host, but feature OFF → no captive redirect to the page
    res = c.get("/something", headers={"Host": "example.com"},
                follow_redirects=False)
    loc = res.headers.get("Location", "")
    assert "203.0.113.9/p/expired" not in loc


def test_captive_redirect_on_foreign_host(monkeypatch):
    app = _make_app(monkeypatch,
                    HOBERADIUS_CAPTIVE_REDIRECT_ENABLED="1",
                    HOBERADIUS_BLOCK_PAGE_URL="http://203.0.113.9/p/expired")
    c = app.test_client()
    res = c.get("/anything", headers={"Host": "captive.gstatic.com"},
                follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"] == "http://203.0.113.9/p/expired"


def test_captive_redirect_never_hijacks_own_host(monkeypatch):
    app = _make_app(monkeypatch,
                    HOBERADIUS_CAPTIVE_REDIRECT_ENABLED="1",
                    HOBERADIUS_BLOCK_PAGE_URL="http://203.0.113.9/p/expired")
    c = app.test_client()
    # the panel IP == the block-page host → auto-whitelisted → NOT redirected
    res = c.get("/", headers={"Host": "203.0.113.9"}, follow_redirects=False)
    assert "203.0.113.9/p/expired" not in res.headers.get("Location", "")


def test_captive_redirect_excludes_panel_prefixes(monkeypatch):
    app = _make_app(monkeypatch,
                    HOBERADIUS_CAPTIVE_REDIRECT_ENABLED="1",
                    HOBERADIUS_BLOCK_PAGE_URL="http://203.0.113.9/p/expired")
    c = app.test_client()
    for path in ("/admin/radius/login", "/p/expired", "/static/x.css",
                 "/api/v1/x"):
        res = c.get(path, headers={"Host": "example.com"},
                    follow_redirects=False)
        loc = res.headers.get("Location", "")
        assert "203.0.113.9/p/expired" not in loc, f"{path} wrongly captured"
