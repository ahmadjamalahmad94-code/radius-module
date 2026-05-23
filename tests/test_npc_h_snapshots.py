"""NPC Phase H — snapshot foundation."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_h_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ─── Migration ──────────────────────────────────────────────


def test_migration_045_creates_snapshot_tables(app):
    with app.app_context():
        from app.radius.db.connection import db
        names = {
            r["name"] for r in db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "network_policy_snapshots" in names
    assert "network_policy_snapshot_items" in names


def test_snapshot_tables_have_no_secret_columns(app):
    """Schema invariant — no private_key/password/secret
    column anywhere on the snapshot tables."""
    with app.app_context():
        from app.radius.db.connection import db
        for tbl in ("network_policy_snapshots",
                    "network_policy_snapshot_items"):
            cols = [
                r["name"] for r in db().execute(
                    f"PRAGMA table_info({tbl})"
                ).fetchall()
            ]
            for c in cols:
                cl = c.lower()
                assert "private_key" not in cl
                assert "password" not in cl
                assert "secret" not in cl, (
                    f"{tbl}.{c} looks like a secret column"
                )


# ─── Repo basics ────────────────────────────────────────────


def test_create_snapshot_round_trip(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=7,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
            created_by="alice",
        )
        row = r.get_snapshot(1, sid)
    assert row["snapshot_type"] == r.SNAPSHOT_TYPE_FILTER
    assert row["router_id"] == 7
    assert row["status"] == r.STATUS_STORED
    assert row["created_by"] == "alice"


def test_create_snapshot_rejects_unknown_enums(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        with pytest.raises(ValueError):
            r.create_snapshot(
                tenant_id=1, router_id=1,
                snapshot_type="bogus",
            )
        with pytest.raises(ValueError):
            r.create_snapshot(
                tenant_id=1, router_id=1,
                snapshot_type=r.SNAPSHOT_TYPE_FILTER,
                status="brand-new",
            )


def test_add_item_persists_payload_and_display(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=7,
            snapshot_type=r.SNAPSHOT_TYPE_COMPOSITE,
        )
        r.add_item(
            snapshot_id=sid, item_kind=r.ITEM_FILTER,
            source_id="*1",
            payload={"chain": "forward",
                     "action": "drop",
                     "dst-address-list": "HOBE_NPC_BLOCK_42"},
            display_text="forward drop → BLOCK_42",
        )
        items = r.list_items(sid)
    assert len(items) == 1
    assert items[0]["item_kind"] == r.ITEM_FILTER
    assert items[0]["payload"]["chain"] == "forward"
    assert items[0]["display_text"].startswith("forward drop")


# ─── Secret rejection ───────────────────────────────────────


def test_secret_in_payload_rejected(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        with pytest.raises(r.SecretInSnapshotError):
            r.add_item(
                snapshot_id=sid, item_kind=r.ITEM_FILTER,
                payload={"user": "admin",
                         "password": "hunter2"},
            )


def test_secret_in_display_text_rejected(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        with pytest.raises(r.SecretInSnapshotError):
            r.add_item(
                snapshot_id=sid, item_kind=r.ITEM_FILTER,
                payload={"ok": True},
                display_text="user pw — password=letmein",
            )


def test_secret_in_nested_payload_rejected(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        with pytest.raises(r.SecretInSnapshotError):
            r.add_item(
                snapshot_id=sid, item_kind=r.ITEM_FILTER,
                payload={
                    "credentials": {
                        "private-key": "AbCd==",
                    },
                },
            )


def test_add_items_many_skips_secret_payloads(app):
    """`add_items_many` should NOT raise — it counts rejected
    items so the caller can decide what to do."""
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_COMPOSITE,
        )
        counts = r.add_items_many(sid, [
            {"item_kind": r.ITEM_FILTER,
             "payload": {"chain": "input"}},
            {"item_kind": r.ITEM_FILTER,
             "payload": {"password": "x"}},
            {"item_kind": "bogus_kind"},
        ])
    assert counts["inserted"] == 1
    assert counts["rejected"] == 2


# ─── Tenant scoping ─────────────────────────────────────────


def test_tenant_scoping_blocks_cross_tenant_read(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        assert r.get_snapshot(1, sid) is not None
        assert r.get_snapshot(2, sid) is None


def test_list_for_router_scoped(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        # Two snapshots in tenant 1, one in tenant 2.
        r.create_snapshot(
            tenant_id=1, router_id=7,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        r.create_snapshot(
            tenant_id=1, router_id=7,
            snapshot_type=r.SNAPSHOT_TYPE_ADDRESS_LIST,
        )
        r.create_snapshot(
            tenant_id=2, router_id=7,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        t1 = r.list_for_router(1, 7)
        t2 = r.list_for_router(2, 7)
    assert len(t1) == 2
    assert len(t2) == 1


# ─── Lifecycle ──────────────────────────────────────────────


def test_set_status_validates_enum(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_FILTER,
        )
        with pytest.raises(ValueError):
            r.set_status(1, sid, "brand-new")
        out = r.set_status(1, sid, r.STATUS_EXPIRED)
    assert out["status"] == r.STATUS_EXPIRED


def test_delete_snapshot_cascades_items(app):
    with app.app_context():
        from app.radius.db.repos import npc_snapshots_repo as r
        sid = r.create_snapshot(
            tenant_id=1, router_id=1,
            snapshot_type=r.SNAPSHOT_TYPE_COMPOSITE,
        )
        r.add_item(
            snapshot_id=sid, item_kind=r.ITEM_FILTER,
            payload={"chain": "forward"},
        )
        assert r.list_items(sid)
        assert r.delete_snapshot(1, sid) is True
        assert r.get_snapshot(1, sid) is None
        assert r.list_items(sid) == []


# ─── Service facade ─────────────────────────────────────────


def test_service_store_returns_inserted_count(app):
    with app.app_context():
        from app.radius.services import (
            npc_snapshot_service as svc,
        )
        out = svc.store(
            tenant_id=1, router_id=3,
            snapshot_type="composite",
            items=[
                {"item_kind": "firewall_filter_rule",
                 "payload": {"chain": "input"}},
                {"item_kind": "address_list_entry",
                 "payload": {"list": "HOBE_NPC_BLOCK_42",
                              "address": "tiktok.com"}},
                # A secret-bearing item is silently rejected.
                {"item_kind": "firewall_filter_rule",
                 "payload": {"password": "letmein"}},
            ],
            created_by="alice",
        )
    assert out.item_count == 2
    assert out.router_id == 3


def test_service_get_includes_items_when_asked(app):
    with app.app_context():
        from app.radius.services import (
            npc_snapshot_service as svc,
        )
        s = svc.store(
            tenant_id=1, router_id=3,
            snapshot_type="firewall_filter",
            items=[{
                "item_kind": "firewall_filter_rule",
                "payload": {"chain": "input"},
            }],
        )
        with_items = svc.get(1, s.id, include_items=True)
        without = svc.get(1, s.id, include_items=False)
    assert "items" in with_items
    assert len(with_items["items"]) == 1
    assert "items" not in without


def test_service_does_not_touch_mikrotik(app):
    """Defence-in-depth: the snapshot service must not even
    import MikrotikClient."""
    import importlib
    import app.radius.services.npc_snapshot_service as m
    importlib.reload(m)
    assert "MikrotikClient" not in dir(m)
