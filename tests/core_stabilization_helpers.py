"""Shared helpers for core stabilization slice tests."""
from __future__ import annotations

import secrets
import time

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def configured_plan(app):
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            """
            UPDATE access_plans
            SET price = 150, duration_minutes = 43200, validity_days = 30
            WHERE tenant_id = 1 AND id = 1
            """
        )


def username(prefix: str = "core") -> str:
    return prefix + "_" + secrets.token_hex(5)


def subscriber(client, *, name: str | None = None, card_batch_id: int | None = None) -> dict:
    payload = {"username": name or username(), "password": "pw1234", "plan_id": 1}
    if card_batch_id:
        payload["card_batch_id"] = card_batch_id
    res = None
    for _ in range(5):
        res = client.post("/api/v1/accounts", json=payload, headers=AUTH)
        if res.status_code != 500:
            break
        time.sleep(0.05)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def batch(client, prefix: str) -> dict:
    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": prefix},
        headers=AUTH,
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["batch"]
