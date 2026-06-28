# -*- coding: utf-8 -*-
"""The backups page must reflect the radius's OWN device-flow Drive store
(where the owner's creds + token live), not only the licensing bridge.

A refactor pointed the status display at the licensing panel, so a radius that
was actually connected (and still uploading via gd.upload_backup) showed
«غير مربوط». _gdrive_status now prefers the local store and falls back to the
bridge only when the radius has no local Drive config at all.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    d = tempfile.mkdtemp(prefix="hr_gdst_")
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


def test_local_connected_store_is_preferred_over_bridge(app, monkeypatch):
    with app.app_context():
        from app.radius.routes import backups
        from app.radius.services import google_drive as gd
        from app.radius.services.admin_panel_client import AdminPanelClient

        monkeypatch.setattr(gd, "status", lambda tid: {
            "configured": True, "connected": True, "email": "owner@gmail.com",
            "folder_name": "HobeRadius Backups", "last_upload_at": "2026-06-01 10:00:00",
            "last_error": "", "pending": False,
        })
        # If the bridge were consulted it would (wrongly) say not connected.
        monkeypatch.setattr(AdminPanelClient, "fetch_google_drive_status",
                            lambda self: {"ok": True, "response": {"connected": False}})
        st = backups._gdrive_status(1)
        assert st["connected"] is True
        assert st["email"] == "owner@gmail.com"
        assert st["source"] == "radius"


def test_local_configured_but_pending_is_preferred(app, monkeypatch):
    with app.app_context():
        from app.radius.routes import backups
        from app.radius.services import google_drive as gd
        monkeypatch.setattr(gd, "status", lambda tid: {
            "configured": True, "connected": False, "pending": True,
            "email": "", "folder_name": "", "last_upload_at": "", "last_error": "",
        })
        st = backups._gdrive_status(1)
        assert st["configured"] is True
        assert st["pending"] is True
        assert st["source"] == "radius"


def test_falls_back_to_bridge_when_no_local_config(app, monkeypatch):
    with app.app_context():
        from app.radius.routes import backups
        from app.radius.services import google_drive as gd
        from app.radius.services.admin_panel_client import AdminPanelClient

        monkeypatch.setattr(gd, "status", lambda tid: {
            "configured": False, "connected": False, "pending": False,
            "email": "", "folder_name": "", "last_upload_at": "", "last_error": "",
        })
        monkeypatch.setattr(AdminPanelClient, "fetch_google_drive_status",
                            lambda self: {"ok": True, "response": {
                                "connected": True, "email": "via-portal@gmail.com",
                                "folder_name": "Backups", "last_upload_at": "2026-06-02 09:00"}})
        st = backups._gdrive_status(1)
        assert st["connected"] is True
        assert st["email"] == "via-portal@gmail.com"
        assert st["source"] == "panel"


def test_unconfigured_everywhere_returns_setup_state(app, monkeypatch):
    with app.app_context():
        from app.radius.routes import backups
        from app.radius.services import google_drive as gd
        from app.radius.services.admin_panel_client import AdminPanelClient

        monkeypatch.setattr(gd, "status", lambda tid: {
            "configured": False, "connected": False, "pending": False,
            "email": "", "folder_name": "", "last_upload_at": "", "last_error": "",
        })
        monkeypatch.setattr(AdminPanelClient, "fetch_google_drive_status",
                            lambda self: {"ok": False, "status": "timeout"})
        st = backups._gdrive_status(1)
        assert st["configured"] is False
        assert st["connected"] is False
        assert st["source"] == "radius"
