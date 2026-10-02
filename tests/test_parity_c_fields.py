"""Parity round (team c, 2026-10-02) — every field wired end-to-end.

Regression tests for the BROKEN fields found comparing the app and the web
in the network domain:
  1. web NAS edit dropped «إصدار RouterOS» (create-only field list)
  2. web network-device edit could not CLEAR ip/mac/location/notes
  3. web smart-alerts: «حلقة (لوب)» never saved; per-router rows pre-filled
     with EFFECTIVE values froze the global defaults on save (web + API)
  4. API router-alerts PATCH reset omitted global toggles to True
  5. bandwidth schedule edit without a days field wiped days_csv;
     restore_mode is a closed list (the web's three)
  6. web network-policy edit could not turn «مفعّلة»/«الوضع الآمن» off
  7. API web-block target skipped the analyzer; walled-garden
     «dst_address_list» was emitted as dst-address=
  8. speed profile units: Gbps on the web, closed list in the API, rename
     to a taken name → 409; web «burst» cleared when emptied
  9. API network-device PATCH refused a device whose router was archived
 (card adjust-time now also returns adjustment.coa — additive; the app's
  message helpers are covered by test/parity_c_sessions_outcomes_test.dart)
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="hr_parity_c_")
    mp.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    mp.setenv("HOBERADIUS_NO_WORKER", "1")
    mp.setenv("HOBERADIUS_NO_SEED", "1")
    mp.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
              os.path.join(tmp, "clients-wizard"))
    mp.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    mp.delenv("HOBERADIUS_ENV", raising=False)
    mp.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    mp.undo()
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _db(app):
    from app.radius.db.connection import db
    return db()


_OWNER: dict = {}


def _login_web(client):
    # One super admin for the whole module: the FIRST admin is the owner
    # (mt:* pages such as /alerts are owner-only).
    from app.radius.db.repos import admins_repo
    u = _OWNER.get("u")
    if not u:
        u = _OWNER["u"] = f"pc_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="pc-pass-1", full_name="PC",
                                 is_super_admin=True)
        # owner-like (co-owner): the mt:* pages (/alerts, network policy)
        # are for the owner; the min-id admin of this DB is not ours.
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE admins SET is_co_owner=1 WHERE username=?", (u,))
    res = client.post("/admin/radius/login", data={"username": u, "password": "pc-pass-1"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/devices/new")
    with client.session_transaction() as sess:
        sess["is_super_admin"] = True
        return sess["_csrf_token"]


def _nas(client, **over):
    body = {"name": f"pc-{uuid4().hex[:8]}", "address": f"192.0.2.{10 + len(over)}",
            "secret": "s3cret-ok", "enabled": False}
    body.update(over)
    res = client.post("/api/v1/nas", json=body, headers=AUTH)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


# ── 1 ─────────────────────────────────────────────────────────────

def test_web_nas_edit_saves_ros_version(app, client):
    nid = _nas(client, address="192.0.2.41", ros_version="7")["id"]
    token = _login_web(client)
    res = client.post(f"/admin/radius/devices/{nid}", data={
        "_csrf_token": token, "name": "pc-ros", "address": "192.0.2.41",
        "secret": "", "vendor": "mikrotik", "nas_type": "hotspot",
        "ros_version": "6", "auth_port": "1812", "acct_port": "1813",
        "coa_port": "3799", "api_port": "8728", "ssh_port": "22"})
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:400]
    with app.app_context():
        row = _db(app).execute("SELECT ros_version FROM nas_devices WHERE id=?",
                               (nid,)).fetchone()
    assert row["ros_version"] == "6"
    # read back through the API the app uses
    got = client.get(f"/api/v1/nas/{nid}", headers=AUTH).get_json()["data"]
    assert got["ros_version"] == "6"


# ── 2 ─────────────────────────────────────────────────────────────

def test_web_network_device_edit_can_clear_text_fields(app, client):
    nid = _nas(client, address="192.0.2.42")["id"]
    res = client.post("/api/v1/network-devices", json={
        "router_id": nid, "name": "ap-1", "ip_address": "10.9.0.5",
        "location": "roof", "notes": "n1"}, headers=AUTH)
    assert res.status_code in {200, 201}, res.get_json()
    data = res.get_json()["data"]
    did = (data.get("device") or data)["id"]
    token = _login_web(client)
    res = client.post(f"/admin/radius/network/devices/{did}", data={
        "_csrf_token": token, "router_id": str(nid), "name": "ap-1",
        "device_type": "ap", "ip_address": "", "mac_address": "",
        "location": "", "management_port": "80", "notes": ""})
    assert res.status_code in {302, 303}
    with app.app_context():
        row = _db(app).execute(
            "SELECT ip_address, location, notes FROM network_devices WHERE id=?",
            (did,)).fetchone()
    assert (row["ip_address"] or "") == ""
    assert row["location"] == "" and row["notes"] == ""


# ── 3 / 4 ─────────────────────────────────────────────────────────

def test_web_alerts_loop_saved_and_router_override_inherits(app, client):
    nid = _nas(client, address="192.0.2.43")["id"]
    token = _login_web(client)
    res = client.post("/admin/radius/alerts/settings", data={
        "_csrf_token": token, "enabled": "1", "telegram": "1", "offline": "1",
        "high_traffic": "1", "high_usage": "1",  # loop UNCHECKED
        "offline_after_min": "1", "default_speed_mbps": "100",
        "default_usage_gb": "200", "usage_window": "bogus",
        f"r_{nid}_present": "1", f"r_{nid}_enabled": "1",
        f"r_{nid}_offline": "", f"r_{nid}_speed": "", f"r_{nid}_usage": "50",
        f"r_{nid}_window": ""})
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.services import smart_alerts
        from app.radius.db.repos import router_alert_settings_repo
        glob = smart_alerts.global_settings(1)
        ov = router_alert_settings_repo.list_for_tenant(1)[nid]
    assert glob["loop"] is False or glob["loop"] in (0, "0")
    assert int(glob["offline_after_min"]) == 2          # floor like the API
    assert glob["usage_window"] == "day"                 # closed list
    # empty boxes = inherit (NULL), not a frozen copy of the default
    assert ov["offline_after_min"] is None and ov["normal_speed_mbps"] is None
    assert ov["usage_window"] is None and ov["normal_usage_gb"] == 50
    # the page renders the OVERRIDE (empty) with the default as placeholder
    html = client.get("/admin/radius/alerts").get_data(as_text=True)
    assert f'name="r_{nid}_speed" value="" placeholder="100"' in html
    assert f'name="r_{nid}_usage" value="50"' in html

    # a later global change reaches the router (not frozen)
    api = client.get("/api/v1/router-alerts/settings", headers=AUTH).get_json()["data"]
    row = next(r for r in api["routers"] if r["id"] == nid)
    assert row["override"] == {"offline_after_min": None, "normal_speed_mbps": None,
                               "normal_usage_gb": 50, "usage_window": None}
    res = client.patch("/api/v1/router-alerts/settings", headers=AUTH,
                       json={"settings": {"default_speed_mbps": 300}})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    row = next(r for r in data["routers"] if r["id"] == nid)
    assert row["normal_speed_mbps"] == 300
    # partial PATCH: telegram/high_usage untouched (were reset to True before)
    client.patch("/api/v1/router-alerts/settings", headers=AUTH,
                 json={"settings": {"telegram": False}})
    client.patch("/api/v1/router-alerts/settings", headers=AUTH,
                 json={"settings": {"default_usage_gb": 250}})
    s = client.get("/api/v1/router-alerts/settings", headers=AUTH).get_json()["data"]["settings"]
    assert s["telegram"] in (False, 0, "0")
    assert int(s["default_usage_gb"]) == 250 and int(s["default_speed_mbps"]) == 300


# ── 5 ─────────────────────────────────────────────────────────────

def _seed_plan(app, pid=971):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                      "VALUES (1, 't1', 'T1', ?)", (_now_iso(),))
            c.execute("INSERT OR REPLACE INTO access_plans(id, tenant_id, name, code, "
                      "speed_down_kbps, speed_up_kbps, created_at) "
                      "VALUES (?, 1, ?, ?, 4096, 1024, ?)",
                      (pid, f"PC plan {pid}", f"pc_{pid}", _now_iso()))
    return pid


def test_schedule_edit_without_days_keeps_them_and_restore_mode_is_closed(app, client):
    pid = _seed_plan(app)
    res = client.post("/api/v1/bandwidth-schedules", headers=AUTH, json={
        "name": "night", "target_type": "plan", "plan_id": pid, "priority": 5,
        "starts_at_time": "22:00", "ends_at_time": "06:00", "days_csv": "sat,sun",
        "speed_down_kbps": 2048, "speed_up_kbps": 512,
        "restore_mode": "keep_current"})
    assert res.status_code in {200, 201}, res.get_json()
    data = res.get_json()["data"]
    sid = (data.get("schedule") or data)["id"]
    token = _login_web(client)
    # the standalone page's edit form has no days field
    client.post(f"/admin/radius/bandwidth-schedules/{sid}/edit", data={
        "_csrf_token": token, "name": "night2", "priority": "4",
        "starts_at_time": "23:00", "ends_at_time": "05:00",
        "speed_down_kbps": "1024", "speed_up_kbps": "256",
        "restore_mode": "profile_default", "enabled": "1"})
    with app.app_context():
        row = _db(app).execute("SELECT name, days_csv, restore_mode FROM bandwidth_schedules "
                               "WHERE id=?", (sid,)).fetchone()
    assert row["name"] == "night2"
    assert row["days_csv"] == "sat,sun"
    assert row["restore_mode"] == "profile_default"
    bad = client.post("/api/v1/bandwidth-schedules", headers=AUTH, json={
        "name": "x", "target_type": "plan", "plan_id": pid,
        "starts_at_time": "01:00", "ends_at_time": "02:00",
        "speed_down_kbps": 100, "restore_mode": "previous_value"})
    assert bad.status_code in {400, 422}


# ── 6 ─────────────────────────────────────────────────────────────

def test_web_policy_edit_can_turn_enabled_and_fail_open_off(app, client):
    nid = _nas(client, address="192.0.2.44")["id"]
    res = client.post("/api/v1/network-policy/web-block/policies", headers=AUTH,
                      json={"name": "pc-wb", "router_id": nid})
    assert res.status_code in {200, 201}, res.get_json()
    pid = res.get_json()["data"]["id"]
    token = _login_web(client)
    res = client.post(f"/admin/radius/network-policy/web-block/{pid}/edit", data={
        "_csrf_token": token, "name": "pc-wb", "scope": "all_users"})
    assert res.status_code in {200, 302, 303}
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pol = wb.get_policy(1, pid)
    assert not pol["enabled"] and not pol["fail_open"]


# ── 7 ─────────────────────────────────────────────────────────────

def test_api_block_target_goes_through_the_analyzer(app, client):
    nid = _nas(client, address="192.0.2.45")["id"]
    pid = client.post("/api/v1/network-policy/web-block/policies", headers=AUTH,
                      json={"name": "pc-wb2", "router_id": nid}).get_json()["data"]["id"]
    url = f"/api/v1/network-policy/web-block/policies/{pid}/targets"
    res = client.post(url, headers=AUTH, json={"value": "93.184.216.34", "target_type": "domain"})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["target_type"] == "ip"   # analyzer wins
    bad = client.post(url, headers=AUTH, json={"value": "not a host!!", "target_type": "domain"})
    assert bad.status_code == 422


def test_walled_garden_address_list_entry_uses_dst_address_list():
    from app.radius.services import npc_walled_garden_planner as p
    out = p.plan({"id": 7, "name": "g", "router_id": 1, "hotspot_profile": "",
                  "enabled": 1},
                 [{"value": "allow-pay", "normalized_value": "allow-pay",
                   "entry_type": "dst_address_list", "status": "active",
                   "dst_port": "", "protocol": ""}])
    cmd = out.walled_garden_ops[0]
    assert cmd.attrs.get("dst-address-list") == "allow-pay"
    assert "dst-address" not in cmd.attrs


# ── 8 ─────────────────────────────────────────────────────────────

def test_speed_profile_units_and_rename_conflict(app, client):
    res = client.post("/api/v1/bandwidth-profiles", headers=AUTH, json={
        "name": "pc-giga", "rate_down": 1, "rate_down_unit": "gbps",
        "rate_up": 500, "rate_up_unit": "Mbps"})
    assert res.status_code == 201, res.get_json()
    p1 = res.get_json()["data"]
    assert p1["rate_down_unit"] == "Gbps"
    assert client.post("/api/v1/bandwidth-profiles", headers=AUTH, json={
        "name": "pc-bad", "rate_down": 1, "rate_down_unit": "Tbps"}).status_code == 422
    p2 = client.post("/api/v1/bandwidth-profiles", headers=AUTH, json={
        "name": "pc-other", "rate_down": 1, "rate_up": 1}).get_json()["data"]
    clash = client.patch(f"/api/v1/bandwidth-profiles/{p2['id']}", headers=AUTH,
                         json={"name": "pc-giga", "rate_down": 1, "rate_up": 1})
    assert clash.status_code == 409
    # the web edit keeps Gbps (the select offers it) and an emptied burst clears
    client.patch(f"/api/v1/bandwidth-profiles/{p1['id']}", headers=AUTH, json={
        "name": "pc-giga", "rate_down": 1, "rate_down_unit": "Gbps",
        "rate_up": 500, "rate_up_unit": "Mbps", "burst": "10M/5M"})
    token = _login_web(client)
    html = client.get(f"/admin/radius/bandwidth/{p1['id']}/edit").get_data(as_text=True)
    assert '<option value="Gbps" selected>' in html
    client.post(f"/admin/radius/bandwidth/{p1['id']}", data={
        "_csrf_token": token, "name": "pc-giga", "rate_down": "1",
        "rate_down_unit": "Gbps", "rate_up": "500", "rate_up_unit": "Mbps",
        "burst": "", "priority": "0"})
    got = client.get(f"/api/v1/bandwidth-profiles/{p1['id']}", headers=AUTH).get_json()["data"]
    assert got["rate_down_unit"] == "Gbps" and got["burst"] == ""


# ── 9 ─────────────────────────────────────────────────────────────

def test_api_device_patch_keeps_an_archived_router(app, client):
    nid = _nas(client, address="192.0.2.46")["id"]
    did_res = client.post("/api/v1/network-devices", json={
        "router_id": nid, "name": "sw-1"}, headers=AUTH).get_json()["data"]
    did = (did_res.get("device") or did_res)["id"]
    assert client.delete(f"/api/v1/nas/{nid}", headers=AUTH).status_code in {200, 204}
    res = client.patch(f"/api/v1/network-devices/{did}", headers=AUTH,
                       json={"router_id": nid, "name": "sw-1b"})
    assert res.status_code == 200, res.get_json()
    other = client.patch(f"/api/v1/network-devices/{did}", headers=AUTH,
                         json={"router_id": 987654})
    assert other.status_code == 422
