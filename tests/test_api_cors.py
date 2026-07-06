"""
Security Slice S-2 regression tests — production CORS allowlist.

CORS configuration is captured at app-creation time, so each scenario
builds a fresh app with monkeypatched env. Workers + demo seeding are
suppressed via env flags to keep app creation fast.

Behaviour matrix (env = HOBERADIUS_ENV or FLASK_ENV):

| env       | HOBERADIUS_CORS_ORIGINS    | expected                         |
|-----------|----------------------------|----------------------------------|
| dev/empty | unset or "*"               | echo any Origin (wildcard)       |
| dev/empty | "https://a,https://b"      | explicit allow-list              |
| prod      | unset, empty, or "*"       | no Access-Control-Allow-Origin   |
| prod      | "https://a,https://b"      | explicit allow-list              |
"""
from __future__ import annotations

import pytest


def _make_app(monkeypatch, *, env=None, origins=None):
    """Create a fresh Flask app with the requested CORS env. Workers/seed
    are off to keep the test fast and idempotent."""
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    if env is None:
        monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)
    else:
        monkeypatch.setenv("HOBERADIUS_ENV", env)
        # Production boot refuses the default FLASK_SECRET (SEC H5) — give it a
        # strong one so these CORS tests exercise the prod path, not the guard.
        monkeypatch.setenv("FLASK_SECRET", "cors-test-strong-flask-secret-32b-xx")
    if origins is None:
        monkeypatch.delenv("HOBERADIUS_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("HOBERADIUS_CORS_ORIGINS", origins)
    from app import create_app
    return create_app()


# Use /api/v1/version because it doesn't require auth — we're testing
# CORS headers, not auth flow.
_PROBE = "/api/v1/version"


# ─────────────── A. dev mode (default) ───────────────

def test_dev_mode_default_echoes_any_origin(monkeypatch):
    app = _make_app(monkeypatch, env=None, origins=None)
    client = app.test_client()
    res = client.get(_PROBE, headers={"Origin": "http://random.example.com"})
    assert res.status_code == 200
    assert res.headers.get("Access-Control-Allow-Origin") == "http://random.example.com"
    assert "Origin" in (res.headers.get("Vary") or "")


def test_dev_mode_explicit_wildcard_still_echoes(monkeypatch):
    app = _make_app(monkeypatch, env=None, origins="*")
    client = app.test_client()
    res = client.get(_PROBE, headers={"Origin": "http://anywhere.example.com"})
    assert res.headers.get("Access-Control-Allow-Origin") == "http://anywhere.example.com"


def test_dev_mode_explicit_allowlist_filters(monkeypatch):
    """An explicit list in dev still filters — used by integration tests."""
    app = _make_app(monkeypatch, env=None,
                    origins="http://allowed.example.com")
    client = app.test_client()
    ok = client.get(_PROBE, headers={"Origin": "http://allowed.example.com"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "http://allowed.example.com"

    blocked = client.get(_PROBE, headers={"Origin": "http://other.example.com"})
    assert blocked.headers.get("Access-Control-Allow-Origin") is None


# ─────────────── B. production mode ───────────────

def test_production_with_no_origins_strips_header(monkeypatch):
    """The headline guarantee: prod with unset HOBERADIUS_CORS_ORIGINS must
    NOT respond with Access-Control-Allow-Origin: *."""
    app = _make_app(monkeypatch, env="production", origins=None)
    client = app.test_client()
    res = client.get(_PROBE, headers={"Origin": "http://random.example.com"})
    assert res.status_code == 200
    assert res.headers.get("Access-Control-Allow-Origin") is None


def test_production_with_empty_origins_strips_header(monkeypatch):
    app = _make_app(monkeypatch, env="production", origins="")
    client = app.test_client()
    res = client.get(_PROBE, headers={"Origin": "http://anything.example.com"})
    assert res.headers.get("Access-Control-Allow-Origin") is None


def test_production_with_literal_wildcard_is_rejected(monkeypatch):
    """Even an explicit `*` value is rejected in prod — there is no path to
    wildcard CORS in production."""
    app = _make_app(monkeypatch, env="production", origins="*")
    client = app.test_client()
    res = client.get(_PROBE, headers={"Origin": "http://anywhere.example.com"})
    assert res.headers.get("Access-Control-Allow-Origin") is None


def test_production_with_allowlist_allows_only_those(monkeypatch):
    app = _make_app(
        monkeypatch,
        env="production",
        origins="https://app.example.com,https://admin.example.com",
    )
    client = app.test_client()

    ok1 = client.get(_PROBE, headers={"Origin": "https://app.example.com"})
    assert ok1.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"

    ok2 = client.get(_PROBE, headers={"Origin": "https://admin.example.com"})
    assert ok2.headers.get("Access-Control-Allow-Origin") == "https://admin.example.com"

    blocked = client.get(_PROBE, headers={"Origin": "https://evil.example.com"})
    assert blocked.headers.get("Access-Control-Allow-Origin") is None


def test_production_native_client_no_origin_header_still_works(monkeypatch):
    """Native mobile/desktop Flutter clients send no Origin header. Those
    requests must still succeed (CORS only gates browsers)."""
    app = _make_app(monkeypatch, env="production", origins=None)
    client = app.test_client()
    res = client.get(_PROBE)  # no Origin header
    assert res.status_code == 200
    # No CORS header expected; that's correct — non-browser callers don't
    # need one.
    assert res.headers.get("Access-Control-Allow-Origin") is None


# ─────────────── C. non-API paths are untouched ───────────────

def test_non_api_paths_get_no_cors_headers(monkeypatch):
    """The hook only adds CORS headers under /api/*. Web admin paths stay
    clean — important so reverse proxies/CSP behave consistently."""
    app = _make_app(monkeypatch, env=None, origins=None)
    client = app.test_client()
    res = client.get(
        "/admin/radius/_health",
        headers={"Origin": "http://anywhere.example.com"},
    )
    assert res.headers.get("Access-Control-Allow-Origin") is None
