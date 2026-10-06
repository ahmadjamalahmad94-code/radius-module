"""FIELDS follow-up (owner decisions 2026-10-06, second pass).

1. Plan «كل الأيام» (``all_days``) REMOVED: no input (the whole «سياسات التجديد
   والتوفر» section went with it), the save path stops writing it, the API
   ignores the key; the stored value is untouched.
2. «IP ثابت» + «IP PPPoE» MERGED into ONE field ``static_ip`` — IPv4 only,
   unique in the tenant. Migration 198 copies PPPoE addresses (outcomes logged
   in ``data_fix_log``); the API maps the old ``pppoe_ip`` key; authorize sends
   Framed-IP-Address from the single field (never an IPv6 legacy value).
3. Bandwidth schedule «طريقة الرجوع»: exactly two modes, both EFFECTIVE in the
   worker at window end — «رجوع مباشر بدون فصل» = rate CoA (no disconnect),
   «فصل الجلسة» = disconnect (no rate CoA). Migration 199 maps old values.
4. Auto-renew catch-up window 15 → 60 minutes (still once per period).
5. Migration 200: a per-router alert threshold EQUAL to the tenant's current
   global value becomes NULL (= inherit); real overrides stay.

Every test here fails on 0d6ce6f1. Run alone (one file per process).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

TOKEN = "fields-fu-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
MIG_DIR = Path(__file__).resolve().parents[1] / "app" / "radius" / "db" / "migrations"
M198 = "198_merge_pppoe_ip_into_static_ip.py"
M199 = "199_bandwidth_schedule_restore_two_modes.sql"
M200 = "200_router_alert_thresholds_inherit_global.py"

# 2026-10-09 = Friday; Gaza = UTC+3 (summer time) then. Naive = UTC.
IN_WINDOW = datetime(2026, 10, 9, 18, 0)      # 21:00 local
AFTER_WINDOW = datetime(2026, 10, 9, 20, 30)  # 23:30 local


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_fu.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fields-fu-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fu")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-fu")
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
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(**cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": "fu_" + uuid4().hex[:6], "duration_minutes": 30 * 1440,
              "price": 30.0, "currency": "ILS", "speed_down_kbps": 4096,
              "speed_up_kbps": 1024, "enabled": 1, "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(username=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "fu_" + uuid4().hex[:8]
    kw.setdefault("expire_at", datetime.utcnow() + timedelta(days=10))
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret1",
        status="enabled", full_name="Follow Up", **kw))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_fu", "password": "owner-pass-fu"})
    assert res.status_code in {302, 303}, res.status_code


def _csrf(client) -> str:
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _internal_auth(client, username, password="secret1"):
    res = client.post("/api/v1/internal/auth", json={
        "User-Name": username, "User-Password": password,
        "Calling-Station-Id": "AA:BB:CC:00:00:01",
        "NAS-IP-Address": "10.0.0.1", "NAS-Port-Type": "Virtual"})
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    return res.get_json()


def _rerun(name: str) -> int:
    from app.radius.db.migrations_runner import run_pending_migrations
    _db().execute("DELETE FROM _migrations WHERE name = ?", (name,))
    return run_pending_migrations()


def _load(name: str):
    spec = importlib.util.spec_from_file_location("m_" + name[:3], str(MIG_DIR / name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═════════════════════ 1. plan «كل الأيام» removed ═════════════════════

def test_plan_form_has_no_all_days_nor_empty_renewal_section(client, app):
    _web_login(client)
    pid = _plan()
    for url in ("/admin/radius/plans/new", f"/admin/radius/plans/{pid}/edit"):
        html = client.get(url).get_data(as_text=True)
        assert 'name="all_days"' not in html, url
        assert 'id="pl-adv"' not in html and 'href="#pl-adv"' not in html, url
        assert "سياسات التجديد والتوفر" not in html, url


def test_plan_save_stops_writing_all_days_and_keeps_old_value(client, app):
    _web_login(client)
    client.get("/admin/radius/plans/new")
    base = {"_csrf_token": _csrf(client), "plan_type": "time", "service_type": "Hotspot",
            "speed_down_kbps": "4096", "speed_up_kbps": "1024", "enabled": "1",
            "all_days": "1"}
    name = "fu-new-" + uuid4().hex[:5]
    res = client.post("/admin/radius/plans", data={**base, "name": name})
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:300]
    meta = json.loads(_db().execute("SELECT metadata FROM access_plans WHERE name=?",
                                    (name,)).fetchone()["metadata"] or "{}")
    assert "all_days" not in json.dumps(meta)
    # an old stored value survives an edit save untouched
    pid = _plan(metadata=json.dumps({"advanced": {"all_days": "1"}}))
    client.get(f"/admin/radius/plans/{pid}/edit")
    res = client.post(f"/admin/radius/plans/{pid}",
                      data={**base, "_csrf_token": _csrf(client), "name": "fu-ed-" + uuid4().hex[:5],
                            "all_days": ""})
    assert res.status_code in (302, 303)
    meta = json.loads(_db().execute("SELECT metadata FROM access_plans WHERE id=?",
                                    (pid,)).fetchone()["metadata"])
    assert meta["advanced"]["all_days"] == "1"


def test_plan_api_ignores_all_days_silently(client, app):
    r = client.post("/api/v1/profiles", headers=AUTH, json={
        "name": "fu-api-" + uuid4().hex[:5], "speed_down_kbps": 2048,
        "speed_up_kbps": 512, "all_days": True})
    assert r.status_code == 201, r.get_json()
    from app.api.v1 import profiles
    assert "all_days" in profiles._IGNORED_FIELDS


# ═════════════════════ 2. one «IP ثابت» field ═════════════════════

def test_migration_198_merges_pppoe_ip_on_seeded_db(app):
    a = _sub(pppoe_ip="10.50.0.1")                          # copied
    b = _sub(static_ip="10.50.0.2", pppoe_ip="10.50.0.2")   # same
    c = _sub(static_ip="10.50.0.3", pppoe_ip="10.50.0.4")   # conflict
    d = _sub(pppoe_ip="2001:db8::1")                        # ipv6
    e = _sub(pppoe_ip="10.0.0.999")                         # invalid
    f = _sub(pppoe_ip="10.50.0.3")                          # duplicate (c owns it)
    g = _sub(pppoe_ip="10.50.0.9")                          # copied (first claim)
    h = _sub(pppoe_ip="10.50.0.9")                          # duplicate (g, same run)
    i = _sub(pppoe_ip="10.50.0.10")                         # deleted
    _db().execute("UPDATE subscribers SET deleted_at = ? WHERE username = ?",
                  (datetime.utcnow().isoformat(), i.username))
    plain = _sub()

    assert _rerun(M198) == 1
    st = {s.username: (_db().execute("SELECT static_ip FROM subscribers WHERE username=?",
                                     (s.username,)).fetchone()[0] or "")
          for s in (a, b, c, d, e, f, g, h, i, plain)}
    assert st == {a.username: "10.50.0.1", b.username: "10.50.0.2",
                  c.username: "10.50.0.3", d.username: "", e.username: "",
                  f.username: "", g.username: "10.50.0.9", h.username: "",
                  i.username: "", plain.username: ""}
    # nothing deleted: the old column keeps its value
    assert _get(d.username).pppoe_ip == "2001:db8::1"
    log = {r["subject"]: r["outcome"] for r in _db().execute(
        "SELECT subject, outcome FROM data_fix_log WHERE migration = ?",
        (M198[:-3],)).fetchall()}
    assert log == {a.username: "copied", c.username: "conflict", d.username: "ipv6",
                   e.username: "invalid", f.username: "duplicate", g.username: "copied",
                   h.username: "duplicate", i.username: "deleted"}
    # idempotent: a second pass copies nothing more
    assert _load(M198).up(_db()).get("copied", 0) == 0
    assert _get(a.username).static_ip == "10.50.0.1"


def test_migration_198_is_safe_without_the_columns(tmp_path):
    c = sqlite3.connect(str(tmp_path / "bare.db"))
    assert _load(M198).up(c) == {}
    c.execute("CREATE TABLE subscribers (id INTEGER PRIMARY KEY, username TEXT)")
    assert _load(M198).up(c) == {}


def test_static_ip_is_ipv4_only_and_unique_in_arabic(client, app):
    _sub(static_ip="10.60.0.5")
    s = _sub()

    def patch(body):
        return client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json=body)

    res = patch({"static_ip": "2001:db8::5"})
    assert res.status_code == 422, res.get_json()
    assert "IPv4" in res.get_json()["error"]["message"]
    for bad in ("999.1.1.1", "abc"):
        res = patch({"static_ip": bad})
        assert res.status_code == 422, (bad, res.get_json())
        assert "IP" in res.get_json()["error"]["message"]
    res = patch({"static_ip": "10.60.0.5"})
    assert res.status_code == 422
    assert "لمشتركٍ آخر" in res.get_json()["error"]["message"]
    res = patch({"static_ip": "10.60.0.6"})
    assert res.status_code == 200, res.get_json()
    assert _get(s.username).static_ip == "10.60.0.6"


def test_api_maps_legacy_pppoe_ip_onto_static_ip(client, app):
    s = _sub()
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"pppoe_ip": "10.61.0.1"})
    assert res.status_code == 200, res.get_json()
    after = _get(s.username)
    assert after.static_ip == "10.61.0.1" and (after.pppoe_ip or "") == ""
    # with static_ip in the same body the old key is ignored
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"static_ip": "10.61.0.2", "pppoe_ip": "10.61.0.3"})
    assert res.status_code == 200, res.get_json()
    assert _get(s.username).static_ip == "10.61.0.2"
    # an empty legacy key clears nothing
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"pppoe_ip": ""})
    assert res.status_code == 200
    assert _get(s.username).static_ip == "10.61.0.2"
    # the mapped value goes through the same IPv4 rule
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"pppoe_ip": "2001:db8::9"})
    assert res.status_code == 422
    name = "fu_c_" + uuid4().hex[:6]
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": name, "password": "secret1", "pppoe_ip": "10.61.0.7"})
    assert res.status_code == 201, res.get_json()
    assert _get(name).static_ip == "10.61.0.7"


def test_authorize_sends_framed_ip_from_the_single_ipv4_field(client, app):
    st = _sub(static_ip="10.62.0.1")
    assert _internal_auth(client, st.username).get("reply:Framed-IP-Address") == "10.62.0.1"
    # a legacy, un-migrated PPPoE-only value is no longer an address source
    pp = _sub(pppoe_ip="10.62.0.2")
    out = _internal_auth(client, pp.username)
    assert out["control:Auth-Type"] == "Accept", out
    assert "reply:Framed-IP-Address" not in out
    # a stored IPv6 value is kept but never sent (Framed-IP-Address is IPv4)
    v6 = _sub(static_ip="2001:db8::7")
    out = _internal_auth(client, v6.username)
    assert out["control:Auth-Type"] == "Accept", out
    assert "reply:Framed-IP-Address" not in out
    assert _get(v6.username).static_ip == "2001:db8::7"


def test_web_form_single_ip_field_and_ipv6_hint(client, app):
    v6 = _sub(static_ip="2001:db8::7")
    ok = _sub(static_ip="10.63.0.1")
    _web_login(client)
    for url in ("/admin/radius/users/new", f"/admin/radius/users/{ok.username}/edit"):
        html = client.get(url).get_data(as_text=True)
        assert 'name="static_ip"' in html, url
        assert 'name="pppoe_ip"' not in html and 'id="uf-pppoe"' not in html, url
        assert "data-ip-fix" not in html, url
    html = client.get(f"/admin/radius/users/{v6.username}/edit").get_data(as_text=True)
    assert "data-ip-fix" in html and "ليس عنوان IPv4" in html


def test_web_save_keeps_stored_pppoe_ip_and_rejects_ipv6(client, app):
    from tests.test_fields_subs_decisions import _post, _scrape, _set
    s = _sub(pppoe_ip="10.64.0.1", static_ip="10.64.0.1")
    _web_login(client)
    fields, _types = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    # a forged old-form POST of pppoe_ip is ignored; the stored value stays
    res = _post(client, f"/admin/radius/users/{s.username}",
                _set(_set(fields, "remark", "web"), "pppoe_ip", "10.64.0.99"))
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    after = _get(s.username)
    assert (after.remark, after.pppoe_ip, after.static_ip) == ("web", "10.64.0.1", "10.64.0.1")
    fields, _types = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    res = _post(client, f"/admin/radius/users/{s.username}",
                _set(fields, "static_ip", "fe80::1"))
    assert res.status_code == 422
    with client.session_transaction() as sess:
        flashed = " ".join(m for _c, m in sess.get("_flashes", []))
    assert "IPv4" in res.get_data(as_text=True) + flashed
    assert _get(s.username).static_ip == "10.64.0.1"


# ═════════════════════ 3. «طريقة الرجوع»: two effective modes ═════════════════════

def _schedule(plan_id: int, restore_mode: str) -> dict:
    from app.radius.services.operations import get_operations_service
    return get_operations_service().create_bandwidth_schedule(tenant_id=1, actor="t", data={
        "name": "night-" + restore_mode, "target_type": "plan", "plan_id": plan_id,
        "priority": 5, "starts_at_time": "21:00", "ends_at_time": "22:00",
        "days_csv": "", "speed_down_kbps": 8000, "speed_up_kbps": 4000,
        "restore_mode": restore_mode, "enabled": True})


@pytest.fixture
def coa(monkeypatch):
    calls = {"rate": [], "disconnect": []}
    from app.radius.integration import radius_coa
    state = {"rate_ok": True}

    def rate(tid, username, *, new_rate_limit):
        calls["rate"].append((username, new_rate_limit))
        return SimpleNamespace(ok=state["rate_ok"], code_name="CoA-ACK" if state["rate_ok"] else "CoA-NAK")

    def disconnect(tid, username, *, session_ids=None):
        calls["disconnect"].append(username)
        return SimpleNamespace(ok=True, code_name="Disconnect-ACK")

    monkeypatch.setattr(radius_coa, "change_user_rate", rate)
    monkeypatch.setattr(radius_coa, "disconnect_user", disconnect)
    monkeypatch.setenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "1")
    calls["state"] = state
    return calls


def _ticks():
    from app.workers import bandwidth_schedule_worker as w
    w.reset_state_for_tests()
    assert w.tick_once(now=IN_WINDOW) == {"engaged": 1, "released": 0}
    return w


def test_worker_restore_disconnect_kicks_instead_of_coa(app, coa):
    pid = _plan(speed_down_kbps=20000, speed_up_kbps=5000)
    s = _sub(plan_id=pid)
    _schedule(pid, "disconnect")
    w = _ticks()
    assert [u for u, _r in coa["rate"]] == [s.username]      # engage = schedule rate CoA
    assert coa["disconnect"] == []
    coa["rate"].clear()
    assert w.tick_once(now=AFTER_WINDOW) == {"engaged": 0, "released": 1}
    assert coa["disconnect"] == [s.username]                # «فصل الجلسة»
    assert coa["rate"] == []                                # no rate CoA on return


def test_worker_restore_live_coa_never_disconnects(app, coa):
    pid = _plan(speed_down_kbps=20000, speed_up_kbps=5000)
    s = _sub(plan_id=pid)
    _schedule(pid, "profile_default")
    w = _ticks()
    coa["rate"].clear()
    coa["state"]["rate_ok"] = False        # even a NAK'd CoA must not turn into a kick
    assert w.tick_once(now=AFTER_WINDOW) == {"engaged": 0, "released": 1}
    assert coa["rate"] and coa["rate"][0][0] == s.username
    assert "20000k" in coa["rate"][0][1] or "20M" in coa["rate"][0][1]
    assert coa["disconnect"] == []


def test_restore_mode_whitelist_two_values_legacy_mapped_unknown_422(client, app):
    pid = _plan()
    from app.radius.services.operations import get_operations_service
    assert _schedule(pid, "keep_current")["restore_mode"] == "profile_default"
    assert _schedule(pid, "disconnect")["restore_mode"] == "disconnect"
    body = {"name": "x", "target_type": "plan", "plan_id": pid, "starts_at_time": "21:00",
            "ends_at_time": "22:00", "speed_down_kbps": 8000, "speed_up_kbps": 4000,
            "restore_mode": "weekly"}
    r = client.post("/api/v1/bandwidth-schedules", headers=AUTH, json=body)
    assert r.status_code == 422, r.get_json()
    r = client.post("/api/v1/bandwidth-schedules", headers=AUTH,
                    json={**body, "restore_mode": "disconnect"})
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["data"]["schedule"]["id"]
    r = client.patch(f"/api/v1/bandwidth-schedules/{sid}", headers=AUTH,
                     json={"restore_mode": "nope"})
    assert r.status_code == 422
    assert get_operations_service()._RESTORE_MODES == ("profile_default", "disconnect")


def test_migration_199_maps_old_restore_modes(app):
    pid = _plan()
    ids = {m: _schedule(pid, "profile_default")["id"]
           for m in ("keep_current", "manual", "DISCONNECT", "disconnect", "")}
    for mode, sid in ids.items():
        _db().execute("UPDATE bandwidth_schedules SET restore_mode=? WHERE id=?", (mode, sid))
    assert _rerun(M199) == 1
    got = {m: _db().execute("SELECT restore_mode FROM bandwidth_schedules WHERE id=?",
                            (sid,)).fetchone()[0] for m, sid in ids.items()}
    assert got == {"keep_current": "profile_default", "manual": "profile_default",
                   "DISCONNECT": "disconnect", "disconnect": "disconnect",
                   "": "profile_default"}


def test_web_selectors_offer_exactly_two_restore_modes(client, app):
    import re
    _web_login(client)
    pid = _plan()
    _schedule(pid, "disconnect")
    for url in ("/admin/radius/bandwidth-schedules", f"/admin/radius/plans/{pid}/edit"):
        html = client.get(url).get_data(as_text=True)
        selects = re.findall(r'<select[^>]*name="(?:sr_)?(?:edit_)?restore_mode[^"]*"[^>]*>(.*?)</select>',
                             html, re.S)
        assert selects, url
        for body in selects:
            vals = re.findall(r'<option value="([^"]+)"', body)
            assert vals == ["profile_default", "disconnect"], (url, vals)
        assert "رجوع مباشر بدون فصل" in html and "إبقاء آخر سرعة" not in html


# ═════════════════════ 4. auto-renew catch-up = 60 minutes ═════════════════════

def test_auto_renew_catches_up_within_the_last_hour_once(app, monkeypatch):
    from app.radius.services import admin_alerts, plan_lifecycle
    monkeypatch.setattr(admin_alerts, "dispatch", lambda *a, **k: None)
    assert plan_lifecycle.RENEW_LOOKBACK == timedelta(minutes=60)
    pid = _plan(auto_renew_mode="free")
    late = _sub(plan_id=pid, expire_at=datetime.utcnow() - timedelta(minutes=45))
    too_late = _sub(plan_id=pid, expire_at=datetime.utcnow() - timedelta(minutes=75))
    assert plan_lifecycle.sweep(1)["renewed"] == 1
    after = _get(late.username)
    # renewed from the expiry moment, by one period
    assert abs((after.expire_at - (late.expire_at + timedelta(days=30))).total_seconds()) < 2
    assert _get(too_late.username).expire_at == too_late.expire_at
    # never twice for one period
    _db().execute("UPDATE subscribers SET expire_at=? WHERE username=?",
                  (late.expire_at.isoformat(), late.username))
    assert plan_lifecycle.sweep(1)["renewed"] == 0


# ═════════════════════ 5. router thresholds equal to the global → inherit ═════════════════════

def test_migration_200_resets_thresholds_equal_to_global(app):
    from app.radius.db.repos import tenants_repo
    from app.radius.services import smart_alerts
    tenants_repo.set_setting(1, "network.alerts.default_speed_mbps", "150")
    tenants_repo.set_setting(1, "network.alerts.default_usage_gb", "junk")   # → default 200
    # offline_after_min / usage_window not stored → defaults 6 / 'day'
    rows = [
        (1, 6, 150, 200, "day"),      # frozen copy of the globals → all four inherit
        (2, 10, 150, 500, "month"),   # speed equals; the other three are overrides
        (3, None, None, None, None),  # already inheriting
        (4, 6, 100, 200, "Day"),      # 100 ≠ 150 (old default) → speed stays
    ]
    for rid, off, spd, gb, win in rows:
        _db().execute(
            "INSERT INTO router_alert_settings(tenant_id, router_id, enabled, "
            "offline_after_min, normal_speed_mbps, normal_usage_gb, usage_window, updated_at) "
            "VALUES(1,?,1,?,?,?,?,'')", (rid, off, spd, gb, win))
    assert _rerun(M200) == 1
    got = {r[0]: tuple(r[1:]) for r in _db().execute(
        "SELECT router_id, offline_after_min, normal_speed_mbps, normal_usage_gb, "
        "usage_window FROM router_alert_settings ORDER BY router_id").fetchall()}
    assert got == {1: (None, None, None, None), 2: (10, None, 500, "month"),
                   3: (None, None, None, None), 4: (None, 100, None, None)}
    # the routers now follow a changed global
    tenants_repo.set_setting(1, "network.alerts.default_speed_mbps", "300")
    glob = smart_alerts.global_settings(1)
    per = {1: dict(router_id=1, **dict(zip(
        ("offline_after_min", "normal_speed_mbps", "normal_usage_gb", "usage_window"), got[1])))}
    assert smart_alerts.effective_for_router(1, glob, per)["normal_speed_mbps"] == 300
    log = _db().execute("SELECT COUNT(*) FROM data_fix_log WHERE migration=?",
                        (M200[:-3],)).fetchone()[0]
    assert log == 3                     # routers 1, 2, 4
    # idempotent
    assert _load(M200).up(_db())["cleared"] == 0


def test_migration_200_is_safe_without_tables(tmp_path):
    c = sqlite3.connect(str(tmp_path / "bare.db"))
    assert _load(M200).up(c) == {}
    c.execute("CREATE TABLE router_alert_settings (tenant_id INTEGER, router_id INTEGER, "
              "offline_after_min INTEGER, normal_speed_mbps INTEGER, normal_usage_gb INTEGER, "
              "usage_window TEXT)")
    c.execute("INSERT INTO router_alert_settings VALUES (1, 7, 6, 100, 200, 'day')")
    # no tenant_settings table → the code defaults are the globals
    assert _load(M200).up(c)["cleared"] == 4
    assert c.execute("SELECT offline_after_min, normal_speed_mbps, normal_usage_gb, "
                     "usage_window FROM router_alert_settings").fetchone() == (None,) * 4
