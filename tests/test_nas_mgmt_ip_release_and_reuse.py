"""Regression: deleting a NAS frees its mgmt IP + slot; allocation reuses gaps.

The setup-wizard-v3 add-router flow allocates a management IP from the WG mgmt
pool 10.10.0.0/24 (server = 10.10.0.1) and stores it on
``nas_devices.vpn_peer_address``. Deleting a NAS is a soft-delete (sets
``deleted_at``). Two things used to leak because both the allocator and the
usage metric scanned ``nas_devices`` WITHOUT excluding archived rows:

  * the allocator kept the deleted router's 10.10.0.x "used" forever, so it only
    ever incremented (delete .6 and .7, next add jumped to .8);
  * «NAS used» kept counting deleted routers (stuck high after deletion).

These tests assert the fixed behavior:
  * allocate → skips live IPs, fills the lowest gap;
  * after freeing 2 IPs (soft-delete), the next allocation reuses the LOWEST
    freed IP, not a new increment;
  * nas_count (the licensing «NAS used» metric) counts only LIVE NAS:
    register 3 → 3; delete 1 → 2;
  * deleting a NAS removes the matching WG mgmt peer file on the VPS.
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "nasip.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app


def _add_nas(name: str, vpn_ip: str = "") -> int:
    db().execute(
        """
        INSERT INTO nas_devices (tenant_id, name, address, vpn_peer_address, created_at)
        VALUES (1, ?, ?, ?, '2026-05-01T00:00:00Z')
        """,
        (name, f"192.168.0.{len(name)}", vpn_ip),
    )
    return int(db().execute("SELECT id FROM nas_devices ORDER BY id DESC LIMIT 1").fetchone()["id"])


def _soft_delete(nas_id: int) -> None:
    from app.radius.db.repos import nas_repo
    assert nas_repo.archive_nas(1, nas_id, actor="test") is True


# ─────────────────── allocator: reuse the lowest freed gap ───────────────────

def test_allocator_fills_lowest_gap_after_deletes(app_db):
    from app.radius.services.setup_wizard_v3 import WizardV3Service

    svc = WizardV3Service()
    # Three live routers on .2/.3/.4 → next free is .5.
    _add_nas("r2", "10.10.0.2")
    _add_nas("r3", "10.10.0.3")
    _add_nas("r4", "10.10.0.4")
    assert svc._allocate_router_vpn_ip(tenant_id=1) == "10.10.0.5"


def test_allocator_reuses_freed_ip_not_new_increment(app_db):
    from app.radius.services.setup_wizard_v3 import WizardV3Service

    svc = WizardV3Service()
    ids = {
        "10.10.0.5": _add_nas("r5", "10.10.0.5"),
        "10.10.0.6": _add_nas("r6", "10.10.0.6"),
        "10.10.0.7": _add_nas("r7", "10.10.0.7"),
    }
    # Full 2..7 would be .8 next; but 2..4 are free so lowest is .2 first.
    # Make .2..4 occupied to isolate the "reuse freed .6/.7" behavior.
    _add_nas("r2", "10.10.0.2")
    _add_nas("r3", "10.10.0.3")
    _add_nas("r4", "10.10.0.4")
    assert svc._allocate_router_vpn_ip(tenant_id=1) == "10.10.0.8"

    # Delete .6 and .7 → next allocation must REUSE the lowest freed (.6), NOT .8.
    _soft_delete(ids["10.10.0.6"])
    _soft_delete(ids["10.10.0.7"])
    assert svc._allocate_router_vpn_ip(tenant_id=1) == "10.10.0.6"


def test_deleted_nas_ip_is_not_reserved(app_db):
    from app.radius.services.setup_wizard_v3 import WizardV3Service

    svc = WizardV3Service()
    nid = _add_nas("only", "10.10.0.2")
    # With one live NAS on .2, next is .3.
    assert svc._allocate_router_vpn_ip(tenant_id=1) == "10.10.0.3"
    _soft_delete(nid)
    # Freed → .2 is available again (lowest).
    assert svc._allocate_router_vpn_ip(tenant_id=1) == "10.10.0.2"


# ─────────────────── usage count: live NAS only ───────────────────

def test_nas_used_count_drops_on_delete(app_db):
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    a = _add_nas("a", "10.10.0.2")
    _add_nas("b", "10.10.0.3")
    _add_nas("c", "10.10.0.4")
    metrics = UsageMeteringService().collect_metrics(tenant_id=1)
    assert metrics["nas_count"] == 3
    assert metrics["routers_count"] == 3

    _soft_delete(a)
    metrics = UsageMeteringService().collect_metrics(tenant_id=1)
    assert metrics["nas_count"] == 2   # not stuck at 3
    assert metrics["routers_count"] == 2


# ─────────────────── VPS peer release on delete ───────────────────

def test_delete_releases_wg_mgmt_peer_file(app_db, monkeypatch, tmp_path):
    peers_dir = tmp_path / "wg-peers.d"
    peers_dir.mkdir()
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", os.fspath(peers_dir))
    # Simulate the setup-wizard-v3 server-peer file for router on 10.10.0.6.
    peer_file = peers_dir / "wizard-v3-42.conf"
    peer_file.write_text(
        "# HOBERADIUS_RUN:42 wizard-v3-server-peer\n"
        "[Peer]\nPublicKey = abc123\nAllowedIPs = 10.10.0.6/32\nPersistentKeepalive = 25\n",
        encoding="utf-8",
    )
    # An unrelated peer that must survive.
    other = peers_dir / "wizard-v3-43.conf"
    other.write_text(
        "[Peer]\nPublicKey = def456\nAllowedIPs = 10.10.0.7/32\n", encoding="utf-8",
    )

    nid = _add_nas("r6", "10.10.0.6")
    from app.radius.services.devices import get_nas_devices_service
    get_nas_devices_service().delete(actor="test", nas_id=nid)

    assert not peer_file.exists()   # released
    assert other.exists()           # untouched


def test_release_peer_by_ip_is_noop_when_no_match(app_db, monkeypatch, tmp_path):
    from app.radius.services.wg_peer_manager import release_peer_by_ip

    peers_dir = tmp_path / "wg-peers.d"
    peers_dir.mkdir()
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", os.fspath(peers_dir))
    keep = peers_dir / "wizard-v3-1.conf"
    keep.write_text("[Peer]\nAllowedIPs = 10.10.0.9/32\n", encoding="utf-8")

    assert release_peer_by_ip("10.10.0.6") == 0
    assert keep.exists()
    assert release_peer_by_ip("10.10.0.9") == 1
    assert not keep.exists()
