"""Regression tests for /api/v1/internal/auth secret delivery.

السبب: FR 3.2.x لا يدعم custom HTTP headers في rlm_rest. الـ secret يصل
من FreeRADIUS عبر JSON body field `_internal_secret` بدل header. Flask
يجب أن يقبل من الطريقَتَين.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app(monkeypatch):
    """app fresh لكل اختبار حتى env variables تُلتقط بدون تداخل."""
    monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", "test-secret-abc123")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _payload(extra: dict | None = None) -> dict:
    base = {
        "User-Name": "nobody",
        "User-Password": "wrong",
        "NAS-IP-Address": "127.0.0.1",
    }
    if extra:
        base.update(extra)
    return base


def test_no_secret_returns_401(client):
    """طلب بدون header أو body secret → 401."""
    res = client.post("/api/v1/internal/auth", json=_payload())
    assert res.status_code == 401


def test_header_secret_accepted(client):
    """طلب بـ X-Internal-Secret صحيح → 200 (decision Reject لأن المستخدم
    غير موجود، لكن 200 وليس 401 = الـ secret قُبِل)."""
    res = client.post(
        "/api/v1/internal/auth",
        json=_payload(),
        headers={"X-Internal-Secret": "test-secret-abc123"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["control:Auth-Type"] == "Reject"


def test_body_secret_accepted_as_fallback(client):
    """FR 3.2.x لا يستطيع إرسال headers — السرّ يصل عبر body field
    `_internal_secret`. Flask يجب أن يقبله ويتابع."""
    res = client.post(
        "/api/v1/internal/auth",
        json=_payload({"_internal_secret": "test-secret-abc123"}),
    )
    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.data!r}"
    body = res.get_json()
    assert body["control:Auth-Type"] == "Reject"


def test_wrong_body_secret_rejected(client):
    res = client.post(
        "/api/v1/internal/auth",
        json=_payload({"_internal_secret": "wrong-secret-xyz"}),
    )
    assert res.status_code == 401


def test_postauth_body_secret_accepted(client):
    res = client.post(
        "/api/v1/internal/postauth",
        json={
            "_internal_secret": "test-secret-abc123",
            "User-Name": "user1",
            "reply_code": "Access-Reject",
            "NAS-IP-Address": "127.0.0.1",
        },
    )
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
