"""T3 — HTTP routes for manual cancel + reclaim sweep."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-routes-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", str(tmp_path / "peers.d"))
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _seed_router(
    *,
    tenant_id: int = 1,
    label: str = "r1",
    ip: str = "10.10.0.5",
    peer_name: str = "hr-peer-1",
    lifecycle: str = "waiting_router_key",
    expires_in_minutes: int | None = -10,
    allocation_index: int = 5,
    wizard_run_id: int = 100,
) -> int:
    now = datetime.utcnow()
    started = now + timedelta(minutes=-20)
    expires = (
        (now + timedelta(minutes=expires_in_minutes)).isoformat() + "Z"
        if expires_in_minutes is not None
        else ""
    )
    conn = db()
    cur = conn.execute(
        """INSERT INTO router_provisioning_registry
        (tenant_id, wizard_run_id, router_label, status, lifecycle_state,
         vpn_pool_cidr, router_vpn_ip, server_vpn_ip,
         wireguard_peer_name, allocation_index,
         created_at, updated_at,
         tentative_started_at, tentative_expires_at)
        VALUES (?, ?, ?, 'reserved', ?,
                '10.10.0.0/24', ?, '10.10.0.1',
                ?, ?, ?, ?, ?, ?)""",
        (
            int(tenant_id), wizard_run_id, label, lifecycle,
            ip, peer_name, allocation_index,
            started.isoformat() + "Z", now.isoformat() + "Z",
            started.isoformat() + "Z", expires,
        ),
    )
    rid = int(cur.lastrowid)
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


# ─── Manual cancel ─────────────────────────────────────────


def test_cancel_tentative_releases_ip(app):
    client = app.test_client()
    _auth(client)
    with app.app_context():
        rid = _seed_router(label="t1")
    res = client.post(
        f"/admin/radius/setup-wizard/fleet/router/{rid}/cancel-tentative",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["cancel"]["status"] == "reclaimed"
    with app.app_context():
        row = db().execute(
            "SELECT lifecycle_state, tentative_reclaim_reason "
            "FROM router_provisioning_registry WHERE id=?",
            (rid,),
        ).fetchone()
    assert row["lifecycle_state"] == "abandoned"
    assert row["tentative_reclaim_reason"] == "manual_cancel"


def test_cancel_tentative_404_for_unknown_id(app):
    client = app.test_client()
    _auth(client)
    res = client.post(
        "/admin/radius/setup-wizard/fleet/router/9999/cancel-tentative",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 404


def test_cancel_tentative_409_for_permanent_router(app):
    """A verified router cannot be cancelled — operator gets a
    clear 409 instead of accidentally wiping a live router."""
    client = app.test_client()
    _auth(client)
    with app.app_context():
        rid = _seed_router(
            label="verified", lifecycle="vpn_verified",
        )
    res = client.post(
        f"/admin/radius/setup-wizard/fleet/router/{rid}/cancel-tentative",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 409


# ─── Reclaim-expired sweep ─────────────────────────────────


def test_reclaim_expired_sweep_endpoint(app):
    client = app.test_client()
    _auth(client)
    with app.app_context():
        _seed_router(label="a", allocation_index=1, wizard_run_id=11)
        _seed_router(label="b", ip="10.10.0.6",
                     peer_name="hr-peer-2",
                     allocation_index=2, wizard_run_id=12)
        # A verified row — must NOT be touched.
        _seed_router(
            label="safe", ip="10.10.0.7",
            peer_name="hr-peer-safe",
            lifecycle="vpn_verified",
            allocation_index=3, wizard_run_id=13,
        )
    res = client.post(
        "/admin/radius/setup-wizard/fleet/reclaim-expired",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["sweep"]["reclaimed_count"] == 2
    with app.app_context():
        safe = db().execute(
            "SELECT lifecycle_state FROM router_provisioning_registry "
            "WHERE router_label='safe'"
        ).fetchone()
    assert safe["lifecycle_state"] == "vpn_verified"


# ─── Fleet data surfaces TTL fields ────────────────────────


def test_fleet_data_includes_tentative_fields(app):
    client = app.test_client()
    _auth(client)
    with app.app_context():
        _seed_router(label="ttl-row", expires_in_minutes=+5)
    res = client.get(
        "/admin/radius/setup-wizard/fleet/data",
        headers={"X-CSRFToken": "test-csrf"},
    )
    assert res.status_code == 200
    body = res.get_json()
    routers = body["fleet"]["routers"]
    row = next(r for r in routers if r["router_label"] == "ttl-row")
    assert row["is_tentative"] is True
    assert row["tentative_expires_at"]
    assert row["is_reclaimed"] is False
