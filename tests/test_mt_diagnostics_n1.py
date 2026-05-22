"""N1 — diagnostics restricts itself to nas_devices.

Pre-N1, `_collect_routers` merged rows from both `mikrotik_configs`
(legacy) and `nas_devices` (current). A stale row in the legacy
table surfaced as "router unreachable" on every diagnostics
page load. This test pins the new contract: only `nas_devices`
is consulted.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_n1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


def test_collect_routers_returns_only_nas_devices_rows(app):
    """After N3 the legacy mikrotik_configs table is dropped, so
    the only thing the collector can possibly return is rows
    from nas_devices. This test pins that contract — `source`
    is never 'mikrotik_configs', and the service doesn't try to
    query the legacy table (which would raise without a swallow).
    """
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                   VALUES (901, 1, 'new-rtr', '203.0.113.99', 's',
                           'mikrotik', 'hotspot', 1, 'hr-abc123',
                           'pw', ?)""",
                (now,),
            )

        from app.radius.services import mt_diagnostics
        routers = mt_diagnostics._collect_routers(1)

    hosts = {r["host"] for r in routers}
    sources = {r["source"] for r in routers}
    assert "203.0.113.99" in hosts
    assert "mikrotik_configs" not in sources
    assert sources == {"nas_devices"}


def test_collect_routers_skips_rows_with_empty_api_user(app):
    """A nas_devices row without api_user can't be API-probed,
    so the collector must skip it instead of returning it and
    flagging 'unreachable'."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                   VALUES (902, 1, 'half-filled', '203.0.113.42',
                           's', 'mikrotik', 'hotspot', 1,
                           '', '', ?)""",
                (now,),
            )

        from app.radius.services import mt_diagnostics
        hosts = {r["host"] for r in mt_diagnostics._collect_routers(1)}
    assert "203.0.113.42" not in hosts


def test_collect_routers_returns_empty_when_no_rows(app):
    """Tenant with zero nas_devices rows gets an empty list
    (post-N3 the legacy table is gone, so there's no other
    source the service could fall back to). This pins that the
    page surfaces an empty state rather than crashing."""
    with app.app_context():
        from app.radius.services import mt_diagnostics
        assert mt_diagnostics._collect_routers(1) == []
