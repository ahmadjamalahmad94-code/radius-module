"""Regression: a subscriber's password must NOT be silently wiped by
an ordinary edit-form submit that didn't carry the password field.

Background (production incident, 2026-05-26):
  Operator edited subscribers via /admin/radius/users/<u>/edit just
  to update contact info or plan. The password field was rendered
  empty on edit (template default) so the form submit sent
  password="". The subscribers_repo.upsert_subscriber call wrote
  password="" to the DB. Subscribers then failed RADIUS auth because
  their stored password had been silently erased. Operator's symptom
  report: "password is being cleared after each login" — actually
  it was cleared after each *edit*, and the operator noticed only
  when the subscriber next tried to log in.

The defense is in services/users.py UsersService.update():
  if the incoming DTO has an empty password AND a row exists with a
  non-empty password, preserve the existing one. The only legitimate
  way to change/clear the password is the dedicated reset_password
  path.

These tests pin that behavior so we never regress it.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pwpres_")
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


def _seed(tenant_id, username, password):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username,
        password=password, user_type="subscriber",
        full_name="Test User", status="enabled",
    ))


def test_update_with_empty_password_preserves_existing(app):
    """The bug: edit form submits password='' → password wiped.
    The fix: UsersService.update preserves the existing password."""
    with app.app_context():
        from app.radius.services.users import get_users_service
        from app.radius.db.repos import subscribers_repo

        original = _seed(1, "alice", "secret-pw-123")
        assert original.password == "secret-pw-123"

        # Simulate the edit form submit: the operator changed
        # full_name but the password field came back empty.
        dto_with_empty_pw = replace(original, password="",
                                    full_name="Alice Updated")
        get_users_service().update(actor="op", sub=dto_with_empty_pw)

        # The stored password MUST still be the original.
        after = subscribers_repo.get_subscriber(1, "alice")
        assert after.password == "secret-pw-123", (
            "edit submit with empty password field must NOT wipe "
            "the stored password — this is the production bug"
        )
        # But the other field DID update.
        assert after.full_name == "Alice Updated"


def test_update_with_whitespace_password_preserves_existing(app):
    """Whitespace-only ('   ') must be treated the same as empty —
    forms sometimes round-trip a space character."""
    with app.app_context():
        from app.radius.services.users import get_users_service
        from app.radius.db.repos import subscribers_repo

        original = _seed(1, "bob", "p@ss-w0rd")

        dto_ws = replace(original, password="   ")
        get_users_service().update(actor="op", sub=dto_ws)

        after = subscribers_repo.get_subscriber(1, "bob")
        assert after.password == "p@ss-w0rd"


def test_update_with_new_password_replaces_existing(app):
    """When the form DOES carry a new password (operator typed one),
    it must overwrite — the preservation guard only triggers on
    empty/whitespace input."""
    with app.app_context():
        from app.radius.services.users import get_users_service
        from app.radius.db.repos import subscribers_repo

        _seed(1, "carol", "old-password")

        original = subscribers_repo.get_subscriber(1, "carol")
        dto_new = replace(original, password="brand-new-password")
        get_users_service().update(actor="op", sub=dto_new)

        after = subscribers_repo.get_subscriber(1, "carol")
        assert after.password == "brand-new-password"


def test_reset_password_path_still_works(app):
    """The dedicated reset_password() repo function is the ONLY
    legitimate way to clear a password. It must NOT be affected
    by the UsersService.update guard."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        _seed(1, "dave", "initial")

        # Explicit reset to a new value.
        subscribers_repo.reset_password(1, "dave", "rotated-pw")
        after = subscribers_repo.get_subscriber(1, "dave")
        assert after.password == "rotated-pw"
