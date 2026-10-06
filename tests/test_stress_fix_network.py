"""Stress campaign A08 (2026-09-28) — NAS / sessions / speed regressions.

Each test pins one finding of the `network` fix stream:
  1. duplicate RADIUS client address (create + edit, all tenants) → 409
  2. NAS address / secret validation; writer refuses non-IP; sync failure
     surfaced (no false success); edit can't blank name/address
  3. strict booleans ("false" is false) + ranged ints (no 500 on 2**63)
  4. /sessions/disconnect: 409 «لا توجد جلسة نشطة» (same as accounts/disconnect)
  5. /sessions/online: real paging + total + search beyond 500
  6. temp speed: "abc" → 422, days unit, silent router → no 2nd timeout
  7. set-speeds: preview:true is a dry run; bounds
  8. timestamps: ISO UTC with Z
  9. Arabic messages
plus web parity for the NAS form and the set-speeds tool.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    """ONE app per module (migrations take ~15 s); `_clean` below resets the
    tables these tests touch before every test."""
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="hr_sf_network_")
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
    created.config["_SF_TMP"] = tmp
    yield created
    mp.undo()
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            for sql in ("DELETE FROM radacct", "DELETE FROM nas_devices",
                        "DELETE FROM subscribers", "DELETE FROM cards",
                        "DELETE FROM access_plans WHERE id >= 900"):
                c.execute(sql)
    yield


@pytest.fixture
def client(app):
    return app.test_client()


def _is_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in str(text or ""))


def _nas(client, **over):
    body = {"name": f"sf-{uuid4().hex[:8]}", "address": "192.0.2.10",
            "secret": "s3cret-ok", "enabled": False}
    body.update(over)
    return client.post("/api/v1/nas", json=body, headers=AUTH)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _seed_tenant(app, tid: int = 1):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                      "VALUES (?, ?, ?, ?)", (tid, f"t{tid}", f"T{tid}", _now_iso()))


# ═══════════════════ 1. duplicate RADIUS client address ═══════════════════

def test_nas_create_duplicate_address_is_409_arabic(client):
    first = _nas(client, address="192.0.2.81")
    assert first.status_code == 201, first.get_json()
    dup = _nas(client, address="192.0.2.81")
    assert dup.status_code == 409, dup.get_json()
    err = dup.get_json()["error"]
    assert err["code"] == "nas_address_conflict"
    assert _is_arabic(err["message"]) and "192.0.2.81" in err["message"]


def test_nas_patch_onto_used_address_is_409_but_own_address_ok(client):
    a = _nas(client, address="192.0.2.82").get_json()["data"]
    b = _nas(client, address="192.0.2.83").get_json()["data"]
    res = client.patch(f"/api/v1/nas/{b['id']}", json={"address": "192.0.2.82"},
                       headers=AUTH)
    assert res.status_code == 409
    # the stored address did not change
    got = client.get(f"/api/v1/nas/{b['id']}", headers=AUTH).get_json()["data"]
    assert got["address"] == "192.0.2.83"
    # re-saving a row with its OWN address is fine
    res = client.patch(f"/api/v1/nas/{a['id']}",
                       json={"address": "192.0.2.82", "description": "x"},
                       headers=AUTH)
    assert res.status_code == 200, res.get_json()


def test_nas_duplicate_detected_across_tenants_without_leaking_name(app, client):
    _seed_tenant(app, 2)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
                      "nas_type, enabled, created_at) VALUES (2, 'other-tenant-secret-name', "
                      "'192.0.2.84', 'x', 'mikrotik', 'hotspot', 1, ?)", (_now_iso(),))
    res = _nas(client, address="192.0.2.84")
    assert res.status_code == 409
    assert "other-tenant-secret-name" not in res.get_json()["error"]["message"]


def test_nas_duplicate_matches_tunnel_ip_and_ignores_archived(app, client):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
                      "nas_type, enabled, vpn_peer_address, created_at) VALUES "
                      "(1, 'wg-rt', '203.0.113.5', 'x', 'mikrotik', 'hotspot', 1, "
                      "'10.10.0.7', ?)", (_now_iso(),))
            c.execute("INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
                      "nas_type, enabled, deleted_at, created_at) VALUES "
                      "(1, 'gone', '192.0.2.85', 'x', 'mikrotik', 'hotspot', 1, ?, ?)",
                      (_now_iso(), _now_iso()))
    assert _nas(client, address="10.10.0.7").status_code == 409
    assert _nas(client, address="192.0.2.85").status_code == 201


# ═══════════════════ 2. address / secret validation ═══════════════════

@pytest.mark.parametrize("address", [
    "abc", "999.1.1.1", "10.0.0.0/8", "192.0.2 .90", '192.0.2.91"',
    "192.0.2.92}", "1" * 10_000, "-bad-.example",
])
def test_nas_bad_address_is_422_arabic(client, address):
    res = _nas(client, address=address)
    assert res.status_code == 422, (address[:40], res.get_json())
    assert _is_arabic(res.get_json()["error"]["message"])


@pytest.mark.parametrize("secret", ['ab"c}', "has space", "x#y", "a{b", "d$ollar", "z" * 200])
def test_nas_bad_secret_is_422(client, secret):
    res = _nas(client, address="192.0.2.30", secret=secret)
    assert res.status_code == 422, res.get_json()
    assert _is_arabic(res.get_json()["error"]["message"])


def test_nas_hostname_allowed_only_when_not_registered_in_radius(client):
    ok_ = _nas(client, address="Router.Example.com", enabled=False)
    assert ok_.status_code == 201
    assert ok_.get_json()["data"]["address"] == "router.example.com"
    bad = _nas(client, address="router2.example.com", enabled=True)
    assert bad.status_code == 422
    assert "IP" in bad.get_json()["error"]["message"]


def test_nas_ipv6_normalised(client):
    res = _nas(client, address="2001:DB8:0:0::1")
    assert res.status_code == 201
    assert res.get_json()["data"]["address"] == "2001:db8::1"


def test_nas_patch_cannot_blank_name_or_address(client):
    nid = _nas(client, address="192.0.2.31").get_json()["data"]["id"]
    for body in ({"name": ""}, {"address": ""}, {"name": "   "}):
        res = client.patch(f"/api/v1/nas/{nid}", json=body, headers=AUTH)
        assert res.status_code == 422, body
        assert _is_arabic(res.get_json()["error"]["message"])


def test_nas_patch_non_object_body_is_422(client):
    nid = _nas(client, address="192.0.2.32").get_json()["data"]["id"]
    res = client.patch(f"/api/v1/nas/{nid}", json=[1, 2], headers=AUTH)
    assert res.status_code == 422


def test_writer_refuses_non_ip_and_unsafe_secret(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR", str(tmp_path))
    from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov
    for ip in ("abc", "999.1.1.1", "10.0.0.0/8", "router.example.com"):
        with pytest.raises(prov.FreeRadiusProvisioningError):
            prov.write_client_for_nas(nas_id=1, ipaddr=ip, secret="okok")
    for secret in ("a b", "a#b", "a{b", "a'b", "a${x}"):
        with pytest.raises(prov.FreeRadiusProvisioningError):
            prov.write_client_for_nas(nas_id=1, ipaddr="10.10.0.9", secret=secret)
    assert not list(tmp_path.glob("nas-*.conf"))
    res = prov.write_client_for_nas(nas_id=1, ipaddr="10.10.0.9", secret="good-Secret1")
    assert res["status"] == "written"


def test_writer_failure_is_reported_not_swallowed(app, client, monkeypatch):
    from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov

    def boom(**kw):
        raise prov.FreeRadiusProvisioningError("cannot write: permission denied")

    monkeypatch.setattr(prov, "write_client_for_nas", boom)
    res = _nas(client, address="192.0.2.33", enabled=True)
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["radius_client"]["ok"] is False
    assert data["radius_client"]["code"] == "radius_client_sync_failed"
    assert _is_arabic(data["warning"])
    # a clean save carries no warning
    monkeypatch.undo()
    res2 = _nas(client, address="192.0.2.34", enabled=True)
    assert res2.status_code == 201
    assert "radius_client" not in res2.get_json()["data"]


def test_enabled_nas_writes_client_file(app, client):
    res = _nas(client, address="192.0.2.35", enabled=True)
    assert res.status_code == 201
    nid = res.get_json()["data"]["id"]
    path = os.path.join(app.config["_SF_TMP"], "clients-wizard", f"nas-{nid}.conf")
    assert os.path.exists(path)
    assert "ipaddr      = 192.0.2.35" in open(path, encoding="utf-8").read()


# ═══════════════════ 3. strict booleans / ranged ints ═══════════════════

@pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), (0, False),
                                          ("true", True), ("1", True), (True, True)])
def test_nas_bool_strings_parsed_strictly(client, raw, expected):
    res = _nas(client, address="192.0.2.40", enabled=raw, monitoring_enabled=raw)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["enabled"] is expected
    assert data["monitoring_enabled"] is expected


def test_nas_bool_garbage_is_422_and_patch_false_disables(client):
    assert _nas(client, address="192.0.2.50", enabled="maybe").status_code == 422
    nid = _nas(client, address="192.0.2.51", enabled=True).get_json()["data"]["id"]
    res = client.patch(f"/api/v1/nas/{nid}", json={"enabled": "false"}, headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["data"]["enabled"] is False


@pytest.mark.parametrize("field,value", [
    ("auth_port", -1), ("coa_port", 70000), ("ports", -5), ("api_port", 2 ** 63 - 1),
    ("api_port", 10 ** 20), ("auth_port", True), ("auth_port", 1812.9),
    ("ssh_port", "abc"), ("acct_port", 0),
])
def test_nas_int_fields_ranged_no_500(client, field, value):
    res = _nas(client, address="192.0.2.60", **{field: value})
    assert res.status_code == 422, (field, value, res.status_code)
    assert _is_arabic(res.get_json()["error"]["message"])


def test_nas_patch_huge_int_is_422_not_500(client):
    nid = _nas(client, address="192.0.2.61").get_json()["data"]["id"]
    res = client.patch(f"/api/v1/nas/{nid}", json={"ssh_port": 2 ** 63}, headers=AUTH)
    assert res.status_code == 422


def test_nas_numeric_strings_and_null_defaults(client):
    res = _nas(client, address="192.0.2.62", auth_port="1645", coa_port=None)
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["auth_port"] == 1645 and data["coa_port"] == 3799


def test_nas_type_whitelist_and_json_fields(client):
    assert _nas(client, address="192.0.2.63", nas_type="weird").status_code == 422
    assert _nas(client, address="192.0.2.64", vendor="").status_code == 201
    res = _nas(client, address="192.0.2.65", metadata={"a": 1}, tags=["x", "y"])
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["metadata"] == '{"a": 1}'
    assert data["tags"] == "x,y"
    res = _nas(client, address="192.0.2.66", name="n" * 10_000)
    assert res.status_code == 422


def test_nas_list_negative_limit_is_clamped(client):
    for i in range(3):
        _nas(client, address=f"192.0.2.7{i}")
    res = client.get("/api/v1/nas?limit=-1", headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["data"]["count"] == 1


def test_nas_test_endpoint_legacy_bad_address_no_500(app, client):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, nas_type, "
                "enabled, api_port, created_at) VALUES (1, 'legacy', ?, '', 'mikrotik', "
                "'hotspot', 0, 8728, ?)", ("9" * 10_000, _now_iso()))
            nid = cur.lastrowid
    res = client.post(f"/api/v1/nas/{nid}/test", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["ok"] is False
    assert _is_arabic(data["message"]) and "Errno" not in data["message"]


def test_legacy_row_can_still_be_disabled(app, client):
    """Only CHANGED fields are re-validated: a legacy row with a bad port can
    still be disabled/renamed."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, nas_type, "
                "enabled, auth_port, created_at) VALUES (1, 'old', '192.0.2.99', 'x', "
                "'mikrotik', 'hotspot', 1, 0, ?)", (_now_iso(),))
            nid = cur.lastrowid
    res = client.patch(f"/api/v1/nas/{nid}", json={"enabled": False, "name": "old2"},
                       headers=AUTH)
    assert res.status_code == 200, res.get_json()


# ═══════════════════ web parity: the NAS form ═══════════════════

def _login_web(client):
    from app.radius.db.repos import admins_repo
    u = f"sfn_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="sfn-pass", full_name="SF",
                             is_super_admin=True)
    res = client.post("/admin/radius/login", data={"username": u, "password": "sfn-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/devices/new")
    with client.session_transaction() as sess:
        sess["is_super_admin"] = True   # tools/set_speeds is a super-only route
        return sess["_csrf_token"]


def _web_form(token, **over):
    data = {"_csrf_token": token, "name": f"web-{uuid4().hex[:6]}",
            "address": "192.0.2.120", "secret": "websecret", "vendor": "mikrotik",
            "nas_type": "hotspot", "auth_port": "1812", "acct_port": "1813",
            "coa_port": "3799", "api_port": "8728", "ssh_port": "22"}
    data.update(over)
    return data


def _nas_count(app, address):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute("SELECT COUNT(*) AS n FROM nas_devices WHERE address=? "
                            "AND deleted_at IS NULL", (address,)).fetchone()["n"]


def test_web_nas_form_same_validation(app, client):
    token = _login_web(client)
    res = client.post("/admin/radius/devices", data=_web_form(token))
    assert res.status_code in {302, 303}
    assert _nas_count(app, "192.0.2.120") == 1
    # duplicate address
    res = client.post("/admin/radius/devices", data=_web_form(token))
    assert res.status_code == 400
    assert "مستخدم لراوتر" in res.get_data(as_text=True)
    assert _nas_count(app, "192.0.2.120") == 1
    # garbage address / port / secret
    for over in ({"address": "abc"}, {"address": "10.0.0.0/8"},
                 # (auth_port left the form 2026-10-06 — owner: nothing reads it)
                 {"address": "192.0.2.121", "api_port": "abc"},
                 {"address": "192.0.2.122", "coa_port": "70000"},
                 {"address": "192.0.2.123", "secret": 'bad"secret'}):
        res = client.post("/admin/radius/devices", data=_web_form(token, **over))
        assert res.status_code == 400, over
    assert _nas_count(app, "192.0.2.121") == 0


# ═══════════════════ sessions: seed helpers ═══════════════════

def _seed_sessions(app, n: int, *, prefix: str = "u", nas_ip: str = "10.20.30.1",
                   start_fmt: str = "iso"):
    base = datetime.utcnow() - timedelta(hours=2)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                      "VALUES (1, 't1', 'T1', ?)", (_now_iso(),))
            c.execute("INSERT OR IGNORE INTO access_plans(id, tenant_id, name, code, "
                      "created_at) VALUES (951, 1, 'SF plan', 'sf_plan', ?)", (_now_iso(),))
            subs, accts = [], []
            for i in range(n):
                user = f"{prefix}{i:04d}"
                started = base + timedelta(seconds=i)
                st = (started.strftime("%Y-%m-%d %H:%M:%S") if start_fmt == "space"
                      else started.isoformat() + "Z")
                subs.append((user, _now_iso()))
                accts.append((f"sid-{user}", f"uid-{user}", user, nas_ip,
                              f"10.9.{i // 250}.{i % 250 + 1}",
                              f"AA:BB:CC:{i // 65536 % 256:02X}:{i // 256 % 256:02X}:{i % 256:02X}",
                              st, st))
            c.executemany("INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                          "status, created_at) VALUES (1, ?, 'x', 951, 'enabled', ?)", subs)
            c.executemany(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                "nasipaddress, framedipaddress, callingstationid, acctstarttime, "
                "acctupdatetime, acctinputoctets, acctoutputoctets, acctstoptime) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 1, 2, NULL)", accts)


# ═══════════════════ 5. online list paging ═══════════════════

def test_online_list_pages_beyond_500_and_searches_everything(app, client):
    _seed_sessions(app, 520)
    res = client.get("/api/v1/sessions/online", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["total"] == 520
    assert data["count"] == 500 and data["has_more"] is True
    assert data["types"]["subscriber"] == 520  # counters = whole result

    res = client.get("/api/v1/sessions/online?limit=100&offset=500", headers=AUTH)
    data = res.get_json()["data"]
    assert data["count"] == 20 and data["has_more"] is False and data["offset"] == 500

    # the OLDEST session (ordered last) is findable by search
    res = client.get("/api/v1/sessions/online?q=u0000", headers=AUTH)
    data = res.get_json()["data"]
    assert data["total"] == 1 and data["items"][0]["username"] == "u0000"

    seen = set()
    for off in range(0, 520, 200):
        page = client.get(f"/api/v1/sessions/online?limit=200&offset={off}",
                          headers=AUTH).get_json()["data"]["items"]
        seen.update(it["session_id"] for it in page)
    assert len(seen) == 520


def test_online_list_bad_paging_is_422(client):
    for qs in ("limit=0", "limit=5000", "offset=-1", "limit=abc"):
        res = client.get(f"/api/v1/sessions/online?{qs}", headers=AUTH)
        assert res.status_code == 422, qs


def test_online_list_is_fast_at_500_sessions(app, client):
    _seed_sessions(app, 500)
    t0 = time.monotonic()
    res = client.get("/api/v1/sessions/online", headers=AUTH)
    elapsed = time.monotonic() - t0
    assert res.status_code == 200 and res.get_json()["data"]["total"] == 500
    assert elapsed < 2.0, elapsed


def test_open_session_indexes_exist(app):
    with app.app_context():
        from app.radius.db.connection import db
        names = {r["name"] for r in db().execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='radacct'")}
    assert {"idx_radacct_open_by_tenant", "idx_radacct_open_by_user"} <= names


# ═══════════════════ 8. timestamps ═══════════════════

def test_session_timestamps_are_iso_utc_z(app, client):
    _seed_sessions(app, 2, prefix="sp", start_fmt="space")
    _seed_sessions(app, 2, prefix="iz", start_fmt="iso")
    items = client.get("/api/v1/sessions/online", headers=AUTH).get_json()["data"]["items"]
    for it in items:
        for key in ("started_at", "last_update_at"):
            assert it[key].endswith("Z") and "T" in it[key], (key, it[key])
    hist = client.get("/api/v1/accounting/sessions", headers=AUTH).get_json()["data"]["items"]
    assert hist
    for row in hist:
        assert row["acctstarttime"].endswith("Z") and "T" in row["acctstarttime"]
    online = client.get("/api/v1/accounting/online", headers=AUTH).get_json()["data"]["items"]
    assert all(r["acctstarttime"].endswith("Z") for r in online)


def test_accounting_events_errors_are_arabic(client):
    for body in ({}, {"status_type": "Start", "nas_ip_address": "10.0.0.1"},
                 {"status_type": "Start", "acct_session_id": "x"}):
        res = client.post("/api/v1/accounting/events", json=body, headers=AUTH)
        assert res.status_code == 422
        assert _is_arabic(res.get_json()["error"]["message"])
    res = client.post("/api/v1/accounting/events", json=[1], headers=AUTH)
    assert res.status_code == 422


def test_iso_utc_z_helper():
    from app.radius.core.strict_input import iso_utc_z
    assert iso_utc_z("2026-09-27 01:49:54") == "2026-09-27T01:49:54Z"
    assert iso_utc_z("2026-09-27T01:49:54.332000") == "2026-09-27T01:49:54.332000Z"
    assert iso_utc_z("2026-09-27T01:49:54Z") == "2026-09-27T01:49:54Z"
    assert iso_utc_z("2026-09-27T04:49:54+03:00") == "2026-09-27T01:49:54Z"
    assert iso_utc_z(datetime(2026, 9, 27, 1, 2, 3)) == "2026-09-27T01:02:03Z"
    assert iso_utc_z(None) is None and iso_utc_z("") is None


# ═══════════════════ 4. disconnect status codes ═══════════════════

def test_disconnect_no_session_is_409_on_both_surfaces(app, client):
    _seed_sessions(app, 1, prefix="dc")   # creates subscriber dc0000
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE radacct SET acctstoptime=? WHERE username='dc0000'",
                      (_now_iso(),))
    r1 = client.post("/api/v1/sessions/disconnect", json={"username": "dc0000"},
                     headers=AUTH)
    r2 = client.post("/api/v1/accounts/dc0000/disconnect", headers=AUTH)
    for r in (r1, r2):
        assert r.status_code == 409, r.get_json()
        err = r.get_json()["error"]
        assert err["code"] == "no_active_session"
        assert "لا توجد جلسة نشطة" in err["message"]
    ghost = client.post("/api/v1/sessions/disconnect",
                        json={"username": "ghost-" + "x" * 200, "session_id": "nope"},
                        headers=AUTH)
    assert ghost.status_code == 409


def test_disconnect_open_session_on_unconfigured_router_says_so(app, client):
    _seed_sessions(app, 1, prefix="rn")   # NAS 10.20.30.1 has no nas_devices row
    res = client.post("/api/v1/sessions/disconnect", json={"username": "rn0000"},
                      headers=AUTH)
    assert res.status_code == 409
    err = res.get_json()["error"]
    assert err["code"] == "router_not_configured"
    assert "راوتر" in err["message"] and "لا توجد جلسة" not in err["message"]


def test_disconnect_router_failure_is_502(app, client, monkeypatch):
    _seed_sessions(app, 1, prefix="rf")
    from app.radius.integration import radius_coa

    monkeypatch.setattr(radius_coa, "disconnect_user", lambda *a, **k: radius_coa.CoaResult(
        ok=False, code_name="all_failed", reply_message="قطع الجلسات فشل على كل الجلسات (1)"))
    res = client.post("/api/v1/sessions/disconnect", json={"username": "rf0000"},
                      headers=AUTH)
    assert res.status_code == 502
    assert res.get_json()["error"]["code"] == "disconnect_failed"


@pytest.mark.parametrize("path", ["/api/v1/sessions/disconnect", "/api/v1/sessions/lock-mac",
                                  "/api/v1/sessions/lock-ip", "/api/v1/sessions/temp-speed",
                                  "/api/v1/sessions/temp-speed/cancel"])
@pytest.mark.parametrize("body", [{"username": 12345, "session_id": "x"},
                                  {"username": ["a"], "session_id": "x"},
                                  [1, 2], "x"])
def test_session_actions_wrong_json_types_are_422(client, path, body):
    res = client.post(path, json=body, headers=AUTH)
    assert res.status_code == 422, (path, body, res.status_code)
    payload = res.get_json()
    assert payload is not None and _is_arabic(payload["error"]["message"])


# ═══════════════════ 6. temp speed ═══════════════════

def _temp(client, **over):
    body = {"username": "ts0000", "session_id": "sid-ts0000", "down_kbps": 2048,
            "up_kbps": 512, "duration": 30}
    body.update(over)
    return client.post("/api/v1/sessions/temp-speed", json=body, headers=AUTH)


def test_temp_speed_rejects_garbage_speed_and_unknown_unit(app, client):
    _seed_sessions(app, 1, prefix="ts")
    for over in ({"down_kbps": "abc"}, {"up_kbps": -5}, {"down_kbps": True},
                 {"duration_unit": "weeks"}, {"duration": "abc"}, {"duration": 1.5}):
        res = _temp(client, **over)
        assert res.status_code == 422, (over, res.get_json())
        assert _is_arabic(res.get_json()["error"]["message"])
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT temporary_speed FROM subscribers WHERE username='ts0000'"
                           ).fetchone()
    assert not row["temporary_speed"]


def test_temp_speed_days_unit_and_z_timestamp(app, client):
    _seed_sessions(app, 1, prefix="ts")
    res = _temp(client, duration=1, duration_unit="days")
    assert res.status_code == 200, res.get_json()
    ends = res.get_json()["data"]["temporary_speed"]["ends_at"]
    assert ends.endswith("Z")
    ends_dt = datetime.fromisoformat(ends[:-1])
    delta = (ends_dt - datetime.utcnow()).total_seconds()
    assert 1435 * 60 < delta <= 1440 * 60 + 5
    # 2 days exceeds the 24 h ceiling → 422 (was silently 2 minutes)
    assert _temp(client, duration=2, duration_unit="days").status_code == 422


def test_temp_speed_silent_router_does_not_wait_twice(app, monkeypatch):
    from app.radius.integration.radius_coa import CoaResult
    from app.radius.services import temp_speed

    _seed_sessions(app, 1, prefix="tq")
    calls = {"reauth": 0}
    monkeypatch.setattr(temp_speed, "_push_rate", lambda *a, **k: CoaResult(
        ok=False, code_name="all_failed", timed_out=True))

    def _reauth(*a, **k):
        calls["reauth"] += 1
        return CoaResult(ok=False, code_name="all_failed", timed_out=True)

    monkeypatch.setattr(temp_speed, "_push_reauth", _reauth)
    with app.app_context():
        res = temp_speed.apply_temp_speed(tenant_id=1, actor="t", username="tq0000",
                                          down_kbps=2048, up_kbps=512, duration_minutes=30)
    assert calls["reauth"] == 0
    assert res["coa"]["ok"] is False


def test_coa_broadcast_parallel_and_timed_out_flag(monkeypatch):
    from app.radius.integration import radius_coa

    import threading
    barrier = threading.Barrier(3, timeout=10)

    def slow():
        # Only passes if all three senders run AT THE SAME TIME (serial
        # execution would break the barrier) — one timeout, not three.
        barrier.wait()
        return radius_coa.CoaResult(ok=False, code_name="timeout")

    results = radius_coa._run_all([slow, slow, slow])
    assert len(results) == 3
    agg = radius_coa._broadcast("x", results, 3)
    assert agg.ok is False and agg.code_name == "all_failed" and agg.timed_out is True
    mixed = radius_coa._broadcast("x", [radius_coa.CoaResult(ok=False, code_name="timeout"),
                                        radius_coa.CoaResult(ok=False, code_name="nak")], 2)
    assert mixed.timed_out is False


# ═══════════════════ 7. set-speeds ═══════════════════

def _seed_plan(app, pid=961, down=4096, up=1024):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                      "VALUES (1, 't1', 'T1', ?)", (_now_iso(),))
            c.execute("INSERT INTO access_plans(id, tenant_id, name, code, speed_down_kbps, "
                      "speed_up_kbps, created_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                      (pid, f"p{pid}", f"p{pid}", down, up, _now_iso()))


def _plan_speed(app, pid=961):
    with app.app_context():
        from app.radius.db.connection import db
        r = db().execute("SELECT speed_down_kbps, speed_up_kbps FROM access_plans WHERE id=?",
                         (pid,)).fetchone()
        return r["speed_down_kbps"], r["speed_up_kbps"]


def test_set_speeds_preview_true_does_not_apply(app, client):
    _seed_plan(app)
    res = client.post("/api/v1/tools/set-speeds",
                      json={"plan_ids": [961], "mult_down": 2, "preview": True}, headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["dry_run"] is True and data["changed"] == 0
    assert data["changes"][0]["after"]["speed_down_kbps"] == 8192
    assert _plan_speed(app) == (4096, 1024)


@pytest.mark.parametrize("over", [{"mult_down": 1e20}, {"mult_down": 0}, {"mult_up": "nan"},
                                  {"mult_down": "inf"}, {"set_down": 10_000_000},
                                  {"set_up": -1}, {"mult_down": "abc"}])
def test_set_speeds_bounds_422_no_500(app, client, over):
    _seed_plan(app)
    body = {"plan_ids": [961]}
    body.update(over)
    res = client.post("/api/v1/tools/set-speeds", json=body, headers=AUTH)
    assert res.status_code == 422, (over, res.status_code)
    assert _is_arabic(res.get_json()["error"]["message"])
    assert _plan_speed(app) == (4096, 1024)


def test_set_speeds_result_over_ceiling_refused(app, client):
    _seed_plan(app, down=900_000)
    res = client.post("/api/v1/tools/set-speeds", json={"plan_ids": [961], "mult_down": 2},
                      headers=AUTH)
    assert res.status_code == 422
    assert _plan_speed(app) == (900_000, 1024)


def test_web_set_speeds_same_bounds(app, client):
    _seed_plan(app)
    token = _login_web(client)
    res = client.post("/admin/radius/tools/set_speeds",
                      data={"_csrf_token": token, "plan_ids": "961", "mult_down": "1e20"})
    assert res.status_code in {302, 303}
    assert _plan_speed(app) == (4096, 1024)
    res = client.post("/admin/radius/tools/set_speeds",
                      data={"_csrf_token": token, "plan_ids": "961", "mult_down": "2"})
    assert _plan_speed(app) == (8192, 1024)


# ═══════════════════ strict helpers ═══════════════════

def test_strict_helpers():
    from app.radius.core.errors import RadiusValidationError
    from app.radius.core.strict_input import parse_ranged_int, parse_strict_bool
    assert parse_strict_bool("false", label="x") is False
    assert parse_strict_bool("No", label="x") is False
    assert parse_strict_bool("on", label="x") is True
    for bad in ("maybe", 2, [1], {}):
        with pytest.raises(RadiusValidationError):
            parse_strict_bool(bad, label="x")
    assert parse_ranged_int("22", label="p", minimum=1, maximum=65535) == 22
    assert parse_ranged_int(None, label="p", minimum=1, maximum=9, default=3) == 3
    # fix wave 2: Arabic-Indic digits read as Latin (core.numbers rule — the web
    # form hook already converted them), so «٣» is 3, not an error.
    assert parse_ranged_int("٣", label="p", minimum=0, maximum=65535) == 3
    for bad in (True, 1.5, "1e3", "x", 2 ** 63, -1, "٣٫٥"):
        with pytest.raises(RadiusValidationError):
            parse_ranged_int(bad, label="p", minimum=0, maximum=65535)


def test_temp_speed_on_unconfigured_router_reports_it(app, client):
    _seed_sessions(app, 1, prefix="ts")   # NAS 10.20.30.1 has no nas_devices row
    res = _temp(client)
    assert res.status_code == 200, res.get_json()
    coa = res.get_json()["data"]["temporary_speed"]["coa"]
    assert coa["ok"] is False and coa["code"] == "router_not_configured"


def test_web_temp_speed_same_strict_intake(app, client):
    _seed_sessions(app, 1, prefix="ts")
    token = _login_web(client)
    base = {"_csrf_token": token, "username": "ts0000", "session_id": "sid-ts0000",
            "down_kbps": "abc", "up_kbps": "512", "duration": "30"}
    res = client.post("/admin/radius/online/temp-speed", data=base)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT temporary_speed FROM subscribers WHERE username='ts0000'"
                           ).fetchone()
    assert not row["temporary_speed"]
    res = client.post("/admin/radius/online/temp-speed",
                      data=dict(base, down_kbps="2048", duration="1", duration_unit="days"))
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.connection import db
        import json as _json
        row = db().execute("SELECT temporary_speed, metadata FROM subscribers "
                           "WHERE username='ts0000'").fetchone()
    assert row["temporary_speed"]
    meta = _json.loads(row["metadata"])
    dur = [v for k, v in meta.items() if "duration" in k]
    assert 1440 in dur, meta


@pytest.mark.parametrize("over", [{"port": "abc"}, {"timeout_sec": "abc"},
                                  {"host": "1" * 5000}, {"port": 70000},
                                  {"use_tls": "maybe"}, {"host": 123}])
def test_mikrotik_test_credentials_bad_input_is_422(client, over):
    body = {"host": "192.0.2.81", "username": "st08", "password": "x"}
    body.update(over)
    res = client.post("/api/v1/mikrotik/test-credentials", json=body, headers=AUTH)
    assert res.status_code == 422, (over, res.status_code)
    assert _is_arabic(res.get_json()["error"]["message"])
    assert client.post("/api/v1/mikrotik/test-credentials", json=[1],
                       headers=AUTH).status_code == 422
