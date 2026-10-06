"""B-14 follow-up — server-local RADIUS sources (accel-ppp on the panel host,
loopback tooling, health probes).

Found by the B-14 pre-deploy detection (tools/sec_b14_detect.sql D6) on a
multi-network pilot server: every session of the server-local accel-ppp
(source = its gateway address on the host, e.g. 10.50.0.1) and of anything
talking from 127.0.0.1 comes from an address no tenant's router row claims.
With B-14 alone such a source is rejected / quarantined on a multi-network
server, and — worse — ANY tenant could add a router row claiming 127.0.0.1 or
the accel gateway and silently receive those sessions.

The rule kept here: an unknown source on a multi-network server still fails
closed. Server-local sources are attributed only through an explicit,
operator-only registry (`radius_local_nas`), never implicitly, and a tenant's
router row can never claim a loopback or registered local address.

The FreeRADIUS queries are executed for real on SQLite, as in
tests/test_sec_b14_radius_tenant_attribution.py (helpers shared from there).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

from test_sec_b14_radius_tenant_attribution import (  # noqa: F401  (fixtures)
    SECRET, T1_TUNNEL_IP, T2_TUNNEL_IP, UNKNOWN_IP, _acct_tenants, _add_nas,
    _add_subscriber, _add_tenant, _auth, _fr_run, _render, _rows, _section,
    app, client, two_networks,
)

ROOT = Path(__file__).resolve().parents[1]
ACCEL_GW = "10.50.0.1"     # accel-ppp gateway on the host (its RADIUS source)
LOOPBACK = "127.0.0.1"


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db_path() -> str:
    from app.radius.db.connection import db
    return db().execute("PRAGMA database_list").fetchone()["file"]


def _register(ip: str, purpose: str, tenant: int | None = None,
              service: str = "accel-ppp") -> None:
    """Register a local source exactly as the operator would (CLI tool)."""
    args = ["--db", _db_path(), "set", ip, "--purpose", purpose, "--service", service]
    if tenant is not None:
        args += ["--tenant", str(tenant)]
    assert _tool("radius_local_nas").main(args) == 0


def _quarantine(kind: str) -> list:
    return _rows("SELECT * FROM radius_unattributed WHERE kind=? ORDER BY id", (kind,))


# ═══════════ 1. local accel attributed to its configured tenant ═══════════

def test_local_accel_source_is_attributed_to_its_configured_tenant(two_networks, client):
    _register(ACCEL_GW, "nas", tenant=2)
    _fr_run("start", Packet_Src_IP_Address=ACCEL_GW, User_Name="dc_user",
            NAS_IP_Address=ACCEL_GW)
    _fr_run("interim-update", Packet_Src_IP_Address=ACCEL_GW, User_Name="dc_user",
            NAS_IP_Address=ACCEL_GW)
    assert _acct_tenants("dc_user") == [2]
    assert _rows("SELECT acctupdatetime FROM radacct WHERE username='dc_user'")[0][0]

    _add_subscriber(1, "dc_shared", "tenant-one-pass")
    _add_subscriber(2, "dc_shared", "tenant-two-pass")
    assert _auth(client, user="dc_shared", password="tenant-two-pass",
                 src=ACCEL_GW, nas_ip=ACCEL_GW)["control:Auth-Type"] == "Accept"
    assert _auth(client, user="dc_shared", password="tenant-one-pass",
                 src=ACCEL_GW, nas_ip=ACCEL_GW)["control:Auth-Type"] == "Reject"
    assert {r["tenant_id"] for r in _rows(
        "SELECT tenant_id FROM radpostauth WHERE username='dc_shared'")} == {2}
    assert _quarantine("acct") == [] and _quarantine("auth") == []


def test_management_only_local_source_belongs_to_no_tenant(two_networks, client):
    """accel carrying only router management tunnels (rtr-*): its accounting is
    infrastructure — never a tenant's session — and it is not a login NAS."""
    _register(ACCEL_GW, "mgmt")
    assert _fr_run("start", Packet_Src_IP_Address=ACCEL_GW, User_Name="rtr-gr3") == 1
    assert _acct_tenants("rtr-gr3") == []
    q = _quarantine("acct")
    assert [(r["src_ip"], r["reason"]) for r in q] == [(ACCEL_GW, "local_mgmt")]
    _add_subscriber(1, "via_mgmt", "pw")
    assert _auth(client, user="via_mgmt", password="pw",
                 src=ACCEL_GW)["control:Auth-Type"] == "Reject"
    assert _rows("SELECT * FROM radpostauth WHERE username='via_mgmt'") == []
    assert _quarantine("auth")[0]["reason"] == "local_mgmt"


# ═══════════ 2. unknown non-local source: still fails closed ═══════════

def test_unknown_non_local_source_is_still_rejected_and_quarantined(two_networks, client):
    _register(ACCEL_GW, "nas", tenant=1)
    _register(LOOPBACK, "probe", service="healthcheck")
    for kind in ("start", "interim-update", "stop"):
        assert _fr_run(kind, Packet_Src_IP_Address=UNKNOWN_IP,
                       User_Name="stray", Acct_Session_Id="s-stray") == 1
    assert _acct_tenants("stray") == []
    assert [r["reason"] for r in _quarantine("acct")] == ["unknown_nas"]
    _add_subscriber(1, "stray", "pw")
    assert _auth(client, user="stray", password="pw",
                 src=UNKNOWN_IP)["control:Auth-Type"] == "Reject"
    assert [r["reason"] for r in _quarantine("auth")] == ["unknown_nas"]


def test_unregistered_loopback_on_multi_network_server_fails_closed(two_networks, client):
    """No implicit «loopback = tenant 1» on a multi-network server."""
    assert _fr_run("start", Packet_Src_IP_Address=LOOPBACK, User_Name="lo_user") == 1
    assert _acct_tenants("lo_user") == []
    assert _quarantine("acct")[0]["reason"] == "unknown_nas"
    _add_subscriber(1, "lo_user", "pw")
    assert _auth(client, user="lo_user", password="pw",
                 src=LOOPBACK)["control:Auth-Type"] == "Reject"


# ═══════════ 3. a tenant cannot claim a loopback / host address ═══════════

def test_tenant_router_row_cannot_hijack_loopback_or_a_registered_local_source(
        two_networks, client):
    _register(ACCEL_GW, "nas", tenant=1)
    # Tenant 2 registers "routers" on the server's own addresses.
    _add_nas(2, "hijack-lo", address=LOOPBACK)
    _add_nas(2, "hijack-lo2", vpn="127.0.0.53/32")
    _add_nas(2, "hijack-accel", address="198.51.100.77", mgmt=ACCEL_GW)
    _add_nas(2, "hijack-v6", address="::1")

    _fr_run("start", Packet_Src_IP_Address=ACCEL_GW, User_Name="acc_user")
    assert _acct_tenants("acc_user") == [1]           # the registry wins
    for ip in (LOOPBACK, "127.0.0.53"):
        _fr_run("start", Packet_Src_IP_Address=ip, User_Name=f"lo_{ip}")
        assert _acct_tenants(f"lo_{ip}") == []         # not tenant 2
    _add_subscriber(2, "t2sub", "pw2")
    assert _auth(client, user="t2sub", password="pw2",
                 src=LOOPBACK)["control:Auth-Type"] == "Reject"
    assert _rows("SELECT * FROM radpostauth WHERE username='t2sub'") == []

    from app.radius.services.nas_tenant import resolve_source_tenant
    assert resolve_source_tenant(LOOPBACK).tenant_id is None
    assert resolve_source_tenant(ACCEL_GW).tenant_id == 1
    assert resolve_source_tenant("::1").tenant_id is None


# ═══════════ 4. health probes: identifiable, no sessions, no charges ═══════════

@pytest.mark.parametrize("multi", [True, False])
def test_probe_source_creates_no_session_and_no_quarantine(app, client, multi):
    if multi:
        _add_tenant(2, "net-two")
    _register(LOOPBACK, "probe", service="healthcheck")
    for kind in ("start", "interim-update", "stop", "accounting-on"):
        # 0 rows changed → rlm_sql noop → the probe still gets its ACK.
        assert _fr_run(kind, Packet_Src_IP_Address=LOOPBACK,
                       User_Name="probe", Acct_Session_Id="p1") == 0
    assert _acct_tenants("probe") == []
    assert _quarantine("acct") == []
    _add_subscriber(1, "probe", "pw")
    out = _auth(client, user="probe", password="pw", src=LOOPBACK)
    assert out["control:Auth-Type"] == "Reject"
    assert "probe" in out["reply:Reply-Message"].lower()
    assert _rows("SELECT * FROM radpostauth WHERE username='probe'") == []
    assert _quarantine("auth") == []


# ═══════════ 5. single-network servers: unchanged ═══════════

def test_single_network_server_without_registry_is_unchanged(app, client):
    _add_nas(1, "lo-router", address=LOOPBACK)    # even a loopback row: same tenant
    _fr_run("start", Packet_Src_IP_Address=LOOPBACK, User_Name="lab")
    _fr_run("start", Packet_Src_IP_Address=ACCEL_GW, User_Name="rtr-x")
    assert _acct_tenants("lab") == [1] and _acct_tenants("rtr-x") == [1]
    _add_subscriber(1, "lab2", "pw")
    assert _auth(client, user="lab2", password="pw",
                 src=LOOPBACK)["control:Auth-Type"] == "Accept"
    assert _quarantine("acct") == [] and _quarantine("auth") == []


# ═══════════ 6. Python ↔ FreeRADIUS parity, registry included ═══════════

def test_python_resolver_agrees_with_freeradius_with_local_registry(two_networks):
    from app.radius.db.connection import db
    from app.radius.services.nas_tenant import resolve_source_tenant
    _register(ACCEL_GW, "nas", tenant=2)
    _register("10.50.0.9", "mgmt")
    _register(LOOPBACK, "probe", service="healthcheck")
    _register("10.50.0.10", "nas", tenant=1)
    db().execute("DELETE FROM tenants WHERE id=1")    # registered tenant gone
    _add_nas(2, "lo", address="127.0.0.2")
    m = re.search(r"\n(\s*)(COALESCE\(\(SELECT tenant_id FROM radius_source_tenant)",
                  _section("start"))
    start = m.start(2)
    depth, i = 0, start
    while True:                                       # balanced COALESCE(...)
        depth += {"(": 1, ")": -1}.get(_section("start")[i], 0)
        i += 1
        if depth == 0 and i > start + 9:
            break
    expr = _section("start")[start:i]
    for ip in (ACCEL_GW, "10.50.0.9", LOOPBACK, "10.50.0.10", "127.0.0.2",
               T1_TUNNEL_IP, T2_TUNNEL_IP, UNKNOWN_IP, ""):
        fr = db().execute("SELECT " + _render(
            expr, {"Packet-Src-IP-Address": ip})).fetchone()[0]
        assert resolve_source_tenant(ip).tenant_id == fr, ip


# ═══════════ 7. operator CLI ═══════════

def test_registry_cli_validates_and_lists(two_networks, capsys):
    tool = _tool("radius_local_nas")
    db = _db_path()
    assert tool.main(["--db", db, "set", "not-an-ip", "--purpose", "probe"]) == 2
    assert tool.main(["--db", db, "set", ACCEL_GW, "--purpose", "nas"]) == 2   # no tenant
    assert tool.main(["--db", db, "set", ACCEL_GW, "--purpose", "nas",
                      "--tenant", "99"]) == 2                                   # no such tenant
    assert tool.main(["--db", db, "set", ACCEL_GW, "--purpose", "mgmt",
                      "--tenant", "1"]) == 2                                    # mgmt has no tenant
    assert tool.main(["--db", db, "set", "10.50.0.1/32", "--purpose", "nas",
                      "--tenant", "2"]) == 0
    capsys.readouterr()
    assert tool.main(["--db", db, "list"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["ip"], r["purpose"], r["tenant_id"]) for r in rows] == [(ACCEL_GW, "nas", 2)]
    assert tool.main(["--db", db, "remove", ACCEL_GW]) == 0
    assert _rows("SELECT * FROM radius_local_nas") == []


def test_registry_is_not_tenant_editable_from_the_app():
    """Only the operator CLI / SQL writes the registry — no route, no repo."""
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"(INSERT|UPDATE|DELETE)[^\"']*radius_local_nas", text), path


# ═══════════ 8. backfill + detection agree with the rule ═══════════

def test_backfill_never_moves_loopback_rows_to_a_tenant_claiming_loopback(two_networks):
    from app.radius.db.connection import db
    _add_nas(2, "hijack-lo", address=LOOPBACK)
    db().execute("INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                 " acctstarttime) VALUES(1,'lo1','lo1',?, datetime('now'))", (LOOPBACK,))
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    out = _tool("sec_b14_backfill").plan(conn)
    assert out["moves"] == []


def test_backfill_uses_the_local_registry_as_owner(two_networks):
    from app.radius.db.connection import db
    db().execute("INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                 " acctstarttime) VALUES(1,'acc1','acc1',?, datetime('now','+1 minute'))",
                 (ACCEL_GW,))
    _register(ACCEL_GW, "nas", tenant=2)
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    out = _tool("sec_b14_backfill").plan(conn)
    assert [m[2] for m in out["moves"]] == [2]


def test_detection_d6_reports_local_sources_separately(two_networks):
    from app.radius.db.connection import db
    for ip, user, start in ((LOOPBACK, "u1", "datetime('now')"),
                            (ACCEL_GW, "rtr-gr3", "datetime('now')"),
                            ("10.99.99.1", "demo", "strftime('%Y-%m-%dT%H:%M:%SZ','now')"),
                            (UNKNOWN_IP, "u2", "datetime('now')")):
        db().execute("INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                     f" nasporttype, acctstarttime) VALUES(1,?,?,?,?, {start})",
                     (user, user, ip, "Virtual" if user.startswith("rtr-") else ""))
    sql = (ROOT / "tools" / "sec_b14_detect.sql").read_text(encoding="utf-8")
    block = sql[sql.index("-- D6"):]
    stmts = [s for s in "\n".join(l for l in block.splitlines()
                                  if not l.lstrip().startswith("--")).split(";")
             if s.strip()]
    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    found = {}
    for s in stmts:
        cur = conn.execute(s)
        cols = [d[0] for d in cur.description]
        if "source_class" in cols:
            found = {r["nasipaddress"]: r["source_class"] for r in cur.fetchall()}
    assert found == {
        LOOPBACK: "local_loopback",
        ACCEL_GW: "local_accel_mgmt_tunnel",
        "10.99.99.1": "not_via_freeradius",
        UNKNOWN_IP: "UNKNOWN",
    }
