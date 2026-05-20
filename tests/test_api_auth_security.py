"""
Security Slice S-1 regression tests.

Covers:
  A. Dev fallback token gated by environment
     - works when HOBERADIUS_ENV is unset/dev
     - rejected when HOBERADIUS_ENV=production
  B. Token expiry enforcement
     - valid (non-expired) DB token passes
     - expired DB token → 401 token_expired
     - revoked DB token → 401 unauthorized
     - missing / unknown token → 401 unauthorized
  C. Login mints a token with the configured TTL.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _admin_token(client) -> str:
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    return res.get_json()["data"]["token"]


# ─────────────── A. dev fallback gating ───────────────

def test_dev_fallback_works_in_dev_mode(client, monkeypatch):
    """With HOBERADIUS_ENV unset (default), the legacy dev token authenticates."""
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer dev-token-please-change"},
    )
    assert res.status_code == 200, res.get_json()


def test_dev_fallback_blocked_in_production(client, monkeypatch):
    """With HOBERADIUS_ENV=production, the dev fallback must not authenticate."""
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer dev-token-please-change"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_env_tokens_still_work_in_production(client, monkeypatch):
    """Explicit HOBERADIUS_API_TOKENS env tokens still authenticate even in prod."""
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "explicit-prod-token-1234")
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer explicit-prod-token-1234"},
    )
    assert res.status_code == 200


def test_api_rate_limit_is_unlimited_unless_explicitly_enabled(monkeypatch):
    from app.api.auth import _configured_api_rpm

    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    assert _configured_api_rpm(tenant_rpm=10) == 0

    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", "250")
    assert _configured_api_rpm(tenant_rpm=10) == 250

    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", "0")
    assert _configured_api_rpm(tenant_rpm=10) == 0


# ─────────────── B. token expiry / validity ───────────────

def test_valid_login_token_authenticates(client):
    token = _admin_token(client)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


def test_expired_token_rejected_with_token_expired_code(app, client):
    """Mint a token, manually set expires_at to the past, expect 401 token_expired."""
    from app.radius.db.connection import transaction
    from app.radius.db.repos import api_tokens_repo

    with app.app_context():
        record, plain = api_tokens_repo.create_token(
            tenant_id=1,
            name="qa_expired_token",
            scopes=["admin:full"],
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        # Backdate via direct UPDATE — simulates the clock advancing past TTL.
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                "UPDATE api_tokens SET expires_at = ? WHERE id = ?",
                (past, record["id"]),
            )

    try:
        res = client.get(
            "/api/v1/accounts",
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert res.status_code == 401
        body = res.get_json()
        assert body["error"]["code"] == "token_expired"
    finally:
        with app.app_context():
            api_tokens_repo.revoke_token(1, record["id"])


def test_revoked_token_rejected(app, client):
    from app.radius.db.repos import api_tokens_repo

    with app.app_context():
        record, plain = api_tokens_repo.create_token(
            tenant_id=1,
            name="qa_revoked_token",
            scopes=["admin:full"],
        )
        api_tokens_repo.revoke_token(1, record["id"])

    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {plain}"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_unknown_token_rejected(client):
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer some-token-that-does-not-exist-xyz"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_missing_token_rejected(client):
    res = client.get("/api/v1/accounts")
    assert res.status_code == 401


# ─────────────── C. login mints a token with TTL ───────────────

def test_login_returns_expires_at(client, monkeypatch):
    """The login response surfaces the new token's expires_at so Flutter can
    warn the user before the session lapses."""
    monkeypatch.setenv("HOBERADIUS_TOKEN_TTL_HOURS", "24")
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["expires_at"] is not None
    # Parse and confirm it's roughly 24h ahead (allow 5min clock skew)
    exp = datetime.fromisoformat(str(data["expires_at"]).replace("Z", ""))
    delta = exp - datetime.utcnow()
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)
