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

import logging
import os
import threading
import time

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


def _fetch_active_sessions(cfg: dict) -> set[tuple[str, str]] | None:
    """Returns a set of (username_lower, mac_upper) for every active
    session on this router across hotspot AND ppp. Returns None on
    failure — caller treats None as 'router unreachable, skip close
    for its NAS this cycle'.
    """
    from app.radius.integration.mikrotik.errors import MikrotikError
    from app.radius.integration.mikrotik.pool import acquire as acquire_mt

    keys: set[tuple[str, str]] = set()
    try:
        with acquire_mt(cfg) as client:
            # Hotspot active sessions
            for r in client.print_("/ip/hotspot/active/print"):
                user = (r.get("user") or "").strip().lower()
                mac  = _norm_mac(r.get("mac-address") or "")
                if user:
                    keys.add((user, mac))
            # PPPoE / PPTP / L2TP / SSTP active sessions
            try:
                for r in client.print_("/ppp/active/print"):
                    user = (r.get("name") or "").strip().lower()
                    mac  = _norm_mac(r.get("caller-id") or "")
                    if user:
                        keys.add((user, mac))
            except MikrotikError:
                # /ppp/active may not exist (hotspot-only router); fine.
                pass
    except MikrotikError as e:
        _LOG.warning("mt_reconciler: router=%s unreachable: %s",
                     cfg.get("host"), e)
        return None
    except Exception:  # noqa: BLE001
        _LOG.exception("mt_reconciler: unexpected error router=%s",
                       cfg.get("host"))
        return None
    return keys


def _reconcile_nas(tenant_id: int, nas_addr: str,
                    active_keys: set[tuple[str, str]]) -> int:
    """Close radacct rows on this NAS whose (user, mac) isn't in the
    MT live set. Returns the number of rows closed."""
    from app.radius.db.connection import db, transaction

    open_rows = db().execute("""
        SELECT radacctid, acctsessionid, username, callingstationid,
               acctupdatetime, acctstarttime
          FROM radacct
        WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
    """, (tenant_id, nas_addr)).fetchall()
    if not open_rows:
        return 0

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
        # Close it.
        with transaction() as conn:
            conn.execute(
                """
                UPDATE radacct
                   SET acctstoptime = COALESCE(acctupdatetime, acctstarttime,
                                                datetime('now')),
                       acctterminatecause = 'NAS-Lost-Session'
                 WHERE radacctid = ?
                   AND acctstoptime IS NULL
                """,
                (row["radacctid"],),
            )
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


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    return [r["id"] for r in db().execute(
        "SELECT id FROM tenants WHERE status = 'active'"
    ).fetchall()]


def reconcile_once() -> dict:
    """One full pass. Returns {tenants, routers_ok, routers_skipped,
    closed_total} for the heartbeat + manual debugging."""
    stats = {"tenants": 0, "routers_ok": 0, "routers_skipped": 0,
             "closed_total": 0}
    for tenant_id in _all_tenants():
        stats["tenants"] += 1
        routers = _collect_router_configs(tenant_id)
        for cfg in routers:
            active = _fetch_active_sessions(cfg)
            if active is None:
                stats["routers_skipped"] += 1
                continue
            stats["routers_ok"] += 1
            closed = _reconcile_nas(tenant_id, cfg["host"], active)
            stats["closed_total"] += closed
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
