"""SEC M5 — the internal FreeRADIUS auth bridge fails CLOSED in production.

/api/v1/internal/auth decides Accept/Reject for RADIUS. When
HOBERADIUS_INTERNAL_SECRET is unset the old code accepted every request
("dev mode"), so a misconfigured production deploy left the auth-decision
endpoint open to anyone who could reach the port. The fix keeps the dev
convenience out of production: unset secret + prod → reject.
"""
from __future__ import annotations

import pytest


def _payload() -> dict:
    return {"User-Name": "nobody", "User-Password": "wrong",
            "NAS-IP-Address": "127.0.0.1"}


def _make_client(monkeypatch, *, env: str | None, secret: str | None):
    monkeypatch.delenv("HOBERADIUS_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    if secret is not None:
        monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", secret)
    if env is not None:
        monkeypatch.setenv("HOBERADIUS_ENV", env)
        # Production boot refuses the default FLASK_SECRET (SEC H5) — give it one.
        monkeypatch.setenv("FLASK_SECRET", "prod-flask-secret-32-bytes-minimum-xx")
    from app import create_app
    return create_app().test_client()


def test_unset_secret_in_production_rejects(monkeypatch):
    """No secret + production → 401. The endpoint must NOT fail open."""
    client = _make_client(monkeypatch, env="production", secret=None)
    res = client.post("/api/v1/internal/auth", json=_payload())
    assert res.status_code == 401, res.data
    assert res.get_json()["control:Auth-Type"] == "Reject"


def test_unset_secret_outside_production_still_dev_accepts(monkeypatch):
    """No secret + development → still accepted (200, decision Reject because
    the user doesn't exist) so local testing keeps working."""
    client = _make_client(monkeypatch, env=None, secret=None)
    res = client.post("/api/v1/internal/auth", json=_payload())
    assert res.status_code == 200, res.data


def test_secret_set_in_production_still_enforced(monkeypatch):
    """With the secret set, production behaves normally: right secret passes."""
    client = _make_client(monkeypatch, env="production", secret="prod-secret-xyz")
    bad = client.post("/api/v1/internal/auth", json=_payload())
    assert bad.status_code == 401
    good = client.post(
        "/api/v1/internal/auth",
        json={**_payload(), "_internal_secret": "prod-secret-xyz"},
    )
    assert good.status_code == 200
