"""Fix wave 2 (re-test R07 + network items of R11/R13), 2026-09-29.

One test (or a small group) per item of the `network` stream:
  1. NAS name: a live name → 409 in Arabic; a deleted router's name is free
  2. /accounting/online pages like /sessions/online (total, offset, has_more)
  3. web «المتصلون الآن» search matches the IP and the subscriber mobile
  4. set-speeds: result < 1 kbps → 422; unknown preview/dry_run value → 422
  5. an Interim without Framed-IP-Address keeps the stored session IP
  6. web NAS edit keeps API metadata, never renders the secret, blank = keep;
     ::ffff:a.b.c.d is the same address as a.b.c.d
  7. web temp-speed end time in panel local time; GA dry run times end in Z
  8. dashboard online count == what the lists show
  9. /online renders 100 rows by default (the 500-row page was ~3.3 MB)
 10. card disable without a session logs one line; router errno → Arabic;
     English leftovers → Arabic labels
 11. /admin/radius (no slash) → relative redirect (keeps :8443)
"""
from __future__ import annotations

import logging
import os
import re
import socket
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="hr_f2_network_")
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


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _is_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in str(text or ""))


def _nas(client, **over):
    body = {"name": f"f2-{uuid4().hex[:8]}", "address": "192.0.2.10",
            "secret": "s3cret-ok", "enabled": False}
    body.update(over)
    return client.post("/api/v1/nas", json=body, headers=AUTH)


def _login_web(client):
    from app.radius.db.repos import admins_repo
    u = f"f2n_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="f2n-pass", full_name="F2",
                             is_super_admin=True)
    res = client.post("/admin/radius/login", data={"username": u, "password": "f2n-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/devices/new")
    with client.session_transaction() as sess:
        sess["is_super_admin"] = True
        return sess["_csrf_token"]


def _flashes(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _cat, m in sess.get("_flashes", [])]


def _seed_plan(app, pid=961, down=4096, up=1024):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                      "VALUES (1, 't1', 'T1', ?)", (_now_iso(),))
            c.execute("INSERT OR REPLACE INTO access_plans(id, tenant_id, name, code, "
                      "speed_down_kbps, speed_up_kbps, created_at) "
                      "VALUES (?, 1, ?, ?, ?, ?, ?)",
                      (pid, f"F2 plan {pid}", f"f2_{pid}", down, up, _now_iso()))


def _plan_speed(app, pid=961):
    with app.app_context():
        from app.radius.db.connection import db
        r = db().execute("SELECT speed_down_kbps, speed_up_kbps FROM access_plans "
                         "WHERE id=?", (pid,)).fetchone()
    return (r["speed_down_kbps"], r["speed_up_kbps"])


def _seed_sessions(app, users, *, kind="subscriber", nas_ip="10.20.30.1",
                   mobile_of=None, ip_of=None):
    """Open radacct rows (fresh update time) for subscribers / cards /
    unknown usernames (kind="unknown": no subscriber or card row)."""
    now = datetime.utcnow()
    _seed_plan(app, 951)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            if kind == "card":
                c.execute("INSERT OR IGNORE INTO card_batches(id, tenant_id, batch_code, "
                          "plan_id, created_at) VALUES (901, 1, 'F2B', 951, ?)",
                          (_now_iso(),))
            for i, user in enumerate(users):
                if kind == "subscriber":
                    c.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                              "status, mobile, created_at) VALUES (1, ?, 'x', 951, 'enabled', ?, ?)",
                              (user, (mobile_of or {}).get(user, ""), _now_iso()))
                elif kind == "card":
                    c.execute("INSERT INTO cards(tenant_id, batch_id, username, password, "
                              "plan_id, used, created_at) VALUES (1, 901, ?, 'x', 951, 1, ?)",
                              (user, _now_iso()))
                st = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
                up = now.strftime("%Y-%m-%d %H:%M:%S")
                ip = (ip_of or {}).get(user, f"10.77.{i // 250}.{i % 250 + 1}")
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                    "nasipaddress, framedipaddress, callingstationid, acctstarttime, "
                    "acctupdatetime, acctinputoctets, acctoutputoctets, acctstoptime) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 1, 2, NULL)",
                    (f"sid-{user}", f"uid-{user}", user, nas_ip, ip,
                     f"AA:BB:CC:00:{i // 256:02X}:{i % 256:02X}", st, up))


# ═══════════════════ 1. NAS name ═══════════════════

def test_nas_live_name_is_409_arabic_on_create_and_patch(client):
    first = _nas(client, name="f2-core", address="192.0.2.31")
    assert first.status_code == 201, first.get_json()
    dup = _nas(client, name="f2-core", address="192.0.2.32")
    assert dup.status_code == 409, dup.get_json()
    err = dup.get_json()["error"]
    assert err["code"] == "nas_name_conflict" and _is_arabic(err["message"])
    # case-insensitive
    assert _nas(client, name="F2-CORE", address="192.0.2.33").status_code == 409
    other = _nas(client, name="f2-edge", address="192.0.2.34").get_json()["data"]
    res = client.patch(f"/api/v1/nas/{other['id']}", json={"name": "f2-core"}, headers=AUTH)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "nas_name_conflict"
    # re-saving its own name is fine
    res = client.patch(f"/api/v1/nas/{other['id']}",
                       json={"name": "f2-edge", "description": "x"}, headers=AUTH)
    assert res.status_code == 200, res.get_json()


def test_deleted_router_name_is_reusable_and_restore_renames(app, client):
    old = _nas(client, name="f2-reuse", address="192.0.2.41").get_json()["data"]
    assert client.delete(f"/api/v1/nas/{old['id']}", headers=AUTH).status_code == 200
    again = _nas(client, name="f2-reuse", address="192.0.2.42")
    assert again.status_code == 201, again.get_json()
    # restoring the old one never 500s: it comes back with a suffixed name
    with app.app_context():
        from app.radius.db.repos import nas_repo
        assert nas_repo.restore_nas(1, old["id"]) is True
        restored = nas_repo.get_nas(1, old["id"])
    assert restored.name != "f2-reuse" and restored.name.startswith("f2-reuse")


def test_nas_unique_index_covers_live_rows_only(app):
    with app.app_context():
        from app.radius.db.connection import db
        rows = {r["name"]: r["sql"] for r in db().execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='nas_devices'").fetchall()}
    assert "idx_nas_unique" not in rows
    assert "deleted_at IS NULL" in rows["idx_nas_unique_live"]


def test_parallel_name_race_maps_integrity_error_to_409(app, client, monkeypatch):
    _nas(client, name="f2-race", address="192.0.2.51")
    from app.radius.services import devices as dev
    monkeypatch.setattr(dev, "find_name_owner", lambda *a, **k: None)  # pre-check slipped
    res = _nas(client, name="f2-race", address="192.0.2.52")
    assert res.status_code == 409, res.get_json()
    assert res.get_json()["error"]["code"] == "nas_name_conflict"


def test_web_nas_duplicate_name_is_400_flash_not_500(app, client):
    token = _login_web(client)
    _nas(client, name="f2-web", address="192.0.2.61")
    res = client.post("/admin/radius/devices", data={
        "_csrf_token": token, "name": "f2-web", "address": "192.0.2.62",
        "secret": "websecret", "vendor": "mikrotik", "nas_type": "hotspot"})
    assert res.status_code == 400
    assert "اسم الراوتر" in res.get_data(as_text=True)


# ═══════════════════ 2. /accounting/online paging ═══════════════════

def test_accounting_online_pages_with_total(app, client):
    _seed_sessions(app, [f"ao{i:04d}" for i in range(520)])
    data = client.get("/api/v1/accounting/online?limit=500", headers=AUTH).get_json()["data"]
    assert data["count"] == 500 and data["total"] == 520 and data["has_more"] is True
    data = client.get("/api/v1/accounting/online?limit=500&offset=500",
                      headers=AUTH).get_json()["data"]
    assert data["count"] == 20 and data["has_more"] is False and data["offset"] == 500
    seen = set()
    for off in range(0, 520, 200):
        page = client.get(f"/api/v1/accounting/online?limit=200&offset={off}",
                          headers=AUTH).get_json()["data"]["items"]
        seen.update(r["acctsessionid"] for r in page)
    assert len(seen) == 520
    for qs in ("limit=0", "limit=5000", "offset=-1", "limit=abc"):
        res = client.get(f"/api/v1/accounting/online?{qs}", headers=AUTH)
        assert res.status_code == 422, qs
        assert _is_arabic(res.get_json()["error"]["message"])


# ═══════════════════ 3. /online search: IP + phone ═══════════════════

def test_web_online_search_matches_ip_and_mobile(app, client):
    _seed_sessions(app, ["srch_a", "srch_b"],
                   mobile_of={"srch_a": "0599123407"},
                   ip_of={"srch_a": "10.77.9.44", "srch_b": "10.77.9.55"})
    _login_web(client)
    html = client.get("/admin/radius/online?q=10.77.9.44").get_data(as_text=True)
    assert 'data-username="srch_a"' in html and 'data-username="srch_b"' not in html
    html = client.get("/admin/radius/online?q=0599123407").get_data(as_text=True)
    assert 'data-username="srch_a"' in html and 'data-username="srch_b"' not in html
    # the API searches the same fields (mobile included)
    data = client.get("/api/v1/sessions/online?q=0599123407", headers=AUTH).get_json()["data"]
    assert [i["username"] for i in data["items"]] == ["srch_a"]


# ═══════════════════ 4. set-speeds ═══════════════════

def test_set_speeds_tiny_multiplier_is_422_not_unlimited(app, client):
    _seed_plan(app)
    for dry in (True, False):
        res = client.post("/api/v1/tools/set-speeds",
                          json={"plan_ids": [961], "mult_down": 0.0001,
                                "mult_up": 0.0001, "dry_run": dry}, headers=AUTH)
        assert res.status_code == 422, res.get_json()
        assert _is_arabic(res.get_json()["error"]["message"])
    assert _plan_speed(app) == (4096, 1024)
    # an unlimited (0) plan stays unlimited under any multiplier
    _seed_plan(app, pid=962, down=0, up=0)
    res = client.post("/api/v1/tools/set-speeds",
                      json={"plan_ids": [962], "mult_down": 0.5, "dry_run": True},
                      headers=AUTH)
    assert res.status_code == 200


def test_web_set_speeds_tiny_multiplier_refused(app, client):
    _seed_plan(app)
    token = _login_web(client)
    client.post("/admin/radius/tools/set_speeds",
                data={"_csrf_token": token, "plan_ids": "961",
                      "mult_down": "0.0001", "mult_up": "0.0001"})
    assert _plan_speed(app) == (4096, 1024)


@pytest.mark.parametrize("body", [{"preview": "maybe"}, {"dry_run": "y?"},
                                  {"preview": 2}, {"dry_run": [1]}])
def test_set_speeds_unknown_preview_value_is_422(app, client, body):
    _seed_plan(app)
    res = client.post("/api/v1/tools/set-speeds",
                      json={"plan_ids": [961], "mult_down": 2, **body}, headers=AUTH)
    assert res.status_code == 422, res.get_json()
    assert _plan_speed(app) == (4096, 1024)


def test_set_speeds_known_preview_spellings_still_dry_run(app, client):
    _seed_plan(app)
    for val in (True, "true", 1, "yes", "نعم"):
        res = client.post("/api/v1/tools/set-speeds",
                          json={"plan_ids": [961], "mult_down": 2, "preview": val},
                          headers=AUTH)
        assert res.status_code == 200 and res.get_json()["data"]["dry_run"] is True, val
    assert _plan_speed(app) == (4096, 1024)


def test_general_adjustments_unknown_dry_run_is_422(client):
    res = client.post("/api/v1/tools/general-adjustments",
                      json={"action": "extend", "usernames": ["nobody"], "minutes": 30,
                            "dry_run": "maybe"}, headers=AUTH)
    assert res.status_code == 422


# ═══════════════════ 5. Interim without IP keeps the IP ═══════════════════

def test_interim_without_framed_ip_keeps_stored_ip(app, client):
    base = {"username": "keepip", "acct_session_id": "keepip-1",
            "nas_ip_address": "198.51.100.10"}
    res = client.post("/api/v1/accounting/events",
                      json={**base, "status_type": "Start", "framed_ip_address": "10.0.0.9"},
                      headers=AUTH)
    assert res.status_code == 200, res.get_json()
    res = client.post("/api/v1/accounting/events",
                      json={**base, "status_type": "Interim-Update", "input_octets": 5},
                      headers=AUTH)
    assert res.status_code == 200, res.get_json()
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT framedipaddress FROM radacct WHERE acctsessionid='keepip-1'"
                           ).fetchone()
    assert row["framedipaddress"] == "10.0.0.9"
    # a NEW ip in an interim still replaces it
    client.post("/api/v1/accounting/events",
                json={**base, "status_type": "Interim-Update", "framed_ip_address": "10.0.0.10"},
                headers=AUTH)
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT framedipaddress FROM radacct WHERE acctsessionid='keepip-1'"
                           ).fetchone()
    assert row["framedipaddress"] == "10.0.0.10"


def test_freeradius_interim_query_keeps_ip():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(here, "deploy", "freeradius", "mods-enabled", "sql"),
                encoding="utf-8").read()
    interim = text.split("            interim-update {", 1)[1].split("            stop {", 1)[0]
    update = interim.split("INSERT INTO", 1)[0]
    assert ("framedipaddress = COALESCE(NULLIF('%{Framed-IP-Address}', ''), framedipaddress)"
            in update)


# ═══════════════════ 6. web NAS edit ═══════════════════

def test_web_nas_edit_keeps_metadata_and_secret_and_hides_them(app, client):
    created = _nas(client, name="f2-meta", address="192.0.2.71", secret="topSecret9",
                   api_password="RouterPw77", metadata={"a": 1, "b": "c"}).get_json()["data"]
    nid = created["id"]
    token = _login_web(client)
    html = client.get(f"/admin/radius/devices/{nid}/edit").get_data(as_text=True)
    assert "topSecret9" not in html and "RouterPw77" not in html
    res = client.post(f"/admin/radius/devices/{nid}", data={
        "_csrf_token": token, "name": "f2-meta", "address": "192.0.2.71",
        "secret": "", "api_password": "", "vendor": "mikrotik", "nas_type": "hotspot",
        "location": "Floor 2", "auth_port": "1812", "acct_port": "1813",
        "coa_port": "3799", "api_port": "8728", "ssh_port": "22"})
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:500]
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT secret, api_password, metadata, location FROM nas_devices "
                           "WHERE id=?", (nid,)).fetchone()
    assert row["location"] == "Floor 2"
    assert row["secret"] == "topSecret9" and row["api_password"] == "RouterPw77"
    import json
    assert json.loads(row["metadata"]) == {"a": 1, "b": "c"}
    # a typed secret still changes it
    client.post(f"/admin/radius/devices/{nid}", data={
        "_csrf_token": token, "name": "f2-meta", "address": "192.0.2.71",
        "secret": "newSecret1", "vendor": "mikrotik", "nas_type": "hotspot"})
    with app.app_context():
        from app.radius.db.connection import db
        assert db().execute("SELECT secret FROM nas_devices WHERE id=?",
                            (nid,)).fetchone()["secret"] == "newSecret1"


def test_ipv4_mapped_ipv6_is_the_same_address(client):
    assert _nas(client, address="192.0.2.171").status_code == 201
    dup = _nas(client, address="::ffff:192.0.2.171")
    assert dup.status_code == 409, dup.get_json()
    ok = _nas(client, address="::ffff:192.0.2.172")
    assert ok.status_code == 201 and ok.get_json()["data"]["address"] == "192.0.2.172"


# ═══════════════════ 7. times ═══════════════════

def test_web_temp_speed_flash_shows_local_time(app, client):
    _seed_sessions(app, ["tsloc"])
    token = _login_web(client)
    client.get("/admin/radius/online")  # consume login flashes
    _flashes(client)
    client.post("/admin/radius/online/temp-speed", data={
        "_csrf_token": token, "username": "tsloc", "session_id": "sid-tsloc",
        "down_kbps": "2048", "up_kbps": "512", "duration": "2", "duration_unit": "hours"})
    msgs = " | ".join(_flashes(client))
    assert "tsloc" in msgs, msgs
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", msgs), msgs
    with app.app_context():
        from app.radius.core.system_config import to_local
        expected = to_local(datetime.utcnow() + timedelta(hours=2))
    assert expected[:13] in msgs, (expected, msgs)


def test_general_adjustments_dry_run_times_end_in_z(app, client):
    _seed_sessions(app, ["gaz"])
    res = client.post("/api/v1/tools/general-adjustments",
                      json={"action": "extend", "usernames": ["gaz"], "minutes": 90,
                            "dry_run": True}, headers=AUTH)
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["new_expire_at"].endswith("Z")


# ═══════════════════ 8. dashboard count == lists ═══════════════════

def test_dashboard_online_counts_only_listed_sessions(app, client):
    _seed_sessions(app, ["real1", "real2", "real3"])
    _seed_sessions(app, [f"T-AA:BB:{i:02d}" for i in range(5)], kind="unknown",
                   nas_ip="10.20.30.2")
    listed = client.get("/api/v1/sessions/online", headers=AUTH).get_json()["data"]["total"]
    dash = client.get("/api/v1/dashboard", headers=AUTH).get_json()["data"]
    assert listed == 3
    assert dash["online_now"] == 3 and dash["subscribers"]["online"] == 3
    # the raw accounting view still shows every open row
    raw = client.get("/api/v1/accounting/online", headers=AUTH).get_json()["data"]
    assert raw["total"] == 8


# ═══════════════════ 9. /online page size ═══════════════════

def test_online_cards_tab_defaults_to_100_rows(app, client):
    _seed_sessions(app, [f"pc{i:04d}" for i in range(150)], kind="card")
    _login_web(client)
    res = client.get("/admin/radius/online?type=card")
    html = res.get_data(as_text=True)
    assert html.count("data-rowctx") == 100
    assert "data-online-page-size" in html and "per_page=500" in html
    assert len(res.data) < 1_500_000
    html = client.get("/admin/radius/online?type=card&per_page=500").get_data(as_text=True)
    assert html.count("data-rowctx") == 150


# ═══════════════════ 10. logs, errno, English leftovers ═══════════════════

def test_disable_card_without_session_logs_one_line(app, caplog):
    _seed_plan(app, 951)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO card_batches(id, tenant_id, batch_code, plan_id, created_at) "
                      "VALUES (902, 1, 'F2D', 951, ?)", (_now_iso(),))
            cid = c.execute("INSERT INTO cards(tenant_id, batch_id, username, password, "
                            "plan_id, created_at) VALUES (1, 902, 'dis1', 'x', 951, ?)",
                            (_now_iso(),)).lastrowid
    with app.test_request_context():
        from app.radius.services.cards import get_cards_service
        caplog.set_level(logging.INFO)
        get_cards_service().disable_card(actor="t", card_id=cid)
    kicks = [r for r in caplog.records if "disable_card" in r.getMessage()]
    assert kicks, [r.getMessage() for r in caplog.records]
    assert all(r.exc_info is None for r in kicks)
    assert all(r.levelno <= logging.INFO for r in kicks)


def test_log_kick_failure_levels(caplog):
    from app.radius.core.errors import RadiusConflict, RadiusError
    from app.radius.services.cards import _log_kick_failure
    caplog.set_level(logging.INFO)
    _log_kick_failure("x", "card=1", RadiusConflict("لا توجد جلسة نشطة"))
    _log_kick_failure("x", "card=1", RadiusError("الراوتر لا يستجيب"))
    assert [r.levelno for r in caplog.records] == [logging.INFO, logging.WARNING]
    assert all(r.exc_info is None for r in caplog.records)


def test_router_connection_refused_is_arabic():
    from app.radius.integration.mikrotik.client import MikrotikClient
    from app.radius.integration.mikrotik.errors import ConnectError, os_error_reason_ar
    msg = os_error_reason_ar(ConnectionRefusedError(111, "Connection refused"))
    assert _is_arabic(msg) and "Errno" not in msg
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens on it now
    with pytest.raises(ConnectError) as ei:
        MikrotikClient(host="127.0.0.1", port=port, username="u", password="p",
                       timeout=2).connect()
    text = str(ei.value)
    assert "تعذّر الاتصال بالراوتر" in text and "Errno" not in text
    assert "refused" not in text.lower()


def test_safe_dial_oserror_is_arabic():
    from contextlib import contextmanager
    from unittest.mock import patch

    from app.radius.services import mikrotik_admin_client as mac

    @contextmanager
    def boom(cfg):
        raise ConnectionRefusedError(111, "Connection refused")
        yield  # pragma: no cover

    nas = {"id": 1, "address": "192.0.2.9", "api_port": 8728, "api_user": "u",
           "api_password": "p"}
    with patch.object(mac, "_pool_acquire", boom):
        res = mac._safe_dial(nas=nas, operation="t", work=lambda c: c)
    assert res.ok is False and "Errno" not in res.error and _is_arabic(res.error)


def test_online_page_mode_label_is_arabic(app, client):
    _seed_sessions(app, ["mode1"])
    _login_web(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    assert "وضع التشغيل: sqlite" not in html and "الوضع sqlite" not in html
    assert "الرديوس المحلّي" in html


def test_backups_status_labels_are_arabic(client):
    data = client.get("/api/v1/backups/status", headers=AUTH).get_json()["data"]
    job = data["job"]
    assert job["last_status"] == "never_run"
    assert job["last_status_label"] == "لم تُشغَّل بعد"
    assert _is_arabic(job["last_message"]) and "No local backup" not in job["last_message"]


def test_admin_alert_descriptions_have_no_code_paths(client):
    data = client.get("/api/v1/alerts/telegram", headers=AUTH).get_json()["data"]
    for item in data["catalogue"]:
        desc = item["description"]
        assert "services/" not in desc and "UsersService" not in desc, desc
        assert not re.search(r"\b[a-z_]+\.[a-z_]+\b", desc), desc
        assert "⚑" not in desc and _is_arabic(desc)
    from app.radius.services.admin_alerts import public_description
    assert public_description("يُرسل عند حظر IP/MAC (access_control.x_y).") == \
        "يُرسل عند حظر IP/MAC."


def test_recycle_bin_labels_are_arabic(app, client):
    _seed_plan(app, 951)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, "
                      "plan_id, status, deleted_at, deleted_by, delete_reason, created_at) "
                      "VALUES (903, 1, 'B-F2-1', 'باقة الاختبار', 951, 'deleted', ?, "
                      "'adapter', 'Archived from card batch operations API', ?)",
                      (_now_iso(), _now_iso()))
    items = client.get("/api/v1/recycle-bin?entity_type=card_batches",
                       headers=AUTH).get_json()["data"]["items"]
    it = [i for i in items if i["id"] == 903][0]
    assert it["label"] == "باقة الاختبار (B-F2-1)"
    assert it["status_label"] == "محذوف (في السلّة)"
    assert it["deleted_by_label"] == "النظام"
    assert _is_arabic(it["delete_reason"])
    _login_web(client)
    html = client.get("/admin/radius/recycle-bin?entity_type=card_batches").get_data(as_text=True)
    assert "Archived from card batch operations API" not in html
    assert ">deleted<" not in html and "باقة الاختبار" in html and "النظام" in html


def test_ticket_category_label(app, client):
    _seed_sessions(app, ["tk_sub"])
    with app.app_context():
        from app.radius.db.connection import db
        sid = db().execute("SELECT id FROM subscribers WHERE username='tk_sub'").fetchone()["id"]
    res = client.post("/api/v1/tickets", json={"subscriber_id": sid, "subject": "s",
                                                "category": "network"}, headers=AUTH)
    assert res.status_code in (200, 201), res.get_json()
    body = res.get_json()["data"]
    ticket = body.get("ticket", body)
    assert ticket["category_label"] == "الشبكة"
    _login_web(client)
    html = client.get("/admin/radius/tickets").get_data(as_text=True)
    assert "الشبكة" in html


def test_checker_mixed_card_time_is_arabic():
    from app.radius.core.duration_fmt import fmt_base_time_ar
    assert fmt_base_time_ar(86400 + 3 * 3600 + 45 * 60) == (
        "1 يوم و3 ساعات و45 دقيقة", False)


def test_ledger_and_delivery_labels():
    from app.radius.services.business_os_finance import _ledger_labels, ledger_account_label
    assert ledger_account_label("cash") == "الصندوق (نقدًا)"
    assert ledger_account_label("wallet:4") == "محفظة #4"
    assert ledger_account_label("wallet:manager:3") == "محفظة المدير #3"
    row = _ledger_labels({"debit_account": "cash", "credit_account": "wallet:4",
                          "target_type": "distributor", "target_id": 1})
    assert row["target_label"] == "موزّع #1"
    from app.radius.services.notification_campaigns import NotificationCampaignService
    svc = NotificationCampaignService.__new__(NotificationCampaignService)
    out = svc._delivery_row({"status": "skipped", "result_json": "{}"})
    assert out["status_label"] == "تم التخطّي"


# ═══════════════════ 11. relative redirect ═══════════════════

def test_admin_radius_no_slash_redirect_keeps_port(client):
    res = client.get("/admin/radius", headers={"Host": "client20.example.test"},
                     follow_redirects=False)
    assert res.status_code in (301, 308)
    assert res.headers["Location"] == "/admin/radius/"


def test_redirect_to_other_host_untouched(app):
    from flask import redirect
    with app.test_request_context("/x", headers={"Host": "client20.example.test"}):
        r = app.process_response(redirect("https://example.org/x"))
        assert r.headers["Location"] == "https://example.org/x"
        r = app.process_response(redirect("http://client20.example.test/admin/radius/y?a=1"))
        assert r.headers["Location"] == "/admin/radius/y?a=1"
        # a scheme change (http → https upgrade) is never made relative
        r = app.process_response(redirect("https://client20.example.test/z"))
        assert r.headers["Location"] == "https://client20.example.test/z"
