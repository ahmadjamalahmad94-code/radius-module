"""Owner decision: ONLY the primary owner account bypasses RBAC + credit caps.

Background — the panel used to have TWO "super" notions that both bypassed:
  (a) the PRIMARY OWNER account (the root admin, smallest admin id), and
  (b) an assignable ``super_admin`` ROLE (and the ``is_super_admin`` flag a
      license override may set on any admin).

The owner downgraded (b): the assignable ``super_admin`` role is now a normal,
fully-configurable role — its access = exactly the permissions granted to it,
and its holder is subject to the debt/سلف caps like any manager. ONLY the
primary owner account is unrestricted (full RBAC bypass + uncapped / may exceed
a manager's cap with a warning).

This pins the four acceptance scenarios:
  1. primary owner → full access everywhere + uncapped (sole override principal).
  2. a ``super_admin``-role admin with a permission OFF → 403 on that route.
  3. the same admin WITH the permission granted → 200.
  4. that admin is subject to HIS OWN debt/سلف caps — a spend beyond cap blocked.

Plus the keystone unit guard: holding the ``is_super_admin`` flag while NOT being
the primary owner no longer grants the bypass (RBAC) nor uncapped status.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


# ─────────────────────────── fixtures (NO_SEED) ───────────────────────────
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_owner_bypass_")
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


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────────────────── helpers ───────────────────────────
def _make_owner():
    """The FIRST admin created = primary owner (smallest id)."""
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(
        username=f"owner_{uuid4().hex[:8]}", password="owner-pass",
        full_name="Primary Owner", is_super_admin=True,
    )


def _make_super_role_admin(*, perms=None, with_flag=False):
    """A NON-owner admin holding a ``super_admin``-style role. ``perms`` defaults
    to the full system catalogue (mirrors the default super_admin role). Set
    ``with_flag`` to also carry the is_super_admin flag (the license-override
    case) — it must STILL NOT grant the owner bypass."""
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.db.repos import admins_repo
    if perms is None:
        perms = tuple(ALL_PERMISSIONS)
    role = admins_repo.create_role(
        name=f"superrole_{uuid4().hex[:6]}", display_name="Super-like role",
        permissions=tuple(perms),
    )
    adm = admins_repo.create_admin(
        username=f"mgr_{uuid4().hex[:8]}", password="mgr-pass",
        full_name="Super-role Manager", role_id=role.id,
        is_super_admin=with_flag,
    )
    return adm, role


def _login(client, username, password):
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.status_code


# ════════════════ UNIT: only the primary owner is the bypass principal ════════
def test_flag_holding_non_owner_is_not_super_nor_uncapped(app):
    """The keystone: a non-owner who carries the is_super_admin flag (assignable
    role / license override) is NEITHER treated as super (RBAC) NOR uncapped."""
    with app.app_context():
        from app.radius.auth.session_helpers import _resolve_is_super
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_credit import ManagerCreditService

        owner = _make_owner()                              # id #1
        flagged, _ = _make_super_role_admin(with_flag=True)  # id #2, flag set

        # owner detection — only the primary owner.
        assert admins_repo.is_primary_owner(owner.id) is True
        assert admins_repo.is_primary_owner(flagged.id) is False

        # RBAC principal (session resolver) — owner yes, flagged non-owner no.
        assert _resolve_is_super(owner) is True
        assert _resolve_is_super(flagged) is False

        # credit gate — owner uncapped, flagged non-owner capped.
        svc = ManagerCreditService(tenant_id=1)
        assert svc.is_uncapped(owner.id) is True
        assert svc.is_uncapped(flagged.id) is False


def test_primary_owner_is_uncapped_even_with_flag_cleared(app):
    """The primary-owner invariant stays intact: the root account is uncapped
    even if its is_super_admin flag is somehow cleared (flag-independent)."""
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_credit import ManagerCreditService

        owner = _make_owner()
        admins_repo.update_admin(owner.id, is_super_admin=False)  # clear the flag
        svc = ManagerCreditService(tenant_id=1)
        assert admins_repo.is_primary_owner(owner.id) is True
        assert svc.is_uncapped(owner.id) is True


# ════════════════ SCENARIO 4: super_admin-role admin is CAPPED ════════════════
def test_super_role_admin_is_subject_to_his_own_caps(app):
    """A ``super_admin``-role admin spending on himself is gated by HIS caps.
    Zero-trust (no caps) → a spend is blocked; raising his debt cap lets it
    through as debt; the owner remains uncapped throughout."""
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_credit import (
            ManagerCreditError, ManagerCreditService,
        )

        _make_owner()                          # id #1 = uncapped provider
        mgr, _ = _make_super_role_admin()      # id #2 = super_admin role, capped
        svc = ManagerCreditService(tenant_id=1)

        assert svc.is_uncapped(mgr.id) is False

        # zero wallet + zero trust → BLOCKED.
        with pytest.raises(ManagerCreditError):
            svc.charge(mgr.id, 50_00, kind="card_package", own=True)
        # advance (سلف) beyond the (disabled) loan cap → BLOCKED too.
        with pytest.raises(ManagerCreditError):
            svc.charge(mgr.id, 10_00, kind="advance", own=True)

        # owner raises his debt cap to 100.00 → now a 50.00 spend is allowed as debt.
        admins_repo.update_admin(mgr.id, debt_cap_enabled=True, debt_cap_minor=100_00)
        res = svc.charge(mgr.id, 50_00, kind="card_package", own=True)
        assert res["mode"] == "debt"
        assert res["debt_recorded_minor"] == 50_00
        # …but a further spend that breaches the 100.00 cap is blocked.
        with pytest.raises(ManagerCreditError):
            svc.charge(mgr.id, 60_00, kind="card_package", own=True)


# ════════════════ SCENARIOS 1-3: RBAC at the route layer ══════════════════════
_AUDIT_PERM = "mikrotik.audit.view"
_AUDIT_URL = "/admin/radius/audit"


def test_owner_reaches_named_perm_route(app, client):
    """Scenario 1 (RBAC side): the primary owner reaches a guarded route with
    full access — no permission needed, the owner bypasses."""
    with app.app_context():
        owner = _make_owner()
    _login(client, owner.username, "owner-pass")
    assert client.get(_AUDIT_URL).status_code == 200


def test_super_role_admin_403_when_permission_off(app, client):
    """Scenario 2: a ``super_admin``-role admin whose role LACKS the required
    permission gets 403 — the role no longer magically bypasses."""
    from app.radius.core.constants import ALL_PERMISSIONS
    with app.app_context():
        _make_owner()                                      # primary owner #1
        perms_without = tuple(p for p in ALL_PERMISSIONS if p != _AUDIT_PERM)
        mgr, _ = _make_super_role_admin(perms=perms_without)
    _login(client, mgr.username, "mgr-pass")
    assert client.get(_AUDIT_URL).status_code == 403


def test_super_role_admin_200_when_permission_on(app, client):
    """Scenario 3: grant the permission → the same admin reaches the route (200).
    Access flows through the assigned permissions exactly like any role. (The
    /audit route lives in the MikroTik permission namespace, so the role must
    carry ``mikrotik.audit.view`` explicitly.)"""
    from app.radius.core.constants import ALL_PERMISSIONS
    with app.app_context():
        _make_owner()                                      # primary owner #1
        mgr, _ = _make_super_role_admin(
            perms=tuple(ALL_PERMISSIONS) + ("mikrotik.audit.view",))
    _login(client, mgr.username, "mgr-pass")
    assert client.get(_AUDIT_URL).status_code == 200


def test_only_owner_session_is_the_bypass_principal(app, client):
    """The bypass principal lives in ``session["is_super_admin"]`` and the central
    RBAC guard / ``can("__super__")`` key off it. After a REAL login it is True
    ONLY for the primary owner — a ``super_admin``-role admin (even with every
    grantable permission) logs in as a normal, non-super principal, so every
    owner-only (``__super__``) surface stays closed to it."""
    from app.radius.core.constants import ALL_PERMISSIONS
    with app.app_context():
        owner = _make_owner()
        mgr, _ = _make_super_role_admin(perms=tuple(ALL_PERMISSIONS))

    # primary owner → session marks them as the bypass principal.
    _login(client, owner.username, "owner-pass")
    with client.session_transaction() as sess:
        assert sess.get("is_super_admin") is True
    with app.test_request_context():
        from flask import session as _s
        from app.radius.auth.ui_permissions import can
        _s["is_super_admin"] = True
        assert can("__super__") is True        # owner clears the owner-only gate

    # super_admin-role admin → NOT the bypass principal despite full perms.
    client2 = app.test_client()
    _login(client2, mgr.username, "mgr-pass")
    with client2.session_transaction() as sess:
        assert sess.get("is_super_admin") is False
    with app.test_request_context():
        from flask import session as _s
        from app.radius.auth.ui_permissions import can
        _s["is_super_admin"] = False
        _s["permissions"] = list(ALL_PERMISSIONS)
        assert can("__super__") is False       # no bypass on an owner-only gate
