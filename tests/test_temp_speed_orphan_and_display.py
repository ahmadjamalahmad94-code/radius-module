"""SRE/QA: temporary-speed «stuck» state — root-cause regression guards.

The user-reported symptom on the subscriber edit page: the toggle reads ON,
the download/upload speed fields are EMPTY, the countdown is 00:00 and a red
"لا يوجد وقت انتهاء محفوظ. احفظ لتثبيت العداد" shows — and it never clears, so
the throttled session never returns to its normal speed.

Two root causes are locked here:

1. ORPHAN ROWS: a subscriber flagged ``temporary_speed = 1`` but with NO
   computable window (legacy rows, a half-written state, or metadata wiped by
   an unrelated save). ``expire_due_temp_speeds`` used to skip these forever
   (it only reverted rows whose end-time had *passed*), so the throttle stuck
   and the UI stayed broken. It now treats "flagged but no end" as expired and
   restores the normal rate + clears the flag.

2. STALE `advanced` SHADOWING: older profile saves mirrored the window/speeds
   into the metadata ``advanced`` group. The revert/cancel only cleared the
   TOP-LEVEL keys, so the stale ``advanced`` copies kept shadowing the
   authoritative values on the edit page (empty/stale fields after a
   cancel/expire). The display now reads ONLY the authoritative sources.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ts_orphan_")
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


def _client(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    return client


def _row(app, username="tsuser"):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT temporary_speed, download_speed_kbps, upload_speed_kbps, metadata "
            "FROM subscribers WHERE username=?", (username,)).fetchone()


# ── Fix 1: orphan rows are reverted ─────────────────────────────────────────

def test_orphan_flag_with_no_window_is_reverted(app):
    """temporary_speed=1 + no window metadata ⇒ reverted (flag cleared, speed
    restored to the plan default). Previously skipped forever."""
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction, db
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,status,created_at,"
                "temporary_speed,bandwidth_control_enabled,download_speed_kbps,"
                "upload_speed_kbps,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (1, "orph", "pw", "enabled", now, 1, 1, 256, 256, "{}"),
            )
        from app.radius.services.temp_speed import expire_due_temp_speeds
        n = expire_due_temp_speeds(tenant_id=1)
        assert n == 1
        r = db().execute(
            "SELECT temporary_speed, download_speed_kbps FROM subscribers "
            "WHERE username='orph'").fetchone()
        assert r["temporary_speed"] == 0
        # no snapshot + no custom override ⇒ cleared to plan default (0)
        assert r["download_speed_kbps"] == 0


def test_active_window_is_not_reverted_early(app):
    """A still-valid future window is NOT touched by the sweep (guard against
    the orphan fix over-reaching)."""
    now = datetime.utcnow()
    meta = {
        "temporary_speed_from": now.isoformat(timespec="seconds"),
        "temporary_speed_to": (now + timedelta(minutes=20)).isoformat(timespec="seconds"),
        "temporary_speed_duration_minutes": 20,
        "temporary_speed_active": 1,
    }
    with app.app_context():
        from app.radius.db.connection import transaction, db
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,status,created_at,"
                "temporary_speed,bandwidth_control_enabled,download_speed_kbps,"
                "upload_speed_kbps,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (1, "active", "pw", "enabled", now.isoformat() + "Z",
                 1, 1, 1000, 1000, json.dumps(meta)),
            )
        from app.radius.services.temp_speed import expire_due_temp_speeds
        assert expire_due_temp_speeds(tenant_id=1) == 0
        r = db().execute(
            "SELECT temporary_speed FROM subscribers WHERE username='active'").fetchone()
        assert r["temporary_speed"] == 1


# ── Fix 2: authoritative display, no stale `advanced` shadowing ──────────────

def _seed_plain(app):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,status,created_at) "
                "VALUES(?,?,?,?,?)", (1, "tsuser", "pw", "enabled", now))


def _csrf(client):
    client.get("/admin/radius/users/tsuser/edit")
    with client.session_transaction() as s:
        return s.get("_csrf_token")


def _render_window(client):
    body = client.get("/admin/radius/users/tsuser/edit").get_data(as_text=True)
    out = {}
    for name in ("temporary_speed_from", "temporary_speed_to"):
        m = re.search(r'name="%s"\s+value="([^"]*)"' % name, body)
        out[name] = m.group(1) if m else "<NF>"
    return out


def test_apply_then_cancel_clears_rendered_window(app):
    """After applying and then cancelling temp speed, the edit page renders an
    EMPTY window (no stale `advanced` copy shadowing the cleared top level)."""
    _seed_plain(app)
    c = _client(app)

    # apply via the profile form
    c.post("/admin/radius/users/tsuser", data={
        "_csrf_token": _csrf(c), "username": "tsuser", "status": "enabled",
        "service_type": "hotspot", "temporary_speed": "on",
        "temporary_speed_duration_minutes": "30",
        "temporary_download_speed_kbps": "5000",
        "temporary_upload_speed_kbps": "2000",
        "temporary_speed_from": "", "temporary_speed_to": "",
    })
    w = _render_window(c)
    assert w["temporary_speed_from"] and w["temporary_speed_to"], "window must persist after apply"

    # cancel (toggle off — no temporary_speed field submitted)
    c.post("/admin/radius/users/tsuser", data={
        "_csrf_token": _csrf(c), "username": "tsuser", "status": "enabled",
        "service_type": "hotspot",
        "temporary_speed_duration_minutes": "30",
    })
    assert _row(app)["temporary_speed"] == 0
    w = _render_window(c)
    assert w["temporary_speed_from"] == "", f"stale from leaked: {w}"
    assert w["temporary_speed_to"] == "", f"stale to leaked: {w}"


def test_revert_purges_advanced_group_copies(app):
    """_revert_one strips the mirrored temp keys from the `advanced` group too,
    so nothing can shadow the authoritative top-level state later."""
    now = datetime.utcnow()
    meta = {
        "temporary_speed_from": (now - timedelta(minutes=40)).isoformat(timespec="seconds"),
        "temporary_speed_to": (now - timedelta(minutes=10)).isoformat(timespec="seconds"),
        "temporary_speed_duration_minutes": 30,
        "temporary_speed_active": 1,
        "advanced": {
            "temporary_speed_from": "2020-01-01T00:00:00",
            "temporary_speed_to": "2020-01-01T00:30:00",
            "temporary_download_speed_kbps": "9999",
            "temporary_upload_speed_kbps": "8888",
        },
    }
    with app.app_context():
        from app.radius.db.connection import transaction, db
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,status,created_at,"
                "temporary_speed,bandwidth_control_enabled,download_speed_kbps,"
                "upload_speed_kbps,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (1, "tsuser", "pw", "enabled", now.isoformat() + "Z",
                 1, 1, 512, 512, json.dumps(meta)),
            )
        from app.radius.services.temp_speed import expire_due_temp_speeds
        assert expire_due_temp_speeds(tenant_id=1) == 1
        m = json.loads(_row(app)["metadata"])
        adv = m.get("advanced") or {}
        for k in ("temporary_speed_from", "temporary_speed_to",
                  "temporary_download_speed_kbps", "temporary_upload_speed_kbps"):
            assert k not in adv, f"stale advanced.{k} survived revert"
