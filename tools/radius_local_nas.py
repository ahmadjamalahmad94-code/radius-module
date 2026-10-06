#!/usr/bin/env python3
"""B-14 — operator registry of SERVER-LOCAL RADIUS sources (migration 197).

On a server holding several networks, a RADIUS packet is attributed to the
tenant whose live router claims its source address. The panel host's own
NAS (accel-ppp sources RADIUS from its gateway address, e.g. 10.50.0.1) and
anything talking from 127.0.0.1 belong to no router row, so they are rejected
and quarantined — unless the operator registers them here, explicitly:

  nas    a local NAS that serves ONE network          --tenant N required
  mgmt   carries only router management tunnels (rtr-*): no tenant; its
         accounting goes to the operator quarantine as local_mgmt
  probe  health probe / smoke test: nothing is recorded, accounting is ACKed,
         logins get a Reject

Operator only — there is deliberately no panel route that writes this table.

  docker exec hoberadius python /app/tools/radius_local_nas.py --db /app/instance/hoberadius.db list
  docker exec hoberadius python /app/tools/radius_local_nas.py --db /app/instance/hoberadius.db set 10.50.0.1 \\
         --purpose nas --tenant 1 --service accel-ppp
  docker exec hoberadius python /app/tools/radius_local_nas.py --db /app/instance/hoberadius.db remove 10.50.0.1

FreeRADIUS reads the table on every packet: no restart is needed.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sqlite3
import sys
from datetime import datetime, timezone

PURPOSES = ("nas", "mgmt", "probe")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_ip(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.endswith("/32"):
        raw = raw[:-3]
    return str(ipaddress.ip_address(raw))     # ValueError on anything else


def _connect(path: str, write: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'rw' if write else 'ro'}"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def cmd_list(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT ip, purpose, tenant_id, service, note, created_at, updated_at "
        "  FROM radius_local_nas ORDER BY ip").fetchall()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1))
    return 0


def cmd_set(conn: sqlite3.Connection, a: argparse.Namespace) -> int:
    try:
        ip = _norm_ip(a.ip)
    except ValueError:
        return _fail(f"not an IP address: {a.ip!r}")
    if a.purpose == "nas":
        if a.tenant is None:
            return _fail("purpose 'nas' needs --tenant (the network it serves)")
        if conn.execute("SELECT 1 FROM tenants WHERE id = ?", (a.tenant,)).fetchone() is None:
            return _fail(f"tenant {a.tenant} does not exist")
    elif a.tenant is not None:
        return _fail(f"purpose '{a.purpose}' belongs to no tenant — drop --tenant")
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO radius_local_nas
                (ip, purpose, tenant_id, service, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                purpose = excluded.purpose, tenant_id = excluded.tenant_id,
                service = excluded.service, note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (ip, a.purpose, a.tenant, a.service or "", a.note or "", now, now))
    claims = conn.execute(
        "SELECT COUNT(*) FROM nas_devices WHERE deleted_at IS NULL AND ? IN "
        "(TRIM(address), TRIM(vpn_peer_address), TRIM(management_remote_address))",
        (ip,)).fetchone()[0]
    if claims:
        print(f"note: {claims} router row(s) also list {ip}; the registry wins, "
              "those rows no longer claim it", file=sys.stderr)
    print(json.dumps({"ok": True, "ip": ip, "purpose": a.purpose, "tenant_id": a.tenant}))
    return 0


def cmd_remove(conn: sqlite3.Connection, a: argparse.Namespace) -> int:
    try:
        ip = _norm_ip(a.ip)
    except ValueError:
        return _fail(f"not an IP address: {a.ip!r}")
    with conn:
        n = conn.execute("DELETE FROM radius_local_nas WHERE ip = ?", (ip,)).rowcount
    print(json.dumps({"ok": True, "removed": n}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("set")
    s.add_argument("ip")
    s.add_argument("--purpose", required=True, choices=PURPOSES)
    s.add_argument("--tenant", type=int)
    s.add_argument("--service", default="")
    s.add_argument("--note", default="")
    r = sub.add_parser("remove")
    r.add_argument("ip")
    a = ap.parse_args(argv)
    conn = _connect(a.db, write=a.cmd != "list")
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'radius_local_nas'"
                        ).fetchone() is None:
            return _fail("table radius_local_nas missing — migration 197 not applied")
        if a.cmd == "list":
            return cmd_list(conn)
        if a.cmd == "set":
            return cmd_set(conn, a)
        return cmd_remove(conn, a)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
