# -*- coding: utf-8 -*-
"""Fix wave 3 (2026-09-30) — `cardsnet` stream, network half (FINAL CAMPAIGN F06).

* H1 — restoring a deleted NAS never yields two live routers on one IP;
  enabling/saving re-runs the address-owner check.
* H2 — zoned IPv6 («fe80::1%eth0») and other addresses radiusd can't parse
  are refused on create / edit / restore, and the clients writer skips /
  quarantines them so one bad row never stops radiusd.
* LOW — online cards tab single pager, web temp-speed «أيام», Arabic units,
  dashed MAC search, HTTP Stop/Interim without Start, GA preview 1-year cap,
  device-health input + real ping result, NAS PATCH null body 422.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="hr_f3_network_")
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
    created.config["F3_CLIENTS_DIR"] = os.path.join(tmp, "clients-wizard")
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
                        "DELETE FROM cards", "DELETE FROM card_batches",
                        "DELETE FROM subscribers",
                        "DELETE FROM access_plans WHERE id >= 900"):
                c.execute(sql)
    yield


@pytest.fixture
def client(app):
    return app.test_client()


def _is_arabic(text) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in str(text or ""))


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _nas(client, **over):
    body = {"name": f"f3-{uuid4().hex[:8]}", "address": "192.0.2.10",
            "secret": "s3cret-ok", "enabled": False}
    body.update(over)
    return client.post("/api/v1/nas", json=body, headers=AUTH)


def _login_web(client):
    from app.radius.db.repos import admins_repo
    u = f"f3n_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="f3n-pass", full_name="F3",
                             is_super_admin=True)
    res = client.post("/admin/radius/login", data={"username": u, "password": "f3n-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/devices/new")
    with client.session_transaction() as sess:
        sess["is_super_admin"] = True
        return sess["_csrf_token"]


def _flashes(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _cat, m in sess.get("_flashes", [])]


def _clients_dir(app) -> Path:
    return Path(app.config["F3_CLIENTS_DIR"])


# ═══════════════════ H1 — restore never duplicates a live IP ═══════════════════

def test_restore_of_nas_whose_ip_was_reused_is_409(app, client):
    old = _nas(client, name="f3-rs-old", address="192.0.2.85", enabled=True,
               secret="OldSecret3").get_json()["data"]
    assert client.delete(f"/api/v1/nas/{old['id']}", headers=AUTH).status_code == 200
    new = _nas(client, name="f3-rs-new", address="192.0.2.85", enabled=True,
               secret="NewSecret4")
    assert new.status_code == 201, new.get_json()
    res = client.post(f"/api/v1/recycle-bin/nas/{old['id']}/restore", headers=AUTH)
    assert res.status_code == 409, res.get_json()
    err = res.get_json()["error"]
    assert err["code"] == "nas_address_conflict" and _is_arabic(err["message"])
    with app.app_context():
        from app.radius.db.connection import db
        live = db().execute("SELECT COUNT(*) AS n FROM nas_devices WHERE address='192.0.2.85' "
                            "AND (deleted_at IS NULL OR deleted_at='')").fetchone()["n"]
    assert live == 1
    # the live router's client file still carries ITS secret
    f = _clients_dir(app) / f"nas-{new.get_json()['data']['id']}.conf"
    assert f.exists() and "NewSecret4" in f.read_text(encoding="utf-8")


def test_restore_without_conflict_comes_back_disabled(app, client):
    old = _nas(client, name="f3-rs-free", address="192.0.2.86").get_json()["data"]
    client.delete(f"/api/v1/nas/{old['id']}", headers=AUTH)
    res = client.post(f"/api/v1/recycle-bin/nas/{old['id']}/restore", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = client.get(f"/api/v1/nas/{old['id']}", headers=AUTH).get_json()["data"]
    assert data["enabled"] is False


def test_web_restore_conflict_flashes_arabic(app, client):
    old = _nas(client, name="f3-rs-web", address="192.0.2.87").get_json()["data"]
    client.delete(f"/api/v1/nas/{old['id']}", headers=AUTH)
    assert _nas(client, name="f3-rs-web2", address="192.0.2.87").status_code == 201
    token = _login_web(client)
    client.post(f"/admin/radius/recycle-bin/nas/{old['id']}/restore",
                data={"_csrf_token": token})
    msgs = _flashes(client)
    assert any("192.0.2.87" in m and _is_arabic(m) for m in msgs), msgs
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT deleted_at FROM nas_devices WHERE id=?",
                           (old["id"],)).fetchone()
    assert row["deleted_at"]


def test_enabling_a_legacy_duplicate_rechecks_the_address(app, client):
    """Data written before the fix (two rows, one IP): `{"enabled": true}`
    (address not in the PATCH) must still be refused."""
    a = _nas(client, name="f3-dup-a", address="192.0.2.88", enabled=True).get_json()["data"]
    b = _nas(client, name="f3-dup-b", address="192.0.2.89").get_json()["data"]
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE nas_devices SET address='192.0.2.88' WHERE id=?", (b["id"],))
    res = client.patch(f"/api/v1/nas/{b['id']}", json={"enabled": True}, headers=AUTH)
    assert res.status_code == 409, res.get_json()
    assert _is_arabic(res.get_json()["error"]["message"])
    # a disabled duplicate can still be edited (to fix it) — no lock-out
    res = client.patch(f"/api/v1/nas/{b['id']}", json={"address": "192.0.2.90"},
                       headers=AUTH)
    assert res.status_code == 200, res.get_json()
    assert client.patch(f"/api/v1/nas/{a['id']}", json={"location": "x"},
                        headers=AUTH).status_code == 200


# ═══════════════════ H2 — zoned IPv6 / unparsable addresses ═══════════════════

@pytest.mark.parametrize("addr", ["fe80::1%eth0", "fe80::1%eth9", "0.0.0.0", "::",
                                  "224.0.0.1"])
def test_create_refuses_addresses_radiusd_cannot_load(client, addr):
    for enabled in (True, False):
        res = _nas(client, address=addr, enabled=enabled)
        assert res.status_code == 422, (addr, res.get_json())
        assert _is_arabic(res.get_json()["error"]["message"])


def test_edit_refuses_zoned_ipv6(client):
    nas = _nas(client, address="192.0.2.91").get_json()["data"]
    res = client.patch(f"/api/v1/nas/{nas['id']}", json={"address": "fe80::1%eth0"},
                       headers=AUTH)
    assert res.status_code == 422


@pytest.mark.parametrize("addr", ["192.0.2.1", "2001:db8::f06", "fe80::1", "10.10.0.2"])
def test_plain_ipv4_and_ipv6_are_accepted(client, addr):
    res = _nas(client, address=addr, enabled=True)
    assert res.status_code == 201, (addr, res.get_json())


def test_restore_of_legacy_zoned_row_is_refused(app, client):
    nas = _nas(client, name="f3-zone-old", address="192.0.2.92").get_json()["data"]
    client.delete(f"/api/v1/nas/{nas['id']}", headers=AUTH)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE nas_devices SET address='fe80::1%eth0' WHERE id=?", (nas["id"],))
    res = client.post(f"/api/v1/recycle-bin/nas/{nas['id']}/restore", headers=AUTH)
    assert res.status_code == 422, res.get_json()
    assert _is_arabic(res.get_json()["error"]["message"])


def test_radiusd_ip_literal_matches_what_radiusd_parses():
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        radiusd_ip_literal)
    assert radiusd_ip_literal("192.0.2.1") == "192.0.2.1"
    assert radiusd_ip_literal(" 2001:DB8::1 ") == "2001:db8::1"
    assert radiusd_ip_literal("fe80::1") == "fe80::1"
    assert radiusd_ip_literal("::ffff:192.0.2.5") == "192.0.2.5"
    for bad in ("fe80::1%eth0", "fe80::1%9", "0.0.0.0", "::", "224.0.0.1",
                "10.0.0.0/24", "abc", "", None, "999.1.1.1"):
        assert radiusd_ip_literal(bad) is None, bad


def test_writer_skips_a_zoned_address_and_logs(app, caplog):
    from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov
    d = _clients_dir(app)
    d.mkdir(parents=True, exist_ok=True)
    stale = d / "nas-777.conf"
    stale.write_text("client nas-777 {\n    ipaddr      = 192.0.2.7\n"
                     "    secret      = s\n}\n", encoding="utf-8")
    with app.app_context(), caplog.at_level("WARNING"):
        with pytest.raises(prov.FreeRadiusProvisioningError):
            prov.write_client_for_nas(nas_id=777, ipaddr="fe80::1%eth0", secret="abc123")
    assert not stale.exists()                        # this row's old file is gone
    assert not any("%" in p.read_text(encoding="utf-8") for p in d.glob("nas-*.conf"))
    assert "FreeRADIUS can load" in caplog.text


def test_dedupe_quarantines_a_bad_file_so_radiusd_keeps_running(app):
    from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov
    d = _clients_dir(app)
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "nas-778.conf"
    bad.write_text("client nas-778 {\n    ipaddr      = fe80::1%eth9\n"
                   "    secret      = s\n}\n", encoding="utf-8")
    good = d / "nas-779.conf"
    good.write_text("client nas-779 {\n    ipaddr      = 192.0.2.79\n"
                    "    secret      = s\n}\n", encoding="utf-8")
    with app.app_context():
        actions = prov._dedupe_clients_by_ipaddr(d)
    assert "nas-778.conf" in actions["invalid"]
    assert not bad.exists() and (d / ".invalid-nas-778.conf").exists()
    assert good.exists()
    good.unlink()
    (d / ".invalid-nas-778.conf").unlink()


def test_reconcile_skips_a_legacy_zoned_row(app, client):
    nas = _nas(client, name="f3-zone-rec", address="192.0.2.93", enabled=True).get_json()["data"]
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE nas_devices SET address='fe80::1%eth0' WHERE id=?", (nas["id"],))
        from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov
        prov.reconcile_nas_client_files()
    for p in _clients_dir(app).glob("nas-*.conf"):
        assert "%" not in p.read_text(encoding="utf-8")


# ═══════════════════ LOW ═══════════════════

def _seed_sessions(app, users, *, kind="subscriber", macs=None):
    now = datetime.utcnow()
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR REPLACE INTO access_plans(id, tenant_id, name, code, "
                      "speed_down_kbps, speed_up_kbps, created_at) "
                      "VALUES (951, 1, 'F3 plan', 'f3_951', 4096, 1024, ?)", (_now_iso(),))
            if kind == "card":
                c.execute("INSERT OR IGNORE INTO card_batches(id, tenant_id, batch_code, "
                          "plan_id, created_at) VALUES (901, 1, 'F3B', 951, ?)", (_now_iso(),))
            for i, user in enumerate(users):
                if kind == "subscriber":
                    c.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                              "status, created_at) VALUES (1, ?, 'x', 951, 'enabled', ?)",
                              (user, _now_iso()))
                else:
                    c.execute("INSERT INTO cards(tenant_id, batch_id, username, password, "
                              "plan_id, used, created_at) VALUES (1, 901, ?, 'x', 951, 1, ?)",
                              (user, _now_iso()))
                st = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
                up = now.strftime("%Y-%m-%d %H:%M:%S")
                mac = (macs or {}).get(user, f"AA:BB:CC:00:{i // 256:02X}:{i % 256:02X}")
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                    "nasipaddress, framedipaddress, callingstationid, acctstarttime, "
                    "acctupdatetime, acctinputoctets, acctoutputoctets, acctstoptime) "
                    "VALUES (1, ?, ?, ?, '10.20.30.1', ?, ?, ?, ?, 1, 2, NULL)",
                    (f"sid-{user}", f"uid-{user}", user, f"10.77.0.{i + 1}", mac, st, up))


def test_online_page_has_one_pager_when_server_paged(app, client):
    _seed_sessions(app, [f"cp{i}" for i in range(5)], kind="card")
    _login_web(client)
    html = client.get("/admin/radius/online?type=card&per_page=100").get_data(as_text=True)
    assert 'data-persist-key="online-sessions"' in html
    assert 'data-paginated="1" data-page-size="50"' not in html


def test_online_page_units_are_arabic(app, client):
    _seed_sessions(app, ["unitu"])
    _login_web(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    assert "10 دقائق" in html                         # session time, was «10m»
    assert '<bdi dir="ltr">10m</bdi>' not in html
    from app.radius.core.duration_fmt import fmt_compact_ar
    assert fmt_compact_ar(480) == "8 دقائق" and fmt_compact_ar(0) == "0 دقيقة"


def test_web_temp_speed_offers_days(app, client):
    _seed_sessions(app, ["tsdays"])
    _login_web(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    assert '<option value="days">' in html


def test_temp_speed_unit_errors_are_arabic():
    from app.radius.services.temp_speed import parse_duration_minutes
    assert parse_duration_minutes(duration=1, unit="days") == 1440
    for unit in ("weeks", 5):
        with pytest.raises(ValueError) as e:
            parse_duration_minutes(duration=1, unit=unit)
        assert "minutes" not in str(e.value) and "دقائق" in str(e.value)


def test_coa_codes_are_arabic_in_flashes():
    from app.radius.integration.radius_coa import coa_code_ar
    for code in ("router_not_configured", "timeout", "CoA-NAK", "weird_code", "unknown-code-7"):
        text = coa_code_ar(code)
        assert _is_arabic(text) and "router_not_configured" not in text
    src = Path(__file__).resolve().parents[1] / "app/radius/routes/sessions.py"
    body = src.read_text(encoding="utf-8")
    assert '({code})' not in body and '{out.code_name}' not in body


@pytest.mark.parametrize("q", ["AA-BB-CC-06-00-05", "aa:bb:cc:06:00:05", "aabb.cc06.0005",
                               "AABBCC060005", "06-00-05"])
def test_mac_search_any_notation(app, client, q):
    _seed_sessions(app, ["macx", "macy"], macs={"macx": "AA:BB:CC:06:00:05",
                                                "macy": "AA:BB:CC:06:00:06"})
    data = client.get("/api/v1/sessions/online", query_string={"q": q},
                      headers=AUTH).get_json()["data"]
    assert [i["username"] for i in data["items"]] == ["macx"], q
    _login_web(client)
    html = client.get("/admin/radius/online", query_string={"q": q}).get_data(as_text=True)
    assert 'data-username="macx"' in html and 'data-username="macy"' not in html


def test_mac_query_never_widens_text_search():
    from app.radius.services.sessions import mac_query_matches
    assert not mac_query_matches("abc", "0A:BC:00:00:00:01")      # < 4 hex digits
    assert not mac_query_matches("cafe user", "CA:FE:00:00:00:01")
    assert mac_query_matches("CA-FE", "CA:FE:00:00:00:01")


def _acct(client, status, sid, **extra):
    body = {"status_type": status, "username": "acctx", "acct_session_id": sid,
            "nas_ip_address": "198.51.100.20", **extra}
    return client.post("/api/v1/accounting/events", json=body, headers=AUTH)


def test_http_stop_without_start_inserts_a_closed_row(app, client):
    res = _acct(client, "Stop", "nostart-1", session_time=300, input_octets=111,
                output_octets=222, framed_ip_address="10.9.9.9")
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["status"] == "stopped" and data["inserted_without_start"] is True
    row = data["session"]
    assert row["acctstoptime"] and row["acctsessiontime"] == 300
    assert row["acctinputoctets"] == 111 and row["acctoutputoctets"] == 222
    start = datetime.fromisoformat(row["acctstarttime"].replace("Z", ""))
    stop = datetime.fromisoformat(row["acctstoptime"].replace("Z", ""))
    assert abs((stop - start).total_seconds() - 300) < 3
    # a retransmitted Stop → no duplicate; a late Interim → no reopen
    again = _acct(client, "Stop", "nostart-1", session_time=300)
    assert again.get_json()["data"]["status"] == "already_stopped"
    late = _acct(client, "Interim-Update", "nostart-1", session_time=320)
    assert late.get_json()["data"]["status"] == "already_stopped"
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid='nostart-1'"
                            ).fetchall()
    assert len(rows) == 1 and rows[0]["acctstoptime"]


def test_http_interim_without_start_opens_the_session(app, client):
    res = _acct(client, "Interim-Update", "nostart-2", session_time=90,
                framed_ip_address="10.9.9.8")
    data = res.get_json()["data"]
    assert data["status"] == "started" and data["inserted_without_start"] is True
    assert data["session"]["acctstoptime"] is None
    assert data["session"]["framedipaddress"] == "10.9.9.8"
    upd = _acct(client, "Interim-Update", "nostart-2", session_time=150)
    assert upd.get_json()["data"]["status"] == "updated"
    stop = _acct(client, "Stop", "nostart-2", session_time=200)
    assert stop.get_json()["data"]["status"] == "stopped"


def test_general_adjustments_preview_applies_one_year_cap(app, client):
    _seed_sessions(app, ["gau"])
    res = client.post("/api/v1/tools/general-adjustments",
                      json={"action": "extend", "usernames": ["gau"], "minutes": 600000,
                            "dry_run": True}, headers=AUTH)
    assert res.status_code == 422
    from app.radius.core.numbers import EXTEND_TOO_LONG_AR
    assert res.get_json()["error"]["message"] == EXTEND_TOO_LONG_AR
    ok = client.post("/api/v1/tools/general-adjustments",
                     json={"action": "extend", "usernames": ["gau"], "minutes": 525600,
                           "dry_run": True}, headers=AUTH)
    assert ok.status_code == 200 and ok.get_json()["data"]["would_succeed"] == 1


def test_general_adjustments_preview_predicts_the_2100_refusal(app, client):
    _seed_sessions(app, ["gav"])
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE subscribers SET expire_at='2100-06-01T00:00:00' "
                      "WHERE username='gav'")
    data = client.post("/api/v1/tools/general-adjustments",
                       json={"action": "extend", "usernames": ["gav"], "minutes": 525600,
                             "dry_run": True}, headers=AUTH).get_json()["data"]
    assert data["would_succeed"] == 0 and data["items"][0]["status"] == "refused"
    assert _is_arabic(data["items"][0]["error"])


def _router(client) -> int:
    return _nas(client, name=f"f3-dh-{uuid4().hex[:6]}", address="127.0.0.9").get_json()["data"]["id"]


@pytest.mark.parametrize("patch", [
    {"name": "x" * 500}, {"monitoring_enabled": "maybe"}, {"subnet_prefix": "abc"},
    {"subnet_prefix": 99}, {"gateway_last_octet": 300}, {"ping_threshold_ms": "fast"},
    {"name": ["a"]},
])
def test_device_health_rejects_sloppy_input(app, client, patch):
    rid = _router(client)
    body = {"router_id": rid, "name": "dev", "interface_name": "ether2",
            "ip_address": "192.168.50.2", "subnet_prefix": 24}
    body.update(patch)
    res = client.post("/api/v1/device-health/devices", json=body, headers=AUTH)
    assert res.status_code == 422, (patch, res.get_json())
    assert _is_arabic(res.get_json()["error"]["message"])


def test_device_health_valid_create_still_works(app, client):
    rid = _router(client)
    res = client.post("/api/v1/device-health/devices", json={
        "router_id": rid, "name": "dev-ok", "interface_name": "ether3",
        "ip_address": "192.168.51.2", "subnet_prefix": "24",
        "monitoring_enabled": "false"}, headers=AUTH)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["device"]["monitoring_enabled"] in (0, False)
    did = res.get_json()["data"]["device"]["id"]
    res = client.patch(f"/api/v1/device-health/devices/{did}",
                       json={"monitoring_enabled": "maybe"}, headers=AUTH)
    assert res.status_code == 422


def test_device_health_ping_reports_the_real_result(app, client, monkeypatch):
    rid = _router(client)
    did = client.post("/api/v1/device-health/devices", json={
        "router_id": rid, "name": "dev-ping", "interface_name": "ether4",
        "ip_address": "192.168.52.2"}, headers=AUTH).get_json()["data"]["device"]["id"]
    from app.radius.services import device_health
    monkeypatch.setattr(device_health, "probe_reachability", lambda *a, **k: {
        "status": "unavailable", "latency_ms": None, "ping_ok": False,
        "error": "تعذّر الاتصال بالراوتر"})
    data = client.post(f"/api/v1/device-health/devices/{did}/test-ping",
                       headers=AUTH).get_json()["data"]
    assert data["ok"] is False and data["status"] == "unavailable"
    monkeypatch.setattr(device_health, "probe_reachability", lambda *a, **k: {
        "status": "up", "latency_ms": 3, "ping_ok": True, "error": ""})
    data = client.post(f"/api/v1/device-health/devices/{did}/test-ping",
                       headers=AUTH).get_json()["data"]
    assert data["ok"] is True and data["status"] == "up"


def test_nas_patch_null_or_malformed_body_is_422(client):
    nas = _nas(client, address="192.0.2.95").get_json()["data"]
    url = f"/api/v1/nas/{nas['id']}"
    for kwargs in ({"data": "null", "content_type": "application/json"},
                   {"data": "{bad json", "content_type": "application/json"},
                   {"data": "", "content_type": "application/json"},
                   {"json": [1, 2]}):
        res = client.patch(url, headers=AUTH, **kwargs)
        assert res.status_code == 422, kwargs
        assert _is_arabic(res.get_json()["error"]["message"])
    assert client.patch(url, json={}, headers=AUTH).status_code == 200
