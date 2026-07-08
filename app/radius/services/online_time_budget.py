# -*- coding: utf-8 -*-
"""online_time_budget — «وقت اليوم» used/total cells for the /online lists.

Owner spec: the «وقت اليوم» column shows «used / total» as a colored badge by
thirds of the consumed fraction — «ثلث المدة أخضر، ثلثين أصفر، آخر ثلث أحمر»:

    used <  1/3 of total → green
    used <  2/3 of total → amber
    used >= 2/3 of total → red        (used > total caps at 100% → red)
    no total (unlimited) → neutral    (never divide by zero / fake a total)

TOTAL source per type — resolved exactly like the enforcement/checker paths:

  * CARD       → the card's time budget from its BATCH via
                 ``card_accounting.budget_seconds`` (validity_after_first_login
                 days > time_value+unit > plan duration > plan validity) — the
                 same source the card checker uses. ``used`` = lifetime
                 accounted seconds (by-seconds mode) or the elapsed part of the
                 from-first-connect countdown (budget − remaining).
  * SUBSCRIBER → the effective DAILY cap (subscriber override else plan
                 ``max_daily_minutes`` — ``policy_engine.effective_daily_cap_min``)
                 with ``used`` = today's accounted seconds; if no daily cap but
                 a TOTAL connection-time limit is set, that limit with lifetime
                 ``used``; else unlimited.

Never raises — an empty dict / missing username degrades the cell to «—».
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Mapping, Optional

from ..core.duration_fmt import fmt_compact

_LOG = logging.getLogger(__name__)

BUCKET_GREEN = "green"
BUCKET_AMBER = "amber"
BUCKET_RED = "red"
BUCKET_NEUTRAL = "neutral"


def thirds_bucket(used_sec: int, total_sec: Optional[int]) -> str:
    """Color bucket for a used/total pair (thirds rule, ratio capped at 1).

    Integer math — no float drift at the exact boundaries. The owner's
    anchor example fixes the edges: 1h of 3h (exactly ⅓) is GREEN, and
    2h of 3h (exactly ⅔) is RED — so green is *inclusive* of ⅓ and red
    *starts at* ⅔; amber is strictly between."""
    if not total_sec or int(total_sec) <= 0:
        return BUCKET_NEUTRAL
    used = min(max(0, int(used_sec or 0)), int(total_sec))  # cap at 100%
    total = int(total_sec)
    if used * 3 <= total:
        return BUCKET_GREEN
    if used * 3 >= total * 2:
        return BUCKET_RED
    return BUCKET_AMBER


def _cell(used_sec: int, total_sec: Optional[int]) -> dict:
    used = max(0, int(used_sec or 0))
    total = int(total_sec) if total_sec and int(total_sec) > 0 else None
    return {
        "used_sec": used,
        "total_sec": total,
        "used_txt": fmt_compact(used),
        "total_txt": fmt_compact(total) if total else "",
        "bucket": thirds_bucket(used, total),
    }


def _usage_bulk(tenant_id: int, usernames: list[str]) -> dict[str, dict]:
    """{username: {first_at: datetime|None, total_sec: int}} — lifetime
    accounting per user in ONE query (same counters the card checker reads:
    first session start + SUM(acctsessiontime))."""
    if not usernames:
        return {}
    from ..db.connection import db
    from .device_limit import acct_norm_sql, parse_acct_dt
    ph = ",".join("?" * len(usernames))
    nrm = acct_norm_sql("acctstarttime")
    rows = db().execute(
        f"SELECT username, MIN({nrm}) AS first_at, "
        f"       COALESCE(SUM(acctsessiontime), 0) AS total_sec "
        f"  FROM radacct WHERE tenant_id=? AND username IN ({ph}) "
        f" GROUP BY username",
        (int(tenant_id), *usernames)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        out[str(d.get("username") or "")] = {
            "first_at": parse_acct_dt(d.get("first_at")),
            "total_sec": int(d.get("total_sec") or 0),
        }
    return out


def _card_cells(tenant_id: int, usernames: list[str],
                now: _dt.datetime) -> dict[str, dict]:
    """used/total for CARDS — total = the batch-sourced time budget (the card
    checker's source of truth), used = the consumed part of that budget."""
    from ..db.connection import db
    from . import card_accounting as ca
    ph = ",".join("?" * len(usernames))
    rows = db().execute(
        f"SELECT c.username, c.expire_at AS card_expire_at, "
        f"       COALESCE(b.count_from_first_connect, 0) AS from_first, "
        f"       COALESCE(b.validity_after_first_login_days, 0) AS vafl_days, "
        f"       COALESCE(b.time_value, 0) AS time_value, "
        f"       COALESCE(b.time_unit, '') AS time_unit, "
        f"       COALESCE(p.duration_minutes, 0) AS duration_minutes, "
        f"       COALESCE(p.validity_days, 0) AS validity_days "
        f"  FROM cards c "
        f"  LEFT JOIN card_batches b ON b.id = c.batch_id AND b.tenant_id = c.tenant_id "
        f"  LEFT JOIN access_plans p "
        f"    ON p.tenant_id = c.tenant_id AND p.id = COALESCE(c.plan_id, b.plan_id) "
        f" WHERE c.tenant_id=? AND c.username IN ({ph})",
        (int(tenant_id), *usernames)).fetchall()
    usage = _usage_bulk(int(tenant_id), usernames)
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        uname = str(d.get("username") or "")
        use = usage.get(uname, {})
        accounted = int(use.get("total_sec") or 0)
        first_at = use.get("first_at")
        mode = ca.accounting_mode(bool(d.get("from_first")))
        budget = ca.budget_seconds(
            validity_after_first_login_days=d.get("vafl_days"),
            time_value=d.get("time_value"),
            time_unit=str(d.get("time_unit") or "days"),
            duration_minutes=d.get("duration_minutes"),
            validity_days=d.get("validity_days"),
        )
        if budget > 0:
            remaining = ca.remaining_seconds(
                mode=mode, budget=budget, now=now,
                first_connection_at=first_at, accounted_seconds=accounted)
            used = budget - int(remaining or 0) if remaining is not None else accounted
            out[uname] = _cell(min(max(0, used), budget), budget)
        else:
            # No time budget → unlimited by time: show the usage, neutral.
            out[uname] = _cell(accounted, None)
    return out


def _subscriber_cells(tenant_id: int, usernames: list[str]) -> dict[str, dict]:
    """used/total for SUBSCRIBERS — daily cap (override else plan) with
    today's usage; else the total connection-time limit with lifetime usage;
    else unlimited (today's usage, neutral)."""
    from ..db.connection import db
    from .policy_engine import daily_used_seconds_bulk
    ph = ",".join("?" * len(usernames))
    rows = db().execute(
        f"SELECT s.username, "
        f"       COALESCE(s.total_connection_time_min, 0) AS sub_total_min, "
        f"       COALESCE(s.daily_connection_time_min, 0) AS sub_daily_min, "
        f"       COALESCE(s.connection_time_limit_enabled, 0) AS lim_enabled, "
        f"       COALESCE(p.max_daily_minutes, 0) AS plan_daily_min "
        f"  FROM subscribers s "
        f"  LEFT JOIN access_plans p ON p.tenant_id = s.tenant_id AND p.id = s.plan_id "
        f" WHERE s.tenant_id=? AND s.username IN ({ph})",
        (int(tenant_id), *usernames)).fetchall()
    used_today = daily_used_seconds_bulk(int(tenant_id), usernames)
    lifetime: Optional[dict[str, dict]] = None  # lazy — only if a total cap shows up
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        uname = str(d.get("username") or "")
        sub_total = int(d.get("sub_total_min") or 0)
        sub_daily = int(d.get("sub_daily_min") or 0)
        # Same precedence as policy_engine._effective_time_caps: the
        # subscriber's own limits win when enabled/any set; else plan daily.
        if bool(d.get("lim_enabled")) or sub_total or sub_daily:
            daily_min, total_min = sub_daily, sub_total
        else:
            daily_min, total_min = int(d.get("plan_daily_min") or 0), 0
        if daily_min > 0:
            out[uname] = _cell(int(used_today.get(uname, 0)), daily_min * 60)
        elif total_min > 0:
            if lifetime is None:
                lifetime = _usage_bulk(int(tenant_id), usernames)
            used = int((lifetime.get(uname) or {}).get("total_sec") or 0)
            out[uname] = _cell(used, total_min * 60)
        else:
            out[uname] = _cell(int(used_today.get(uname, 0)), None)
    return out


def day_time_cells(tenant_id: int, sessions, *, card_view: bool,
                   now: Optional[_dt.datetime] = None) -> dict[str, dict]:
    """{username: {used_sec, total_sec, used_txt, total_txt, bucket}} for the
    «وقت اليوم» column of the /online list. ``card_view=True`` for the
    ?type=card tab (all rows are cards), else the subscribers tab.
    Never raises — {} on any failure (the column degrades to «—»)."""
    try:
        names = sorted({str(getattr(s, "username", "") or "").strip()
                        for s in (sessions or [])
                        if str(getattr(s, "username", "") or "").strip()})
        if not names:
            return {}
        now = now or _dt.datetime.utcnow()
        if card_view:
            return _card_cells(int(tenant_id), names, now)
        return _subscriber_cells(int(tenant_id), names)
    except Exception:  # noqa: BLE001 — لا تكسر تصيير القائمة بسبب العمود
        _LOG.warning("online_time_budget: cells failed", exc_info=True)
        return {}
