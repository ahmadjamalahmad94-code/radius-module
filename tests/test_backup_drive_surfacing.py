# -*- coding: utf-8 -*-
"""run_local_backup must SURFACE the Google Drive upload result (success or
failure) instead of swallowing it — the owner explicitly wants Drive errors to
show, never a silent fail."""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    d = tempfile.mkdtemp(prefix="hr_bkdrv_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(d, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.path.join(d, "t.db"))
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        yield application
    reset_for_tests(None)


def test_drive_failure_is_surfaced_not_swallowed(app, monkeypatch):
    with app.app_context():
        from app.radius.services import google_drive as gd
        from app.radius.services.operations import get_operations_service
        # Drive is connected, but the upload fails (expired token / API error).
        monkeypatch.setattr(gd, "status", lambda tid: {"connected": True})
        monkeypatch.setattr(gd, "upload_backup",
                            lambda tid, path, name: {"ok": False, "error": "token_refresh_failed"})
        res = get_operations_service().run_local_backup(tenant_id=1, actor="tester")
        assert res["verified"] is True              # local backup still works
        assert res.get("drive") is not None         # NOT swallowed
        assert res["drive"]["ok"] is False
        assert res["drive"]["error"] == "token_refresh_failed"


def test_drive_exception_is_recorded_not_swallowed(app, monkeypatch):
    with app.app_context():
        from app.radius.services import google_drive as gd
        from app.radius.services.operations import get_operations_service
        monkeypatch.setattr(gd, "status", lambda tid: {"connected": True})

        def _boom(tid, path, name):
            raise RuntimeError("drive_api_500")

        monkeypatch.setattr(gd, "upload_backup", _boom)
        res = get_operations_service().run_local_backup(tenant_id=1, actor="tester")
        assert res["verified"] is True
        assert res["drive"]["ok"] is False
        assert "drive_api_500" in res["drive"]["error"]
        # the local backup still succeeded (Drive failure never breaks it)
        assert res["run"]["status"] == "success"


def test_drive_success_is_surfaced(app, monkeypatch):
    with app.app_context():
        from app.radius.services import google_drive as gd
        from app.radius.services.operations import get_operations_service
        monkeypatch.setattr(gd, "status", lambda tid: {"connected": True})
        monkeypatch.setattr(gd, "upload_backup",
                            lambda tid, path, name: {"ok": True, "file_id": "f123"})
        res = get_operations_service().run_local_backup(tenant_id=1, actor="tester")
        assert res["drive"] == {"ok": True, "file_id": "f123"}


def test_no_drive_key_when_not_connected(app, monkeypatch):
    with app.app_context():
        from app.radius.services import google_drive as gd
        from app.radius.services.operations import get_operations_service
        monkeypatch.setattr(gd, "status", lambda tid: {"connected": False})
        res = get_operations_service().run_local_backup(tenant_id=1, actor="tester")
        assert "drive" not in res                    # nothing attempted, nothing reported
