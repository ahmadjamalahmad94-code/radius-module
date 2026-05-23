"""NPC Live Executor — adapter that talks to a real MikroTik.

The executor is installed by default and works against any
router present in `nas_devices`. The upstream safety stack
(permission + contracts + audit) does the gating. Tests stub
the MikroTik client + pool to exercise the wire-level flow
without opening a real socket.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_live_exec_")
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


def _seed_router(
    app, *, api_user="admin", api_password="pw", enabled=1,
):
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
                "'router',0,'',1812,1813,3799,8728,?,"
                "?,0,'','',0,'',?,0,22,'','{}',"
                "'2026-01-01','2026-01-01')",
                (f"rt-{suffix}", f"rt-{suffix}",
                 f"10.0.{int(suffix, 16) % 256}.1",
                 api_user, api_password, int(enabled)),
            )
            return int(cur.lastrowid)


# ─── Empty / oversized scripts ─────────────────────────────


def test_refuses_empty_script(app):
    rid = _seed_router(app)
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(rid, "")
    assert not out.ok
    assert "empty" in out.error_message


def test_refuses_oversized_script(app):
    rid = _seed_router(app)
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(max_script_bytes=100)
    with app.app_context():
        out = ex.execute_forward(rid, "x" * 200)
    assert not out.ok
    assert "exceeds max size" in out.error_message


# ─── Missing nas / credentials ─────────────────────────────


def test_returns_failed_for_unknown_router(app):
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(9999, "/log info \"hi\"\n")
    assert not out.ok
    assert "not found" in out.error_message


def test_returns_failed_when_credentials_missing(app):
    rid = _seed_router(app, api_user="", api_password="")
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"hi\"\n")
    assert not out.ok
    assert "credentials" in out.error_message


# ─── Live forward path ─────────────────────────────────────


def test_forward_creates_runs_and_removes_temp_script(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()
    fake_client.run.return_value = []
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(
            rid, "/log info \"forward\"\n",
        )
    assert out.ok
    assert out.status == "succeeded"

    paths = [c.args[0] for c in fake_client.run.call_args_list]
    assert paths[0] == "/system/script/add"
    assert paths[1] == "/system/script/run"
    assert paths[2] == "/system/script/remove"
    add_attrs = fake_client.run.call_args_list[0].args[1]
    assert add_attrs["source"] == "/log info \"forward\"\n"
    assert add_attrs["name"].startswith("hobe_npc_forward_")


def test_rollback_uses_rollback_prefix_in_script_name(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()
    fake_client.run.return_value = []
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        ex.execute_rollback(rid, "/log info \"rb\"\n")
    add_attrs = fake_client.run.call_args_list[0].args[1]
    assert add_attrs["name"].startswith("hobe_npc_rollback_")


# ─── Dry-run via internal arg ──────────────────────────────


def test_dry_run_arg_skips_script_creation(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()
    fake_client.print_.return_value = iter([
        {".id": "*1", "name": "test-router"},
    ])
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        # Use the private kwarg — exposed to the apply service
        # for explicit dry-run checks.
        out = ex._execute(
            rid, "/log info \"hi\"\n",
            kind="forward", dry_run=True,
        )
    assert out.ok
    assert "dry-run" in out.stdout
    fake_client.print_.assert_called_once_with(
        "/system/identity/print",
    )
    # No /system/script/* call.
    for c in fake_client.run.call_args_list:
        path = c.args[0] if c.args else ""
        assert "/system/script" not in str(path)


# ─── Failure mapping ───────────────────────────────────────


def test_script_run_trap_returns_failed_result(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()

    from app.radius.integration.mikrotik.errors import (
        MikrotikTrap,
    )

    def _run(path, attrs=None, queries=None):
        if path == "/system/script/run":
            raise MikrotikTrap(
                "invalid value for argument chain",
            )
        return []

    fake_client.run.side_effect = _run
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(
            rid, "/ip/firewall/filter add chain=BAD\n",
        )
    assert not out.ok
    assert out.status == "failed"
    assert "chain" in out.stderr
    paths = [c.args[0] for c in fake_client.run.call_args_list]
    assert "/system/script/remove" in paths


def test_script_add_trap_skips_run_and_remove(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()

    from app.radius.integration.mikrotik.errors import (
        MikrotikTrap,
    )

    def _run(path, attrs=None, queries=None):
        if path == "/system/script/add":
            raise MikrotikTrap("script policy rejected")
        return []

    fake_client.run.side_effect = _run
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"x\"\n")
    assert not out.ok
    paths = [c.args[0] for c in fake_client.run.call_args_list]
    assert paths == ["/system/script/add"]


def test_connect_error_returns_structured_failure(
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

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor()
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"x\"\n")
    assert not out.ok
    assert "ConnectError" in out.error_message
    assert "connection refused" in out.error_message
