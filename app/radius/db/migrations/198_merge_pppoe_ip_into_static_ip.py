"""198 — «IP ثابت» و«IP PPPoE» صارا خانةً واحدة (قرار المالك، متابعة 2026-10-06).

The subscriber had two fixed-address inputs that both went out as the same
``Framed-IP-Address``: ``static_ip`` («IP ثابت») and ``pppoe_ip`` («عنوان IP
للبرودباند»). They are merged into ONE field, ``static_ip`` (IPv4 only, unique
across the tenant). This data fix copies every PPPoE address into the static
address of its subscriber when that is still empty.

Never a silent drop — every row with a PPPoE address gets an outcome, written
to ``data_fix_log`` (and summed in the boot log):

  copied       static_ip was empty → it now holds the PPPoE address
  same         static_ip already equals it (nothing to do)
  conflict     static_ip holds a DIFFERENT address → left as is
  ipv6         the PPPoE value is IPv6 (Framed-IP-Address is IPv4-only)
  invalid      the PPPoE value is not an IP address
  duplicate    another live subscriber of the tenant already owns the address
               as its static IP (or claimed it earlier in this run)
  deleted      the subscriber is in the recycle bin → left as is

Nothing is deleted: ``pppoe_ip`` keeps its value (no form edits it any more).
Idempotent: a second run finds the copies as ``same`` and changes nothing.
"""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime

NAME = "198_merge_pppoe_ip_into_static_ip"


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_log_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_fix_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            migration   TEXT NOT NULL,
            tenant_id   INTEGER NOT NULL DEFAULT 1,
            subject     TEXT NOT NULL DEFAULT '',
            outcome     TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_data_fix_log_migration "
                 "ON data_fix_log(migration, outcome)")


def _classify_ip(value: str) -> str:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return "invalid"
    return "ok" if isinstance(addr, ipaddress.IPv4Address) else "ipv6"


def up(conn) -> dict:
    if not _table_exists(conn, "subscribers"):
        return {}
    cols = _columns(conn, "subscribers")
    if not {"static_ip", "pppoe_ip"} <= cols:
        return {}
    ensure_log_table(conn)
    has_deleted = "deleted_at" in cols
    deleted_expr = "deleted_at" if has_deleted else "NULL"
    now = datetime.utcnow().isoformat() + "Z"

    # Addresses already owned as «IP ثابت» by a live subscriber, per tenant.
    owned: dict[tuple[int, str], int] = {}
    for r in conn.execute(
            f"SELECT id, COALESCE(tenant_id, 1) AS tid, trim(static_ip) AS ip "
            f"  FROM subscribers WHERE trim(COALESCE(static_ip, '')) != '' "
            f"   AND {deleted_expr} IS NULL").fetchall():
        owned.setdefault((int(r[1]), str(r[2])), int(r[0]))

    counts: dict[str, int] = {}
    rows = conn.execute(
        f"SELECT id, COALESCE(tenant_id, 1) AS tid, username, "
        f"       trim(COALESCE(static_ip, '')) AS st, trim(pppoe_ip) AS pp, "
        f"       {deleted_expr} AS del "
        f"  FROM subscribers WHERE trim(COALESCE(pppoe_ip, '')) != '' "
        f" ORDER BY id").fetchall()
    for r in rows:
        sid, tid, username = int(r[0]), int(r[1]), str(r[2] or "")
        static, pppoe, deleted = str(r[3] or ""), str(r[4] or ""), r[5]
        detail = {"pppoe_ip": pppoe, "static_ip": static}
        if static == pppoe:
            outcome = "same"
        elif deleted is not None:
            outcome = "deleted"
        elif static:
            outcome = "conflict"
        else:
            kind = _classify_ip(pppoe)
            if kind != "ok":
                outcome = kind
            else:
                holder = owned.get((tid, pppoe))
                if holder is not None and holder != sid:
                    outcome = "duplicate"
                    detail["owned_by_subscriber_id"] = holder
                else:
                    conn.execute("UPDATE subscribers SET static_ip = ? WHERE id = ?",
                                 (pppoe, sid))
                    owned[(tid, pppoe)] = sid
                    outcome = "copied"
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome != "same":
            conn.execute(
                "INSERT INTO data_fix_log(migration, tenant_id, subject, outcome, "
                "detail, created_at) VALUES(?,?,?,?,?,?)",
                (NAME, tid, username, outcome, json.dumps(detail, ensure_ascii=False),
                 now))
    return counts
