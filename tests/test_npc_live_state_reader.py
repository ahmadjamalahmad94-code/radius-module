"""NPC Live State Reader — adapter that reads a real MikroTik."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_live_read_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_router(app, *, api_user="admin", api_password="pw"):
    import secrets as _sec
    suffix = _sec.token_hex(3)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, "
                "shortname, address, secret, vendor, nas_type, "
                "ports, snmp_community, auth_port, acct_port, "
                "coa_port, api_port, api_user, api_password, "
                "api_use_tls, location, coordinates, "
                "monitoring_enabled, description, enabled, "
                "require_message_authenticator, ssh_port, "
                "tags, metadata, created_at, updated_at) "
                "VALUES (1, ?, ?, ?,'','mikrotik',"
                "'router',0,'',1812,1813,3799,8728,?,?,0,"
                "'','',0,'',1,0,22,'','{}',"
                "'2026-01-01','2026-01-01')",
                (f"rt-{suffix}", f"rt-{suffix}",
                 f"10.0.{int(suffix, 16) % 256}.1",
                 api_user, api_password),
            )
            return int(cur.lastrowid)


def _stub_pool(monkeypatch, client):
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _acquire(_cfg):
        yield client

    monkeypatch.setattr(pool, "acquire", _acquire)


# ─── Allowlist refusal ─────────────────────────────────────


def test_allowlist_refuses_unlisted_router(app):
    rid = _seed_router(app)
    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    from app.radius.services.npc_router_state_reader import (
        StateReaderNotConfigured,
    )
    rd = LiveRouterStateReader(allowed_router_ids=())
    with app.app_context():
        with pytest.raises(StateReaderNotConfigured):
            rd.read_firewall_filters(rid)
        with pytest.raises(StateReaderNotConfigured):
            rd.read_address_lists(rid)
        with pytest.raises(StateReaderNotConfigured):
            rd.read_walled_garden(rid)
        with pytest.raises(StateReaderNotConfigured):
            rd.read_walled_garden_ip(rid)
        with pytest.raises(StateReaderNotConfigured):
            rd.read_managed_scheduler(rid)


# ─── Firewall filter parse ─────────────────────────────────


def test_read_firewall_filters_maps_rows_to_router_items(
    app, monkeypatch,
):
    rid = _seed_router(app)
    client = MagicMock()
    client.print_.return_value = iter([
        {".id": "*1", "chain": "input", "action": "accept",
         "comment": "HOBE_NPC_REMOTE:5:1"},
        {".id": "*2", "chain": "forward", "action": "drop",
         "comment": ""},
    ])
    _stub_pool(monkeypatch, client)

    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(rid,))
    with app.app_context():
        items = rd.read_firewall_filters(rid)
    assert len(items) == 2
    assert items[0].item_kind == "firewall_filter_rule"
    assert items[0].source_id == "*1"
    assert "input accept" in items[0].display_text
    assert items[0].payload["comment"] == "HOBE_NPC_REMOTE:5:1"
    client.print_.assert_called_once_with(
        "/ip/firewall/filter/print",
    )


def test_rows_without_source_id_are_skipped(app, monkeypatch):
    """A row with no `.id` cannot be diffed later — skip it
    rather than fabricate an id."""
    rid = _seed_router(app)
    client = MagicMock()
    client.print_.return_value = iter([
        {".id": "*1", "chain": "input", "action": "accept"},
        {"chain": "forward", "action": "drop"},  # no .id
    ])
    _stub_pool(monkeypatch, client)

    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(rid,))
    with app.app_context():
        items = rd.read_firewall_filters(rid)
    assert len(items) == 1
    assert items[0].source_id == "*1"


# ─── Address list parse ────────────────────────────────────


def test_read_address_lists_maps_rows(app, monkeypatch):
    rid = _seed_router(app)
    client = MagicMock()
    client.print_.return_value = iter([
        {".id": "*9", "list": "ops",
         "address": "203.0.113.5"},
    ])
    _stub_pool(monkeypatch, client)

    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(rid,))
    with app.app_context():
        items = rd.read_address_lists(rid)
    assert items[0].item_kind == "address_list_entry"
    assert "ops" in items[0].display_text
    assert "203.0.113.5" in items[0].display_text


# ─── Walled garden + scheduler use distinct API paths ──────


def test_walled_garden_and_scheduler_use_correct_paths(
    app, monkeypatch,
):
    rid = _seed_router(app)
    client = MagicMock()
    client.print_.return_value = iter([])
    _stub_pool(monkeypatch, client)

    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(rid,))
    with app.app_context():
        rd.read_walled_garden(rid)
        rd.read_walled_garden_ip(rid)
        rd.read_managed_scheduler(rid)
    paths = [c.args[0] for c in client.print_.call_args_list]
    assert "/ip/hotspot/walled-garden/print" in paths
    assert "/ip/hotspot/walled-garden/ip/print" in paths
    assert "/system/scheduler/print" in paths


# ─── Transport / auth failures map to StateReadError ───────


def test_connect_error_raises_state_read_error(
    app, monkeypatch,
):
    rid = _seed_router(app)
    from app.radius.integration.mikrotik.errors import (
        ConnectError,
    )

    @contextmanager
    def _fake_acquire(_cfg):
        raise ConnectError("connection refused")
        yield

    from app.radius.integration.mikrotik import pool
    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    from app.radius.services.npc_router_state_reader import (
        StateReadError,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(rid,))
    with app.app_context():
        with pytest.raises(StateReadError) as excinfo:
            rd.read_firewall_filters(rid)
    assert "connection refused" in str(excinfo.value)


def test_unknown_router_raises_state_read_error(app):
    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    from app.radius.services.npc_router_state_reader import (
        StateReadError,
    )
    rd = LiveRouterStateReader(allowed_router_ids=(9999,))
    with app.app_context():
        with pytest.raises(StateReadError):
            rd.read_firewall_filters(9999)
