"""schedule_window — enforce connection-schedule / allowed-hours windows on
ALREADY-ACTIVE sessions (not only at authorize).

The gap this closes
-------------------
``policy_engine._check_schedule`` runs ONLY at RADIUS *authorize* (a new login):
it rejects a login made outside the allowed window, but nothing terminates a
session that was opened *inside* the window and is still live when the window
*closes*. A subscriber who connected at 03:30 with a 04:00 cutoff therefore
stayed online indefinitely past 04:00 — the cutoff only bit at the next re-auth,
which may be hours away (or never).

Two complementary mechanisms fix this (see the owner report):

  (a) **Session-Timeout at authorize** — ``seconds_until_window_end`` computes the
      seconds from *now* until the current allowed window closes, so
      ``policy_engine._build_accept_attrs`` can cap ``Session-Timeout`` and the
      NAS auto-disconnects exactly at the boundary (login 03:30, cutoff 04:00 →
      ``Session-Timeout=1800``). Robust primary mechanism.

  (b) **Periodic enforcer** — ``enforce_active_session_windows`` sweeps live
      radacct sessions and CoA-disconnects any whose *effective* schedule now
      falls OUTSIDE the allowed window, with reason «خارج وقت السماح»
      (``out_of_window``). This catches sessions that were live before the
      boundary and long-lived sessions the NAS never re-authed. Driven by
      :mod:`app.workers.schedule_window_worker` on a short cadence.

Effective schedule (source precedence)
--------------------------------------
The *effective* window is a single unified :mod:`access_schedule` dict. The
per-subscriber schedule OVERRIDES the offer/plan schedule entirely when set
(same precedence as ``policy_engine._check_schedule``):

  1. subscriber ``connection_schedule`` (unified JSON, if it has windows)
  2. subscriber ``working_days`` (legacy CSV → day-only windows)
  3. plan ``connection_schedule`` (unified JSON, if it has windows)
  4. plan offer/allowed hours + allowed days, synthesized into one window
     (offer hours «ساعات العرض من–إلى» preferred over the legacy
     ``allowed_hours_*``; ``allowed_days`` only restricts when it is a proper
     subset of the week).

An empty effective schedule = NO restriction → the session is never touched
(unlimited subscribers are left alone).

Timezone
--------
Every comparison happens in the tenant's LOCAL wall-clock time
(``system_config.local_now`` / ``tenant_tzinfo``, DST-safe, default UTC+3), so a
window that "ends at 04:00" means 04:00 for the owner — not 04:00 UTC.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from ..core import access_schedule
from ..core.types import AccessPlan, Subscriber

_LOG = logging.getLogger(__name__)

# How far ahead ``seconds_until_window_end`` scans for the window boundary. A
# daily window always closes within 24h; we allow a little slack. A schedule
# that stays allowed beyond this horizon (e.g. a several-day day-only window) is
# treated as "no near boundary" → no Session-Timeout cap (the periodic sweep is
# the backstop). Minute-granular scan → at most this many cheap iterations.
_SCAN_HORIZON_MIN = 25 * 60


# ─────────────────────────── effective schedule ─────────────────────────────


def _has_windows(sched: dict) -> bool:
    return bool((sched or {}).get("windows"))


def subscriber_schedule(sub: Subscriber) -> Optional[dict]:
    """The subscriber's own effective schedule dict, or ``None`` when the
    subscriber has NO personal schedule (→ the plan/offer schedule governs).

    Mirrors ``policy_engine._subscriber_has_schedule`` +
    ``_check_subscriber_schedule`` so the boolean result agrees exactly with the
    authorize-time reject.
    """
    raw = (getattr(sub, "connection_schedule", "") or "").strip()
    if raw:
        try:
            parsed = access_schedule.parse(raw)
            if _has_windows(parsed):
                return parsed
        except Exception:  # noqa: BLE001 — malformed schedule ≠ a restriction
            pass
    days = (getattr(sub, "working_days", "") or "").strip()
    if days:
        codes = [d.strip().lower() for d in days.split(",") if d.strip()]
        codes = [d for d in access_schedule.DAYS if d in set(codes)]
        if codes:
            return {"windows": [{"days": codes, "from": "", "to": ""}]}
    return None


def _restricting_days(plan: AccessPlan) -> list[str]:
    """``plan.allowed_days`` as canonical codes, but ONLY when it actually
    restricts (a proper, non-empty subset of the week). The default all-seven
    tuple (or empty) means "every day" → returns ``[]`` (no restriction)."""
    raw = getattr(plan, "allowed_days", None) or ()
    codes = {str(d).strip().lower() for d in raw if str(d).strip()}
    codes &= set(access_schedule.DAYS)
    if not codes or codes == set(access_schedule.DAYS):
        return []
    return [d for d in access_schedule.DAYS if d in codes]


def effective_plan_hours(plan: AccessPlan) -> tuple[str, str]:
    """(from, to) HH:MM for the plan/offer — the offer window «ساعات العرض من–إلى»
    (``offer_hours_*``) is preferred, falling back to the legacy
    ``allowed_hours_*``. Both empty → no hour restriction."""
    f = (getattr(plan, "offer_hours_from", "") or "").strip() \
        or (getattr(plan, "allowed_hours_from", "") or "").strip()
    t = (getattr(plan, "offer_hours_to", "") or "").strip() \
        or (getattr(plan, "allowed_hours_to", "") or "").strip()
    return f, t


def plan_schedule(plan: Optional[AccessPlan]) -> dict:
    """The plan/offer effective schedule dict (``{"windows": []}`` when the plan
    imposes no schedule). Unified ``plan.connection_schedule`` wins; otherwise a
    single window is synthesized from allowed days + offer/allowed hours."""
    if plan is None:
        return {"windows": []}
    raw = (getattr(plan, "connection_schedule", "") or "").strip()
    if raw:
        try:
            parsed = access_schedule.parse(raw)
            if _has_windows(parsed):
                return parsed
        except Exception:  # noqa: BLE001
            pass
    days = _restricting_days(plan)
    f, t = effective_plan_hours(plan)
    hours_active = bool(f and t)
    if not days and not hours_active:
        return {"windows": []}
    return {"windows": [{
        "days": days,
        "from": f if hours_active else "",
        "to": t if hours_active else "",
    }]}


def effective_schedule(sub: Subscriber, plan: Optional[AccessPlan]) -> dict:
    """The single effective schedule for ``sub`` under ``plan``. The subscriber's
    own schedule OVERRIDES the plan/offer entirely when present. Empty
    ``windows`` = no restriction (never enforced)."""
    own = subscriber_schedule(sub)
    if own is not None:
        return own
    return plan_schedule(plan)


# ─────────────────────────── evaluation helpers ─────────────────────────────


def _as_naive_local(when: datetime) -> datetime:
    """Local wall-clock as a naive datetime (drop tzinfo). ``weekday()``/``time()``
    on the aware local instant already read the local wall clock; stripping
    tzinfo lets us step minute-by-minute without DST arithmetic surprises."""
    return when.replace(tzinfo=None) if when.tzinfo is not None else when


def _allowed_at(windows: list, when_naive: datetime) -> bool:
    """Whether any window allows ``when_naive`` (pre-parsed windows; reuses the
    canonical per-window rule so semantics match ``access_schedule.is_allowed``)."""
    if not windows:
        return True
    code = access_schedule._PY_WEEKDAY_TO_CODE[when_naive.weekday()]
    t = when_naive.time().replace(second=0, microsecond=0)
    return any(access_schedule._window_allows(w, code, t) for w in windows)


def is_out_of_window(sub: Subscriber, plan: Optional[AccessPlan],
                     local_dt: datetime) -> bool:
    """True when ``sub``'s effective schedule DISALLOWS ``local_dt`` (tenant-local).
    No schedule / empty schedule → always False (never out of window)."""
    sched = effective_schedule(sub, plan)
    windows = sched.get("windows") or []
    if not windows:
        return False
    return not _allowed_at(windows, _as_naive_local(local_dt))


def seconds_until_window_end(sub: Subscriber, plan: Optional[AccessPlan],
                             local_dt: datetime) -> Optional[int]:
    """Seconds from ``local_dt`` until the currently-open allowed window closes,
    for use as a ``Session-Timeout`` cap. Returns:

      • ``None`` — no effective schedule, OR currently allowed with no boundary
        within the scan horizon (nothing to cap).
      • ``0``    — currently OUTSIDE the window (defensive; the accept path only
        reaches here when allowed, so this is a safety floor).
      • ``N>0``  — seconds until the window boundary (login 03:30, cutoff 04:00 →
        ``1800``).
    """
    sched = effective_schedule(sub, plan)
    windows = sched.get("windows") or []
    if not windows:
        return None
    base = _as_naive_local(local_dt)
    if not _allowed_at(windows, base):
        return 0
    floor = base.replace(second=0, microsecond=0)
    for i in range(1, _SCAN_HORIZON_MIN + 1):
        cand = floor + timedelta(minutes=i)
        if not _allowed_at(windows, cand):
            return max(1, int((cand - base).total_seconds()))
    return None


# ─────────────────────────── periodic enforcer ──────────────────────────────


def _active_tenant_ids() -> list[int]:
    from ..db.connection import db
    try:
        return [int(r["id"]) for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001 — table may be missing very early in boot
        return [1]


def enforce_active_session_windows(tenant_id: Optional[int] = None) -> dict:
    """Sweep live radacct sessions and CoA-disconnect any whose effective
    connection-schedule window is now CLOSED, with reason «خارج وقت السماح»
    (``out_of_window``). Reuses the policy-reconciler enumeration/resolution and
    the live-session-control disconnect + mikrotik-actions reason plumbing.

    ``tenant_id=None`` → every active tenant (the worker path); otherwise the one
    tenant (direct/test calls). Never raises — fully fail-safe."""
    from . import live_session_control as lsc
    from . import policy_reconciler as pr
    from ..core import system_config

    stats = {"checked": 0, "out_of_window": 0, "disconnected": 0, "failed": 0}
    tenants = [int(tenant_id)] if tenant_id is not None else _active_tenant_ids()
    for tid in tenants:
        try:
            rows = pr._live_rows(int(tid))
        except Exception:  # noqa: BLE001 — one tenant must not stall the rest
            _LOG.exception("schedule_window: live-rows query failed tenant=%s", tid)
            continue
        try:
            local_dt = system_config.local_now(int(tid))
        except Exception:  # noqa: BLE001
            local_dt = datetime.utcnow()
        for row in rows:
            username = str(row.get("username") or "").strip()
            if not username:
                continue
            stats["checked"] += 1
            try:
                sub, plan, _src = pr._resolve(int(tid), username)
            except Exception:  # noqa: BLE001
                continue
            if sub is None:
                continue
            try:
                if not is_out_of_window(sub, plan, local_dt):
                    continue
            except Exception:  # noqa: BLE001 — a bad schedule ≠ a disconnect
                _LOG.warning("schedule_window: window check failed for %r",
                             username, exc_info=True)
                continue
            stats["out_of_window"] += 1
            sid = str(row.get("acctsessionid") or "")
            outcome = None
            try:
                outcome = lsc.disconnect_live(
                    tenant_id=int(tid), username=username, session_id=sid)
                ok = bool(getattr(outcome, "ok", False))
            except Exception:  # noqa: BLE001 — NAS unreachable / network error
                ok = False
            # Surface the automated eviction in the unified MikroTik-actions feed
            # with the «خارج وقت السماح» reason, router, and real result.
            try:
                from .mt_action_log import record_disconnect
                record_disconnect(
                    actor="system:schedule-window",
                    username=username, tenant_id=int(tid), ok=ok,
                    reason="out_of_window", session_id=sid,
                    nas_ip=str(getattr(outcome, "nas_ip", "") or ""),
                    error="" if ok else str(
                        getattr(outcome, "reply_message", "")
                        or getattr(outcome, "code_name", "")
                        or "PoD undeliverable"))
            except Exception:  # noqa: BLE001
                pass
            if ok:
                stats["disconnected"] += 1
                _LOG.info("schedule_window: disconnected %r session=%s "
                          "(out of allowed window)", username, sid)
            else:
                stats["failed"] += 1
                _LOG.warning("schedule_window: PoD failed/undeliverable for %r "
                             "session=%s (will be rejected at next re-auth)",
                             username, sid)
    return stats


__all__ = [
    "subscriber_schedule", "plan_schedule", "effective_schedule",
    "effective_plan_hours",
    "is_out_of_window", "seconds_until_window_end",
    "enforce_active_session_windows",
]
