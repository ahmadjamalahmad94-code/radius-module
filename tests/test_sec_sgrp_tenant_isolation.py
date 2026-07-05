"""SEC H8 — share-group membership is tenant-scoped.

A share group and its members belong to one tenant. Removing (or adding) a
member must only affect the caller's own tenant — otherwise an operator in
tenant B could strip members out of, or inject members into, tenant A's group
by guessing group/subscriber ids (IDOR).

The fix scopes `remove_member` by tenant_id and pre-checks group ownership in
both the panel route and the API layer. These repo-level pins prove the DELETE
can never reach across the tenant boundary.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sgrp_iso_")
    db_file = os.path.join(tmp, "t.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _member_count(gid: int) -> int:
    from app.radius.db.repos import share_groups_repo
    for g in share_groups_repo.list_groups(1):
        if g["id"] == gid:
            return g["members"]
    return -1


def test_remove_member_ignores_foreign_tenant(app):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import share_groups_repo, subscribers_repo

    sub = subscribers_repo.upsert_subscriber(
        Subscriber(id=None, username="victim", password="pw",
                   tenant_id=1, status="enabled"))
    gid = share_groups_repo.create(tenant_id=1, name="A-group")
    share_groups_repo.add_member(1, gid, sub.id)
    assert _member_count(gid) == 1

    # A caller in another tenant must NOT be able to strip the membership.
    FOREIGN_TENANT = 9999
    share_groups_repo.remove_member(FOREIGN_TENANT, gid, sub.id)
    assert _member_count(gid) == 1, "cross-tenant remove_member leaked!"

    # The owning tenant still can.
    share_groups_repo.remove_member(1, gid, sub.id)
    assert _member_count(gid) == 0


def test_remove_member_signature_requires_tenant():
    """The repo function takes tenant_id first — a positional-arg drift would
    silently re-open the hole, so pin the arity."""
    import inspect
    from app.radius.db.repos import share_groups_repo
    params = list(inspect.signature(share_groups_repo.remove_member).parameters)
    assert params[:3] == ["tenant_id", "group_id", "subscriber_id"]
