"""card_accounting — single source of truth for a card's *accounting mode* and
*time budget*, and for the *remaining time* computed consistently with that
mode.

Historically three places disagreed about what a card's "remaining time" means:

  * ``card_checker`` derived the label purely from ``started_at`` (has the card
    ever connected?) and computed remaining purely from ``cards.expire_at`` —
    ignoring the batch's ``count_from_first_connect`` flag entirely.
  * ``card_batch_flags._materialize_first_login_validity`` only understood a
    validity expressed in whole DAYS, so a sub-day budget (3h, 10min) never
    produced an expiry.
  * the migration engine never carried the source accounting mode / budget onto
    the batch at all.

This module centralises the two primitives everyone needs:

  * :func:`budget_seconds` — the card's total time budget in seconds, unit-aware
    (minutes / hours / days / weeks), drawn from the batch window first and the
    plan second.
  * :func:`remaining_seconds` — the remaining time, honouring the mode:
      - Mode B (count-from-first-connect): a wall-clock countdown that begins at
        the first connection → ``max(0, first_connection + budget - now)``.
      - Mode A (count-by-seconds): a usage-seconds budget that only burns while
        online → ``max(0, budget - accounted_seconds)``.

The functions are pure and take primitives (ints / datetimes / a mode string)
so they unit-test without a DB and are reused by the checker service, the
first-login materialiser, and the reconcile tool alike.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# Accounting-mode constants — kept as plain strings so they serialise straight
# into the Card Checker JSON payload and the reconcile report.
MODE_FROM_FIRST_CONNECT = "from_first_connect"
MODE_BY_SECONDS = "by_seconds"

# Unit → seconds. Mirrors the units the batch (time_unit) and the adv source
# (exp_unit_val) can express. "months"/"years" are approximated the same way
# the migration's parse_duration does (30d / 365d) so a validity carried as a
# calendar unit still yields a sane budget.
_UNIT_SECONDS = {
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
    "weeks": 604800,
    "months": 2592000,   # 30 days
    "years": 31536000,   # 365 days
}


def unit_to_seconds(value: int, unit: str) -> int:
    """``value`` of ``unit`` (minutes/hours/days/…) → seconds. Unknown unit is
    treated as days (the legacy default), never raising."""
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    mult = _UNIT_SECONDS.get((unit or "days").strip().lower(), 86400)
    return v * mult


def accounting_mode(count_from_first_connect: bool) -> str:
    """The card's accounting mode as a stable string for payload/report use."""
    return MODE_FROM_FIRST_CONNECT if count_from_first_connect else MODE_BY_SECONDS


def budget_seconds(
    *,
    validity_after_first_login_days: int = 0,
    time_value: int = 0,
    time_unit: str = "days",
    duration_minutes: int = 0,
    validity_days: int = 0,
) -> int:
    """Total time budget of a card, in seconds.

    Priority (most specific → most general), identical for both modes so the
    two never diverge:

      1. batch ``validity_after_first_login_days`` (whole days)
      2. batch ``time_value`` + ``time_unit`` (unit-aware — carries sub-day
         budgets like 3h / 10min that days-only fields cannot)
      3. plan ``duration_minutes``
      4. plan ``validity_days``

    Returns 0 when nothing is set (i.e. "no time budget" — unlimited by time).
    """
    days = _int(validity_after_first_login_days)
    if days > 0:
        return days * 86400
    win = unit_to_seconds(_int(time_value), time_unit)
    if win > 0:
        return win
    dm = _int(duration_minutes)
    if dm > 0:
        return dm * 60
    vd = _int(validity_days)
    if vd > 0:
        return vd * 86400
    return 0


def remaining_seconds(
    *,
    mode: str,
    budget: int,
    now: datetime,
    first_connection_at: Optional[datetime] = None,
    accounted_seconds: int = 0,
    expire_at: Optional[datetime] = None,
) -> Optional[int]:
    """Remaining time in seconds, honouring ``mode``.

    * ``MODE_FROM_FIRST_CONNECT``: countdown from the first connection.
      - Not connected yet → the full budget is still ahead → ``budget`` (or
        ``None`` if there is no budget, meaning "unlimited/unknown").
      - Connected → ``max(0, first_connection + budget - now)``.
    * ``MODE_BY_SECONDS``: usage-seconds budget → ``max(0, budget - used)``.

    ``expire_at`` is a *fallback only* — used when the mode yields no budget
    (a legacy calendar card with just an expiry date). It is deliberately NOT
    applied as a ceiling: migrated from-first-connect cards carry a stale
    generation-time ``expire_at`` in the past, and using it as a ceiling is
    exactly what wrongly zeroed the remaining time. When a budget exists, the
    mode's own countdown is authoritative.
    """
    budget = _int(budget)
    computed: Optional[int]
    if mode == MODE_FROM_FIRST_CONNECT:
        if first_connection_at is None:
            computed = budget if budget > 0 else None
        elif budget > 0:
            end = first_connection_at + timedelta(seconds=budget)
            computed = max(0, int((end - now).total_seconds()))
        else:
            computed = None
    else:  # MODE_BY_SECONDS
        if budget > 0:
            computed = max(0, budget - _int(accounted_seconds))
        else:
            computed = None
    # Legacy fallback: no budget in either mode → honour a plain expiry date.
    if computed is None and expire_at is not None:
        return max(0, int((expire_at - now).total_seconds()))
    return computed


def first_connect_expiry(
    first_connection_at: datetime, budget: int
) -> Optional[datetime]:
    """The wall-clock expiry of a from-first-connect card: first connection +
    budget. Returns ``None`` when there is no budget (unlimited by time)."""
    b = _int(budget)
    if b <= 0:
        return None
    return first_connection_at + timedelta(seconds=b)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "MODE_FROM_FIRST_CONNECT",
    "MODE_BY_SECONDS",
    "unit_to_seconds",
    "accounting_mode",
    "budget_seconds",
    "remaining_seconds",
    "first_connect_expiry",
]
