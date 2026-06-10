"""RadiusPolicy CRUD is now real persistence (was a no-op in the adapter)."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture()
def app_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pol_")
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "policies.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app
    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


def _policy(**kw):
    from app.radius.core.types import RadiusPolicy
    base = dict(id=None, name="p1", policy_type="ratelimit",
                params={"rate": "10m"}, enabled=True, priority=50,
                description="cap")
    base.update(kw)
    return RadiusPolicy(**base)


def test_upsert_then_list_round_trips(app_db):
    from app.radius.db.repos import radius_policies_repo as repo

    saved = repo.upsert_policy(1, _policy())
    assert saved.id is not None
    assert saved.params == {"rate": "10m"}

    rows = repo.list_policies(1)
    assert len(rows) == 1
    assert rows[0].name == "p1"
    assert rows[0].priority == 50
    assert rows[0].enabled is True


def test_upsert_updates_in_place_by_name(app_db):
    from app.radius.db.repos import radius_policies_repo as repo

    first = repo.upsert_policy(1, _policy())
    again = repo.upsert_policy(1, _policy(description="updated", priority=10))

    assert again.id == first.id  # same row, not a duplicate
    assert again.description == "updated"
    assert again.priority == 10
    assert len(repo.list_policies(1)) == 1


def test_delete_removes_policy(app_db):
    from app.radius.db.repos import radius_policies_repo as repo

    saved = repo.upsert_policy(1, _policy())
    repo.delete_policy(1, saved.id)
    assert repo.list_policies(1) == []


def test_tenant_isolation(app_db):
    from app.radius.db.repos import radius_policies_repo as repo

    repo.upsert_policy(1, _policy(name="t1"))
    repo.upsert_policy(2, _policy(name="t2"))
    assert [p.name for p in repo.list_policies(1)] == ["t1"]
    assert [p.name for p in repo.list_policies(2)] == ["t2"]


def test_sqlite_adapter_crud_is_no_longer_noop(app_db):
    """The adapter methods (previously []/echo/None) now hit real storage."""
    from app.radius.integration.sqlite_adapter import SqliteAdapter

    adapter = SqliteAdapter()
    assert list(adapter.list_policies()) == []
    saved = adapter.upsert_policy(_policy(name="adp"))
    assert saved.id is not None
    assert [p.name for p in adapter.list_policies()] == ["adp"]
    adapter.delete_policy(saved.id)
    assert list(adapter.list_policies()) == []
