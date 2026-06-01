"""Dunning scheduler — near-expiry reminders (Phase 3).

A background polling thread (mirrors :mod:`app.workers.backup_scheduler_worker`)
that wakes roughly hourly and, for every tenant whose ``near_expiry`` rule is
enabled, finds subscribers whose subscription expires within the configured
``days_before`` window and fires ``notify_event('near_expiry', …)`` for each.

Anti-spam: each subscriber is reminded at most **once per calendar day**. The
last-sent date is tracked per tenant in ``tenant_settings`` under
``notif.near_expiry.sent_log`` (a small JSON map ``{subscriber_id: "YYYY-MM-DD"}``)
so a subscriber who sits in the window for several days gets one nudge per day,
not one per tick. The map is pruned to the current window each run so it can
never grow without bound.

Like the other workers it does nothing harmful when idle: if the rule is
disabled, no channel is configured, or nothing is expiring, the tick is a
cheap no-op. It NEVER raises — a scheduling error can't kill the thread.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timezone

_LOG = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()

# tenant_settings key holding the per-tenant {subscriber_id: date} sent-log.
_SENT_LOG_KEY = "notif.near_expiry.sent_log"

# Default poll cadence: hourly. Overridable for tests / tuning.
_DEFAULT_POLL_SECONDS = 3600


def _loop(poll_interval: int) -> None:
    while True:
        try:
            run_once()
        except Exception:  # noqa: BLE001 — never let a tick kill the worker
            _LOG.exception("[dunning] tick failed — continuing")
        time.sleep(poll_interval)


def run_once() -> dict:
    """One sweep across every tenant. Returns a small summary dict (for tests).

    Public so a route/test can trigger it on demand without the timer.
    """
    summary = {"tenants": 0, "checked": 0, "notified": 0}
    for tenant_id in _all_tenant_ids():
        summary["tenants"] += 1
        try:
            checked, notified = _run_for_tenant(int(tenant_id))
            summary["checked"] += checked
            summary["notified"] += notified
        except Exception:  # noqa: BLE001 — one tenant's failure can't stop the rest
            _LOG.exception("[dunning] tenant=%s sweep failed", tenant_id)
    if summary["notified"]:
        _LOG.info(
            "[dunning] swept %d tenant(s), %d checked, %d reminded",
            summary["tenants"], summary["checked"], summary["notified"],
        )
    return summary


def _run_for_tenant(tenant_id: int) -> tuple[int, int]:
    """Fire near_expiry reminders for one tenant. Returns (checked, notified)."""
    from app.radius.services import notifications_engine as ne

    rule = ne.load_rule(tenant_id, "near_expiry")
    if rule is None or not rule.enabled or not rule.active_channels():
        return 0, 0

    days_before = int(rule.days_before or ne.DEFAULT_DAYS_BEFORE)
    candidates = _expiring_subscribers(tenant_id, days_before)
    if not candidates:
        return 0, 0

    today = _today_str()
    sent_log = _load_sent_log(tenant_id)
    notified = 0
    fresh_log: dict[str, str] = {}

    for sub, days_left in candidates:
        sid = str(int(getattr(sub, "id", 0) or 0))
        if sid == "0":
            continue
        # Keep this subscriber in the pruned log regardless (it's in-window).
        already = sent_log.get(sid) == today
        if already:
            fresh_log[sid] = today
            continue
        outcome = ne.notify_event(
            "near_expiry",
            tenant_id=tenant_id,
            subscriber=sub,
            context={"days": days_left},
        )
        # WhatsApp expiry notice via the gated, fail-safe bridge helper. It
        # reuses THIS once-per-day dedup (we're inside the `already` guard), so
        # it fires at most once per subscriber per day. A bridge/enqueue failure
        # can never abort the sweep — notify_whatsapp swallows everything.
        _notify_expiry_whatsapp(tenant_id, sub)
        # Record the attempt for today so we don't re-nudge on the next tick,
        # even if a channel was unconfigured (avoids hammering a bad gateway).
        fresh_log[sid] = today
        if outcome.fired:
            notified += 1

    _save_sent_log(tenant_id, fresh_log)
    return len(candidates), notified


# ── WhatsApp expiry notice (gated, fail-safe) ────────────────────────────
def _notify_expiry_whatsapp(tenant_id: int, sub) -> None:
    """Enqueue the gated 'expiry_notice' WhatsApp message for one subscriber.

    Fail-safe by construction: notify_whatsapp swallows every error, and this
    extra try/except guarantees even an import/attribute error can't abort the
    dunning sweep. Called once-per-day per subscriber (the caller's dedup).
    """
    try:
        from app.radius.services.whatsapp_notify import notify_whatsapp

        sid = int(getattr(sub, "id", 0) or 0)
        if sid <= 0:
            return
        phone = str(getattr(sub, "mobile", "") or "").strip()
        name = str(getattr(sub, "full_name", "") or getattr(sub, "username", "") or "")
        expiry_date = _expiry_date_str(getattr(sub, "expire_at", None))
        notify_whatsapp(
            int(tenant_id or 1),
            "expiry_notice",
            gate="expiry",
            recipient_phone=phone,
            template_key="subscription_expiry_soon",
            subscriber_id=sid,
            variables=[name, expiry_date, _portal_url(tenant_id)],
            idempotency_key=f"exp:{int(tenant_id or 1)}:{sid}:{_today_str()}",
        )
    except Exception:  # noqa: BLE001 — never let the notice abort the sweep
        _LOG.exception("[dunning] whatsapp expiry notice failed (non-fatal)")


def _expiry_date_str(value) -> str:
    """Best-effort YYYY-MM-DD for the expiry-notice template variable."""
    dt = _parse_dt(value)
    if dt is None:
        return ""
    try:
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def _portal_url(tenant_id: int) -> str:
    """Subscriber-portal URL for the notice, best-effort (may be empty)."""
    try:
        from app.radius.services.admin_panel_client import bridge_setting

        return (bridge_setting("license_admin_bridge.base_url", "") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


# ── candidate query ────────────────────────────────────────────────────
def _expiring_subscribers(tenant_id: int, days_before: int) -> list[tuple[object, int]]:
    """Active subscribers expiring within ``days_before`` days (inclusive).

    Returns a list of ``(subscriber, days_left)`` where days_left >= 0. Pulls a
    generous page and filters in Python on the parsed ``expire_at`` so we stay
    independent of the DB's date-string format.
    """
    from app.radius.db.repos import subscribers_repo

    out: list[tuple[object, int]] = []
    try:
        # Only real, non-deleted subscribers; exclude obviously-inactive ones.
        rows = subscribers_repo.list_subscribers(
            int(tenant_id or 1), user_type="subscriber", limit=5000
        )
    except Exception:  # noqa: BLE001
        return out

    for sub in rows:
        status = str(getattr(sub, "status", "") or "").strip().lower()
        if status in ("disabled", "expired", "suspended", "blocked"):
            continue
        days_left = _days_until(getattr(sub, "expire_at", None))
        if days_left is None:
            continue
        if 0 <= days_left <= days_before:
            out.append((sub, days_left))
    return out


def _days_until(value) -> int | None:
    """Whole days from *now* until ``value`` (a datetime/ISO string). None if absent."""
    dt = _parse_dt(value)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    # Round up so "expires in 18 hours" counts as 1 day, not 0.
    secs = delta.total_seconds()
    if secs < 0:
        # Already expired (within today counts as 0; truly past → negative).
        return -1
    return int((secs + 86399) // 86400)


def _parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip().replace("Z", "").replace("T", " ")
    s = s.split(".")[0][:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ── sent-log persistence (tenant_settings JSON) ──────────────────────────
def _load_sent_log(tenant_id: int) -> dict[str, str]:
    from app.radius.db.repos import tenants_repo

    raw = tenants_repo.get_setting(int(tenant_id or 1), _SENT_LOG_KEY, "")
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _save_sent_log(tenant_id: int, sent_log: dict[str, str]) -> None:
    from app.radius.db.repos import tenants_repo

    try:
        tenants_repo.set_setting(
            int(tenant_id or 1),
            _SENT_LOG_KEY,
            json.dumps(sent_log, ensure_ascii=False, sort_keys=True),
            by=0,
        )
    except Exception:  # noqa: BLE001
        _LOG.debug("[dunning] could not persist sent-log for tenant=%s", tenant_id)


def _today_str() -> str:
    return date.today().isoformat()


def _all_tenant_ids() -> list[int]:
    """Distinct tenant ids that own subscribers (so we honour every tenant)."""
    try:
        from app.radius.db.connection import db

        cur = db().execute("SELECT DISTINCT tenant_id FROM subscribers")
        return [int(r["tenant_id"]) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return [1]


# ── public start ──────────────────────────────────────────────────────────
def start_dunning_worker() -> None:
    """Start the polling thread (unless workers are globally disabled)."""
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    with _lock:
        if _started:
            return
        poll_interval = max(
            300,
            int(os.environ.get("HOBERADIUS_DUNNING_INTERVAL_SECONDS", str(_DEFAULT_POLL_SECONDS)) or _DEFAULT_POLL_SECONDS),
        )
        threading.Thread(
            target=_loop, args=(poll_interval,), daemon=True, name="hoberadius-dunning"
        ).start()
        _started = True
        _LOG.info("[dunning] started — poll every %ds", poll_interval)
