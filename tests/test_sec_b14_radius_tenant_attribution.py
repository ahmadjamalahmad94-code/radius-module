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
