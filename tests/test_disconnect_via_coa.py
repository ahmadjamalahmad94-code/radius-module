"""R11.16 regression: the "قطع" button must route through CoA Disconnect
(UDP/3799) rather than the MT API sync queue (TCP/8728).

Pre-R11.16 the button called `enqueue_disconnect` → sync_worker → MT API,
which silently failed in deployments behind NAT or without a public IP
(the queue would just keep retrying forever). Post-R11.16 the adapter
calls `disconnect_user` from radius_coa, which travels over the same UDP
channel that already works for accounting + CoA rate changes.

Coverage:
 1. Adapter.disconnect routes through radius_coa.disconnect_user (no
    sync_queue enqueue).
 2. Successful CoA-ACK is a no-op (no RadiusError raised).
 3. CoA failure (no_active_session, CoA-NAK, timeout) raises RadiusError
    so the route's flash shows a meaningful message instead of silently
    succeeding.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r1116_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


def _adapter():
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    return SqliteAdapter()


def test_adapter_disconnect_calls_radius_coa_not_sync_queue(app, monkeypatch):
    """Adapter.disconnect must reach radius_coa.disconnect_user. The old
    path (enqueue_disconnect) must NOT be invoked."""
    with app.app_context():
        from app.radius.integration import radius_coa, router_sync

        coa_calls = []
        def _fake_coa(tenant_id, username):
            coa_calls.append((tenant_id, username))
            return radius_coa.CoaResult(ok=True, code=41,
                                         code_name="Disconnect-ACK",
                                         reply_message="acked")
        monkeypatch.setattr(radius_coa, "disconnect_user", _fake_coa)

        enq_calls = []
        def _fake_enqueue(tenant_id, username):
            enq_calls.append((tenant_id, username))
        monkeypatch.setattr(router_sync, "enqueue_disconnect", _fake_enqueue)

        _adapter().disconnect("ahmad")

        assert coa_calls == [(1, "ahmad")], \
            "disconnect must call radius_coa.disconnect_user"
        assert enq_calls == [], \
            "disconnect must NOT enqueue to MT API sync_queue (R11.16)"


def test_adapter_disconnect_silent_on_success(app, monkeypatch):
    """A CoA-ACK result must complete without raising."""
    with app.app_context():
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=True, code=41, code_name="Disconnect-ACK",
                reply_message=""))

        _adapter().disconnect("ahmad")  # no exception


def test_adapter_disconnect_raises_on_failure(app, monkeypatch):
    """No active session → RadiusError propagates so the flash message
    shows the user what happened."""
    with app.app_context():
        from app.radius.core.errors import RadiusError
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=False, code=0, code_name="no_active_session",
                reply_message="لا جلسة نشطة لـ ahmad"))

        with pytest.raises(RadiusError) as ei:
            _adapter().disconnect("ahmad")
        assert "ahmad" in str(ei.value) or "لا جلسة" in str(ei.value)


def test_adapter_disconnect_raises_on_coa_nak(app, monkeypatch):
    """A CoA-NAK (e.g. NAS rejected) must surface as RadiusError too."""
    with app.app_context():
        from app.radius.core.errors import RadiusError
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=False, code=42, code_name="Disconnect-NAK",
                reply_message="Session not found"))

        with pytest.raises(RadiusError):
            _adapter().disconnect("ghost")
