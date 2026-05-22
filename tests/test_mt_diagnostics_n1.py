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


def test_collect_routers_ignores_mikrotik_configs(app):
    """Seed BOTH tables with a row at the same address. The new
    `_collect_routers` should return only the nas_devices entry."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            # Legacy row — must NOT show up in diagnostics
            c.execute(
                """INSERT INTO mikrotik_configs
                    (tenant_id, name, host, port, username, password,
                     use_tls, verify_tls, timeout_sec, enabled,
                     created_at, updated_at)
                   VALUES (1, 'legacy-test', '203.0.113.5', 8728,
                           'admin', 'oldpass', 0, 1, 10, 1, ?, ?)""",
                (now, now),
            )
            # Current row — IS what the diagnostics page should
            # surface (note: must have api_user populated, else
            # the collector skips it as un-probable)
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
    # The current router shows up.
    assert "203.0.113.99" in hosts
    # The legacy mikrotik_configs row is intentionally excluded.
    assert "203.0.113.5" not in hosts
    # `source` field never says "mikrotik_configs" any more.
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


def test_collect_routers_returns_empty_when_only_legacy_rows(app):
    """Edge case: a tenant whose only routers live in the legacy
    table should now see an EMPTY diagnostics list, not a list of
    legacy rows being probed."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO mikrotik_configs
                    (tenant_id, name, host, port, username, password,
                     use_tls, verify_tls, timeout_sec, enabled,
                     created_at, updated_at)
                   VALUES (1, 'lonely', '198.51.100.1', 8728,
                           'admin', '', 0, 1, 10, 1, ?, ?)""",
                (now, now),
            )

        from app.radius.services import mt_diagnostics
        routers = mt_diagnostics._collect_routers(1)
    assert routers == []
