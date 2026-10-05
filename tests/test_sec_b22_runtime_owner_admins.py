"""SEC B-22 (radius side) — the runtime-contract ``owner_admins`` must be signed.

The licence panel (branch agent/sec-fix-b22, c289390) now attaches ``_bridge_sig``
to /runtime-contract and /capacity-contract — the same HMAC-SHA256 keyed by the
customer's OWN licence key that identity-sync already uses (SEC C1). Before this
fix ``LicenseAdminRuntimeSyncService.sync_runtime_contract_once`` applied the
``owner_admins`` list unconditionally, so anything able to answer the radius's
request (rogue / repointed panel URL, MITM on a plain-http link) could name any
local admin OWNER (full RBAC bypass, every tenant on a hosting server).

Contract pinned here:
  * unsigned / foreign-key / tampered / self-signed-with-echoed-key lists are
    REFUSED — the existing designation is kept and a warning is logged;
  * a correctly signed list is applied (verified over the RAW response, so a
    payload carrying sensitive keys such as license_key / bridge_token still
    verifies even though the stored snapshot masks them);
  * the rest of the runtime contract (limits/services/status) still syncs;
  * an older panel (no signature at all) never strips the current owner.

All keys below are TEST keys.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import pytest

from app.radius.db.connection import reset_for_tests

LOCAL_KEY = "HBR-TEST-AAAA-1111"      # this radius's own (test) licence key
OTHER_KEY = "HBR-TEST-BBBB-2222"      # another customer's (test) licence key


def _panel_sign(payload: dict, license_key: str) -> dict:
    """Byte-identical copy of the panel signer
    (radius-module-admin app/license_signing.py::attach_bridge_signature)."""
    body = {k: v for k, v in payload.items() if k != "_bridge_sig"}
    msg = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    key = license_key.strip().upper().encode("utf-8")
    out = dict(payload)
    out["_bridge_sig"] = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return out


class _Transport:
    def __init__(self, response):
        self.response = response

    def request_json(self, **kwargs):
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "b22.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


def _contract(owner_admins, **extra):
    contract = {
        "license": {"active": True, "status": "active"},
        "limits": {"subscribers": {"max_total": 7}},
        "services": {},
        "owner_admins": owner_admins,
    }
    payload = {"ok": True, "status": "active", "contract": contract, **contract}
    payload.update(extra)
    return payload


def _sync(response):
    from app.radius.services.admin_panel_client import (
        AdminBridgeConfig, AdminPanelClient, LicenseAdminSnapshotStore)
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    cfg = AdminBridgeConfig(enabled=True, base_url="https://panel.test",
                            license_key=LOCAL_KEY, timeout_seconds=1.0, retry_count=0)
    store = LicenseAdminSnapshotStore()
    client = AdminPanelClient(config=cfg, store=store, transport=_Transport(response))
    svc = LicenseAdminRuntimeSyncService(config=cfg, admin_client=client, store=store)
    return svc.sync_runtime_contract_once(tenant_id=1)


def _owners():
    from app.radius.db.repos import admins_repo
    return admins_repo.designated_owner_keys()


def _seed_owner():
    from app.radius.db.repos import admins_repo
    admins_repo.set_designated_owners(["real-owner"])
    assert _owners() == {"real-owner"}


# ───────────────────────────── refused ─────────────────────────────
def test_unsigned_owner_list_is_refused_and_logged(app_db, caplog):
    _seed_owner()
    with caplog.at_level(logging.WARNING):
        result = _sync(_contract(["attacker"]))
    assert result["ok"] is True                     # rest of the contract synced
    assert result["limits"] == {"subscribers": {"max_total": 7}}
    assert result["owner_admins"] == []
    assert _owners() == {"real-owner"}              # current owner kept
    assert any("owner_admins" in r.getMessage() and "REFUSED" in r.getMessage()
               for r in caplog.records)


def test_owner_list_signed_with_another_customers_key_is_refused(app_db):
    """Cross-tenant: customer B's key cannot designate owners on A's radius."""
    _seed_owner()
    result = _sync(_panel_sign(_contract(["attacker"]), OTHER_KEY))
    assert result["owner_admins"] == []
    assert _owners() == {"real-owner"}


def test_tampered_owner_list_is_refused(app_db):
    _seed_owner()
    signed = _panel_sign(_contract(["real-owner"]), LOCAL_KEY)
    signed["owner_admins"] = ["attacker"]
    signed["contract"] = dict(signed["contract"], owner_admins=["attacker"])
    result = _sync(signed)
    assert result["owner_admins"] == []
    assert _owners() == {"real-owner"}


def test_signature_with_echoed_key_in_response_is_refused(app_db):
    """The verifier must use the LOCAL key, never the license_key in the body."""
    _seed_owner()
    forged = _panel_sign(_contract(["attacker"], license_key=OTHER_KEY), OTHER_KEY)
    result = _sync(forged)
    assert result["owner_admins"] == []
    assert _owners() == {"real-owner"}


def test_garbage_signature_is_refused(app_db):
    _seed_owner()
    payload = _contract(["attacker"])
    payload["_bridge_sig"] = "0" * 64
    assert _sync(payload)["owner_admins"] == []
    assert _owners() == {"real-owner"}


# ───────────────────────────── accepted ─────────────────────────────
def test_correctly_signed_owner_list_is_applied(app_db):
    _seed_owner()
    result = _sync(_panel_sign(_contract(["alice", "bob"]), LOCAL_KEY))
    assert result["owner_admins"] == ["alice", "bob"]
    assert _owners() == {"alice", "bob"}


def test_signed_list_with_sensitive_fields_verifies_over_raw_response(app_db):
    """license_key / bridge_token are masked in the stored snapshot; the
    signature must be checked over the raw body or a genuine panel is refused."""
    _seed_owner()
    payload = _contract(
        ["alice"], license_key=LOCAL_KEY,
        bridge_token={"token": "tok_test_0123456789abcdef", "version": 3})
    result = _sync(_panel_sign(payload, LOCAL_KEY))
    assert result["owner_admins"] == ["alice"]
    assert _owners() == {"alice"}


def test_key_case_and_whitespace_match_panel_normalisation(app_db):
    """Panel signs with key.strip().upper(); a lower-case local copy still verifies."""
    from app.radius.services.admin_panel_client import (
        AdminBridgeConfig, AdminPanelClient, LicenseAdminSnapshotStore)
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    cfg = AdminBridgeConfig(enabled=True, base_url="https://panel.test",
                            license_key="  " + LOCAL_KEY.lower() + " ",
                            timeout_seconds=1.0, retry_count=0)
    store = LicenseAdminSnapshotStore()
    client = AdminPanelClient(config=cfg, store=store,
                              transport=_Transport(_panel_sign(_contract(["carol"]), LOCAL_KEY)))
    res = LicenseAdminRuntimeSyncService(config=cfg, admin_client=client,
                                         store=store).sync_runtime_contract_once()
    assert res["owner_admins"] == ["carol"]


# ───────────────────────── rollout compatibility ─────────────────────────
def test_old_panel_without_owner_list_or_signature_keeps_owner(app_db):
    _seed_owner()
    payload = _contract(None)
    payload.pop("owner_admins")
    payload["contract"].pop("owner_admins")
    result = _sync(payload)
    assert result["ok"] is True
    assert result["owner_admins"] == []
    assert _owners() == {"real-owner"}


def test_signed_empty_list_never_strips(app_db):
    _seed_owner()
    assert _sync(_panel_sign(_contract([]), LOCAL_KEY))["owner_admins"] == []
    assert _owners() == {"real-owner"}


def test_no_local_key_refuses(app_db):
    """No local licence key → nothing can be verified → refuse."""
    from app.radius.services.admin_panel_client import (
        AdminBridgeConfig, AdminPanelClient, LicenseAdminSnapshotStore)
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    _seed_owner()
    cfg = AdminBridgeConfig(enabled=True, base_url="https://panel.test",
                            license_key="", timeout_seconds=1.0, retry_count=0)
    store = LicenseAdminSnapshotStore()
    payload = _contract(["attacker"])
    client = AdminPanelClient(config=cfg, store=store, transport=_Transport(payload))
    res = LicenseAdminRuntimeSyncService(config=cfg, admin_client=client,
                                         store=store).sync_runtime_contract_once()
    assert res.get("owner_admins", []) == []
    assert _owners() == {"real-owner"}
