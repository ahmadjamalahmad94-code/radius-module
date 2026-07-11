"""The login-states report must show the ATTEMPTED client MAC
(Calling-Station-Id) for a network auth row — essential to diagnose a
«MAC غير مطابق» rejection (you need to see which device tried).

radpostauth now carries a `calling_station` column; the network builder
in login_events surfaces it as the row's `mac`.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mac_")
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


def test_attempted_mac_shows_in_network_login_row(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.login_events import fetch_login_events

        # a failed subscriber network auth with a mac_mismatch reason and a
        # concrete Calling-Station-Id (the wrong device that tried).
        with transaction() as c:
            c.execute(
                "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
                "authdate, class, nas, calling_station) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (1, "0598550496", "wrongpw", "Access-Reject",
                 "2026-07-11 14:03:00", "mac_mismatch", "10.50.0.3",
                 "AA:BB:CC:DD:EE:FF"))

        data = fetch_login_events(1, actor="subscriber", source="network")
        rows = [r for r in data["rows"] if r["username"] == "0598550496"]
        assert rows, "the network attempt row should appear"
        r = rows[0]
        assert r["mac"] == "AA:BB:CC:DD:EE:FF"     # the attempted MAC is shown
        assert r["success"] is False
        assert "MAC" in r["reason"] or "مطابق" in r["reason"]   # reason labelled


def test_missing_calling_station_falls_back_gracefully(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.login_events import fetch_login_events
        # an older row (no MAC captured) must not break the report.
        with transaction() as c:
            c.execute(
                "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
                "authdate, class, nas, calling_station) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (1, "bukshuku", "***", "Access-Accept",
                 "2026-07-11 11:30:00", "", "10.50.0.3", ""))
        data = fetch_login_events(1, actor="subscriber", source="network")
        r = [x for x in data["rows"] if x["username"] == "bukshuku"][0]
        assert r["mac"] == ""          # empty, no crash
        assert r["success"] is True
