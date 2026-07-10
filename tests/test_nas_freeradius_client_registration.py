"""Regression: a router added through the plain "Devices" page must
be registered as a FreeRADIUS client (source IP + shared secret),
otherwise FreeRADIUS silently discards its packets (RFC 2865) and
RouterOS reports «الرديوس لا يستجيب».

Before the fix, `NasDevicesService.create` → `sqlite_adapter.upsert_nas`
→ `nas_repo.upsert_nas` wrote ONLY the `nas_devices` inventory row.
`freeradius_translator.sync_nas` (the only writer of the FreeRADIUS
`nas`/client entries) was reachable only from the emergency
`resync_all`. So a manually-added router could never authenticate.

These tests pin:
  * write/remove of the per-NAS client file (nas-<id>.conf),
  * the duplicate-ipaddr collision guard vs the wizard's files,
  * the end-to-end adapter path (upsert → client file appears,
    disable/delete → client file gone),
  * the SQL `nas` client row is written too (restart durability).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "nas-fr-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv(
        "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
        str(tmp_path / "clients-wizard"),
    )
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


@pytest.fixture
def clients_dir(tmp_path) -> Path:
    return tmp_path / "clients-wizard"


# ── unit: write / remove the per-NAS client file ────────────────

def test_write_client_for_nas_creates_file_and_trigger(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    res = prov.write_client_for_nas(
        nas_id=7, ipaddr="10.10.0.7", secret="s3cr3t-xyz",
        shortname="branch-7",
    )
    assert res["status"] == "written"
    f = clients_dir / "nas-7.conf"
    assert f.exists()
    body = f.read_text(encoding="utf-8")
    assert "client nas-7 {" in body
    assert "ipaddr      = 10.10.0.7" in body
    assert "secret      = s3cr3t-xyz" in body
    assert "require_message_authenticator = no" in body
    assert (clients_dir / ".reload-trigger").exists()


def test_remove_client_for_nas(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    prov.write_client_for_nas(nas_id=9, ipaddr="10.10.0.9", secret="abc")
    assert (clients_dir / "nas-9.conf").exists()
    res = prov.remove_client_for_nas(nas_id=9)
    assert res["status"] == "removed"
    assert not (clients_dir / "nas-9.conf").exists()
    # removing again is a no-op, not an error
    assert prov.remove_client_for_nas(nas_id=9)["status"] == "absent"


def test_unsafe_secret_rejected(app):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    with pytest.raises(prov.FreeRadiusProvisioningError):
        prov.write_client_for_nas(
            nas_id=1, ipaddr="10.10.0.1", secret='bad"secret',
        )


def test_require_message_authenticator_honored(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    prov.write_client_for_nas(
        nas_id=3, ipaddr="10.10.0.3", secret="k",
        require_message_authenticator=True,
    )
    body = (clients_dir / "nas-3.conf").read_text(encoding="utf-8")
    assert "require_message_authenticator = yes" in body


# ── collision guard vs wizard files ─────────────────────────────

def test_skips_when_wizard_owns_the_ip_same_secret(app, clients_dir):
    """No two client blocks may share an ipaddr — FreeRADIUS
    crash-loops. If the wizard already registered this IP with the
    SAME secret (normal v3 flow), the manual path must NOT write a
    competing nas-*.conf — it defers to the wizard file."""
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    # Wizard owns 10.10.0.5 with a secret the nas row also carries.
    prov.write_client_for_run(
        run_id=42, router_vpn_ip="10.10.0.5", radius_secret="samesecret",
    )
    res = prov.write_client_for_nas(
        nas_id=5, ipaddr="10.10.0.5", secret="samesecret",
    )
    assert res["status"] == "skipped_wizard_owns_ip"
    assert res["wizard_file"] == "wizard-run-42.conf"
    assert not (clients_dir / "nas-5.conf").exists()
    # the wizard file is untouched
    assert (clients_dir / "wizard-run-42.conf").exists()


def test_nas_wins_when_wizard_owns_ip_with_different_secret(app, clients_dir):
    """Root-cause fix for the 'first customer' secret mismatch: a
    stale/abandoned wizard-run file shadows a finalized nas_devices row
    with a DIFFERENT secret, so FreeRADIUS validates against the wrong
    secret ('Shared secret is incorrect'). The nas row is authoritative
    — write_client_for_nas removes the stale wizard file and writes its
    own client so there is exactly ONE block, with the router's secret."""
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    prov.write_client_for_run(
        run_id=42, router_vpn_ip="10.10.0.5", radius_secret="stale-wizard",
    )
    res = prov.write_client_for_nas(
        nas_id=5, ipaddr="10.10.0.5", secret="real-router-secret",
    )
    assert res["status"] == "written"
    # stale wizard file removed → no duplicate ipaddr, no shadowing
    assert not (clients_dir / "wizard-run-42.conf").exists()
    nas_file = clients_dir / "nas-5.conf"
    assert nas_file.exists()
    assert "secret      = real-router-secret" in nas_file.read_text()


def test_purges_stale_nas_file_on_same_ip(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    prov.write_client_for_nas(nas_id=1, ipaddr="10.10.0.20", secret="a")
    # A different NAS row claims the same source IP → old one is stale.
    prov.write_client_for_nas(nas_id=2, ipaddr="10.10.0.20", secret="b")
    assert not (clients_dir / "nas-1.conf").exists()
    assert (clients_dir / "nas-2.conf").exists()


# ── reconciler must ignore nas-*.conf (only owns wizard-run-*) ──

def test_reconciler_does_not_delete_nas_files(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    prov.write_client_for_nas(nas_id=11, ipaddr="10.10.0.11", secret="z")
    # No active wizard runs at all; reconcile would delete orphan
    # wizard files but must leave nas-*.conf alone.
    prov.reconcile_with_state(tenant_id=None)
    assert (clients_dir / "nas-11.conf").exists()


# ── end-to-end: the manual "Devices" adapter path ───────────────

def _mk_device(**over):
    from app.radius.core.types import NasDevice
    base = dict(
        id=None, name="Branch Router", address="10.10.0.30",
        secret="router-secret-1", vendor="mikrotik",
        nas_type="hotspot", enabled=True,
    )
    base.update(over)
    return NasDevice(**base)


def test_adapter_upsert_registers_freeradius_client(app, clients_dir):
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    from app.radius.db.repos import freeradius_repo
    with app.app_context():
        adapter = SqliteAdapter()
        saved = adapter.upsert_nas(_mk_device())
        # (1) client file dropped for live pickup
        f = clients_dir / f"nas-{saved.id}.conf"
        assert f.exists(), "manual add must register a FreeRADIUS client file"
        body = f.read_text(encoding="utf-8")
        assert "ipaddr      = 10.10.0.30" in body
        assert "secret      = router-secret-1" in body
        # (2) files are the SINGLE source of truth — we must NOT also
        # write the SQL `nas` table, else FreeRADIUS aborts on a
        # duplicate client (same ipaddr from file + SQL) at reload.
        clients = freeradius_repo.list_all_nas_clients()
        assert not any(
            c["nasname"] == "10.10.0.30" for c in clients
        ), "manual add must NOT write a duplicate SQL nas client row"


def test_adapter_disable_revokes_client_file(app, clients_dir):
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    with app.app_context():
        adapter = SqliteAdapter()
        saved = adapter.upsert_nas(_mk_device())
        f = clients_dir / f"nas-{saved.id}.conf"
        assert f.exists()
        # disable → client file must disappear
        from dataclasses import replace
        adapter.upsert_nas(replace(saved, enabled=False))
        assert not f.exists(), "disabling a NAS must revoke its client file"


def test_adapter_delete_revokes_client_file(app, clients_dir):
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    with app.app_context():
        adapter = SqliteAdapter()
        saved = adapter.upsert_nas(_mk_device())
        f = clients_dir / f"nas-{saved.id}.conf"
        assert f.exists()
        adapter.delete_nas(saved.id)
        assert not f.exists(), "deleting a NAS must revoke its client file"


# ── self-healing reconcile of nas-*.conf ────────────────────────

def test_reconcile_heals_missing_client_file(app, clients_dir):
    """A router whose row exists but whose client file was lost
    (manual delete / backup restore) must get it back within one
    reconcile — else it silently can't authenticate."""
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    with app.app_context():
        adapter = SqliteAdapter()
        saved = adapter.upsert_nas(_mk_device())
        f = clients_dir / f"nas-{saved.id}.conf"
        assert f.exists()
        f.unlink()  # simulate a lost file
        res = prov.reconcile_nas_client_files(tenant_id=None)
        assert f"nas-{saved.id}.conf" in res["rewritten"]
        assert f.exists()


def test_reconcile_is_idempotent_no_reload_storm(app, clients_dir):
    """A clean reconcile must not rewrite files or bump the reload
    trigger — otherwise FreeRADIUS would restart every interval."""
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    with app.app_context():
        adapter = SqliteAdapter()
        adapter.upsert_nas(_mk_device())
        # First reconcile converges everything (incl. any seeded
        # rows). The SECOND must be a clean no-op.
        prov.reconcile_nas_client_files(tenant_id=None)
        trigger = clients_dir / ".reload-trigger"
        before = trigger.stat().st_mtime if trigger.exists() else 0
        res = prov.reconcile_nas_client_files(tenant_id=None)
        assert res["rewritten"] == [] and res["deleted"] == []
        after = trigger.stat().st_mtime if trigger.exists() else 0
        assert before == after, "clean reconcile must not touch reload trigger"


def test_client_keyed_on_sstp_tunnel_ip_not_public_address(app, clients_dir):
    """ROOT CAUSE: an SSTP/accel router sources RADIUS from its
    tunnel IP (management_remote_address, e.g. 10.50.0.x), not its
    public address. The FreeRADIUS client MUST be registered on the
    tunnel IP — else source-IP != client-IP → silent drop."""
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    from app.radius.db.connection import db, transaction
    with app.app_context():
        adapter = SqliteAdapter()
        # public/LAN address on the row (what the operator typed)
        saved = adapter.upsert_nas(_mk_device(address="192.168.88.1"))
        # provisioning sets the SSTP tunnel IP columns (raw, like mt_setup)
        with transaction() as c:
            c.execute(
                "UPDATE nas_devices SET management_remote_address=?, "
                "vpn_peer_address=? WHERE id=?",
                ("10.50.0.7", "10.50.0.7", saved.id),
            )
        # re-register (as mt_setup / mt_sstp_sync now do)
        from app.radius.services import freeradius_translator
        from app.radius.db.repos import nas_repo
        freeradius_translator.sync_nas(nas_repo.get_nas(1, saved.id))
        body = (clients_dir / f"nas-{saved.id}.conf").read_text(encoding="utf-8")
        assert "ipaddr      = 10.50.0.7" in body, "must key on the tunnel IP"
        assert "192.168.88.1" not in body, "must NOT key on the public address"


def test_reconcile_uses_tunnel_ip_for_sstp(app, clients_dir):
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    from app.radius.db.connection import transaction
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    with app.app_context():
        adapter = SqliteAdapter()
        saved = adapter.upsert_nas(_mk_device(address="203.0.113.5"))
        with transaction() as c:
            c.execute(
                "UPDATE nas_devices SET management_remote_address=? WHERE id=?",
                ("10.50.0.9", saved.id),
            )
        (clients_dir / f"nas-{saved.id}.conf").unlink()  # lose the file
        prov.reconcile_nas_client_files(tenant_id=None)
        body = (clients_dir / f"nas-{saved.id}.conf").read_text(encoding="utf-8")
        assert "ipaddr      = 10.50.0.9" in body


def test_reconcile_deletes_orphan_nas_file(app, clients_dir):
    from app.radius.services import (
        setup_wizard_v3_radius_server_provisioning as prov,
    )
    with app.app_context():
        # file for a nas_devices row that doesn't exist
        prov.write_client_for_nas(nas_id=999, ipaddr="10.10.0.99", secret="x")
        assert (clients_dir / "nas-999.conf").exists()
        res = prov.reconcile_nas_client_files(tenant_id=None)
        assert "nas-999.conf" in res["deleted"]
        assert not (clients_dir / "nas-999.conf").exists()
