"""200 — عتبات تنبيه الراوتر المساوية للعامّة تُعاد «يرث» (قرار المالك، متابعة 2026-10-06).

``router_alert_settings`` holds one row per (tenant, router); a NULL column
means «inherit the tenant-global value» (smart_alerts.effective_for_router).
Before parity-c the per-router form saved EVERY threshold, so most rows hold a
frozen copy of the global value of that day: changing the global later did not
reach those routers. This one-off fix sets a per-router threshold back to NULL
wherever it EQUALS the tenant's current global value; a value that differs is a
deliberate override and stays.

Global values — read exactly like ``smart_alerts.global_settings``: the
``tenant_settings`` table, keys ``network.alerts.*`` (an absent or unparsable
key falls back to the code default):

  router column        tenant_settings key                     default
  offline_after_min    network.alerts.offline_after_min        6
  normal_speed_mbps    network.alerts.default_speed_mbps       100
  normal_usage_gb      network.alerts.default_usage_gb         200
  usage_window         network.alerts.usage_window             'day'

``enabled`` (mute) is a per-router choice, never a copy of a global: untouched.
Idempotent (a NULL is never compared again) and a no-op where the table does
not exist. Every cleared value is written to ``data_fix_log``.
"""
from __future__ import annotations

import json
from datetime import datetime

NAME = "200_router_alert_thresholds_inherit_global"

# (router column, tenant_settings key, code default, kind)
_METRICS = (
    ("offline_after_min", "network.alerts.offline_after_min", 6, "int"),
    ("normal_speed_mbps", "network.alerts.default_speed_mbps", 100, "int"),
    ("normal_usage_gb", "network.alerts.default_usage_gb", 200, "int"),
    ("usage_window", "network.alerts.usage_window", "day", "text"),
)


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def global_values(conn, tenant_id: int) -> dict:
    """The tenant's current global thresholds (as smart_alerts reads them)."""
    stored: dict[str, str] = {}
    if _table_exists(conn, "tenant_settings"):
        keys = [m[1] for m in _METRICS]
        rows = conn.execute(
            f"SELECT key, value FROM tenant_settings WHERE tenant_id = ? "
            f"AND key IN ({','.join('?' * len(keys))})", (int(tenant_id), *keys)).fetchall()
        stored = {str(r[0]): r[1] for r in rows}
    out = {}
    for col, key, default, kind in _METRICS:
        raw = stored.get(key)
        if kind == "int":
            out[col] = _int(raw, default) if raw is not None else default
        else:
            out[col] = (str(raw).strip() if raw is not None else "") or default
    return out


def _same(stored, glob, kind: str) -> bool:
    if stored is None or stored == "":
        return False
    if kind == "int":
        try:
            return int(str(stored).strip()) == int(glob)
        except (TypeError, ValueError):
            return False
    return str(stored).strip().lower() == str(glob).strip().lower()


def up(conn) -> dict:
    if not _table_exists(conn, "router_alert_settings"):
        return {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(router_alert_settings)")}
    metrics = [m for m in _METRICS if m[0] in cols]
    if not metrics:
        return {}
    _ensure_log_table(conn)
    now = datetime.utcnow().isoformat() + "Z"
    counts = {"rows": 0, "cleared": 0, "kept_override": 0}
    globs: dict[int, dict] = {}
    rows = conn.execute(
        f"SELECT tenant_id, router_id, {', '.join(m[0] for m in metrics)} "
        f"  FROM router_alert_settings ORDER BY tenant_id, router_id").fetchall()
    for r in rows:
        tid, rid = int(r[0] or 1), int(r[1])
        glob = globs.setdefault(tid, global_values(conn, tid))
        counts["rows"] += 1
        cleared = {}
        for i, (col, _key, _default, kind) in enumerate(metrics, start=2):
            val = r[i]
            if val is None or val == "":
                continue
            if _same(val, glob[col], kind):
                cleared[col] = val
            else:
                counts["kept_override"] += 1
        if not cleared:
            continue
        sets = ", ".join(f"{c} = NULL" for c in cleared)
        conn.execute(
            f"UPDATE router_alert_settings SET {sets} WHERE tenant_id = ? AND router_id = ?",
            (tid, rid))
        counts["cleared"] += len(cleared)
        conn.execute(
            "INSERT INTO data_fix_log(migration, tenant_id, subject, outcome, "
            "detail, created_at) VALUES(?,?,?,?,?,?)",
            (NAME, tid, f"router:{rid}", "inherit_global",
             json.dumps({"cleared": cleared, "global": {c: glob[c] for c in cleared}},
                        ensure_ascii=False), now))
    return counts


def _ensure_log_table(conn) -> None:
    """``data_fix_log`` — same DDL as migration 198 (this file stays runnable
    on its own)."""
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
