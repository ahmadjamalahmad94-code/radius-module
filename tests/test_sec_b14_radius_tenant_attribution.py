"""B-14 — RADIUS accounting / login attempts must land in the tenant that owns
the NAS, never in tenant 1 by default.

Before the fix (security review, BYPASS_PATHS.md B-14 + B-10):
  • `deploy/freeradius/mods-enabled/sql` wrote the literal `1` as `tenant_id`
    on every accounting INSERT (start / interim fallback / stop fallback) and
    on the post-auth INSERT;
  • `/api/v1/internal/auth` resolved the tenant from the in-packet
    NAS-IP-Address (often a private LAN address, and set by the router
    itself), falling back to tenant 1 for anything it did not recognise —
    so login attempts (with the attempted password) were logged in tenant 1
    and the subscriber was even authenticated against tenant 1's users.

On a server holding more than one network, tenant 1 therefore saw every
network's sessions and failed logins.

The FreeRADIUS queries are executed for real against SQLite here: each
`query = "…"` of a type is rendered with the xlats a MikroTik packet would
expand, and run in order the way rlm_sql does (the next query only when the
previous one changed 0 rows).
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "b14-token"            # env API token → tenant 1 (app/api/auth.py)
SECRET = "b14-internal-secret"

T2_TUNNEL_IP = "10.10.0.22"     # tenant 2 router, WireGuard tunnel source IP
T2_LAN_IP = "192.168.88.1"      # what that router writes in NAS-IP-Address
T1_TUNNEL_IP = "10.10.0.11"     # tenant 1 router
UNKNOWN_IP = "10.10.0.99"       # a RADIUS client with no nas_devices row


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ─────────────────────────── fixtures ───────────────────────────

@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "b14.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", SECRET)
    monkeypatch.setenv("FLASK_SECRET", "b14-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _add_tenant(tid: int, slug: str) -> None:
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO tenants(id, slug, name, display_name, status, created_at) "
        "VALUES(?,?,?,?, 'active', ?)", (tid, slug, slug, slug, _now()))


def _add_nas(tid: int, name: str, *, address: str = "", vpn: str = "",
             mgmt: str = "", enabled: int = 1, deleted: bool = False) -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
        " nas_type, enabled, created_at, vpn_peer_address, "
        " management_remote_address, deleted_at) "
        "VALUES(?,?,?,?, 'mikrotik', 'hotspot', ?, ?, ?, ?, ?)",
        (tid, name, address, "s3cret", enabled, _now(), vpn, mgmt,
         _now() if deleted else None))
    return int(cur.lastrowid)


@pytest.fixture
def two_networks(app):
    """Tenant 1 and tenant 2, one router each, both behind the WG tunnel.
    Tenant 2's router is registered with its public address + tunnel IP."""
    _add_tenant(2, "net-two")
    _add_nas(1, "t1-router", address="203.0.113.10", vpn=T1_TUNNEL_IP)
    _add_nas(2, "t2-router", address="198.51.100.20", vpn=T2_TUNNEL_IP)
    return app


# ─────────────── FreeRADIUS sql module, executed for real ───────────────

_DEFAULTS = {
    "Acct-Session-Id": "81a00001", "User-Name": "acct_user",
    "Acct-Session-Time": "120", "Acct-Terminate-Cause": "User-Request",
    "Acct-Input-Octets": "1000", "Acct-Output-Octets": "2000",
    "Calling-Station-Id": "AA:BB:CC:DD:EE:01", "NAS-IP-Address": T2_LAN_IP,
}


def _section(kind: str) -> str:
    sql = _read("deploy/freeradius/mods-enabled/sql")
    if kind == "post-auth":
        start = sql.index("\n    post-auth {")
        return sql[start: sql.index("\n    }", start + 1)]
    start = sql.index(f"\n            {kind} {{")
    return sql[start: sql.index("\n            }", start + 1)]


def _render(q: str, values: dict) -> str:
    q = q.replace("\\\n", " ")
    q = q.replace("${....acct_table1}", "radacct").replace("${....acct_table2}", "radacct")
    q = re.sub(r"%\{%\{([^}]+)\}:-0\}", lambda m: values.get(m.group(1), "0"), q)
    q = re.sub(r"%\{tolower:[^}]+\}", "", q)
    q = re.sub(r"%\{([^}]+)\}", lambda m: values.get(m.group(1), ""), q)
    return q


def _fr_queries(kind: str, **values) -> list[str]:
    vals = dict(_DEFAULTS)
    vals.update({k.replace("_", "-"): v for k, v in values.items()})
    raw = re.findall(r'query = "(.*?)"\s*\n', _section(kind) + "\n", re.S)
    assert raw, f"no query in {kind}"
    return [_render(q, vals) for q in raw]


def _fr_run(kind: str, **values) -> int:
    """rlm_sql semantics: run the queries of the type in order; the next one
    only when the previous one changed 0 rows. Returns the rows changed."""
    from app.radius.db.connection import db
    for q in _fr_queries(kind, **values):
        n = db().execute(q).rowcount
        if n and n > 0:
            return n
    return 0


def _rows(sql: str, args=()) -> list:
    from app.radius.db.connection import db
    return db().execute(sql, args).fetchall()


def _acct_tenants(username: str) -> list[int]:
    return [r["tenant_id"] for r in _rows(
        "SELECT tenant_id FROM radacct WHERE username=? ORDER BY radacctid", (username,))]


def _auth(client, *, user: str, password: str, src: str | None,
          nas_ip: str = T2_LAN_IP) -> dict:
    body = {"_internal_secret": SECRET, "User-Name": user,
            "User-Password": password, "NAS-IP-Address": nas_ip,
            "Calling-Station-Id": "AA:BB:CC:DD:EE:02"}
    if src is not None:
        body["Packet-Src-IP-Address"] = src
    res = client.post("/api/v1/internal/auth", json=body)
    assert res.status_code == 200, res.data
    return res.get_json()


def _api_get(client, path: str):
    return client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})


def _add_subscriber(tid: int, username: str, password: str) -> None:
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tid, username=username, password=password,
        status="enabled"))


# ═══════════════════ 1. reproduction (fails before the fix) ═══════════════════

def test_accounting_start_from_tenant2_router_lands_in_tenant2(two_networks, client):
    _fr_run("start", Packet_Src_IP_Address=T2_TUNNEL_IP, User_Name="t2_user")
    assert _acct_tenants("t2_user") == [2]

    from app.radius.services.accounting_events import AccountingEventsService
    svc = AccountingEventsService()
    assert [r["username"] for r in svc.list_online(tenant_id=2)] == ["t2_user"]
    assert svc.list_online(tenant_id=1) == []
    # tenant 1's API (env token = tenant 1) does not see tenant 2's session.
    res = _api_get(client, "/api/v1/accounting/online")
    assert res.status_code == 200
    assert "t2_user" not in res.get_data(as_text=True)


def test_interim_and_stop_fallback_inserts_land_in_tenant2(two_networks):
    # Interim for a session whose Start was lost → fallback INSERT.
    _fr_run("interim-update", Packet_Src_IP_Address=T2_TUNNEL_IP,
            User_Name="t2_interim", Acct_Session_Id="s-int")
    # Stop for a session that was never stored → fallback INSERT (closed).
    _fr_run("stop", Packet_Src_IP_Address=T2_TUNNEL_IP,
            User_Name="t2_stop", Acct_Session_Id="s-stop")
    assert _acct_tenants("t2_interim") == [2]
    assert _acct_tenants("t2_stop") == [2]


def test_login_attempt_from_tenant2_router_is_logged_in_tenant2(two_networks, client):
    _add_subscriber(2, "ghost", "the-real-pass")
    out = _auth(client, user="ghost", password="typo-of-real-pass", src=T2_TUNNEL_IP)
    assert out["control:Auth-Type"] == "Reject"
    rows = _rows("SELECT tenant_id, pass FROM radpostauth WHERE username='ghost'")
    assert [r["tenant_id"] for r in rows] == [2]
    # tenant 1's failed-login API shows nothing of tenant 2 (attempted password
    # included).
    res = _api_get(client, "/api/v1/tools/radius-log")
    assert res.status_code == 200
    assert "ghost" not in res.get_data(as_text=True)


def test_same_username_in_two_tenants_authenticates_against_the_routers_tenant(
        two_networks, client):
    _add_subscriber(1, "shared", "pass-of-tenant-one")
    _add_subscriber(2, "shared", "pass-of-tenant-two")
    # Tenant 2's router: tenant 1's password must NOT work there…
    bad = _auth(client, user="shared", password="pass-of-tenant-one", src=T2_TUNNEL_IP)
    assert bad["control:Auth-Type"] == "Reject"
    # …tenant 2's own password does.
    good = _auth(client, user="shared", password="pass-of-tenant-two", src=T2_TUNNEL_IP)
    assert good["control:Auth-Type"] == "Accept"
    # And on tenant 1's router the reverse holds.
    assert _auth(client, user="shared", password="pass-of-tenant-one",
                 src=T1_TUNNEL_IP, nas_ip="192.168.88.1")["control:Auth-Type"] == "Accept"
    assert _auth(client, user="shared", password="pass-of-tenant-two",
                 src=T1_TUNNEL_IP, nas_ip="192.168.88.1")["control:Auth-Type"] == "Reject"
    tenants = sorted({r["tenant_id"] for r in _rows(
        "SELECT tenant_id FROM radpostauth WHERE username='shared'")})
    assert tenants == [1, 2]


def test_shipped_config_has_no_literal_tenant_one():
    sql = _read("deploy/freeradius/mods-enabled/sql")
    # no `SELECT 1, '%{Acct-Session-Id}'` / `VALUES (1, '%{User-Name}'`
    assert not re.search(r"SELECT\s*\\?\s*1,\s*'%\{Acct-Session-Id\}", sql)
    assert not re.search(r"\(\s*1,\s*'%\{User-Name\}'", sql)
    rest = _read("deploy/freeradius/mods-enabled/rest")
    # authorize AND post-auth carry the packet's real source address
    assert rest.count('"Packet-Src-IP-Address": "%{Packet-Src-IP-Address}"') == 2


# ═══════════════════ 2. unknown / ambiguous router: quarantine ═══════════════════

def _quarantine(kind: str) -> list:
    return _rows("SELECT * FROM radius_unattributed WHERE kind=? ORDER BY id", (kind,))


def test_unknown_router_on_multi_network_server_is_quarantined_not_tenant1(
        two_networks, client):
    for kind in ("start", "interim-update", "stop"):
        changed = _fr_run(kind, Packet_Src_IP_Address=UNKNOWN_IP,
                          User_Name="stray", Acct_Session_Id="s-stray")
        assert changed == 1          # written somewhere -> the NAS gets its ACK
    assert _acct_tenants("stray") == []           # in NO tenant's radacct
    q = _quarantine("acct")
    assert len(q) == 1
    assert (q[0]["src_ip"], q[0]["username"], q[0]["reason"]) == (
        UNKNOWN_IP, "stray", "unknown_nas")
    assert q[0]["packets"] == 3 and q[0]["last_status"] == "Stop"
    assert q[0]["acctoutputoctets"] == 2000

    # A login from it is rejected — not evaluated against tenant 1's users.
    _add_subscriber(1, "stray", "tenant-one-pass")
    out = _auth(client, user="stray", password="tenant-one-pass", src=UNKNOWN_IP)
    assert out["control:Auth-Type"] == "Reject"
    assert _rows("SELECT * FROM radpostauth WHERE username='stray'") == []
    a = _quarantine("auth")
    assert len(a) == 1 and a[0]["username"] == "stray"
    # the quarantine never holds a password
    cols = [r["name"] for r in _rows("PRAGMA table_info(radius_unattributed)")]
    assert not any("pass" in c for c in cols)


def test_unknown_router_does_not_feed_tenant1_webhooks(two_networks, client, monkeypatch):
    sent = []
    import app.webhooks.dispatcher as disp
    monkeypatch.setattr(disp, "dispatch_event",
                        lambda ev, payload, tenant_id=None: sent.append(tenant_id))
    body = {"_internal_secret": SECRET, "User-Name": "u", "reply_code": "Access-Accept",
            "NAS-IP-Address": T2_LAN_IP}
    assert client.post("/api/v1/internal/postauth",
                       json={**body, "Packet-Src-IP-Address": UNKNOWN_IP}).status_code == 200
    assert sent == []
    assert client.post("/api/v1/internal/postauth",
                       json={**body, "Packet-Src-IP-Address": T2_TUNNEL_IP}).status_code == 200
    assert sent == [2]


def test_same_address_registered_in_two_tenants_is_never_guessed(two_networks, client):
    _add_nas(1, "t1-clash", address="10.10.0.50")
    _add_nas(2, "t2-clash", vpn="10.10.0.50")
    _fr_run("start", Packet_Src_IP_Address="10.10.0.50", User_Name="clash")
    assert _acct_tenants("clash") == []
    assert _quarantine("acct")[0]["reason"] == "ambiguous_nas"
    _add_subscriber(1, "clash", "p1")
    assert _auth(client, user="clash", password="p1",
                 src="10.10.0.50")["control:Auth-Type"] == "Reject"
    assert _quarantine("auth")[0]["reason"] == "ambiguous_nas"


# ═══════════════════ 3. single-network servers: unchanged ═══════════════════

def test_single_network_server_keeps_attributing_unknown_clients(app, client):
    # no router registered at all, one tenant — previous behaviour kept
    _fr_run("start", Packet_Src_IP_Address="127.0.0.1", User_Name="lab")
    assert _acct_tenants("lab") == [1]
    _add_subscriber(1, "lab2", "pw")
    assert _auth(client, user="lab2", password="pw",
                 src="127.0.0.1")["control:Auth-Type"] == "Accept"
    # an rlm_rest body from before the fix (no Packet-Src-IP-Address) still works
    assert _auth(client, user="lab2", password="pw", src=None,
                 nas_ip="192.168.88.1")["control:Auth-Type"] == "Accept"
    assert _quarantine("acct") == [] and _quarantine("auth") == []


# ═══════════════════ 4. adversarial: reuse / rename / spoof ═══════════════════

def test_reused_nas_address_never_touches_the_previous_tenants_rows(app):
    from app.radius.db.connection import db
    _add_tenant(2, "net-two")
    # Tenant 1 used 10.10.0.60, left a zombie open session 81a00001, then
    # deleted the router; tenant 2 now owns the address.
    _add_nas(1, "old-t1", vpn="10.10.0.60", deleted=True)
    db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        " nasipaddress, acctstarttime) VALUES(1,'81a00001','old','olduser',"
        " '10.10.0.60', datetime('now','-1 day'))")
    _add_nas(2, "new-t2", vpn="10.10.0.60")
    # Same session id (MikroTik counters restart) from the new owner.
    _fr_run("interim-update", Packet_Src_IP_Address="10.10.0.60",
            User_Name="olduser", Acct_Session_Id="81a00001")
    old = _rows("SELECT * FROM radacct WHERE tenant_id=1")[0]
    assert old["acctupdatetime"] is None and old["acctinputoctets"] == 0
    assert _acct_tenants("olduser") == [1, 2]
    _fr_run("stop", Packet_Src_IP_Address="10.10.0.60",
            User_Name="olduser", Acct_Session_Id="81a00001")
    assert _rows("SELECT acctstoptime FROM radacct WHERE tenant_id=1")[0][0] is None
    # Accounting-On (router reboot) of the new owner closes only its own rows.
    _fr_run("start", Packet_Src_IP_Address="10.10.0.60", User_Name="t2live",
            Acct_Session_Id="81a00002")
    _fr_run("accounting-on", Packet_Src_IP_Address="10.10.0.60")
    assert _rows("SELECT acctstoptime FROM radacct WHERE tenant_id=1")[0][0] is None
    assert _rows("SELECT acctstoptime FROM radacct WHERE username='t2live'")[0][0]


def test_disabled_router_of_another_tenant_does_not_make_the_address_ambiguous(app):
    _add_tenant(2, "net-two")
    _add_nas(1, "t1-off", vpn="10.10.0.70", enabled=0)
    _add_nas(2, "t2-on", vpn="10.10.0.70")
    _fr_run("start", Packet_Src_IP_Address="10.10.0.70", User_Name="dis")
    assert _acct_tenants("dis") == [2]


def test_renamed_router_keeps_its_attribution(two_networks, client):
    from app.radius.db.connection import db
    db().execute("UPDATE nas_devices SET name='renamed', shortname='renamed' "
                 "WHERE tenant_id=2")
    _fr_run("start", Packet_Src_IP_Address=T2_TUNNEL_IP, User_Name="ren")
    assert _acct_tenants("ren") == [2]
    _add_subscriber(2, "ren2", "pw2")   # (ren has a live session: device limit)
    assert _auth(client, user="ren2", password="pw2",
                 src=T2_TUNNEL_IP)["control:Auth-Type"] == "Accept"


def test_spoofed_nas_ip_address_attribute_cannot_pick_the_tenant(two_networks, client):
    """Tenant 2's router writes tenant 1's router address in NAS-IP-Address."""
    _add_subscriber(1, "victim", "tenant-one-pass")
    out = _auth(client, user="victim", password="tenant-one-pass",
                src=T2_TUNNEL_IP, nas_ip="203.0.113.10")
    assert out["control:Auth-Type"] == "Reject"
    _fr_run("start", Packet_Src_IP_Address=T2_TUNNEL_IP, User_Name="victim",
            NAS_IP_Address="203.0.113.10")
    assert _acct_tenants("victim") == [2]


def test_sstp_management_address_and_cidr_suffix_are_matched(app):
    _add_tenant(2, "net-two")
    _add_tenant(3, "net-three")
    _add_nas(2, "sstp", address="198.51.100.30", mgmt="10.50.0.7")
    _add_nas(3, "wg", vpn="10.10.0.33/32")
    _fr_run("start", Packet_Src_IP_Address="10.50.0.7", User_Name="sstp_u")
    _fr_run("start", Packet_Src_IP_Address="10.10.0.33", User_Name="wg_u")
    assert _acct_tenants("sstp_u") == [2]
    assert _acct_tenants("wg_u") == [3]


def test_python_resolver_agrees_with_the_freeradius_expression(two_networks):
    from app.radius.db.connection import db
    from app.radius.services.nas_tenant import resolve_source_tenant
    _add_nas(1, "c1", address="10.10.0.80")
    _add_nas(2, "c2", address="10.10.0.80")
    expr = re.search(r"(COALESCE\(\(SELECT tenant_id FROM radius_source_tenant.*?"
                     r"radius_sole_tenant\)\))", _section("start"), re.S).group(1)
    for ip in (T1_TUNNEL_IP, T2_TUNNEL_IP, "198.51.100.20", UNKNOWN_IP,
               "10.10.0.80", ""):
        fr = db().execute("SELECT " + _render(
            expr, {"Packet-Src-IP-Address": ip})).fetchone()[0]
        assert resolve_source_tenant(ip).tenant_id == fr, ip


def test_quarantine_table_is_pruned_by_log_retention():
    from app.radius.services import log_retention
    assert any(r.table == "radius_unattributed" for r in log_retention._RULES)
