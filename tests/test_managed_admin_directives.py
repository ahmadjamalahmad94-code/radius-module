"""Consumer side of FULL panel-admin management — feat/panel-admins-full-mgmt.

The licensing owner manages this panel's admins from the customer file; the
desired state rides the signed identity-sync bridge as declarative
``admin_directives``. This module proves the panel APPLIES them idempotently:

  • create  — a new ``license_managed`` admin from a one-time werkzeug hash,
              forced to change the password on first login; can log in + can
              later change its own password locally (NOT license_admin-managed).
  • update  — set permissions (role) idempotently.
  • deactivate — recoverable disable, GUARDED: never an owner, never the last.
  • re-apply is a no-op (idempotent).
  • a plaintext-bearing directive is rejected, never applied.
  • a CustomerUser identity-managed admin (provider 'license_admin') is untouched.
  • the first-login force-change is enforced + cleared on local change.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_managed_adm_")
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


def _seed_root():
    """A pre-existing local admin so the panel is never adminless (last-admin guard)."""
    from app.radius.db.repos import admins_repo
    admins_repo.ensure_default_roles()
    return admins_repo.create_admin(username="root", password="root-pass-1",
                                    is_super_admin=True)


_HASH = generate_password_hash("initial-pass-1")


def _create_directive(username="newadmin", role_key="admin"):
    return {
        "op": "upsert", "username": username, "role_key": role_key, "active": True,
        "password_hash": _HASH, "password_hash_scheme": "werkzeug",
        "must_change_password": True,
    }


# ── CREATE ───────────────────────────────────────────────────────────────────
def test_directive_creates_managed_admin(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        outcome = admins_repo.apply_managed_admin_directive(**_create_directive())
        assert outcome == "created"
        a = admins_repo.get_by_username("newadmin")
        assert a is not None and a.enabled
        assert a.external_identity_provider == "license_managed"
        assert a.managed_by_license_admin is False     # password is LOCAL
        assert a.must_change_password is True
        # the role maps operator → its permission set is applied.
        role = admins_repo.get_role(a.role_id)
        assert role is not None and role.name == "operator"
        # the one-time hash actually authenticates.
        assert admins_repo.authenticate("newadmin", "initial-pass-1") is not None


def test_create_is_idempotent(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        assert admins_repo.apply_managed_admin_directive(**_create_directive()) == "created"
        # re-applying the SAME directive changes nothing.
        assert admins_repo.apply_managed_admin_directive(**_create_directive()) == "unchanged"


def test_create_without_hash_is_skipped(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        d = _create_directive()
        d.pop("password_hash")
        assert admins_repo.apply_managed_admin_directive(**d) == "skipped_no_password"
        assert admins_repo.get_by_username("newadmin") is None


# ── UPDATE permissions (role) ────────────────────────────────────────────────
def test_directive_updates_role(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        admins_repo.apply_managed_admin_directive(**_create_directive(role_key="viewer"))
        before = admins_repo.get_by_username("newadmin")
        # change permissions: viewer → billing (no password material on update).
        outcome = admins_repo.apply_managed_admin_directive(
            op="upsert", username="newadmin", role_key="billing", active=True)
        assert outcome == "updated"
        after = admins_repo.get_by_username("newadmin")
        assert after.role_id != before.role_id
        assert admins_repo.get_role(after.role_id).name == "billing"


def test_adopt_local_admin(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        # a purely-local admin (no provider tag).
        local = admins_repo.create_admin(username="localguy", password="local-pass-1")
        assert local.external_identity_provider == ""
        outcome = admins_repo.apply_managed_admin_directive(
            op="upsert", username="localguy", role_key="support", active=True)
        assert outcome == "updated"
        adopted = admins_repo.get_by_username("localguy")
        assert adopted.external_identity_provider == "license_managed"
        assert admins_repo.get_role(adopted.role_id).name == "support"


# ── DEACTIVATE (recoverable) + guards ────────────────────────────────────────
def test_deactivate(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        admins_repo.apply_managed_admin_directive(**_create_directive())
        outcome = admins_repo.apply_managed_admin_directive(
            op="deactivate", username="newadmin", active=False)
        assert outcome == "deactivated"
        assert admins_repo.get_by_username("newadmin").enabled is False
        # re-applying deactivate is a no-op.
        assert admins_repo.apply_managed_admin_directive(
            op="deactivate", username="newadmin", active=False) == "unchanged"


def test_guard_never_deactivate_owner(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        root = _seed_root()
        admins_repo.apply_managed_admin_directive(**_create_directive())  # 2nd admin
        # designate 'root' as the owner → deactivating it must be refused.
        admins_repo.set_designated_owners(["root"])
        outcome = admins_repo.apply_managed_admin_directive(
            op="deactivate", username="root", active=False)
        assert outcome == "skipped_owner"
        assert admins_repo.get_by_username("root").enabled is True


def test_guard_never_deactivate_last_admin(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        root = _seed_root()   # the ONLY admin
        outcome = admins_repo.apply_managed_admin_directive(
            op="deactivate", username="root", active=False)
        assert outcome == "skipped_last_admin"
        assert admins_repo.get_by_username("root").enabled is True


# ── precedence: identity-managed admins are NOT touched ──────────────────────
def test_skips_identity_managed_admin(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        # a CustomerUser identity-synced admin (provider 'license_admin').
        admins_repo.upsert_license_admin_user(
            external_user_id=99, username="iduser", password_hash=_HASH,
            password_hash_scheme="werkzeug", role_key="viewer")
        outcome = admins_repo.apply_managed_admin_directive(
            op="upsert", username="iduser", role_key="billing", active=True)
        assert outcome == "skipped_identity_managed"


# ── service-level: plaintext is rejected, summary is accurate ────────────────
def test_apply_admin_directives_rejects_plaintext(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.license_admin_identity_sync import apply_admin_directives
        _seed_root()
        summary = apply_admin_directives([
            _create_directive(username="okadmin"),
            {"op": "upsert", "username": "evil", "password": "plaintext!", "role_key": "owner"},
        ])
        assert summary["created"] == 1
        assert summary["rejected_plaintext"] == 1
        assert admins_repo.get_by_username("okadmin") is not None
        assert admins_repo.get_by_username("evil") is None


# ── first-login force-change is ENFORCED (protected pages bounce to /account) ─
def test_must_change_password_enforced(app, client):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        _seed_root()
        admins_repo.apply_managed_admin_directive(**_create_directive(username="freshadmin"))
    # log in with the initial password → any protected page bounces to /account.
    r = client.post("/admin/radius/login",
                    data={"username": "freshadmin", "password": "initial-pass-1"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = client.get("/admin/radius/", follow_redirects=False)
    assert r2.status_code in (302, 303)
    assert "/account" in r2.headers.get("Location", "")    # the must-change guard


# ── changing the password locally CLEARS the force-change flag ───────────────
# Driven through the account view directly so the assertion is independent of
# the license-lifecycle gate (which locks an unlicensed panel in this fixture).
def test_local_password_change_clears_flag(app):
    with app.app_context():
        from flask import session
        from app.radius.db.repos import admins_repo
        from app.radius.routes.account import account_password
        _seed_root()
        admin = admins_repo.apply_managed_admin_directive(**_create_directive(username="freshadmin")) or \
            admins_repo.get_by_username("freshadmin")
        admin = admins_repo.get_by_username("freshadmin")
        assert admin.must_change_password is True
        with app.test_request_context(
            "/admin/radius/account/password", method="POST",
            data={"current_password": "initial-pass-1",
                  "new_password": "brand-new-pass-9",
                  "confirm_password": "brand-new-pass-9"},
        ):
            session["admin_id"] = admin.id
            resp = account_password()
        assert getattr(resp, "status_code", 302) in (302, 303)
        refreshed = admins_repo.get_by_username("freshadmin")
        assert refreshed.must_change_password is False
        # the new password authenticates; the old one no longer does.
        assert admins_repo.authenticate("freshadmin", "brand-new-pass-9") is not None
        assert admins_repo.authenticate("freshadmin", "initial-pass-1") is None
