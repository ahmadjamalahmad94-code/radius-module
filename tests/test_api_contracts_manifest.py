"""GET /api/v1/contracts — real capability manifest (was an unused 501 stub)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_contracts_")
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


def test_contracts_requires_auth(client):
    res = client.get("/api/v1/contracts")
    assert res.status_code == 401


def test_contracts_lists_real_v1_endpoints(client):
    res = client.get("/api/v1/contracts", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["version"] == "v1"
    assert data["count"] > 0
    paths = {e["path"] for e in data["endpoints"]}
    # The manifest reflects the live URL map — these real routes must appear.
    assert "/api/v1/contracts" in paths
    assert any(p.endswith("/api/v1/backups/status") for p in paths)
    # No 501 / not_implemented anywhere in the response.
    assert "not_implemented" not in res.get_data(as_text=True)
    # Each entry exposes concrete HTTP methods.
    sample = next(e for e in data["endpoints"] if e["path"] == "/api/v1/contracts")
    assert "GET" in sample["methods"]
