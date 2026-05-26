"""Postmortem #20 — invariants for the wizard's FreeRADIUS
client-file provisioning. Pins the three guarantees:

  INV-1: every active run has a matching wizard-run-<id>.conf
         with state_json's secret.
  INV-2: every wizard-run-<id>.conf belongs to an active run.
  INV-3: at most one file per ipaddr.

Run with the test app context so DB queries work.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-recon-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv(
        "HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"),
    )
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv(
        "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
        str(tmp_path / "clients-wizard"),
    )
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def _seed_run(
    *,
    run_id: int,
    vpn_ip: str,
    secret: str,
    state: str = "REGISTERING",
    tenant_id: int = 1,
) -> None:
    state_json = json.dumps({
        "router_vpn_ip": vpn_ip,
        "radius_secret": secret,
        "router_name": f"router-{run_id}",
    }, ensure_ascii=False)
    db().execute(
        """INSERT OR REPLACE INTO setup_wizard_runs
           (id, tenant_id, status, created_at, updated_at,
            current_step, last_error,
            verification_status_json,
            v3_state, state_json, v3_diagnostics_json)
           VALUES (?, ?, 'active', '2026-01-01', '2026-01-01',
                   'collecting', '', '{}', ?, ?, '[]')""",
        (int(run_id), int(tenant_id), state, state_json),
    )
    db().commit()


def _files_in_dir(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


# ─── INV-1: missing file gets rewritten ────────────────────


def test_reconciler_writes_missing_file_for_active_run(app, tmp_path):
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state,
    )
    target = tmp_path / "clients-wizard"
    with app.app_context():
        _seed_run(run_id=101, vpn_ip="10.10.0.5",
                  secret="a" * 32)
        result = reconcile_with_state(tenant_id=1)
    assert "wizard-run-101.conf" in result["rewritten"]
    assert "wizard-run-101.conf" in _files_in_dir(target)


# ─── INV-1: wrong-secret file gets rewritten ───────────────


def test_reconciler_rewrites_file_with_drifted_secret(app, tmp_path):
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state, write_client_for_run,
    )
    target = tmp_path / "clients-wizard"
    with app.app_context():
        # Seed run with correct secret in DB.
        _seed_run(run_id=102, vpn_ip="10.10.0.6",
                  secret="correct" + "0" * 24)
        # Write a file with WRONG secret on disk.
        write_client_for_run(
            run_id=102,
            router_vpn_ip="10.10.0.6",
            radius_secret="wrong" + "0" * 26,
        )
        result = reconcile_with_state(tenant_id=1)
        # File should be rewritten with the correct secret.
        text = (target / "wizard-run-102.conf").read_text()
    assert "wizard-run-102.conf" in result["rewritten"]
    assert "correct" + "0" * 24 in text
    assert "wrong" + "0" * 26 not in text


# ─── INV-2: orphan file (no matching active run) deleted ──


def test_reconciler_deletes_orphan_file(app, tmp_path):
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state, write_client_for_run,
    )
    target = tmp_path / "clients-wizard"
    with app.app_context():
        # No DB row for run 999, but a file exists.
        write_client_for_run(
            run_id=999,
            router_vpn_ip="10.10.0.99",
            radius_secret="orphan-" + "x" * 24,
        )
        assert (target / "wizard-run-999.conf").exists()
        result = reconcile_with_state(tenant_id=1)
    assert "wizard-run-999.conf" in result["deleted"]
    assert not (target / "wizard-run-999.conf").exists()


# ─── INV-3: duplicate ipaddr files get deduplicated ───────


def test_reconciler_dedupes_files_sharing_ipaddr(app, tmp_path):
    """If two files claim the same ipaddr, the reconciler
    keeps only the newest (highest run_id)."""
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state,
    )
    target = tmp_path / "clients-wizard"
    target.mkdir(parents=True, exist_ok=True)
    # Manually write two files with same ipaddr but different
    # run IDs. The reconciler should keep the newest.
    (target / "wizard-run-201.conf").write_text(
        "client router-201 {\n"
        "    ipaddr      = 10.10.0.7\n"
        "    secret      = oldold" + "0" * 26 + "\n"
        "    require_message_authenticator = no\n"
        "    nas_type    = mikrotik\n"
        "    shortname   = old\n"
        "}\n",
        encoding="utf-8",
    )
    (target / "wizard-run-202.conf").write_text(
        "client router-202 {\n"
        "    ipaddr      = 10.10.0.7\n"
        "    secret      = newnew" + "0" * 26 + "\n"
        "    require_message_authenticator = no\n"
        "    nas_type    = mikrotik\n"
        "    shortname   = new\n"
        "}\n",
        encoding="utf-8",
    )
    with app.app_context():
        # Seed only run 202 as active, so 201 also becomes an
        # orphan and gets deleted via INV-2 first.
        _seed_run(run_id=202, vpn_ip="10.10.0.7",
                  secret="newnew" + "0" * 26)
        result = reconcile_with_state(tenant_id=1)
    # 201 deleted (orphan); 202 stays.
    assert "wizard-run-201.conf" in result["deleted"]
    assert (target / "wizard-run-202.conf").exists()


# ─── Stale-ip cleanup on write ─────────────────────────────


def test_write_client_purges_stale_file_for_same_ip(app, tmp_path):
    """Re-running the wizard for the same router (new run_id,
    same vpn_ip) must remove the old run's file so two files
    don't claim the same ipaddr at any moment."""
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        write_client_for_run,
    )
    target = tmp_path / "clients-wizard"
    with app.app_context():
        write_client_for_run(
            run_id=301,
            router_vpn_ip="10.10.0.8",
            radius_secret="first" + "0" * 27,
        )
        assert (target / "wizard-run-301.conf").exists()
        # New run, same ipaddr.
        write_client_for_run(
            run_id=302,
            router_vpn_ip="10.10.0.8",
            radius_secret="second" + "0" * 26,
        )
    assert not (target / "wizard-run-301.conf").exists(), (
        "stale file for run 301 should have been purged "
        "when run 302 took over 10.10.0.8"
    )
    assert (target / "wizard-run-302.conf").exists()


# ─── No double-trigger if everything's clean ──────────────


def test_reconciler_quiet_when_everything_matches(app, tmp_path):
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state, write_client_for_run,
    )
    with app.app_context():
        _seed_run(run_id=401, vpn_ip="10.10.0.9",
                  secret="matching" + "0" * 23)
        write_client_for_run(
            run_id=401,
            router_vpn_ip="10.10.0.9",
            radius_secret="matching" + "0" * 23,
        )
        result = reconcile_with_state(tenant_id=1)
    assert result["rewritten"] == []
    assert result["deleted"] == []
    assert result["deduped"] == []
    assert "wizard-run-401.conf" in result["ok"]
