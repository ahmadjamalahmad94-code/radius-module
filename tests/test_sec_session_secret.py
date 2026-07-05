"""SEC H5 — session-cookie hardening + production FLASK_SECRET guard.

The customer panel is served over plain HTTP with no cookie hardening and a
shipped default FLASK_SECRET (which also roots at-rest key derivation).
- HttpOnly + SameSite=Lax are set unconditionally (pure wins).
- SESSION_COOKIE_SECURE defaults ON in production, OFF otherwise (so the
  current HTTP deployment keeps working; HTTPS deploys opt in).
- Booting in production on the default FLASK_SECRET is refused (fail-closed).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


def _fresh_app(monkeypatch, **env):
    tmp = tempfile.mkdtemp(prefix="hr_h5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("FLASK_SECRET", raising=False)
    monkeypatch.delenv("HOBERADIUS_SESSION_COOKIE_SECURE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def test_cookie_flags_hardened_by_default(monkeypatch):
    app = _fresh_app(monkeypatch)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    # dev (no prod env) → Secure off so plain-HTTP login still works.
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_secure_cookie_defaults_on_in_production(monkeypatch):
    app = _fresh_app(monkeypatch, HOBERADIUS_ENV="production",
                     FLASK_SECRET="a-strong-production-secret-value-32bytes")
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_secure_cookie_env_override(monkeypatch):
    app = _fresh_app(monkeypatch, HOBERADIUS_SESSION_COOKIE_SECURE="1")
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_production_refuses_default_secret(monkeypatch):
    with pytest.raises(RuntimeError, match="FLASK_SECRET"):
        _fresh_app(monkeypatch, HOBERADIUS_ENV="production")  # no FLASK_SECRET


def test_production_boots_with_strong_secret(monkeypatch):
    app = _fresh_app(monkeypatch, HOBERADIUS_ENV="production",
                     FLASK_SECRET="a-strong-production-secret-value-32bytes")
    assert app.secret_key == "a-strong-production-secret-value-32bytes"
