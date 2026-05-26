"""Emergency Fleet Reset tests.

Cover the confirmation gate, row counts, multi-table delete,
peers.d file cleanup, and the audit-log write.
"""
from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-reset-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", str(tmp_path / "peers.d"))
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def _seed_fleet(now: str = "2026-05-26T00:00:00Z") -> dict[str, int]:
    """Seed a few rows into every wizard-fleet table so the
    reset has something to delete."""
    conn = db()
    # router_provisioning_registry
    conn.execute(
        """INSERT INTO router_provisioning_registry
        (tenant_id, wizard_run_id, router_label, status,
         vpn_pool_cidr, router_vpn_ip, server_vpn_ip,
         wireguard_peer_name, allocation_index,
         created_at, updated_at)
        VALUES (1, 100, 'r1', 'reserved',
                '10.10.0.0/24', '10.10.0.5', '10.10.0.1',
                'hr-peer-1', 5, ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO router_ip_allocations
        (tenant_id, registry_id, pool_name, ip_address,
         allocation_type, status, created_at)
        VALUES (1, 1, '10.10.0.0/24', '10.10.0.5',
                'router_vpn', 'active', ?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO setup_wizard_runs
        (tenant_id, status, created_at, updated_at,
         current_step, last_error,
         verification_status_json, created_by)
        VALUES (1, 'active', ?, ?, 'internet', '', '{}', 'qa')""",
        (now, now),
    )
    conn.commit()
    return {"runs": 1, "registry": 1, "allocations": 1}


def test_preview_counts_show_seeded_rows(app):
    """Preview must show non-zero counts after seeding."""
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        preview = SetupWizardFleetEmergencyReset().preview(tenant_id=1)
    assert preview["total_rows"] >= 3
    assert preview["row_counts"]["router_provisioning_registry"] == 1
    assert preview["row_counts"]["router_ip_allocations"] == 1
    assert preview["row_counts"]["setup_wizard_runs"] == 1
    assert preview["confirm_phrase"] == "RESET-WIZARD-FLEET"


def test_reset_without_confirm_raises(app):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        FleetResetConfirmationError,
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        with pytest.raises(FleetResetConfirmationError):
            SetupWizardFleetEmergencyReset().reset(
                tenant_id=1, confirm="", actor="qa",
            )


def test_reset_with_wrong_phrase_raises(app):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        FleetResetConfirmationError,
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        with pytest.raises(FleetResetConfirmationError):
            SetupWizardFleetEmergencyReset().reset(
                tenant_id=1, confirm="reset", actor="qa",
            )


def test_reset_with_correct_phrase_wipes_all_tables(app):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        svc = SetupWizardFleetEmergencyReset()
        result = svc.reset(
            tenant_id=1,
            confirm="RESET-WIZARD-FLEET",
            actor="qa",
        )
        # After reset, every count should be zero.
        post = svc.preview(tenant_id=1)
    assert result["deleted"]["router_provisioning_registry"] == 1
    assert result["deleted"]["router_ip_allocations"] == 1
    assert result["deleted"]["setup_wizard_runs"] == 1
    assert result["total_deleted"] >= 3
    assert post["total_rows"] == 0


def test_reset_is_tenant_scoped(app):
    """Tenant 1's reset must not touch tenant 2's rows."""
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        # Seed a tenant-2 row.
        db().execute(
            """INSERT INTO router_provisioning_registry
            (tenant_id, wizard_run_id, router_label, status,
             vpn_pool_cidr, router_vpn_ip, server_vpn_ip,
             wireguard_peer_name, allocation_index,
             created_at, updated_at)
            VALUES (2, 200, 'r-other', 'reserved',
                    '10.20.0.0/24', '10.20.0.5', '10.20.0.1',
                    'hr-peer-other', 5,
                    '2026-05-26T00:00:00Z', '2026-05-26T00:00:00Z')"""
        )
        db().commit()
        SetupWizardFleetEmergencyReset().reset(
            tenant_id=1,
            confirm="RESET-WIZARD-FLEET",
            actor="qa",
        )
        row = db().execute(
            "SELECT COUNT(*) AS c FROM router_provisioning_registry "
            "WHERE tenant_id=2"
        ).fetchone()
    assert int(row["c"]) == 1


def test_reset_removes_hoberadius_peer_files(app, tmp_path):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    peers_dir = tmp_path / "peers.d"
    peers_dir.mkdir()
    hr_peer = peers_dir / "hr-peer-1.conf"
    hr_router = peers_dir / "hr-router-5.conf"
    foreign = peers_dir / "vps-admin.conf"  # must NOT be removed
    hr_peer.write_text("[Peer]\nPublicKey=...\n")
    hr_router.write_text("[Peer]\nPublicKey=...\n")
    foreign.write_text("[Peer]\nPublicKey=foreign\n")

    with app.app_context():
        _seed_fleet()
        svc = SetupWizardFleetEmergencyReset(peers_dir=str(peers_dir))
        result = svc.reset(
            tenant_id=1,
            confirm="RESET-WIZARD-FLEET",
            actor="qa",
            clear_peer_files=True,
        )

    assert not hr_peer.exists()
    assert not hr_router.exists()
    assert foreign.exists(), "foreign peer file must NOT be touched"
    assert len(result["peer_files_removed"]) == 2


def test_reset_skips_peer_files_when_flag_false(app, tmp_path):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    peers_dir = tmp_path / "peers.d"
    peers_dir.mkdir()
    hr_peer = peers_dir / "hr-peer-1.conf"
    hr_peer.write_text("[Peer]\n")

    with app.app_context():
        _seed_fleet()
        svc = SetupWizardFleetEmergencyReset(peers_dir=str(peers_dir))
        result = svc.reset(
            tenant_id=1,
            confirm="RESET-WIZARD-FLEET",
            actor="qa",
            clear_peer_files=False,
        )

    assert hr_peer.exists()
    assert result["peer_files_removed"] == []


def test_preview_with_empty_db_returns_zero(app):
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        preview = SetupWizardFleetEmergencyReset().preview(tenant_id=1)
    assert preview["total_rows"] == 0
    assert all(c == 0 for c in preview["row_counts"].values())


def test_audit_log_records_reset(app):
    """Every reset writes a critical-severity audit row so an
    operator can trace who wiped the fleet and when."""
    from app.radius.services.setup_wizard_fleet_emergency_reset import (
        SetupWizardFleetEmergencyReset,
    )
    with app.app_context():
        _seed_fleet()
        SetupWizardFleetEmergencyReset().reset(
            tenant_id=1,
            confirm="RESET-WIZARD-FLEET",
            actor="qa_test",
        )
        row = db().execute(
            "SELECT action, severity FROM audit_log "
            "WHERE action='setup_wizard_fleet_reset' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["action"] == "setup_wizard_fleet_reset"
    assert row["severity"] == "critical"
