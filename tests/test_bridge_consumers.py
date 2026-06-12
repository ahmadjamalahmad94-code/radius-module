"""Tests for the bridge CONSUMER features:

  * CHR tunnels (request / sync / lifecycle / no raw-secret storage)
  * super-admin enforcement (producer report + consumer overrides)

All bridge I/O is mocked; no network is touched.
"""
from __future__ import annotations

import os

import pytest


class RoutingTransport:
    """Mock transport that answers by URL suffix and records every call."""

    def __init__(self, routes: dict[str, dict] | None = None):
        self.routes = routes or {}
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        url = kwargs.get("url") or ""
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return {"ok": False, "status": "unexpected"}

    def urls(self):
        return [c["url"].split("/api/integration/hoberadius")[-1] for c in self.calls]


def _reset(db_file):
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "bridge_consumers.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    _reset(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo

        run_pending_migrations()
        admins_repo.ensure_default_roles()
        yield app
    _reset(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    return AdminBridgeConfig(
        enabled=True,
        base_url="https://license-panel.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC",
        timeout_seconds=1.0,
        retry_count=0,
    )


# ───────────────────────── super-admin enforcement ─────────────────────────

def test_super_override_applies_by_id_then_username_idempotently(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.license_admin_identity_sync import apply_super_admin_overrides

    a = admins_repo.create_admin(username="op-one", password="x" * 10, is_super_admin=False)
    b = admins_repo.create_admin(username="op-two", password="x" * 10, is_super_admin=False)

    summary = apply_super_admin_overrides([
        {"radius_admin_id": a.id, "username": "ignored", "is_super_admin": True},
        {"username": "op-two", "is_super_admin": True},
        {"radius_admin_id": 999999, "username": "ghost", "is_super_admin": True},
    ])

    assert summary == {"changed": 2, "unchanged": 0, "not_found": 1}
    assert admins_repo.get_admin(a.id).is_super_admin is True
    assert admins_repo.get_by_username("op-two").is_super_admin is True

    # Re-applying the same decision is a no-op (idempotent).
    again = apply_super_admin_overrides([{"radius_admin_id": a.id, "is_super_admin": True}])
    assert again == {"changed": 0, "unchanged": 1, "not_found": 0}


def test_super_override_does_not_touch_password_or_identity_provider(app_db):
    from werkzeug.security import generate_password_hash

    from app.radius.db.repos import admins_repo
    from app.radius.services.license_admin_identity_sync import apply_super_admin_overrides

    admins_repo.upsert_license_admin_user(
        external_user_id=42,
        username="managed-owner",
        password_hash=generate_password_hash("Secret123!"),
        password_hash_scheme="werkzeug",
        password_version=3,
        role_key="owner",
        active=True,
    )
    before = admins_repo.get_by_username("managed-owner")

    apply_super_admin_overrides([{"username": "managed-owner", "is_super_admin": False}])

    after = admins_repo.get_by_username("managed-owner")
    assert after.is_super_admin is False
    assert after.password_hash == before.password_hash
    assert after.external_identity_provider == "license_admin"
    assert after.external_password_version == 3
    assert after.managed_by_license_admin is True


def test_identity_sync_applies_super_overrides_from_payload(app_db):
    from werkzeug.security import generate_password_hash

    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    local = admins_repo.create_admin(username="local-admin", password="x" * 10, is_super_admin=False)
    payload = {
        "ok": True,
        "customer_id": 1,
        "users": [{
            "external_user_id": 7,
            "username": "customer-admin",
            "role_key": "owner",
            "active": True,
            "password_hash": generate_password_hash("Secret123!"),
            "password_hash_scheme": "werkzeug",
            "password_version": 1,
        }],
        "admin_super_overrides": [{"radius_admin_id": local.id, "is_super_admin": True}],
    }
    store = LicenseAdminSnapshotStore()
    transport = RoutingTransport({"/identity-sync": payload})
    admin_client = AdminPanelClient(config=_config(), transport=transport, store=store)

    result = LicenseAdminIdentitySyncService(config=_config(), admin_client=admin_client, store=store).sync_once(tenant_id=1)

    assert result["ok"] is True
    assert result["super_overrides"] == {"changed": 1, "unchanged": 0, "not_found": 0}
    assert admins_repo.get_admin(local.id).is_super_admin is True


def test_admins_report_producer_sends_inventory_without_password(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_inventory_report import (
        LicenseAdminInventoryReportService,
        build_admin_inventory,
    )

    admins_repo.create_admin(username="boss", password="x" * 10, is_super_admin=True)
    inventory = build_admin_inventory()
    assert inventory and all("password_hash" not in a and "password" not in a for a in inventory)
    assert set(inventory[0].keys()) == {
        "id", "username", "role", "is_super_admin", "enabled",
        "managed_by_license_admin", "external_identity_provider",
    }

    transport = RoutingTransport({"/admins/report": {"ok": True, "status": "ok"}})
    admin_client = AdminPanelClient(config=_config(), transport=transport)
    result = LicenseAdminInventoryReportService(config=_config(), admin_client=admin_client).report_once(tenant_id=1)

    assert result["ok"] is True
    assert result["reported_count"] == len(inventory)
    body = transport.calls[0]["json_body"]
    # Bearer-in-body (post 2026-06-11 purge): license_key carries the
    # auth, signature is gone.
    assert body["license_key"]
    assert "signature" not in body
    assert isinstance(body["admins"], list)
    assert all("password_hash" not in a for a in body["admins"])


# ───────────────────────────── CHR tunnels ─────────────────────────────────

def test_tunnel_request_stores_only_fingerprint_not_raw_secret(app_db):
    from app.radius.db.repos import bridge_tunnels_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_tunnel_bridge import LicenseTunnelBridgeService

    transport = RoutingTransport({
        "/vpn/tunnels/request": {
            "ok": True, "status": "ok",
            "tunnel": {
                "name": "cust-sstp-01", "type": "sstp",
                "username": "u-sstp-01", "password": "TopSecretPass!",
                "remote_address": "vpn.example.test", "vpn_subnet": "10.8.0.0/24",
            },
        },
        "/vpn/tunnels/ack": {"ok": True, "status": "ok"},
    })
    client = AdminPanelClient(config=_config(), transport=transport)
    result = LicenseTunnelBridgeService(config=_config(), admin_client=client).request_tunnel(
        tenant_id=1, tunnel_type="sstp", label="branch-1",
    )

    assert result["ok"] is True
    # Raw credentials returned ONCE for injection.
    assert result["credentials"]["password"] == "TopSecretPass!"
    assert result["tunnel"]["acked"] is True

    row = bridge_tunnels_repo.get_by_remote_name(1, "cust-sstp-01")
    assert row is not None
    assert row["username"] == "u-sstp-01"
    assert row["source"] == "requested"
    assert row["acked"] == 1
    # No raw secret persisted — only a non-reversible fingerprint.
    assert row["secret_ref"].startswith("ref:")
    assert "TopSecretPass!" not in "".join(str(v) for v in row.values())
    assert "password" not in row  # the table has no password column at all


def test_tunnel_sync_applies_lifecycle_and_acks(app_db):
    from app.radius.db.repos import bridge_tunnels_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_tunnel_bridge import LicenseTunnelBridgeService

    # Pre-seed a tunnel that the panel will report as revoked.
    bridge_tunnels_repo.upsert_tunnel(tenant_id=1, remote_name="old-revoked", status="active")

    transport = RoutingTransport({
        "/vpn/tunnels": {
            "ok": True, "status": "ok",
            "tunnels": [
                {"name": "act-1", "type": "sstp", "status": "active",
                 "username": "u1", "password": "p1"},
                {"name": "susp-1", "type": "l2tp", "status": "suspended", "username": "u2"},
                {"name": "old-revoked", "status": "revoked"},
            ],
        },
        "/vpn/tunnels/ack": {"ok": True, "status": "ok"},
    })
    client = AdminPanelClient(config=_config(), transport=transport)
    result = LicenseTunnelBridgeService(config=_config(), admin_client=client).sync_tunnels(tenant_id=1)

    assert result["ok"] is True
    assert result["active_count"] == 1
    assert result["suspended_count"] == 1
    assert result["revoked_count"] == 1
    assert result["acked_count"] == 2  # the two non-revoked names

    # revoked → deleted locally
    assert bridge_tunnels_repo.get_by_remote_name(1, "old-revoked") is None
    # suspended → kept but disabled
    susp = bridge_tunnels_repo.get_by_remote_name(1, "susp-1")
    assert susp["status"] == "suspended" and susp["enabled"] == 0
    # active → enabled + acked
    act = bridge_tunnels_repo.get_by_remote_name(1, "act-1")
    assert act["status"] == "active" and act["enabled"] == 1 and act["acked"] == 1
    # ack call carried both stored names
    ack_call = [c for c in transport.calls if c["url"].endswith("/vpn/tunnels/ack")][0]
    assert sorted(ack_call["json_body"]["tunnel_names"]) == ["act-1", "susp-1"]


def test_tunnels_page_renders(client, app_db):
    from app.radius.db.repos import admins_repo, bridge_tunnels_repo

    bridge_tunnels_repo.upsert_tunnel(
        tenant_id=1, remote_name="render-tun", tunnel_type="sstp",
        status="active", username="ru", secret_ref="ref:abc123",
        remote_address="vpn.example.test",
    )
    admin = admins_repo.create_admin(username="viewer-admin", password="x" * 10, is_super_admin=True)
    with client.session_transaction() as sess:
        sess["admin_id"] = admin.id
        sess["admin_user"] = admin.username
        sess["admin_name"] = admin.username
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "tun-csrf"

    res = client.get("/admin/radius/tunnels")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "render-tun" in body
    assert "الأنفاق" in body
