"""mt_reconciler — closes orphan radacct sessions by comparing with MT.

The Problem
-----------
RADIUS keeps a row in `radacct` with `acctstoptime IS NULL` for every
session it believes is online. The row only gets closed when:

  (a) the NAS sends an Acct-Stop packet (the happy path), OR
  (b) `stale_session_reaper` notices no interim-update for 15 min, OR
  (c) the operator manually calls Disconnect.

In real deployments (a) fails surprisingly often: the router reboots
without sending Accounting-On, UDP packets are dropped, the user's
DHCP lease expires silently, MT crashes mid-session, etc. Until 15 min
have passed, the Card Checker, the online-users dashboard, and the
operator all see ghost sessions that aren't really on the wire.

What this worker does
---------------------
Every N seconds (default 30s):

  1. For every active tenant, for every enabled MikroTik router:
       • query /ip/hotspot/active/print
       • query /ppp/active/print
     A router that fails (timeout, auth, network blip) is **skipped
     entirely for this cycle** — we never false-close based on partial
     visibility.

  2. From the responses, build a set of `(username_lower, mac_upper)`
     tuples representing every session the router actually knows about
     right now.

  3. For every open `radacct` row on this NAS (acctstoptime IS NULL),
     compute the same `(username, mac)` key. If the key is NOT in the
     MT set, close the row:
         SET acctstoptime = COALESCE(acctupdatetime, acctstarttime),
             acctterminatecause = 'NAS-Lost-Session'
     and dispatch a `session.stopped` webhook.

The matching uses (username, mac) instead of acctsessionid because MT
exposes `.id` (its internal session-list ID, e.g. `*1A`) in the API
but stores the RADIUS-side acctsessionid only in the live session's
RADIUS attributes — not reliably retrievable through the print verb.
The (username, mac) pair is unique enough for any concurrent session
on a single NAS (a card with 3 phones still has 3 different MACs).

Safety
------
• A router that times out → its rows are left alone (the stale-time
  reaper will catch them after 15 min as a fallback).
• Closing uses the LAST KNOWN acctupdatetime (or acctstarttime) for
  the stop timestamp, so session duration stays accurate.
• Writes are upserts on EXISTING rows only — no conflict with the
  FreeRADIUS rlm_sql writer that produces new radacct rows.

Env vars
--------
HOBERADIUS_RECONCILER_INTERVAL_SEC (default 30, min 10)
HOBERADIUS_RECONCILER_ENABLED      (default 1 → on)
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "mt_reconciler"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 30


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_RECONCILER_INTERVAL_SEC", "")
    try:
        v = int(raw)
        return max(v, 10)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_RECONCILER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _norm_mac(s: str) -> str:
    """Normalize MAC to AA:BB:CC:DD:EE:FF upper-case.
    Returns '' for anything that doesn't have 12 hex chars."""
    if not s:
        return ""
    cleaned = "".join(c for c in s if c.isalnum())
    if len(cleaned) != 12:
        return s.strip().upper()  # last-ditch: trust the input casing
    return ":".join(cleaned[i:i+2] for i in range(0, 12, 2)).upper()


def _safe_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _parse_ros_uptime(s: str) -> int:
    """RouterOS uptime → seconds. Handles the API unit form ('1w2d3h4m5s',
    '22m54s', '3h') AND the colon form ('00:22:54', '1:02:03'). 0 on junk."""
    import re

    s = (s or "").strip().lower()
    if not s:
        return 0
    units = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    matches = re.findall(r"(\d+)\s*([wdhms])", s)
    if matches:
        return sum(int(n) * units[u] for n, u in matches)
    if ":" in s:  # H:M:S or M:S (left-to-right, smallest on the right)
        try:
            parts = [int(p) for p in s.split(":")]
        except ValueError:
            return 0
        total = 0
        for p in parts:
            total = total * 60 + p
        return total
    return _safe_int(s)


def _map_active_rows(hotspot_rows, ppp_rows) -> list[dict]:
    """Pure mapper: RouterOS active-session attr dicts → normalized session
    dicts. Hotspot uses user/mac-address; PPP uses name/caller-id. PPP
    caller-id is a real MAC only for PPPoE, so it is best-effort."""
    out: list[dict] = []
    for r in (hotspot_rows or []):
        user = (r.get("user") or "").strip()
        if not user:
            continue
        out.append({
            "username":   user,
            "mac":        _norm_mac(r.get("mac-address") or ""),
            "framed_ip":  (r.get("address") or "").strip(),
            "uptime_sec": _parse_ros_uptime(r.get("uptime") or ""),
            "bytes_in":   _safe_int(r.get("bytes-in")),
            "bytes_out":  _safe_int(r.get("bytes-out")),
            "source":     "hotspot",
        })
    for r in (ppp_rows or []):
        user = (r.get("name") or "").strip()
        if not user:
            continue
        out.append({
            "username":   user,
            "mac":        _norm_mac(r.get("caller-id") or ""),
            "framed_ip":  (r.get("address") or "").strip(),
            "uptime_sec": _parse_ros_uptime(r.get("uptime") or ""),
            "bytes_in":   _safe_int(r.get("bytes-in")),
            "bytes_out":  _safe_int(r.get("bytes-out")),
            "source":     "ppp",
        })
    return out


def _collect_router_configs(tenant_id: int) -> list[dict]:
    """Same dual-source pattern as device_fingerprint_sync — pull MT
    creds from BOTH mikrotik_configs (preferred) and nas_devices (any
    NAS row with api_user set). De-duped by host."""
    from app.radius.db.repos import mikrotik_repo, nas_repo

    out: dict[str, dict] = {}

    try:
        for r in mikrotik_repo.list_configs(int(tenant_id)):
            if not r.get("enabled"):
                continue
            host = (r.get("host") or "").strip()
            if not host:
                continue
            out[host] = {
                "id":          r["id"],
                "host":        host,
                "port":        int(r.get("port") or 8728),
                "username":    r.get("username") or "admin",
                "password":    r.get("password") or "",
                "use_tls":     bool(r.get("use_tls")),
                "verify_tls":  bool(r.get("verify_tls")),
                "timeout_sec": int(r.get("timeout_sec") or 20),
            }
    except Exception:  # noqa: BLE001
        _LOG.exception("mt_reconciler: mikrotik_configs list failed tenant=%s",
                       tenant_id)

    try:
        for nas in nas_repo.list_nas(int(tenant_id), limit=1000):
            if not getattr(nas, "enabled", False):
                continue
            host = (getattr(nas, "address", "") or "").strip()
            api_user = getattr(nas, "api_user", "") or ""
            if not host or not api_user or host in out:
                continue
            out[host] = {
                "id":          nas.id,
                "host":        host,
                "port":        int(getattr(nas, "api_port", 8728) or 8728),
                "username":    api_user,
                "password":    getattr(nas, "api_password", "") or "",
                "use_tls":     bool(getattr(nas, "api_use_tls", False)),
                "verify_tls":  True,
                "timeout_sec": 20,
            }
    except Exception:  # noqa: BLE001
        _LOG.exception("mt_reconciler: nas_devices list failed tenant=%s",
                       tenant_id)
    return list(out.values())


def _fetch_active_rows(cfg: dict) -> list[dict] | None:
    """Returns normalized active-session rows (username, mac, framed_ip,
    uptime_sec, bytes_in/out, source) across hotspot AND ppp. Returns None
    on failure — caller treats None as 'router unreachable, skip this NAS'."""
    from app.radius.integration.mikrotik.errors import MikrotikError
    from app.radius.integration.mikrotik.pool import acquire as acquire_mt

    hotspot: list[dict] = []
    ppp: list[dict] = []
    try:
        with acquire_mt(cfg) as client:
            hotspot = list(client.print_("/ip/hotspot/active/print"))
            try:
                ppp = list(client.print_("/ppp/active/print"))
            except MikrotikError:
                ppp = []  # /ppp/active may not exist (hotspot-only router)
    except MikrotikError as e:
        _LOG.warning("mt_reconciler: router=%s unreachable: %s",
                     cfg.get("host"), e)
        return None
    except Exception:  # noqa: BLE001
        _LOG.exception("mt_reconciler: unexpected error router=%s",
                       cfg.get("host"))
        return None
    return _map_active_rows(hotspot, ppp)


def _keys_from_rows(rows: list[dict]) -> set[tuple[str, str]]:
    """(username_lower, mac_upper) set used by the close-orphans pass."""
    return {((r["username"] or "").strip().lower(), r.get("mac") or "")
            for r in rows if (r.get("username") or "").strip()}


def _fetch_active_sessions(cfg: dict) -> set[tuple[str, str]] | None:
    """Back-compat wrapper: the (username, mac) set (close-orphans view)."""
    rows = _fetch_active_rows(cfg)
    return None if rows is None else _keys_from_rows(rows)


def _reconcile_nas(tenant_id: int, nas_addr: str,
                    active_keys: set[tuple[str, str]]) -> int:
    """Close radacct rows on this NAS whose (user, mac) isn't in the
    MT live set. Returns the number of rows closed."""
    from app.radius.db.connection import db, transaction

    open_rows = db().execute("""
        SELECT radacctid, acctsessionid, username, callingstationid,
               acctupdatetime, acctstarttime, acctsessiontime
          FROM radacct
        WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
    """, (tenant_id, nas_addr)).fetchall()
    if not open_rows:
        return 0

    # المسار القانوني الموحّد للإغلاق (يَحسب acctsessiontime + idempotent).
    from app.radius.services.session_reconciler import (
        CAUSE_NAS_LOST, close_session_row,
    )

    closed = 0
    closed_session_ids: list[str] = []
    for row in open_rows:
        user_key = (row["username"] or "").strip().lower()
        mac_key  = _norm_mac(row["callingstationid"] or "")
        if not user_key:
            continue  # paranoia: never auto-close anonymous rows
        # Primary match: exact (user, mac). Fallback: user-only when
        # the radacct row has no MAC recorded (some PPP-only sessions).
        is_active = (
            (user_key, mac_key) in active_keys
            or (not mac_key and any(u == user_key for (u, _) in active_keys))
        )
        if is_active:
            continue
        # Close it via the canonical Accounting-Stop path so acctsessiontime
        # is computed and the terminate cause is consistent across reconcilers.
        try:
            with transaction() as conn:
                n = close_session_row(conn, row, cause=CAUSE_NAS_LOST)
        except Exception:  # noqa: BLE001 — a bad row must not abort the batch
            _LOG.exception("mt_reconciler: failed closing radacctid=%s",
                           row["radacctid"])
            continue
        if not n:
            continue
        closed += 1
        if row["acctsessionid"]:
            closed_session_ids.append(row["acctsessionid"])

    # Dispatch webhook for each closed session — fire-and-forget.
    for sid in closed_session_ids:
        try:
            from app.webhooks.dispatcher import dispatch_event
            dispatch_event(
                "session.stopped",
                {"session_id": sid, "terminate_cause": "NAS-Lost-Session"},
                tenant_id=tenant_id,
            )
        except Exception:  # noqa: BLE001
            pass

    if closed:
        _LOG.info(
            "mt_reconciler: closed %d orphan session(s) tenant=%d nas=%s",
            closed, tenant_id, nas_addr,
        )
    return closed


_MTSYNC_PREFIX = "mtsync:"


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _materialize_enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_SESSION_SYNC_MATERIALIZE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _materialize_nas(tenant_id: int, nas_addr: str, rows: list[dict]) -> dict:
    """Bring radacct UP to match the router: for each live router session
    with no open radacct row, INSERT a synthetic open row tagged
    `mtsync:` (so cookie/auto-login sessions — which never hit RADIUS —
    show in /online and are CoA-targetable). Refresh octets/uptime on our
    own synthetic rows; NEVER touch a real RADIUS row. Closing is handled
    by the existing orphan pass (a vanished session drops from the live
    set → gets closed)."""
    if not _materialize_enabled() or not rows:
        return {"inserted": 0, "updated": 0}
    from app.radius.db.connection import db, transaction

    open_rows = db().execute(
        "SELECT radacctid, username, callingstationid, acctuniqueid "
        "FROM radacct WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL",
        (tenant_id, nas_addr),
    ).fetchall()
    existing: dict[tuple[str, str], dict] = {}
    for r in open_rows:
        key = ((r["username"] or "").strip().lower(), _norm_mac(r["callingstationid"] or ""))
        existing[key] = r

    now = _utcnow()
    inserted = updated = 0
    for row in rows:
        user = (row.get("username") or "").strip()
        if not user:
            continue
        mac = row.get("mac") or ""
        key = (user.lower(), mac)
        match = existing.get(key)
        if match is not None:
            # Refresh ONLY our own synthetic rows; a real RADIUS row wins untouched.
            if str(match["acctuniqueid"] or "").startswith(_MTSYNC_PREFIX):
                db().execute(
                    "UPDATE radacct SET acctupdatetime = ?, acctinputoctets = ?, "
                    "acctoutputoctets = ?, acctsessiontime = ?, "
                    "framedipaddress = COALESCE(NULLIF(?, ''), framedipaddress) "
                    "WHERE radacctid = ?",
                    (now, int(row.get("bytes_in") or 0), int(row.get("bytes_out") or 0),
                     int(row.get("uptime_sec") or 0), row.get("framed_ip") or "",
                     match["radacctid"]),
                )
                updated += 1
            continue
        # No open row for this (user, mac) — materialize a synthetic session.
        uniq = f"{_MTSYNC_PREFIX}{nas_addr}:{user.lower()}:{mac}"
        sid = "mtsync-" + hashlib.md5(uniq.encode("utf-8")).hexdigest()[:16]
        start = (datetime.utcnow()
                 - timedelta(seconds=int(row.get("uptime_sec") or 0))).isoformat() + "Z"
        try:
            with transaction() as conn:
                conn.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                    "nasipaddress, acctstarttime, acctupdatetime, callingstationid, "
                    "framedipaddress, acctinputoctets, acctoutputoctets, acctsessiontime) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tenant_id, sid, uniq, user, nas_addr, start, now, mac,
                     row.get("framed_ip") or "", int(row.get("bytes_in") or 0),
                     int(row.get("bytes_out") or 0), int(row.get("uptime_sec") or 0)),
                )
            inserted += 1
        except Exception:  # noqa: BLE001 — a racing real Acct-Start can win; skip
            existing[key] = {"radacctid": None, "acctuniqueid": ""}
    if inserted or updated:
        _LOG.info("mt_reconciler: materialized %d new + refreshed %d session(s) "
                  "tenant=%d nas=%s", inserted, updated, tenant_id, nas_addr)
    return {"inserted": inserted, "updated": updated}


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    return [r["id"] for r in db().execute(
        "SELECT id FROM tenants WHERE status = 'active'"
    ).fetchall()]


def _reconcile_tenant(tenant_id: int) -> dict:
    """Live NAS cross-check for ONE tenant. Returns per-tenant counters.
    A router that fails to answer is SKIPPED (never false-close on partial
    visibility) — the interim-timeout reaper is the fallback for those."""
    out = {"routers_ok": 0, "routers_skipped": 0,
           "closed_total": 0, "materialized_total": 0}
    # سجلّ قابليّة الوصول الحيّة: العدّاد/الواجهة يعتمدانه ليُظهرا «المتصلون
    # الآن» من الراوترات القابلة للوصول فقط (فارغ عند الانقطاع). نُسجّل لكلّ
    # راوتر نجاحًا (مع عدد الجلسات الحيّة) أو فشلاً — ونُصالح كلّ استطلاع ناجح
    # (فالمصالحة تحدث تلقائيًّا فور عودة الراوتر).
    try:
        from app.radius.services import nas_liveness
    except Exception:  # noqa: BLE001
        nas_liveness = None  # type: ignore[assignment]
    for cfg in _collect_router_configs(int(tenant_id)):
        host = cfg["host"]
        rows = _fetch_active_rows(cfg)
        if rows is None:
            out["routers_skipped"] += 1
            if nas_liveness is not None:
                nas_liveness.record_unreachable(int(tenant_id), host)
            continue
        out["routers_ok"] += 1
        if nas_liveness is not None:
            # Feed «connected now» only real subscriber/card sessions — exclude
            # mac-cookie (`T-<MAC>`) and trial (`Default service` / `مؤقت`) rows
            # the router reports, so the chip matches the real-only list.
            # Defensive: fall back to raw row count on any resolver error.
            try:
                from app.radius.services import live_sessions
                active_count = live_sessions.count_real_sessions(
                    int(tenant_id), [r.get("username") for r in rows])
            except Exception:  # noqa: BLE001
                active_count = len(rows)
            nas_liveness.record_reachable(int(tenant_id), host,
                                          active_count=active_count)
        # Close ghosts (radacct rows no longer on the router)…
        out["closed_total"] += _reconcile_nas(
            int(tenant_id), host, _keys_from_rows(rows))
        # …and open missed/cookie sessions (on the router, absent from radacct).
        out["materialized_total"] += _materialize_nas(
            int(tenant_id), host, rows)["inserted"]
    return out


def reconcile_once(tenant_id: int | None = None) -> dict:
    """One full pass. Returns {tenants, routers_ok, routers_skipped,
    closed_total, materialized_total} for the heartbeat + manual debugging.

    ``tenant_id=None`` reconciles every active tenant (the background-worker
    behaviour). Passing a tenant id scopes the pass to that tenant only (used
    by the on-demand «مصالحة الجلسات الآن» button so one operator's click
    doesn't churn other tenants)."""
    stats = {"tenants": 0, "routers_ok": 0, "routers_skipped": 0,
             "closed_total": 0, "materialized_total": 0}
    tenants = [int(tenant_id)] if tenant_id is not None else _all_tenants()
    for tid in tenants:
        stats["tenants"] += 1
        t = _reconcile_tenant(tid)
        for k in ("routers_ok", "routers_skipped",
                  "closed_total", "materialized_total"):
            stats[k] += t[k]
    return stats


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("mt_reconciler started — interval=%ds", interval_sec)
    while True:
        stats = {"closed_total": 0}
        try:
            stats = reconcile_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("mt_reconciler tick failed")
        beat(_NAME, info={
            "interval_sec":     interval_sec,
            "last_closed":      stats.get("closed_total", 0),
            "last_routers_ok":  stats.get("routers_ok", 0),
            "last_routers_skipped": stats.get("routers_skipped", 0),
        })
        time.sleep(interval_sec)


def start_mt_reconciler() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("mt_reconciler disabled by HOBERADIUS_RECONCILER_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval},
            daemon=True, name="hr-mt-reconciler",
        )
        t.start()
        _started = True
