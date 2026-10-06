"""zero-w2 — app<->web parity: operational reports + bandwidth schedules.

1. ``/api/v1/operational-reports/login-status`` and ``manager-login-status``
   read the SAME source as the web pages (``login_events.fetch_login_events``)
   — not the subscribers / admins rosters — with the web's filters.
2. Report date filters are LOCAL (Asia/Gaza) days, inclusive (login events
   too — they compared on UTC days).
3. Bandwidth schedules: edit (partial PATCH) / enable toggle / delete from the
   API with the web's validation and permissions.
4. A ``subscriber_group`` schedule affects the group's MEMBERS only — not
   everyone on the group's default plan.

Run this file alone (one file per process).
"""
from __future__ import annotations

import os
import sys

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "zero_w2.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                         "VALUES (1,'t1','T1','2026-01-01 00:00:00')")
            conn.execute("INSERT OR IGNORE INTO access_plans(id, tenant_id, name, code, created_at) "
                         "VALUES (940,1,'P940','p940','2026-01-01 00:00:00')")
    yield flask_app
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _web_auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "zw2_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "zw2-csrf"


def _exec(app, sql, params=()):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            cur = conn.execute(sql, params)
            return cur.lastrowid


def _seed_logins(app):
    # a subscriber that never logged in: the OLD login-status roster listed it.
    _exec(app, "INSERT INTO subscribers(tenant_id, username, password, plan_id, status, created_at) "
               "VALUES (1,'roster_only','x',940,'enabled','2026-01-01 00:00:00')")
    # network auth: 22:30 UTC on 06-07 = 01:30 on 06-08 in Gaza (UTC+3, DST).
    _exec(app, "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) "
               "VALUES (1,'net_late','','Access-Accept','2026-06-07 22:30:00','','10.0.0.1')")
    _exec(app, "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) "
               "VALUES (1,'net_noon','','Access-Reject','2026-06-07 09:00:00','password_wrong','10.0.0.1')")
    # panel logins (audit_log, ISO 'T' timestamps).
    for actor, status, when in (("mgr_a", "success", "2026-06-08T05:00:00Z"),
                                ("mgr_b", "failed", "2026-06-07T09:00:00Z")):
        _exec(app, "INSERT INTO audit_log(tenant_id, actor, action, target_type, target_id, "
                   "result_status, created_at) VALUES (1,?,?,?,?,?,?)",
              (actor, "auth_login" if status == "success" else "auth_login_failed",
               "admin", actor, status, when))


def _api_report(client, slug, **params):
    res = client.get(f"/api/v1/operational-reports/{slug}", headers=AUTH,
                     query_string=params)
    assert res.status_code == 200, res.get_json()
    return res.get_json()["data"]


# ─────────────────────────── 1+2: report parity ───────────────────────────

def test_login_status_api_reads_login_events_like_web(app, client):
    _seed_logins(app)
    data = _api_report(client, "login-status", limit=50)
    names = [r["username"] for r in data["items"]]
    assert "roster_only" not in names                 # not the subscribers roster
    assert {"net_late", "net_noon", "mgr_a", "mgr_b"} <= set(names)
    with app.test_request_context():
        from app.radius.services.login_events import fetch_login_events
        web_rows = fetch_login_events(1)["rows"]      # what /reports/login_status renders
    assert names == [r["username"] for r in web_rows][:50]
    assert data["matched"] == len(web_rows)
    # and the web page itself shows the same events
    _web_auth(client)
    html = client.get("/admin/radius/reports/login_status").get_data(as_text=True)
    for n in names:
        assert n in html
    assert "roster_only" not in html


def test_login_status_result_and_source_filters_match_web(app, client):
    _seed_logins(app)
    fails = _api_report(client, "login-status", result="fail")["items"]
    assert sorted(r["username"] for r in fails) == ["mgr_b", "net_noon"]
    net = _api_report(client, "login-status", source="network")["items"]
    assert sorted(r["username"] for r in net) == ["net_late", "net_noon"]


def test_manager_login_status_api_is_manager_login_attempts(app, client):
    _seed_logins(app)
    data = _api_report(client, "manager-login-status")
    assert sorted(r["username"] for r in data["items"]) == ["mgr_a", "mgr_b"]
    assert all(r["actor_type"] == "admin" for r in data["items"])
    with app.test_request_context():
        from app.radius.services.login_events import fetch_login_events
        web_rows = fetch_login_events(1, actor="admin")["rows"]
    assert [r["username"] for r in data["items"]] == [r["username"] for r in web_rows]


def test_login_report_dates_are_local_days(app, client):
    _seed_logins(app)
    # 22:30Z on 06-07 is 01:30 on 06-08 in Gaza — it belongs to the 8th.
    day = _api_report(client, "login-status", date_from="2026-06-08", date_to="2026-06-08")
    assert sorted(r["username"] for r in day["items"]) == ["mgr_a", "net_late"]
    prev = _api_report(client, "login-status", date_from="2026-06-07", date_to="2026-06-07")
    assert sorted(r["username"] for r in prev["items"]) == ["mgr_b", "net_noon"]
    mgr = _api_report(client, "manager-login-status",
                      date_from="2026-06-08", date_to="2026-06-08")
    assert [r["username"] for r in mgr["items"]] == ["mgr_a"]


def test_report_paging_offset(app, client):
    _seed_logins(app)
    full = [r["username"] for r in _api_report(client, "login-status", limit=10)["items"]]
    page2 = [r["username"] for r in
             _api_report(client, "login-status", limit=2, offset=2)["items"]]
    assert page2 == full[2:4]


def test_bad_report_date_is_422(client):
    res = client.get("/api/v1/operational-reports/login-status?date_from=2026-13-45",
                     headers=AUTH)
    assert res.status_code == 422


# ─────────────────────── 3: schedules edit/delete/toggle ───────────────────────

def _create_schedule(client, **fields):
    body = {"target_type": "plan", "plan_id": 940, "name": "Night",
            "starts_at_time": "22:00", "ends_at_time": "06:00",
            "speed_down_kbps": 2048, "speed_up_kbps": 1024,
            "days_csv": "sat,sun", "priority": 5, "notes": "n1"}
    body.update(fields)
    body = {k: v for k, v in body.items() if v is not None}
    res = client.post("/api/v1/bandwidth-schedules", headers=AUTH, json=body)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["schedule"]


def test_schedule_partial_patch_keeps_other_fields(client):
    s = _create_schedule(client)
    res = client.patch(f"/api/v1/bandwidth-schedules/{s['id']}", headers=AUTH,
                       json={"speed_down_kbps": 4096})
    assert res.status_code == 200, res.get_json()
    got = res.get_json()["data"]["schedule"]
    assert got["speed_down_kbps"] == 4096
    for key in ("name", "starts_at_time", "ends_at_time", "speed_up_kbps",
                "days_csv", "priority", "notes", "restore_mode"):
        assert got[key] == s[key], key
    assert bool(got["enabled"]) is True


def test_schedule_patch_full_edit_and_validation(client):
    s = _create_schedule(client)
    url = f"/api/v1/bandwidth-schedules/{s['id']}"
    res = client.patch(url, headers=AUTH, json={
        "name": "Peak", "starts_at_time": "18:00", "ends_at_time": "23:00",
        "speed_down_kbps": 8000, "speed_up_kbps": 2000, "restore_mode": "keep_current",
        "priority": 2, "enabled": False, "notes": "edited"})
    assert res.status_code == 200, res.get_json()
    got = res.get_json()["data"]["schedule"]
    assert (got["name"], got["starts_at_time"], got["restore_mode"], got["priority"]) == \
        ("Peak", "18:00", "keep_current", 2)
    assert not got["enabled"]
    # same rules as the web form (parity-c): unknown restore mode, no speeds.
    assert client.patch(url, headers=AUTH, json={"restore_mode": "manual"}).status_code == 422
    assert client.patch(url, headers=AUTH,
                        json={"speed_down_kbps": 0, "speed_up_kbps": 0}).status_code == 422
    assert client.patch(url, headers=AUTH, json={
        "speed_down_kbps": 0, "speed_up_kbps": 0,
        "restore_mode": "disconnect"}).status_code == 200
    assert client.patch(url, headers=AUTH, json={"starts_at_time": "25:99"}).status_code == 422
    assert client.patch(url, headers=AUTH, json={"enabled": "maybe"}).status_code == 422
    assert client.patch("/api/v1/bandwidth-schedules/999999", headers=AUTH,
                        json={"name": "x"}).status_code == 404


def test_schedule_enable_toggle_and_delete(client):
    s = _create_schedule(client)
    base = f"/api/v1/bandwidth-schedules/{s['id']}"
    res = client.post(base + "/enabled", headers=AUTH, json={"enabled": False})
    assert res.status_code == 200
    assert not res.get_json()["data"]["schedule"]["enabled"]
    res = client.post(base + "/enabled", headers=AUTH, json={"enabled": True})
    assert res.get_json()["data"]["schedule"]["enabled"]
    assert client.post(base + "/enabled", headers=AUTH, json={}).status_code == 422
    assert client.delete(base, headers=AUTH).status_code == 200
    assert client.get(base, headers=AUTH).status_code == 404
    assert client.delete(base, headers=AUTH).status_code == 404


def test_schedule_endpoints_guarded_like_web_routes():
    from app.api.permission_guard import API_PERMISSIONS
    assert API_PERMISSIONS["v1.bandwidth_schedules_update"] == "web:bandwidth_schedules_update"
    assert API_PERMISSIONS["v1.bandwidth_schedules_set_enabled"] == "web:bandwidth_schedules_update"
    assert API_PERMISSIONS["v1.bandwidth_schedules_delete"] == "web:bandwidth_schedules_delete"


# ─────────────────────── 4: group schedule = members only ───────────────────────

def _seed_group(app):
    gid = _exec(app, "INSERT INTO subscriber_groups(tenant_id, name, description, "
                     "default_plan_id, created_at) VALUES (1,'G1','',940,'2026-01-01 00:00:00')")
    _exec(app, "INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
               "subscriber_group_id, created_at) VALUES (1,'grp_member','x',940,'enabled',?,"
               "'2026-01-01 00:00:00')", (gid,))
    _exec(app, "INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
               "group_name, created_at) VALUES (1,'grp_legacy','x',940,'enabled','G1',"
               "'2026-01-01 00:00:00')")
    _exec(app, "INSERT INTO subscribers(tenant_id, username, password, plan_id, status, created_at) "
               "VALUES (1,'plan_only','x',940,'enabled','2026-01-01 00:00:00')")
    return gid


def test_group_schedule_applies_to_members_only(app, client):
    # fields-sched: days are effective now — these always-on rules use
    # days_csv="" (every day) instead of the helper's default «sat,sun».
    gid = _seed_group(app)
    s = _create_schedule(client, target_type="subscriber_group", subscriber_group_id=gid,
                         plan_id=None, starts_at_time="00:00", ends_at_time="00:00",
                         days_csv="",
                         speed_down_kbps=1111, speed_up_kbps=222)
    with app.test_request_context():
        from app.radius.db.repos import operations_repo
        for member in ("grp_member", "grp_legacy"):
            rule = operations_repo.resolve_effective_bandwidth_schedule(
                1, subscriber_username=member, plan_id=940)
            assert rule and rule["id"] == s["id"], member
        assert operations_repo.resolve_effective_bandwidth_schedule(
            1, subscriber_username="plan_only", plan_id=940) is None
        users = operations_repo.usernames_for_bandwidth_schedule(1, s)
        assert sorted(users) == ["grp_legacy", "grp_member"]
        # the auth path (policy_engine) gives the member the group speed only
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import _build_accept_attrs
        member = subscribers_repo.get_subscriber(1, "grp_member")
        other = subscribers_repo.get_subscriber(1, "plan_only")
        assert _build_accept_attrs(member, None).get("Mikrotik-Rate-Limit") == "222k/1111k"
        assert _build_accept_attrs(other, None).get("Mikrotik-Rate-Limit") != "222k/1111k"


def test_subscriber_rule_beats_group_rule(app, client):
    gid = _seed_group(app)
    _create_schedule(client, target_type="subscriber_group", subscriber_group_id=gid,
                     plan_id=None, starts_at_time="00:00", ends_at_time="00:00",
                     days_csv="")
    own = _create_schedule(client, target_type="subscriber", subscriber_username="grp_member",
                           plan_id=None, starts_at_time="00:00", ends_at_time="00:00",
                         days_csv="",
                           name="Own")
    with app.test_request_context():
        from app.radius.db.repos import operations_repo
        rule = operations_repo.resolve_effective_bandwidth_schedule(
            1, subscriber_username="grp_member", plan_id=940)
        assert rule["id"] == own["id"]
