from __future__ import annotations

import pytest
import secrets

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


def test_saas_module_routes_require_auth(client):
    for path in (
        "/api/v1/bandwidth-profiles",
        "/api/v1/pools",
        "/api/v1/vouchers",
        "/api/v1/invoices",
        "/api/v1/tickets",
        "/api/v1/services",
        "/api/v1/share-groups",
    ):
        res = client.get(path)
        assert res.status_code == 401, path


def test_bandwidth_profile_crud_api(client):
    suffix = secrets.token_hex(4)
    created = client.post(
        "/api/v1/bandwidth-profiles",
        json={
            "name": f"API 5M {suffix}",
            "rate_down": 5000,
            "rate_up": 2000,
            "priority": 10,
        },
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()
    item = created.get_json()["data"]
    assert item["rate_down"] == 5000

    patched = client.patch(
        f"/api/v1/bandwidth-profiles/{item['id']}",
        json={"name": f"API 6M {suffix}", "rate_down": 6000, "rate_up": 3000},
        headers=AUTH,
    )
    assert patched.status_code == 200, patched.get_json()
    assert patched.get_json()["data"]["name"] == f"API 6M {suffix}"

    listed = client.get("/api/v1/bandwidth-profiles", headers=AUTH)
    assert listed.status_code == 200
    assert any(p["id"] == item["id"] for p in listed.get_json()["data"]["items"])


def test_pool_and_share_group_real_crud(client):
    suffix = secrets.token_hex(4)
    pool = client.post(
        "/api/v1/pools",
        json={
            "pool_name": f"API pool {suffix}",
            "range_ip": f"10.{int(suffix[:2], 16)}.0.10-10.{int(suffix[:2], 16)}.0.99",
        },
        headers=AUTH,
    )
    assert pool.status_code == 201, pool.get_json()
    assert pool.get_json()["data"]["range_ip"].startswith("10.")

    group = client.post(
        "/api/v1/share-groups",
        json={
            "name": f"API group {suffix}",
            "shared_quota_mb": 1024,
            "shared_speed_down_kbps": 4000,
        },
        headers=AUTH,
    )
    assert group.status_code == 201, group.get_json()
    group_id = group.get_json()["data"]["id"]

    details = client.get(f"/api/v1/share-groups/{group_id}", headers=AUTH)
    assert details.status_code == 200
    assert details.get_json()["data"]["members"] == []


def test_voucher_generation_and_revoke_api(client):
    generated = client.post(
        "/api/v1/vouchers",
        json={"amount": 5.0, "count": 2},
        headers=AUTH,
    )
    assert generated.status_code == 201, generated.get_json()
    items = generated.get_json()["data"]["items"]
    assert len(items) == 2
    assert "code" in items[0]

    revoked = client.post(
        f"/api/v1/vouchers/{items[0]['id']}/revoke",
        json={},
        headers=AUTH,
    )
    assert revoked.status_code == 200, revoked.get_json()
    assert revoked.get_json()["data"]["status"] == "revoked"


def test_saas_validation_routes_do_not_500(client):
    probes = (
        ("/api/v1/invoices", {"amount": 10}),
        ("/api/v1/tickets", {"subject": ""}),
        ("/api/v1/services", {"name": "router"}),
    )
    for path, payload in probes:
        res = client.post(path, json=payload, headers=AUTH)
        assert res.status_code == 422, (path, res.status_code, res.get_data(as_text=True))
        assert res.get_json()["error"]["code"] == "validation_error"
