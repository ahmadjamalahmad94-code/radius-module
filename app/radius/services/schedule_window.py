"""schedule_window — the effective allowed-hours window and its enforcement.

Two INDEPENDENT time-window settings restrict when a subscriber may be online,
and BOTH must be honored (the effective window is their INTERSECTION):

  • Offer service-hours «ساعات العرض — من / إلى» — ``access_plans.offer_hours_from``
    / ``offer_hours_to`` (legacy fallback ``allowed_hours_from``/``allowed_hours_to``).
    A daily time-of-day window that says WHEN THE OFFER is available. It always
    applies to every subscriber on that plan.

  • Connection schedule «الجدولة» — the unified ``connection_schedule`` (JSON
    day/time windows, :mod:`access_schedule`) on the subscriber, or its legacy
    ``working_days`` CSV; falling back to the plan's ``connection_schedule`` /
    ``allowed_days`` when the subscriber sets none. The per-subscriber schedule
    OVERRIDES the plan-level schedule *within this dimension* (a per-account
    override of the plan default).

Effective rule
--------------
    allowed(T) = schedule_dimension_allows(T) AND offer_hours_allows(T)

  • Only the schedule set  → that one governs.
  • Only offer-hours set   → offer-hours govern.
  • Both set               → intersection (a login must satisfy BOTH).
  • Neither set            → unlimited (never restricted / never disconnected).

This closes two authorize-time bugs where an out-of-window login was WRONGLY
accepted: (1) a half-open offer window (owner sets only «إلى 04:00», leaving
«من» blank) was silently ignored; (2) a subscriber ``connection_schedule`` made
the code skip the offer-hours entirely (override instead of intersection), so a
07:00 login against a 04:00 offer cutoff succeeded.

Enforced in three places, all reading the SAME effective window:
  (a) authorize REJECT — ``policy_engine._check_schedule`` denies an out-of-window
      login with «خارج وقت السماح» (Access-Reject).
  (b) authorize Session-Timeout — ``seconds_until_window_end`` caps Session-Timeout
      to the window boundary so the NAS auto-drops at e.g. 04:00.
  (c) periodic sweep — ``enforce_active_session_windows`` CoA-disconnects live
      sessions whose window has since closed.

Timezone: every comparison uses tenant-LOCAL wall-clock (``system_config`` /
``tenant_tzinfo``, DST-safe, default UTC+3). Overnight windows (22:00→04:00) and
half-open windows (→04:00 / 20:00→) are handled by :mod:`access_schedule`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from ..core import access_schedule
from ..core.types import AccessPlan, Subscriber

_LOG = logging.getLogger(__name__)

# How far ``seconds_until_window_end`` scans (minute granularity) for the next
# boundary. A daily window always closes within 24h; a schedule that stays open
# past this horizon (e.g. a multi-day day-only window) yields no Session-Timeout
# cap and relies on the periodic sweep as the backstop.
_SCAN_HORIZON_MIN = 25 * 60


# ─────────────────────── schedule dimension (windows) ───────────────────────


def _windows_from_days(codes: list[str]) -> list[dict]:
    return [{"days": codes, "from": "", "to": ""}] if codes else []


def _restricting_days(plan: AccessPlan) -> list[str]:
    """``plan.allowed_days`` as canonical codes, but ONLY when it actually
    restricts (a proper, non-empty subset of the week). The default all-seven
    tuple (or empty) means "every day" → ``[]`` (no restriction)."""
    raw = getattr(plan, "allowed_days", None) or ()
    codes = {str(d).strip().lower() for d in raw if str(d).strip()}
    codes &= set(access_schedule.DAYS)
    if not codes or codes == set(access_schedule.DAYS):
        return []
    return [d for d in access_schedule.DAYS if d in codes]


def _subscriber_schedule_windows(sub: Subscriber) -> Optional[list]:
    """The subscriber's OWN schedule windows, or ``None`` when the subscriber
    sets no personal schedule (→ the plan schedule governs this dimension)."""
    raw = (getattr(sub, "connection_schedule", "") or "").strip()
    if raw:
        try:
            parsed = access_schedule.parse(raw)
            if parsed.get("windows"):
                return parsed["windows"]
        except Exception:  # noqa: BLE001 — malformed ≠ a restriction
            pass
    days = (getattr(sub, "working_days", "") or "").strip()
    if days:
        codes = {d.strip().lower() for d in days.split(",") if d.strip()}
        codes &= set(access_schedule.DAYS)
        if codes:
            return _windows_from_days([d for d in access_schedule.DAYS if d in codes])
    return None


def _plan_schedule_windows(plan: Optional[AccessPlan]) -> Optional[list]:
    """The plan-level schedule windows (unified ``connection_schedule`` else
    ``allowed_days`` day restriction), or ``None`` when the plan sets none.
    Does NOT include offer-hours — those are the separate offer dimension."""
    if plan is None:
        return None
    raw = (getattr(plan, "connection_schedule", "") or "").strip()
    if raw:
        try:
            parsed = access_schedule.parse(raw)
            if parsed.get("windows"):
                return parsed["windows"]
        except Exception:  # noqa: BLE001
            pass
    days = _restricting_days(plan)
    if days:
        return _windows_from_days(days)
    return None


def schedule_dim_windows(sub: Subscriber,
                         plan: Optional[AccessPlan]) -> Optional[list]:
    """The effective schedule-dimension windows: the subscriber's own schedule
    OVERRIDES the plan's when present; otherwise the plan's. ``None`` = this
    dimension imposes no restriction."""
    own = _subscriber_schedule_windows(sub)
    if own is not None:
        return own
    return _plan_schedule_windows(plan)


# ─────────────────────── offer service-hours (windows) ──────────────────────


def effective_plan_hours(plan: AccessPlan) -> tuple[str, str]:
    """(from, to) HH:MM for the offer service-hours «ساعات العرض من–إلى»
    (``offer_hours_*``), falling back to the legacy ``allowed_hours_*``. Either
    may be empty (half-open) — access_schedule reads empty ``from`` as 00:00 and
    empty ``to`` as 24:00. Both empty → no hour restriction."""
    f = (getattr(plan, "offer_hours_from", "") or "").strip() \
        or (getattr(plan, "allowed_hours_from", "") or "").strip()
    t = (getattr(plan, "offer_hours_to", "") or "").strip() \
        or (getattr(plan, "allowed_hours_to", "") or "").strip()
    return f, t


def offer_windows(plan: Optional[AccessPlan]) -> Optional[list]:
    """The offer service-hours as a single daily window, or ``None`` when the
    plan sets no offer-hours. Half-open (only one bound) and overnight windows
    are handled by :mod:`access_schedule`."""
    if plan is None:
        return None
    f, t = effective_plan_hours(plan)
    if not f and not t:
        return None
    return [{"days": [], "from": f, "to": t}]


# ─────────────────────────── evaluation helpers ─────────────────────────────


def _as_naive_local(when: datetime) -> datetime:
    """Local wall-clock as a naive datetime (drop tzinfo). ``weekday()``/``time()``
    already read the local wall clock; stripping tzinfo lets us step minute by
    minute without DST arithmetic surprises."""
    return when.replace(tzinfo=None) if when.tzinfo is not None else when


def _windows_allow(windows: list, when_naive: datetime) -> bool:
    """Whether any window allows ``when_naive`` (canonical per-window rule → same
    semantics as ``access_schedule.is_allowed``)."""
    if not windows:
        return True
    code = access_schedule._PY_WEEKDAY_TO_CODE[when_naive.weekday()]
    t = when_naive.time().replace(second=0, microsecond=0)
    return any(access_schedule._window_allows(w, code, t) for w in windows)


def _dim_allows(windows: Optional[list], when_naive: datetime) -> bool:
    """A dimension with no windows (``None``) imposes no restriction."""
    return windows is None or _windows_allow(windows, when_naive)


def is_out_of_window(sub: Subscriber, plan: Optional[AccessPlan],
                     local_dt: datetime) -> bool:
    """True when ``local_dt`` (tenant-local) is OUTSIDE the effective window =
    outside the schedule dimension OR outside the offer service-hours. No
    settings at all → always False (never out of window)."""
    sd = schedule_dim_windows(sub, plan)
    od = offer_windows(plan)
    if sd is None and od is None:
        return False
    base = _as_naive_local(local_dt)
    return not (_dim_allows(sd, base) and _dim_allows(od, base))


def seconds_until_window_end(sub: Subscriber, plan: Optional[AccessPlan],
                             local_dt: datetime) -> Optional[int]:
    """Seconds from ``local_dt`` until the currently-open effective window closes
    (the earliest boundary of EITHER dimension), for use as a Session-Timeout
    cap. ``None`` = no window / no boundary within the scan horizon; ``0`` =
    currently outside (defensive floor); ``N>0`` = seconds to the boundary
    (login 03:30, cutoff 04:00 → 1800)."""
    sd = schedule_dim_windows(sub, plan)
    od = offer_windows(plan)
    if sd is None and od is None:
        return None
    base = _as_naive_local(local_dt)

    def allowed(dt: datetime) -> bool:
        return _dim_allows(sd, dt) and _dim_allows(od, dt)

    if not allowed(base):
        return 0
    floor = base.replace(second=0, microsecond=0)
    for i in range(1, _SCAN_HORIZON_MIN + 1):
        cand = floor + timedelta(minutes=i)
        if not allowed(cand):
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
    window (schedule ∩ offer-hours) is now CLOSED, reason «خارج وقت السماح»
    (``out_of_window``). Reuses the policy-reconciler enumeration/resolution and
    the live-session disconnect + mikrotik-actions reason plumbing.

    ``tenant_id=None`` → every active tenant (worker path); otherwise the one
    tenant. Never raises — fully fail-safe."""
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
    "schedule_dim_windows", "offer_windows", "effective_plan_hours",
    "is_out_of_window", "seconds_until_window_end",
    "enforce_active_session_windows",
]
