"""admins-report v2 contract — radius module (producer + consumer side).

Pins the four contract fixtures the licensing panel side depends on:

1. A full snapshot always includes the PRIMARY/local admin and only carries
   non-secret identity fields (no password hash).
2. Identity-sync response with ``request_admin_report: true`` fires an
   immediate admins-report — no waiting for the periodic worker tick.
3. Deleting a local admin then reporting a full snapshot removes it from the
   panel via absence (main fix for «deleted-still-shows»).
4. A single tombstone report ``{"id": N, "deleted": true}`` deletes exactly
   one admin from the panel while keeping the primary admin visible.

All bridge I/O is mocked; no network is touched.
"""
from __future__ import annotations

import os

import pytest


_LICENSE_KEY = "HBR-2026-VVVV-WWWW-XXXX"


class RoutingTransport:
    """Mock transport that answers by URL suffix and records every call."""

    def __init__(self, routes: dict[str, dict] | None = None):
        self.routes = routes or {}
        self.calls: list[dict] = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        url = kwargs.get("url") or ""
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return {"ok": False, "status": "unexpected"}

    def bodies_for(self, suffix: str) -> list[dict]:
        return [
            c["json_body"] for c in self.calls
            if (c.get("url") or "").endswith(suffix)
        ]


def _reset(db_file):
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "admins_report_v2.db")
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


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://panel.example.test",
        license_key=_LICENSE_KEY,
        timeout_seconds=5.0,
        retry_count=1,
    )


# ─── 1) Full snapshot: primary admin present, no secrets, right shape ─────


def test_full_snapshot_includes_primary_admin_and_carries_no_secrets(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_inventory_report import (
        LicenseAdminInventoryReportService, build_admin_inventory,
    )

    # create_app() bootstraps a min-id `admin` account — the actual PRIMARY.
    # Add a second admin so we can prove non-primary rows too.
    primary_id = admins_repo.primary_admin_id()
    assert primary_id is not None
    ops = admins_repo.create_admin(username="ops", password="Password2!")

    inventory = build_admin_inventory()
    inv_ids = [row["id"] for row in inventory]
    # Both the bootstrap primary and the new ops admin are reported.
    assert primary_id in inv_ids
    assert ops.id in inv_ids
    # Row shape — the exact keys the contract advertises.
    assert set(inventory[0].keys()) == {
        "id", "username", "role", "is_super_admin", "is_primary",
        "enabled", "managed_by_license_admin", "external_identity_provider",
    }
    # is_primary flag is True EXACTLY on the primary row.
    by_id = {row["id"]: row for row in inventory}
    assert by_id[primary_id]["is_primary"] is True
    assert by_id[ops.id]["is_primary"] is False

    # No password material in ANY row.
    for row in inventory:
        for banned in ("password", "password_hash", "password_hash_scheme"):
            assert banned not in row, f"leaked {banned} in {row}"

    transport = RoutingTransport(
        {"/admins/report": {"ok": True, "status": "ok"}})
    result = LicenseAdminInventoryReportService(
        config=_config(),
        admin_client=AdminPanelClient(config=_config(), transport=transport),
    ).report_once(tenant_id=1)
    assert result["ok"] is True
    assert result["reported_count"] == len(inventory)
    assert result["full_snapshot"] is True

    body = transport.bodies_for("/admins/report")[0]
    # v2 envelope carries full_snapshot=true so the panel can prune by absence.
    assert body["full_snapshot"] is True
    # Panel sees the primary row.
    ids_in_body = [a["id"] for a in body["admins"]]
    assert primary_id in ids_in_body
    # No password material at the wire level either.
    for row in body["admins"]:
        assert "password" not in row and "password_hash" not in row


def test_empty_admins_report_is_refused_at_the_producer(app_db):
    """An accidental full snapshot with an empty admin list would delete every
    admin from the panel. The producer must refuse BEFORE sending."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = RoutingTransport(
        {"/admins/report": {"ok": True, "status": "ok"}})
    client = AdminPanelClient(config=_config(), transport=transport)
    result = client.post_admins_report(admins=[], full_snapshot=True)
    assert result["ok"] is False
    assert result["status"] == "empty_admins"
    # Nothing hit the wire.
    assert transport.calls == []


# ─── 2) request_admin_report on identity-sync triggers a fresh report ─────


def _sign_payload(payload: dict, key: str = _LICENSE_KEY) -> dict:
    import hashlib, hmac, json
    body = {k: v for k, v in payload.items() if k != "_bridge_sig"}
    msg = json.dumps(body, ensure_ascii=False,
                     separators=(",", ":"), sort_keys=True)
    payload["_bridge_sig"] = hmac.new(
        key.strip().upper().encode("utf-8"),
        msg.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return payload


def test_request_admin_report_flag_fires_immediate_report(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_identity_sync import (
        LicenseAdminIdentitySyncService,
    )

    admins_repo.create_admin(
        username="owner", password="Password1!", is_super_admin=True)

    # The panel responds to identity-sync with request_admin_report=true.
    identity_payload = _sign_payload({
        "ok": True,
        "customer_id": "cust-abc",
        "license_key": _LICENSE_KEY,
        "users": [],
        "request_admin_report": True,
    })
    # The mock transport returns the identity payload DIRECTLY (as the panel
    # would) — the client wraps it into {ok, payload, ...} internally.
    transport = RoutingTransport({
        "/identity-sync": identity_payload,
        "/admins/report": {"ok": True, "status": "ok"},
    })
    result = LicenseAdminIdentitySyncService(
        config=_config(),
        admin_client=AdminPanelClient(config=_config(), transport=transport),
    ).sync_once(tenant_id=1)

    assert result["ok"] is True
    # A report call landed as a direct consequence of the flag.
    report_bodies = transport.bodies_for("/admins/report")
    assert len(report_bodies) == 1, (
        "identity-sync with request_admin_report=true should fire exactly "
        "one admins-report immediately"
    )
    assert report_bodies[0]["full_snapshot"] is True
    assert "admin_report_result" in result
    assert result["admin_report_result"]["ok"] is True


def test_identity_sync_without_flag_does_not_fire_admin_report(app_db):
    """Backwards-compat: older panels don't set the flag → no report fires
    from the identity-sync path (the periodic worker still handles it)."""
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_identity_sync import (
        LicenseAdminIdentitySyncService,
    )

    admins_repo.create_admin(
        username="owner", password="Password1!", is_super_admin=True)
    identity_payload = _sign_payload({
        "ok": True, "customer_id": "cust-abc",
        "license_key": _LICENSE_KEY, "users": [],
    })
    # The mock transport returns the identity payload DIRECTLY (as the panel
    # would) — the client wraps it into {ok, payload, ...} internally.
    transport = RoutingTransport({
        "/identity-sync": identity_payload,
        "/admins/report": {"ok": True, "status": "ok"},
    })
    result = LicenseAdminIdentitySyncService(
        config=_config(),
        admin_client=AdminPanelClient(config=_config(), transport=transport),
    ).sync_once(tenant_id=1)

    assert result["ok"] is True
    assert transport.bodies_for("/admins/report") == []
    assert "admin_report_result" not in result


# ─── 3) Delete then full-snapshot removes the admin via absence ─────


def test_full_snapshot_after_delete_removes_admin_via_absence(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_inventory_report import (
        LicenseAdminInventoryReportService,
    )

    primary = admins_repo.create_admin(
        username="owner", password="Password1!", is_super_admin=True)
    doomed = admins_repo.create_admin(username="ops", password="Password2!")

    transport = RoutingTransport(
        {"/admins/report": {"ok": True, "status": "ok"}})
    svc = LicenseAdminInventoryReportService(
        config=_config(),
        admin_client=AdminPanelClient(config=_config(), transport=transport),
    )

    # First report: both admins present.
    svc.report_once(tenant_id=1)
    first = transport.bodies_for("/admins/report")[0]
    ids_first = [a["id"] for a in first["admins"]]
    assert primary.id in ids_first and doomed.id in ids_first
    assert first["full_snapshot"] is True

    # Archive (soft-delete) the second one, then re-report.
    admins_repo.archive_admin(doomed.id, actor="tests")
    svc.report_once(tenant_id=1)
    second = transport.bodies_for("/admins/report")[1]
    ids_second = [a["id"] for a in second["admins"]]
    # The primary is still there (invariant), the deleted admin is absent —
    # the panel prunes it thanks to full_snapshot=true.
    assert primary.id in ids_second
    assert doomed.id not in ids_second
    assert second["full_snapshot"] is True


# ─── 4) Tombstone deletes exactly one admin ────────────────────


def test_tombstone_report_deletes_only_the_named_admin(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_inventory_report import (
        LicenseAdminInventoryReportService,
    )

    # The bootstrap `admin` is the min-id primary.
    primary_id = admins_repo.primary_admin_id()
    assert primary_id is not None
    admins_repo.create_admin(username="ops", password="Password2!")
    doomed = admins_repo.create_admin(username="ex", password="Password3!")

    transport = RoutingTransport(
        {"/admins/report": {"ok": True, "status": "ok"}})
    svc = LicenseAdminInventoryReportService(
        config=_config(),
        admin_client=AdminPanelClient(config=_config(), transport=transport),
    )

    result = svc.report_tombstone(deleted_admin_id=doomed.id, tenant_id=1)
    assert result["ok"] is True
    assert result["full_snapshot"] is False
    assert result["tombstone"] == doomed.id

    body = transport.bodies_for("/admins/report")[0]
    assert body["full_snapshot"] is False
    # Body contains the primary row + the tombstone — nothing else.
    ids = [a["id"] for a in body["admins"]]
    assert primary_id in ids
    assert doomed.id in ids
    tombstone = next(a for a in body["admins"] if a["id"] == doomed.id)
    assert tombstone.get("deleted") is True
    # And critically: the tombstone body carries NO extra identity fields
    # about the deleted admin (it's a signal, not a re-broadcast).
    assert "username" not in tombstone
    assert "password_hash" not in tombstone
