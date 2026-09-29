# -*- coding: utf-8 -*-
"""Owner decisions 2026-09-29: panel zone = Palestine, currency = shekel.

* ``billing.timezone`` defaults to **Asia/Gaza** (Asia/Hebron offered too — same
  rules): UTC+2 in winter, UTC+3 in summer. Every local computation goes through
  zoneinfo — never the fixed ``billing.timezone_offset`` (fallback only).
* ``billing.currency`` defaults to **ILS**; both pickers sit side by side on the
  settings page (Palestine zones first, live local-time preview) and a change
  applies at once on the web and the API.
* The API exposes the effective zone to the app: ``system.timezone`` (IANA name)
  and ``system.utc_offset_minutes`` (current offset) in ``/api/admin/me`` and
  ``/api/v1/settings``.

DST checks use a January date (+2) and a July date (+3). Run this file alone.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

TOKEN = "tz-pal-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
JAN = datetime(2026, 1, 15, 12, 0)   # Gaza winter: UTC+2
JUL = datetime(2026, 7, 15, 12, 0)   # Gaza summer: UTC+3


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tz.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "tz-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_tz")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    # Yield OUTSIDE the app context: each request then gets a fresh ``g`` (an
    # outer context would be reused, leaking ``g._api_authed`` between the
    # env token and the admin token requests).
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def req(app):
    with app.test_request_context("/"):
        import flask
        flask.g.tenant_id = 1
        yield


def _set(key, value):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, key, value)


# ─────────────── defaults ───────────────

def test_defaults_are_gaza_and_shekel(req):
    from app.radius.core import system_config as sc
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    from app.radius.routes.settings import _SETTINGS_KEYS
    assert sc._DEFAULTS["billing.timezone"] == "Asia/Gaza"
    assert sc._DEFAULTS["billing.currency"] == "ILS"
    assert sc.default_currency() == "ILS"
    catalog = {k: d for k, _l, d in _SETTINGS_KEYS}
    assert catalog["billing.timezone"] == "Asia/Gaza"
    assert catalog["billing.currency"] == "ILS"
    assert Tenant(id=None, slug="x", name="x").timezone == "Asia/Gaza"
    t = tenants_repo.get_tenant(1) if hasattr(tenants_repo, "get_tenant") else None
    if t is not None:
        assert t.currency == "ILS" and t.timezone == "Asia/Gaza"
    # Palestine first in the picker
    assert [z for z, _ in sc.PANEL_TIMEZONES[:2]] == ["Asia/Gaza", "Asia/Hebron"]
    assert sc.PANEL_TIMEZONES[0][1] == "غزة (فلسطين)"
    assert sc.PANEL_TIMEZONES[1][1] == "الخليل (فلسطين)"
    assert sc.CURRENCY_CHOICES[0][0] == "ILS"


# ─────────────── DST: January (+2) vs July (+3) ───────────────

@pytest.mark.parametrize("zone", ["Asia/Gaza", "Asia/Hebron"])
def test_effective_offset_follows_dst(req, zone):
    from app.radius.core.system_config import effective_timezone
    _set("billing.timezone", zone)
    _set("billing.timezone_offset", "3")          # legacy fixed value must NOT win
    winter, summer = effective_timezone(at=JAN), effective_timezone(at=JUL)
    assert winter["timezone"] == zone and summer["timezone"] == zone
    assert winter["utc_offset_minutes"] == 120
    assert summer["utc_offset_minutes"] == 180
    assert winter["local_time"] == "2026-01-15 14:00"
    assert summer["local_time"] == "2026-07-15 15:00"


def test_expiry_display_crosses_midnight_correctly(req):
    """dt_local on an expiry: 22:30 UTC is already the next day in Gaza — at
    00:30 in winter (+2) and 01:30 in summer (+3). A fixed +3 would show 01:30
    in January (an hour late)."""
    from app.radius.core.system_config import to_local, to_local_date
    assert to_local("2026-01-15 22:30:00") == "2026-01-16 00:30"
    assert to_local("2026-07-15 22:30:00") == "2026-07-16 01:30"
    assert to_local_date("2026-01-15T21:59:00Z") == "2026-01-15"   # 23:59 local
    assert to_local_date("2026-01-15T22:00:00Z") == "2026-01-16"   # 00:00 local
    assert to_local_date("2026-07-15T20:59:00Z") == "2026-07-15"
    assert to_local_date("2026-07-15T21:00:00Z") == "2026-07-16"


def test_day_boundaries_for_reports(req):
    from app.radius.core.system_config import local_period_utc_range
    assert local_period_utc_range("daily", "2026-01-15") == (
        "2026-01-14 22:00:00", "2026-01-15 22:00:00")
    assert local_period_utc_range("daily", "2026-07-15") == (
        "2026-07-14 21:00:00", "2026-07-15 21:00:00")
    # a month spanning the DST switch (late March in Palestine)
    start, end = local_period_utc_range("monthly", "2026-03")
    assert start == "2026-02-28 22:00:00"            # 1 March 00:00 at +2
    assert end == "2026-03-31 21:00:00"              # 1 April 00:00 at +3


def test_picked_expiry_is_stored_utc_per_season(req):
    """«23:59 local» typed by the operator → UTC with that date's offset."""
    from app.radius.core.system_config import from_local
    assert from_local("2026-01-15 23:59") == datetime(2026, 1, 15, 21, 59)
    assert from_local("2026-07-15 23:59") == datetime(2026, 7, 15, 20, 59)


def test_invalid_zone_falls_back_to_offset_not_crash(req):
    from app.radius.core.system_config import effective_timezone
    _set("billing.timezone", "Not/AZone")
    _set("billing.timezone_offset", "3")
    tz = effective_timezone(at=JAN)
    assert tz["timezone"] == "Etc/GMT-3" and tz["utc_offset_minutes"] == 180


def test_access_control_daily_window_uses_zoneinfo(req):
    """Block 08:00–09:00 local. 06:30 UTC in January is 08:30 in Gaza (+2) →
    in effect; the old fixed +3 made it 09:30 → not in effect."""
    from zoneinfo import ZoneInfo
    from app.radius.services import access_control as ac
    b = {"active": 1, "duration_mode": "daily_window",
         "window_start": "08:00", "window_end": "09:00"}
    gaza = ZoneInfo("Asia/Gaza")
    assert ac.is_block_in_effect(b, datetime(2026, 1, 15, 6, 30), tz=gaza)
    assert not ac.is_block_in_effect(b, datetime(2026, 7, 15, 6, 30), tz=gaza)  # 09:30 +3
    assert ac.is_block_in_effect(b, datetime(2026, 7, 15, 5, 30), tz=gaza)      # 08:30 +3
    assert ac.tz_offset_hours(1) in (2.0, 3.0)          # current Gaza offset, not 0


def test_access_schedule_default_now_is_panel_clock(req, monkeypatch):
    from app.radius.core import access_schedule, system_config
    fixed = datetime(2026, 1, 15, 8, 30, tzinfo=system_config.tenant_tzinfo())
    monkeypatch.setattr(system_config, "local_now", lambda tenant_id=None: fixed)
    sched = {"windows": [{"days": [], "from": "08:00", "to": "09:00"}]}
    assert access_schedule.is_allowed(sched) is True


# ─────────────── API exposure ───────────────

def _admin_token(client) -> dict:
    res = client.post("/api/admin/login",
                      json={"username": "owner_tz", "password": "owner-pass"})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def test_api_me_and_settings_expose_zone_and_offset(client):
    hdr = _admin_token(client)
    me = client.get("/api/admin/me", headers=hdr).get_json()["data"]["system"]
    assert me["timezone"] == "Asia/Gaza"
    assert me["utc_offset_minutes"] in (120, 180)
    assert me["currency"] == "ILS"
    assert me["tz_name"] == "Asia/Gaza" and me["tz_offset"] == me["utc_offset_minutes"] / 60
    st = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]
    assert st["system"]["timezone"] == "Asia/Gaza"
    assert st["system"]["utc_offset_minutes"] in (120, 180)
    assert st["settings"]["billing.timezone"] == "Asia/Gaza"
    assert st["settings"]["billing.currency"] == "ILS"


def test_api_patch_validates_and_applies_at_once(client):
    hdr = _admin_token(client)
    bad = client.patch("/api/v1/settings", headers=AUTH,
                       json={"billing.currency": "USD", "billing.timezone": "Mars/Base"})
    assert bad.status_code == 422
    assert "المنطقة الزمنية" in bad.get_json()["error"]["message"]
    # nothing half-saved
    st = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]
    assert st["system"]["currency"] == "ILS"
    ok = client.patch("/api/v1/settings", headers=AUTH,
                      json={"billing.currency": "usd", "billing.timezone": "Asia/Hebron"})
    assert ok.status_code == 200, ok.get_json()
    st = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]
    assert st["system"]["currency"] == "USD" and st["system"]["timezone"] == "Asia/Hebron"
    body = client.get("/api/admin/me", headers=hdr).get_json()
    assert "data" in body, body
    me = body["data"]["system"]
    assert me["currency"] == "USD" and me["timezone"] == "Asia/Hebron"


# ─────────────── web settings page ───────────────

def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_tz", "password": "owner-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/settings")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def test_settings_page_pickers_side_by_side_with_preview(client):
    _web_login(client)
    html = client.get("/admin/radius/settings").get_data(as_text=True)
    fin = html.split('data-st-panel="finance"', 1)[1].split('data-st-panel="time"', 1)[0]
    assert 'name="billing.currency"' in fin and 'name="billing.timezone"' in fin
    assert html.count('name="billing.timezone"') == 1        # moved, not duplicated
    assert fin.index("غزة (فلسطين)") < fin.index("الخليل (فلسطين)")
    assert '<option value="Asia/Gaza" selected>' in fin
    assert '<option value="ILS" selected>' in fin
    assert "الوقت المحلّيّ الآن" in fin and "data-st-tz-clock" in fin
    assert "(UTC+2)" in fin or "(UTC+3)" in fin


def test_settings_page_save_validates_and_applies(client):
    csrf = _web_login(client)
    res = client.post("/admin/radius/settings", data={
        "_csrf_token": csrf, "billing.timezone": "Mars/Base"})
    assert res.status_code in {302, 303}
    from app.radius.db.repos import tenants_repo
    assert tenants_repo.get_setting(1, "billing.timezone", "") in ("", "Asia/Gaza")
    res = client.post("/admin/radius/settings", data={
        "_csrf_token": csrf, "billing.timezone": "Asia/Hebron", "billing.currency": "JOD"})
    assert res.status_code in {302, 303}
    st = client.get("/api/v1/settings", headers=AUTH).get_json()["data"]["system"]
    assert st["timezone"] == "Asia/Hebron" and st["currency"] == "JOD"
