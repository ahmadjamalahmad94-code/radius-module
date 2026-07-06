"""SEC (hardening) — baseline security headers on every response.

nosniff + Referrer-Policy everywhere, X-Frame-Options SAMEORIGIN on the panel
(anti-clickjacking) but NOT on the cross-origin store API, and HSTS only when
the connection is already secure (never traps a plain-http dev box).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sechdr_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
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


def test_baseline_headers_present(client):
    res = client.get("/admin/radius/login")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_no_hsts_over_plain_http(client):
    res = client.get("/admin/radius/login")
    # Test client speaks http → HSTS must be absent (never trap a dev box).
    assert "Strict-Transport-Security" not in res.headers


def test_hsts_set_over_https(client):
    res = client.get("/admin/radius/login", base_url="https://localhost")
    assert res.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_store_api_not_frame_blocked(client):
    # Cross-origin store endpoints must not carry X-Frame-Options
    # (they are consumed by the router captive page).
    res = client.get("/api/v1/store/nonexistent")
    assert "X-Frame-Options" not in res.headers
    # …but the universal nosniff still applies.
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
