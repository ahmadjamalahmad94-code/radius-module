"""S3.3 — Router scope enforcement contract."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s3_3_")
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


def _seed_tenant(app, *, tenant_id):
    """Make sure a tenant row exists. tenant_id=1 always does
    (DEFAULT_TENANT_ID seeded at boot); cross-tenant tests need
    a second one created explicitly so the FK to tenants holds.
    """
    if tenant_id == 1:
        return
    with app.app_context():
        from app.radius.db.connection import db, transaction
        existing = db().execute(
            "SELECT 1 FROM tenants WHERE id=?", (tenant_id,),
        ).fetchone()
        if existing:
            return
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO tenants "
                "  (id, slug, name, display_name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, f"t{tenant_id}",
                 f"Tenant {tenant_id}",
                 f"Tenant {tenant_id}", now),
            )


def _seed_nas(app, *, nas_id, tenant_id):
    _seed_tenant(app, tenant_id=tenant_id)
    with app.app_context():
        from app.radius.db.connection import transaction
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, ?, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, tenant_id,
                 f"scope-rtr-{tenant_id}-{nas_id}",
                 f"203.0.113.{(nas_id % 250) + 1}",
                 now),
            )


def test_predicate_allows_same_tenant(app):
    from app.radius.services.mt_scope import (
        admin_can_access_router,
    )
    nas = {"id": 1, "tenant_id": 1}
    assert admin_can_access_router(99, 1, nas) is True


def test_predicate_blocks_cross_tenant(app):
    from app.radius.services.mt_scope import (
        admin_can_access_router,
    )
    nas = {"id": 1, "tenant_id": 1}
    assert admin_can_access_router(99, 2, nas) is False


def test_predicate_blocks_missing_admin(app):
    from app.radius.services.mt_scope import (
        admin_can_access_router,
    )
    nas = {"id": 1, "tenant_id": 1}
    assert admin_can_access_router(None, 1, nas) is False


def test_predicate_blocks_missing_row(app):
    from app.radius.services.mt_scope import (
        admin_can_access_router,
    )
    assert admin_can_access_router(99, 1, None) is False


def test_assert_router_accessible_returns_dict_for_owned_row(app):
    _seed_nas(app, nas_id=10, tenant_id=1)
    with app.test_request_context("/"):
        from flask import session
        session["admin_id"] = 1
        session["tenant_id"] = 1
        from app.radius.services.mt_scope import (
            assert_router_accessible,
        )
        row = assert_router_accessible(10)
        assert row["id"] == 10
        assert row["tenant_id"] == 1


def test_assert_router_accessible_raises_for_cross_tenant(app):
    """Row belongs to tenant 2; admin is in tenant 1 → deny."""
    _seed_nas(app, nas_id=20, tenant_id=2)
    with app.test_request_context("/"):
        from flask import session
        session["admin_id"] = 1
        session["tenant_id"] = 1
        from app.radius.services.mt_scope import (
            assert_router_accessible, RouterAccessDenied,
        )
        with pytest.raises(RouterAccessDenied):
            assert_router_accessible(20)


def test_assert_router_accessible_raises_for_missing_row(app):
    with app.test_request_context("/"):
        from flask import session
        session["admin_id"] = 1
        session["tenant_id"] = 1
        from app.radius.services.mt_scope import (
            assert_router_accessible, RouterAccessDenied,
        )
        with pytest.raises(RouterAccessDenied):
            assert_router_accessible(99999)


def test_assert_router_accessible_raises_when_no_admin_session(app):
    _seed_nas(app, nas_id=30, tenant_id=1)
    with app.test_request_context("/"):
        # No admin_id in session.
        from app.radius.services.mt_scope import (
            assert_router_accessible, RouterAccessDenied,
        )
        with pytest.raises(RouterAccessDenied):
            assert_router_accessible(30)
