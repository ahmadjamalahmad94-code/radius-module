"""Synced OWNER designation — feat/owner-admins-consume.

The licensing panel designates this customer panel's OWNER account(s) EXPLICITLY
and syncs them down in the license contract as ``owner_admins`` (a list of stable
username/email keys). This panel consumes that set and keys the unrestricted
owner principle off membership in it — replacing the min-id heuristic — while
supporting MULTIPLE owners. If no designation has synced yet it falls back to the
legacy min-id owner so the existing owner is never locked out.

Pins the acceptance scenarios from the task:
  • a synced set of 2 usernames → BOTH admins are owners (bypass + uncapped);
    a third admin is NOT (capped, 403 on an ungranted route).
  • match works by email as well as username.
  • a designation is authoritative: it can move ownership off the min-id admin.
  • with NO synced set, the min-id fallback still grants the legacy owner.
  • the sync consumer persists a non-empty set; an absent/empty field never
    strips an existing designation.
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
    tmp = tempfile.mkdtemp(prefix="hr_owner_desig_")
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


def _make_admin(*, perms=None, email=""):
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.db.repos import admins_repo
    role = None
    if perms is not None:
        role = admins_repo.create_role(
            name=f"role_{uuid4().hex[:6]}", display_name="Role",
            permissions=tuple(perms))
    return admins_repo.create_admin(
        username=f"a_{uuid4().hex[:8]}", password="pass-1234",
        full_name="Admin", email=email,
        role_id=role.id if role else None,
    )


def _login(client, username, password="pass-1234"):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": password},
                      follow_redirects=False)
    assert res.status_code in {302, 303}, res.status_code


_AUDIT_PERM = "mikrotik.audit.view"
_AUDIT_URL = "/admin/radius/audit"


# ════════════════ two designated owners both qualify; a third does not ════════
def test_two_designated_owners_both_owners_third_not(app):
    with app.app_context():
        from app.radius.auth.session_helpers import _resolve_is_super
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_credit import ManagerCreditService

        owner1 = _make_admin()                 # id #1 (would be min-id owner)
        owner2 = _make_admin()                 # id #2
        third = _make_admin()                  # id #3 — NOT designated

        admins_repo.set_designated_owners([owner1.username, owner2.username])

        # both designated admins are owners — by the set, not by min-id.
        assert admins_repo.is_primary_owner(owner1.id) is True
        assert admins_repo.is_primary_owner(owner2.id) is True
        assert admins_repo.is_primary_owner(third.id) is False

        # session resolver agrees (the RBAC bypass principal).
        assert _resolve_is_super(owner1) is True
        assert _resolve_is_super(owner2) is True
        assert _resolve_is_super(third) is False

        # credit gate: both owners uncapped, the third capped.
        svc = ManagerCreditService(tenant_id=1)
        assert svc.is_uncapped(owner1.id) is True
        assert svc.is_uncapped(owner2.id) is True
        assert svc.is_uncapped(third.id) is False


# ════════════════ match by email as well as username ═════════════════════════
def test_owner_matched_by_email(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo

        adm = _make_admin(email="boss@example.com")
        _make_admin()  # another, to ensure min-id isn't what's matching
        admins_repo.set_designated_owners(["BOSS@EXAMPLE.COM"])  # case-insensitive
        assert admins_repo.is_primary_owner(adm.id) is True


# ════════════ a designation is authoritative — moves ownership off min-id ═════
def test_designation_overrides_min_id(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo

        owner1 = _make_admin()                 # id #1 — the legacy min-id owner
        owner2 = _make_admin()                 # id #2 — the designated owner
        # before any designation: min-id fallback → owner1 is the owner.
        assert admins_repo.is_primary_owner(owner1.id) is True
        assert admins_repo.is_primary_owner(owner2.id) is False
        # designate ONLY owner2 → authoritative, ownership moves off the min-id.
        admins_repo.set_designated_owners([owner2.username])
        assert admins_repo.is_primary_owner(owner1.id) is False
        assert admins_repo.is_primary_owner(owner2.id) is True


# ════════════════ FALLBACK: no synced set → legacy min-id owner ══════════════
def test_min_id_fallback_when_no_designation(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.manager_credit import ManagerCreditService

        owner = _make_admin()                  # id #1
        other = _make_admin()                  # id #2
        # no set_designated_owners() called → designation absent.
        assert admins_repo.designated_owner_keys() is None
        assert admins_repo.is_primary_owner(owner.id) is True
        assert admins_repo.is_primary_owner(other.id) is False
        svc = ManagerCreditService(tenant_id=1)
        assert svc.is_uncapped(owner.id) is True
        assert svc.is_uncapped(other.id) is False


# ════════════════ consumer: non-empty persists; empty/absent never strips ════
def test_consumer_applies_and_never_strips(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.license_admin_runtime_sync import (
            apply_owner_admins_designation,
        )

        # non-empty list → persisted (de-duped, trimmed).
        applied = apply_owner_admins_designation([" alice ", "alice", "bob"])
        assert applied == ["alice", "bob"]
        assert admins_repo.designated_owner_keys() == {"alice", "bob"}

        # absent (non-list) → no change.
        assert apply_owner_admins_designation(None) == []
        assert admins_repo.designated_owner_keys() == {"alice", "bob"}

        # empty list → no change (never strips the existing designation).
        assert apply_owner_admins_designation([]) == []
        assert admins_repo.designated_owner_keys() == {"alice", "bob"}

        # explicit clear reverts to the fallback.
        admins_repo.clear_designated_owners()
        assert admins_repo.designated_owner_keys() is None


# ════════════════ route layer: designated owners bypass, third gets 403 ══════
def test_route_designated_owner_bypasses_third_403(app, client):
    from app.radius.core.constants import ALL_PERMISSIONS
    with app.app_context():
        from app.radius.db.repos import admins_repo
        owner1 = _make_admin()
        owner2 = _make_admin()
        # third holds a full role MINUS the audit perm → must 403 (no bypass).
        perms_without = tuple(p for p in ALL_PERMISSIONS if p != _AUDIT_PERM)
        third = _make_admin(perms=perms_without)
        admins_repo.set_designated_owners([owner1.username, owner2.username])
        o1u, o2u, t3u = owner1.username, owner2.username, third.username

    # both designated owners reach the guarded route with no permission.
    _login(client, o1u)
    assert client.get(_AUDIT_URL).status_code == 200
    c2 = app.test_client()
    _login(c2, o2u)
    assert c2.get(_AUDIT_URL).status_code == 200
    # the third admin — not an owner — is gated by its permissions (403).
    c3 = app.test_client()
    _login(c3, t3u)
    assert c3.get(_AUDIT_URL).status_code == 403
