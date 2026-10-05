#!/usr/bin/env python3
"""B-14 backfill — move radacct rows FreeRADIUS stored under tenant 1 to the
tenant that owns the router (security fix, 2026-10-05).

DRY-RUN BY DEFAULT. It never runs by itself: it is not a migration, not
imported by the app, not scheduled. A human runs it, per server, after the
fix is deployed and after taking a backup:

    # 1. what would change (opens the DB read-only, writes nothing)
    python tools/sec_b14_backfill.py --db /data/hoberadius.db
    # 2. apply (one transaction) — writes an undo file first
    python tools/sec_b14_backfill.py --db /data/hoberadius.db --apply \
           --undo-file /data/sec_b14_undo.json
    # 3. rollback of step 2
    python tools/sec_b14_backfill.py --db /data/hoberadius.db \
           --rollback /data/sec_b14_undo.json --apply

Rules (conservative — a row is moved only when there is no doubt):
  • server has ONE tenant          → nothing to do, exit 0;
  • row stored under tenant 1 (the literal FreeRADIUS wrote);
  • its nasipaddress (the packet source) is claimed by exactly ONE other
    tenant's live, enabled router (same rule as migration 196);
  • the row started at/after that router was created (an address reused
    after tenant 1 gave it up is NOT tenant 2's history);
  • the target tenant has no row for the same (session id, NAS, user) —
    such twins (the session re-materialised after the fix) are reported,
    never merged.
Rows whose address is unknown or claimed by two tenants stay where they are
and are reported.

radpostauth (login attempts) is NOT moved: it never stored the source
address, so its owner cannot be proven. `--redact-suspect-attempt-passwords`
(opt-in, with --apply) blanks the attempted password (`pass` → '***') of
tenant-1 rows whose `nas` is not one of tenant 1's routers — removing the
sensitive part without deleting the log line. Retention (30 days) removes
those rows anyway.

Prints counts only — never a password, never a username list.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

SOURCE_TENANT = 1

_OWNER_CTE = """
WITH addr AS (
    SELECT CASE WHEN TRIM(management_remote_address) LIKE '%/32'
                THEN SUBSTR(TRIM(management_remote_address), 1, LENGTH(TRIM(management_remote_address)) - 3)
                ELSE TRIM(management_remote_address) END AS ip, tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL
       AND TRIM(COALESCE(management_remote_address, '')) <> ''
    UNION ALL
    SELECT CASE WHEN TRIM(vpn_peer_address) LIKE '%/32'
                THEN SUBSTR(TRIM(vpn_peer_address), 1, LENGTH(TRIM(vpn_peer_address)) - 3)
                ELSE TRIM(vpn_peer_address) END, tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL
       AND TRIM(COALESCE(vpn_peer_address, '')) <> ''
    UNION ALL
    SELECT TRIM(address), tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL
       AND TRIM(COALESCE(address, '')) <> ''
), owner AS (
    SELECT ip,
           CASE WHEN COUNT(DISTINCT tenant_id) = 1 THEN MIN(tenant_id) END AS tenant_id,
           COUNT(DISTINCT tenant_id) AS n,
           -- normalised 'YYYY-MM-DD HH:MM:SS' (radacct uses a space, the panel 'T…Z')
           MIN(SUBSTR(REPLACE(created_at, 'T', ' '), 1, 19)) AS since
      FROM addr GROUP BY ip
)
"""

_CLASSIFY = _OWNER_CTE + """
SELECT r.radacctid AS id, r.tenant_id AS stored, o.tenant_id AS owner, o.n AS n,
       CASE
         WHEN o.ip IS NULL THEN 'unknown_nas'
         WHEN o.tenant_id IS NULL THEN 'ambiguous_nas'
         WHEN o.tenant_id = r.tenant_id THEN 'correct'
         WHEN SUBSTR(REPLACE(COALESCE(r.acctstarttime, ''), 'T', ' '), 1, 19) < o.since
              THEN 'before_router_existed'
         WHEN EXISTS (SELECT 1 FROM radacct t
                       WHERE t.tenant_id = o.tenant_id
                         AND t.acctsessionid = r.acctsessionid
                         AND t.nasipaddress = r.nasipaddress
                         AND t.username = r.username) THEN 'twin_in_target'
         ELSE 'move'
       END AS verdict,
       r.acctstoptime IS NULL AS is_open
  FROM radacct r LEFT JOIN owner o ON o.ip = r.nasipaddress
 WHERE r.tenant_id = ?
"""

_SUSPECT_POSTAUTH = """
SELECT p.id FROM radpostauth p
 WHERE p.tenant_id = ?
   AND p.pass NOT IN ('', '***')
   AND NOT EXISTS (SELECT 1 FROM nas_devices n
                    WHERE n.tenant_id = p.tenant_id AND n.deleted_at IS NULL
                      AND p.nas IN (n.address, n.vpn_peer_address, n.management_remote_address))
"""


def _connect(path: str, write: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'rw' if write else 'ro'}"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    if not write:
        conn.execute("PRAGMA query_only = ON")
    return conn


def plan(conn: sqlite3.Connection) -> dict:
    tenants = conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    out: dict = {"tenants": tenants, "verdicts": {}, "open_by_verdict": {},
                 "moves": [], "suspect_attempt_rows": 0}
    if tenants <= 1:
        return out
    for row in conn.execute(_CLASSIFY, (SOURCE_TENANT,)):
        v = row["verdict"]
        out["verdicts"][v] = out["verdicts"].get(v, 0) + 1
        if row["is_open"]:
            out["open_by_verdict"][v] = out["open_by_verdict"].get(v, 0) + 1
        if v == "move":
            out["moves"].append((int(row["id"]), int(row["stored"]), int(row["owner"])))
    out["suspect_attempt_rows"] = len(
        conn.execute(_SUSPECT_POSTAUTH, (SOURCE_TENANT,)).fetchall())
    return out


def _summary(p: dict) -> dict:
    by_target: dict = {}
    for _id, _old, new in p["moves"]:
        by_target[new] = by_target.get(new, 0) + 1
    return {"tenants": p["tenants"], "radacct_tenant1_by_verdict": p["verdicts"],
            "open_rows_by_verdict": p["open_by_verdict"],
            "rows_to_move_by_target_tenant": by_target,
            "radpostauth_tenant1_rows_with_foreign_nas_and_password": p["suspect_attempt_rows"]}


def apply(conn: sqlite3.Connection, p: dict, undo_file: str,
          redact_attempts: bool) -> dict:
    redacted = []
    with conn:  # one transaction
        conn.execute("BEGIN IMMEDIATE")
        if redact_attempts:
            redacted = [int(r[0]) for r in conn.execute(_SUSPECT_POSTAUTH, (SOURCE_TENANT,))]
        undo = {"created_at": datetime.now(timezone.utc).isoformat(),
                "radacct": [[i, old, new] for i, old, new in p["moves"]],
                # the passwords themselves are NOT kept: redaction is one-way.
                "radpostauth_redacted_ids": redacted}
        with open(undo_file, "x", encoding="utf-8") as fh:   # never overwrite
            json.dump(undo, fh)
        conn.executemany(
            "UPDATE radacct SET tenant_id = ? WHERE radacctid = ? AND tenant_id = ?",
            [(new, i, old) for i, old, new in p["moves"]])
        if redacted:
            conn.executemany("UPDATE radpostauth SET pass = '***' WHERE id = ?",
                             [(i,) for i in redacted])
    return {"moved": len(p["moves"]), "redacted_attempt_passwords": len(redacted),
            "undo_file": undo_file}


def rollback(conn: sqlite3.Connection, undo_file: str, do_it: bool) -> dict:
    with open(undo_file, encoding="utf-8") as fh:
        undo = json.load(fh)
    rows = undo.get("radacct") or []
    if not do_it:
        return {"would_restore": len(rows), "dry_run": True}
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "UPDATE radacct SET tenant_id = ? WHERE radacctid = ? AND tenant_id = ?",
            [(old, i, new) for i, old, new in rows])
    return {"restored": len(rows),
            "note": "redacted attempt passwords cannot be restored (by design)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="path to hoberadius.db")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run, DB opened read-only)")
    ap.add_argument("--undo-file", help="required with --apply: JSON undo log (must not exist)")
    ap.add_argument("--redact-suspect-attempt-passwords", action="store_true",
                    help="with --apply: blank `pass` of tenant-1 login attempts "
                         "from routers that are not tenant 1's (irreversible)")
    ap.add_argument("--rollback", metavar="UNDO_FILE",
                    help="restore tenant_id from an undo file (dry-run unless --apply)")
    a = ap.parse_args(argv)

    if a.rollback:
        conn = _connect(a.db, write=a.apply)
        print(json.dumps(rollback(conn, a.rollback, a.apply), indent=2))
        return 0

    conn = _connect(a.db, write=a.apply)
    p = plan(conn)
    print(json.dumps({"dry_run": not a.apply, **_summary(p)}, indent=2))
    if p["tenants"] <= 1:
        print("single-tenant server: nothing to backfill.")
        return 0
    if not a.apply:
        return 0
    if not a.undo_file:
        print("--apply needs --undo-file", file=sys.stderr)
        return 2
    print(json.dumps(apply(conn, p, a.undo_file,
                           a.redact_suspect_attempt_passwords), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
