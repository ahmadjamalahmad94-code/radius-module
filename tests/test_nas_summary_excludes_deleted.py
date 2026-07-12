"""Regression: the dashboard "الشبكة" network card total must NOT count
soft-deleted (archived) devices.

Deleting a router/NAS is a SOFT delete (nas_repo.archive_nas stamps deleted_at
+ forces enabled=0). get_nas_summary()['total'] used to `COUNT(*)` without a
deleted_at filter, so archived routers kept inflating the "جهاز" tile (e.g. 7
shown while only 1 live device exists). This guards the deleted_at filter.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_nas_sum_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "t.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def test_nas_summary_excludes_soft_deleted(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import nas_repo
        from app.radius.services.dashboard_metrics import get_nas_summary

        # one live device
        nas_repo.upsert_nas(NasDevice(
            id=None, tenant_id=1, name="LIVE", address="10.0.0.1",
            secret="s", vendor="mikrotik", enabled=True))
        # two devices, then archived (soft delete) — must NOT be counted
        for i in range(2):
            d = nas_repo.upsert_nas(NasDevice(
                id=None, tenant_id=1, name=f"DEL{i}", address=f"10.0.0.{i + 2}",
                secret="s", vendor="mikrotik", enabled=True))
            assert nas_repo.archive_nas(1, int(d.id), actor="test", reason="test")

        summary = get_nas_summary(1)
        assert summary["total"] == 1     # was 3 before the fix (counted archived)
        assert summary["enabled"] == 1   # live + enabled only
