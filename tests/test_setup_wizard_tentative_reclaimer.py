"""SetupWizardTentativeReclaimer tests."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-ttl-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", str(tmp_path / "peers.d"))
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


_next_alloc_idx = [10]
_next_run_id = [100]


def _alloc_idx() -> int:
    _next_alloc_idx[0] += 1
    return _next_alloc_idx[0]


def _next_run() -> int:
    _next_run_id[0] += 1
    return _next_run_id[0]


def _seed_router(
    *,
    tenant_id: int = 1,
    registry_id: int | None = None,
    label: str = "r1",
    ip: str = "10.10.0.5",
    peer_name: str = "hr-peer-1",
    lifecycle: str = "waiting_router_key",
    expires_in_minutes: int | None = -10,  # -10 = already expired
    started_offset_minutes: int = -20,
    allocation_index: int | None = None,
) -> int:
    """Insert a registry row + matching IP allocation. Returns
    the registry id."""
    now = datetime.utcnow()
    started = now + timedelta(minutes=started_offset_minutes)
    expires = (
        now + timedelta(minutes=expires_in_minutes)
        if expires_in_minutes is not None
        else None
    )
    conn = db()
    cur = conn.execute(
        """INSERT INTO router_provisioning_registry
        (id, tenant_id, wizard_run_id, router_label, status,
         lifecycle_state,
         vpn_pool_cidr, router_vpn_ip, server_vpn_ip,
         wireguard_peer_name, allocation_index,
         created_at, updated_at,
         tentative_started_at, tentative_expires_at)
        VALUES (?, ?, ?, ?, 'reserved', ?,
                '10.10.0.0/24', ?, '10.10.0.1',
                ?, ?, ?, ?, ?, ?)""",
        (
            registry_id,
            int(tenant_id),
            _next_run(),
            label,
            lifecycle,
            ip,
            peer_name,
            allocation_index or _alloc_idx(),
            started.isoformat() + "Z",
            now.isoformat() + "Z",
            started.isoformat() + "Z",
            (expires.isoformat() + "Z") if expires else "",
        ),
    )
    rid = registry_id or int(cur.lastrowid)
    conn.execute(
        """INSERT INTO router_ip_allocations
        (tenant_id, registry_id, pool_name, ip_address,
         allocation_type, status, created_at)
        VALUES (?, ?, '10.10.0.0/24', ?,
                'router_vpn', 'active', ?)""",
        (int(tenant_id), rid, ip, started.isoformat() + "Z"),
    )
    conn.commit()
    return rid


# ─── find_expired ──────────────────────────────────────────


def test_find_expired_returns_only_past_ttl_rows(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        _seed_router(label="expired", expires_in_minutes=-5)
        _seed_router(label="active", ip="10.10.0.6",
                     peer_name="hr-peer-2",
                     expires_in_minutes=+10)
        rows = SetupWizardTentativeReclaimer().find_expired(tenant_id=1)
    assert len(rows) == 1
    assert rows[0]["router_label"] == "expired"


def test_find_expired_skips_permanent_lifecycle(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        # Permanent (vpn_verified) — expired but immune.
        _seed_router(label="verified", lifecycle="vpn_verified",
                     expires_in_minutes=-100)
        rows = SetupWizardTentativeReclaimer().find_expired(tenant_id=1)
    assert rows == []


def test_find_expired_skips_rows_without_ttl(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        _seed_router(label="legacy", expires_in_minutes=None)
        rows = SetupWizardTentativeReclaimer().find_expired(tenant_id=1)
    assert rows == []


# ─── reclaim_one ───────────────────────────────────────────


def test_reclaim_one_releases_ip_and_marks_abandoned(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(label="failed-r")
        result = SetupWizardTentativeReclaimer().reclaim_one(
            tenant_id=1, registry_id=rid, reason="ttl_expired",
        )
        # IP allocation released
        ip_row = db().execute(
            "SELECT status FROM router_ip_allocations "
            "WHERE registry_id=?",
            (rid,),
        ).fetchone()
        # Registry stamped
        reg_row = db().execute(
            "SELECT lifecycle_state, tentative_reclaimed_at, "
            "tentative_reclaim_reason, failure_reason "
            "FROM router_provisioning_registry WHERE id=?",
            (rid,),
        ).fetchone()
    assert result["status"] == "reclaimed"
    assert result["released_ip"] == "10.10.0.5"
    assert ip_row["status"] == "released"
    assert reg_row["lifecycle_state"] == "abandoned"
    assert reg_row["tentative_reclaimed_at"]
    assert reg_row["tentative_reclaim_reason"] == "ttl_expired"
    assert "tentative" in reg_row["failure_reason"].lower()


def test_reclaim_one_writes_lifecycle_event(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(label="ev")
        SetupWizardTentativeReclaimer().reclaim_one(
            tenant_id=1, registry_id=rid, actor="janitor_test",
        )
        ev = db().execute(
            """SELECT from_state, to_state, event_type, actor
               FROM router_lifecycle_events
               WHERE registry_id=? AND event_type='tentative_reclaimed'""",
            (rid,),
        ).fetchone()
    assert ev is not None
    assert ev["to_state"] == "abandoned"
    assert ev["actor"] == "janitor_test"


def test_reclaim_one_writes_audit_log(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(label="audit-r")
        SetupWizardTentativeReclaimer().reclaim_one(
            tenant_id=1, registry_id=rid, actor="qa_test",
        )
        row = db().execute(
            "SELECT action, severity, target_id FROM audit_log "
            "WHERE action='setup_wizard_tentative_reclaimed' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["severity"] == "critical"
    assert row["target_id"] == str(rid)


def test_reclaim_one_idempotent(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(label="idem")
        svc = SetupWizardTentativeReclaimer()
        first = svc.reclaim_one(tenant_id=1, registry_id=rid)
        second = svc.reclaim_one(tenant_id=1, registry_id=rid)
    assert first["status"] == "reclaimed"
    assert second["status"] == "already_reclaimed"


def test_reclaim_one_refuses_permanent_state(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(label="ok", lifecycle="vpn_verified")
        result = SetupWizardTentativeReclaimer().reclaim_one(
            tenant_id=1, registry_id=rid,
        )
    assert result["status"] == "skipped_permanent"


def test_reclaim_one_deletes_hoberadius_peer_file(app, tmp_path):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    peers_dir = tmp_path / "peers.d"
    peers_dir.mkdir()
    hr_peer = peers_dir / "hr-peer-1.conf"
    foreign = peers_dir / "vps-admin.conf"
    hr_peer.write_text("[Peer]\n")
    foreign.write_text("[Peer]\n")

    with app.app_context():
        rid = _seed_router(label="peer", peer_name="hr-peer-1")
        result = SetupWizardTentativeReclaimer(
            peers_dir=str(peers_dir),
        ).reclaim_one(tenant_id=1, registry_id=rid)

    assert not hr_peer.exists()
    assert foreign.exists(), "foreign peer file must NOT be touched"
    assert result["peer_file_removed"].endswith("hr-peer-1.conf")


# ─── reclaim_all_expired ───────────────────────────────────


def test_reclaim_all_expired_processes_many(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        for i in range(3):
            _seed_router(
                label=f"r{i}",
                ip=f"10.10.0.{10 + i}",
                peer_name=f"hr-peer-{i}",
            )
        # One verified row in the middle — must NOT be touched.
        _seed_router(
            label="safe", ip="10.10.0.20",
            peer_name="hr-peer-safe",
            lifecycle="vpn_verified",
        )
        result = SetupWizardTentativeReclaimer().reclaim_all_expired(
            tenant_id=1, actor="sweep_test",
        )
        verified = db().execute(
            "SELECT lifecycle_state FROM router_provisioning_registry "
            "WHERE router_label='safe'"
        ).fetchone()
    assert result["scanned"] == 3
    assert result["reclaimed_count"] == 3
    assert verified["lifecycle_state"] == "vpn_verified"


# ─── start / extend / promote helpers ──────────────────────


def test_start_tentative_stamps_expires_at(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        start_tentative,
    )
    with app.app_context():
        rid = _seed_router(expires_in_minutes=None)
        expires = start_tentative(
            tenant_id=1, registry_id=rid, ttl_minutes=30,
        )
        row = db().execute(
            "SELECT tentative_expires_at, tentative_started_at "
            "FROM router_provisioning_registry WHERE id=?",
            (rid,),
        ).fetchone()
    assert expires
    assert row["tentative_expires_at"] == expires
    assert row["tentative_started_at"]


def test_extend_tentative_pushes_ttl_forward(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        extend_tentative,
    )
    with app.app_context():
        rid = _seed_router(expires_in_minutes=-1)  # expired
        # Before extend: row would be reclaimable.
        new_expires = extend_tentative(
            tenant_id=1, registry_id=rid, ttl_minutes=60,
        )
        row = db().execute(
            "SELECT tentative_expires_at "
            "FROM router_provisioning_registry WHERE id=?",
            (rid,),
        ).fetchone()
    assert row["tentative_expires_at"] == new_expires
    # After extend, the new timestamp is in the future.
    assert new_expires > datetime.utcnow().isoformat() + "Z"


def test_promote_to_permanent_clears_ttl(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        promote_to_permanent,
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        rid = _seed_router(expires_in_minutes=-5)
        promote_to_permanent(tenant_id=1, registry_id=rid)
        # Reclaim should not pick it up anymore.
        rows = SetupWizardTentativeReclaimer().find_expired(tenant_id=1)
        row = db().execute(
            "SELECT tentative_expires_at "
            "FROM router_provisioning_registry WHERE id=?",
            (rid,),
        ).fetchone()
    assert row["tentative_expires_at"] == ""
    assert rid not in {r["id"] for r in rows}


def test_default_ttl_is_30_minutes(app):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        default_ttl,
    )
    assert default_ttl() == 30


def test_default_ttl_respects_env_override(app, monkeypatch):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        default_ttl,
    )
    monkeypatch.setenv("HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN", "60")
    assert default_ttl() == 60


def test_default_ttl_clamps_extreme_values(app, monkeypatch):
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        default_ttl,
    )
    monkeypatch.setenv("HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN", "1")
    assert default_ttl() == 5  # min clamp
    monkeypatch.setenv("HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN", "99999")
    assert default_ttl() == 1440  # max clamp


def test_find_expired_tenant_scoped(app):
    """Tenant 2 expired row must not appear in tenant 1's sweep."""
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    with app.app_context():
        _seed_router(tenant_id=1, label="t1-row")
        _seed_router(tenant_id=2, label="t2-row", ip="10.20.0.5",
                     peer_name="hr-peer-t2")
        rows = SetupWizardTentativeReclaimer().find_expired(tenant_id=1)
    labels = {r["router_label"] for r in rows}
    assert labels == {"t1-row"}
