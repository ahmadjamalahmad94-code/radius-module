"""feat/api-first-parity — mikrotik push/metrics setup-script inputs (group 7g).

يعكس مدخلات صفحتي «دفع DHCP» و«دفع القياسات» (base_url/ingest/tokens/
scheduler). شغّل وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtss_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/push-setup").status_code == 401


def test_push_setup(client):
    res = client.get("/api/v1/mikrotik/push-setup", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["ingest_endpoint"] == "/api/v1/devices/ingest"
    assert d["ingest_url"].endswith("/api/v1/devices/ingest")
    assert d["scheduler"]["name"] == "hoberadius-push-dhcp"
    assert isinstance(d["tokens"], list)


def test_push_setup_base_url_override(client):
    d = client.get("/api/v1/mikrotik/push-setup?base_url=https://my.vps:8443/",
                   headers=AUTH).get_json()["data"]
    assert d["base_url"] == "https://my.vps:8443"
    assert d["ingest_url"] == "https://my.vps:8443/api/v1/devices/ingest"


def test_metrics_setup(client):
    res = client.get("/api/v1/mikrotik/metrics-setup?router_id=5&base_url=http://x", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["ingest_endpoint_template"] == "/api/v1/routers/{router_id}/metrics/ingest"
    assert d["ingest_url"] == "http://x/api/v1/routers/5/metrics/ingest"
    assert d["scheduler"]["name"] == "hoberadius-push-metrics"
    assert "routers" in d


def test_metrics_setup_no_router(client):
    d = client.get("/api/v1/mikrotik/metrics-setup", headers=AUTH).get_json()["data"]
    assert d["ingest_url"] == ""  # بلا router_id → رابط فارغ
    assert "tokens" in d


def test_token_value_never_returned(app, client):
    # حتى لو وُجد توكن، نُعيد الاسم فقط لا القيمة/الهاش
    with app.app_context():
        from app.radius.db.repos import api_tokens_repo
        try:
            api_tokens_repo.create_token(tenant_id=1, name="flutter")
        except Exception:
            pass
    body = client.get("/api/v1/mikrotik/push-setup", headers=AUTH).get_json()
    assert "token_hash" not in str(body) and "Bearer <API_TOKEN>" in str(body)
