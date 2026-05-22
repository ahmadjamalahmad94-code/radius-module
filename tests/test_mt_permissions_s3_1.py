"""S3.1 — MikroTik permission helper.

Pure service-layer tests. No Flask request, no route. The
permission strings are a stable contract; tests pin both the
allowlist and the resolution policy (super admin → all,
PERM_ADMIN → implies the operational subset).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_allowlist_contains_every_documented_permission():
    """Pin the catalogue so a rename = a failing test (renames
    break stored role rows; never rename, add new ones)."""
    from app.radius.services.mt_permissions import (
        ALL_PERMISSIONS,
    )
    assert set(ALL_PERMISSIONS) == {
        "mikrotik.view",
        "mikrotik.diagnostics",
        "mikrotik.manage",
        "mikrotik.program",
        "mikrotik.deploy_login",
        "mikrotik.rollback",
        "mikrotik.backup",
        "mikrotik.restore",
        "mikrotik.audit.view",
        "mikrotik.admin",
        # VX2 — VPS site-exit routing surface.
        "site_exit.view",
        "site_exit.manage",
        "site_exit.preview",
        "site_exit.apply",
        "site_exit.override_backup_warning",
        "site_exit.enable_risky_groups",
    }


def test_super_admin_holds_every_permission():
    from app.radius.services.mt_permissions import (
        admin_permissions, ALL_PERMISSIONS,
    )
    admin = SimpleNamespace(id=1, is_super_admin=True)
    assert admin_permissions(admin) == frozenset(ALL_PERMISSIONS)


def test_none_admin_holds_nothing():
    from app.radius.services.mt_permissions import (
        admin_permissions,
    )
    assert admin_permissions(None) == frozenset()


def test_non_admin_resolves_via_permissions_of(monkeypatch):
    """Role-bound permissions flow through the admins service.
    Stub it so the helper logic is exercised in isolation."""
    from app.radius.services import mt_permissions as mp
    admin = SimpleNamespace(id=2, is_super_admin=False)

    class _Svc:
        def permissions_of(self, a):
            return ("mikrotik.view", "mikrotik.diagnostics",
                    "ignored.permission",)
    monkeypatch.setattr(mp, "get_admins_service",
                        lambda: _Svc(), raising=False)
    # Patch the real import path.
    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    perms = mp.admin_permissions(admin)
    # In the allowlist → kept.
    assert "mikrotik.view" in perms
    assert "mikrotik.diagnostics" in perms
    # Outside the allowlist → silently dropped.
    assert "ignored.permission" not in perms
    # Not granted → not present.
    assert "mikrotik.program" not in perms


def test_perm_admin_implies_operational_subset(monkeypatch):
    """A role with just `mikrotik.admin` covers the whole
    operational surface without listing each sub-permission."""
    from app.radius.services import mt_permissions as mp
    admin = SimpleNamespace(id=3, is_super_admin=False)

    class _Svc:
        def permissions_of(self, a):
            return ("mikrotik.admin",)
    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    perms = mp.admin_permissions(admin)
    for sub in (
        mp.PERM_VIEW, mp.PERM_DIAGNOSTICS, mp.PERM_MANAGE,
        mp.PERM_PROGRAM, mp.PERM_DEPLOY_LOGIN, mp.PERM_ROLLBACK,
        mp.PERM_BACKUP, mp.PERM_RESTORE, mp.PERM_AUDIT_VIEW,
    ):
        assert sub in perms


def test_has_returns_false_for_unknown_permission_name():
    from app.radius.services.mt_permissions import has
    admin = SimpleNamespace(id=1, is_super_admin=True)
    assert has(admin, "not.a.real.permission") is False


def test_has_returns_true_only_when_granted(monkeypatch):
    from app.radius.services import mt_permissions as mp
    admin = SimpleNamespace(id=4, is_super_admin=False)

    class _Svc:
        def permissions_of(self, a): return ("mikrotik.view",)
    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())
    assert mp.has(admin, mp.PERM_VIEW) is True
    assert mp.has(admin, mp.PERM_PROGRAM) is False


def test_require_perms_returns_arabic_reason_when_no_admin(app):
    """`require_perms` returns (ok, reason). The reason is
    operator-facing Arabic so a 403 page can render it directly.
    """
    from app.radius.services.mt_permissions import (
        require_perms, PERM_PROGRAM,
    )
    with app.test_request_context("/"):
        ok, reason = require_perms(PERM_PROGRAM)
        assert ok is False
        assert "تسجيل الدخول" in reason


def test_require_perms_returns_missing_list(app, monkeypatch):
    from app.radius.services import mt_permissions as mp
    with app.test_request_context("/"):
        # Pretend an admin is logged in but lacks the needed perm.
        from flask import g
        g.admin_id = 5

        class _Svc:
            def _store_by_id(self, _): return SimpleNamespace(
                id=5, is_super_admin=False)
        # Patch both lookups (current_admin + permissions_of).
        import app.radius.services.admins as admins_mod
        admin_obj = SimpleNamespace(id=5, is_super_admin=False)

        class _ServiceWithStore:
            class _Store:
                def by_id(self, _): return admin_obj
            _store = _Store()
            def permissions_of(self, a):
                return ("mikrotik.view",)
        monkeypatch.setattr(admins_mod, "get_admins_service",
                            lambda: _ServiceWithStore())
        ok, reason = mp.require_perms(mp.PERM_PROGRAM)
        assert ok is False
        assert "mikrotik.program" in reason


@pytest.fixture
def app(monkeypatch):
    """Minimal app fixture so the `current_admin` resolution path
    in require_perms() can find a Flask context."""
    import os
    import sys
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hr_s3_1_")
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
