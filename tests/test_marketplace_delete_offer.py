"""«حذف العرض» — soft-delete a card-marketplace offer.

The offer leaves the marketplace (list + get) but the row is kept (so
already-issued cards stay valid), and the UNIQUE name slot is freed so the
same offer name can be re-created.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_offerdel_")
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


def _seed_plan(c) -> int:
    c.execute(
        "INSERT INTO access_plans (tenant_id, name, price, validity_days, "
        "duration_minutes, currency, created_at) "
        "VALUES (?,?,?,?,?,?,datetime('now'))",
        (1, "P1", 5.0, 30, 60, "JOD"))
    return int(c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def test_delete_offer_soft_deletes_hides_and_frees_name(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService, CardMarketplaceError,
        )
        with transaction() as c:
            plan_id = _seed_plan(c)
        svc = CardUsersMarketplaceService(tenant_id=1)

        pkg = svc.create_package(name="طلاب 1 ساعة", plan_id=plan_id, price="1")
        pid = int(pkg["id"])
        assert any(p["id"] == pid for p in svc.list_packages(active_only=False))

        svc.delete_package(pid, actor="tester")

        # gone from the marketplace list + get raises
        assert not any(p["id"] == pid for p in svc.list_packages(active_only=False))
        with pytest.raises(CardMarketplaceError):
            svc.get_package(pid)

        # the underlying row still exists (card integrity) but flagged deleted
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT deleted_at, active FROM card_marketplace_packages WHERE id=?",
            (pid,)).fetchone()
        assert row["deleted_at"] and int(row["active"]) == 0

        # the name is freed → the same offer name can be created again
        pkg2 = svc.create_package(name="طلاب 1 ساعة", plan_id=plan_id, price="1")
        assert int(pkg2["id"]) != pid
