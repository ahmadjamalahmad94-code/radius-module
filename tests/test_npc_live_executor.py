"""NPC Live Executor — adapter that talks to a real MikroTik.

These tests stub the MikroTik client + pool so we can exercise
the allow-list, dry-run, and `/system/script` flow without
opening a real socket. A real-router smoke test is the
operator's job — never claimed here.
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
    app, *, router_id=None, api_user="admin", api_password="pw",
    enabled=1,
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


@contextmanager
def _fake_pool_acquire(client_factory):
    """Helper: build a context manager that yields `client_factory()`.
    Lets each test pre-program the client."""
    @contextmanager
    def _acquire(_cfg):
        yield client_factory()
    yield _acquire


# ─── Allowlist refusal ─────────────────────────────────────


def test_allowlist_refuses_router_not_in_set(app):
    """A router not on the allowlist raises ExecutorNotConfigured
    so the apply service emits 'live executor not configured'."""
    rid = _seed_router(app)
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    from app.radius.services.npc_router_executor import (
        ExecutorNotConfigured,
    )
    # Note: allowlist is empty.
    ex = LiveRouterExecutor(allowed_router_ids=())
    with app.app_context():
        with pytest.raises(ExecutorNotConfigured):
            ex.execute_forward(rid, "/log info \"hi\"\n")
        with pytest.raises(ExecutorNotConfigured):
            ex.execute_rollback(rid, "/log info \"hi\"\n")


# ─── Empty / oversized scripts ─────────────────────────────


def test_refuses_empty_script(app):
    rid = _seed_router(app)
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(rid, "")
    assert not out.ok
    assert "empty" in out.error_message


def test_refuses_oversized_script(app):
    rid = _seed_router(app)
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(
        allowed_router_ids=(rid,),
        max_script_bytes=100,
    )
    with app.app_context():
        out = ex.execute_forward(rid, "x" * 200)
    assert not out.ok
    assert "exceeds max size" in out.error_message


# ─── Missing nas / credentials ─────────────────────────────


def test_returns_failed_for_unknown_router(app):
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    # Allowlist contains the id but the row doesn't exist.
    ex = LiveRouterExecutor(allowed_router_ids=(9999,))
    with app.app_context():
        out = ex.execute_forward(9999, "/log info \"hi\"\n")
    assert not out.ok
    assert "not found" in out.error_message


def test_returns_failed_when_credentials_missing(app):
    rid = _seed_router(
        app, api_user="", api_password="",
    )
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"hi\"\n")
    assert not out.ok
    assert "credentials" in out.error_message


# ─── Dry-run path ──────────────────────────────────────────


def test_dry_run_pings_identity_and_does_not_send_script(
    app, monkeypatch,
):
    """force_dry_run=True must connect + read /system/identity
    but NEVER call /system/script/add."""
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
    ex = LiveRouterExecutor(
        allowed_router_ids=(rid,),
        force_dry_run=True,
    )
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"hi\"\n")
    assert out.ok
    assert "dry-run" in out.stdout
    # We must have read identity.
    fake_client.print_.assert_called_once_with(
        "/system/identity/print",
    )
    # We must NOT have created a script.
    for c in fake_client.run.call_args_list:
        path = c.args[0] if c.args else c.kwargs.get("command", "")
        assert "/system/script" not in str(path)


# ─── Live forward path ─────────────────────────────────────


def test_forward_creates_runs_and_removes_temp_script(
    app, monkeypatch,
):
    rid = _seed_router(app)
    fake_client = MagicMock()
    # No exceptions from .run → success path.
    fake_client.run.return_value = []
    from app.radius.integration.mikrotik import pool

    @contextmanager
    def _fake_acquire(_cfg):
        yield fake_client

    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(
            rid, "/log info \"forward\"\n",
        )
    assert out.ok
    assert out.status == "succeeded"

    paths = [c.args[0] for c in fake_client.run.call_args_list]
    # Add → Run → Remove
    assert paths[0] == "/system/script/add"
    assert paths[1] == "/system/script/run"
    assert paths[2] == "/system/script/remove"

    # Script source matches what we passed.
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
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        ex.execute_rollback(rid, "/log info \"rb\"\n")
    add_attrs = fake_client.run.call_args_list[0].args[1]
    assert add_attrs["name"].startswith("hobe_npc_rollback_")


# ─── Failure mapping: MikrotikTrap → ExecutionResult ───────


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
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(
            rid, "/ip/firewall/filter add chain=BAD\n",
        )
    assert not out.ok
    assert out.status == "failed"
    assert "chain" in out.stderr
    # Add + Run + Remove (cleanup attempted even after trap).
    paths = [c.args[0] for c in fake_client.run.call_args_list]
    assert "/system/script/remove" in paths


def test_script_add_trap_skips_run_and_remove(
    app, monkeypatch,
):
    """If /system/script/add itself is rejected, we should NOT
    attempt to run or remove a script that was never created."""
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
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"x\"\n")
    assert not out.ok
    paths = [c.args[0] for c in fake_client.run.call_args_list]
    # Only the add attempt — no run, no remove.
    assert paths == ["/system/script/add"]


# ─── Connect / auth failures don't leak ────────────────────


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
        yield  # unreachable

    from app.radius.integration.mikrotik import pool
    monkeypatch.setattr(pool, "acquire", _fake_acquire)

    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    ex = LiveRouterExecutor(allowed_router_ids=(rid,))
    with app.app_context():
        out = ex.execute_forward(rid, "/log info \"x\"\n")
    assert not out.ok
    assert "ConnectError" in out.error_message
    assert "connection refused" in out.error_message
