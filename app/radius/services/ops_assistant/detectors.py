"""Level 4 — event detectors (code, not the model).

Each detector reads the tenant's data and returns event payloads for CONTEXT
(``{"type", "data"}``). Detection only: the model may DRAFT a proposal for an
event, which then needs the same confirmation as level 2/3 — nothing runs by
itself (level 5 is out of scope by owner decision).

Scope: subscriber-level events are filtered with the same predicate as the API
(``subscriber_in_scope``); network-wide events (rejects per NAS) are only
shown to full-access admins (owner / «مدير عام»), card stock is limited to the
batches the admin can see (``batch_in_scope``). Ids/usernames inside an event
are issued to the conversation, so a proposal may reference them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ...db.connection import db
from . import units

EVENT_TYPES = ("expiring_tomorrow", "repeated_rejects", "low_card_stock", "plan_without_offers")
MAX_ITEMS = 20

DEFAULTS = {
    "ops_assistant.rejects_window_minutes": 15,
    "ops_assistant.rejects_threshold": 20,
    "ops_assistant.low_card_threshold": 20,
}


def _setting(tenant_id: int, key: str) -> int:
    try:
        from ...db.repos import tenants_repo
        return max(1, int(tenants_repo.get_setting(int(tenant_id), key, str(DEFAULTS[key]))
                          or DEFAULTS[key]))
    except Exception:  # noqa: BLE001
        return DEFAULTS[key]


def _utc_bounds_of_local_day(tenant_id: int, day) -> tuple[str, str]:
    tz = units.tzinfo_for(tenant_id)
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)).astimezone(
        timezone.utc)
    # expire_at is stored as naive UTC ISO ('YYYY-MM-DDTHH:MM:SS'); compare on
    # the normalised text (replace ' ' by 'T') — see radacct lexical-bug notes.
    return (start.replace(tzinfo=None).isoformat(timespec="seconds"),
            end.replace(tzinfo=None).isoformat(timespec="seconds"))


def expiring_tomorrow(tenant_id: int, *, now_utc: Optional[datetime] = None,
                      in_scope=None) -> Optional[dict]:
    """Subscribers (not cards) whose subscription ends TOMORROW (local day)."""
    tomorrow = units.local_today(tenant_id, now_utc) + timedelta(days=1)
    lo, hi = _utc_bounds_of_local_day(tenant_id, tomorrow)
    rows = db().execute(
        "SELECT username, plan_id, expire_at FROM subscribers WHERE tenant_id=? "
        "AND deleted_at IS NULL AND COALESCE(user_type,'subscriber') != 'card' "
        "AND status='enabled' AND expire_at IS NOT NULL "
        "AND replace(expire_at,' ','T') >= ? AND replace(expire_at,' ','T') < ? "
        "ORDER BY expire_at LIMIT 500", (int(tenant_id), lo, hi)).fetchall()
    items = []
    for r in rows:
        if in_scope is not None and not in_scope(r["username"]):
            continue
        items.append({"username": r["username"], "plan_id": r["plan_id"],
                      "expire_at": str(r["expire_at"]).replace(" ", "T")[:19] + "Z"})
    if not items:
        return None
    return {"type": "expiring_tomorrow",
            "data": {"date_local": tomorrow.isoformat(), "count": len(items),
                     "subscribers": items[:MAX_ITEMS]}}


def repeated_rejects(tenant_id: int, *, now_utc: Optional[datetime] = None) -> Optional[dict]:
    """NAS devices with ≥ threshold rejected logins in the last window. Never
    reads ``radpostauth.pass`` (the attempted password)."""
    window = _setting(tenant_id, "ops_assistant.rejects_window_minutes")
    threshold = _setting(tenant_id, "ops_assistant.rejects_threshold")
    now = (now_utc or units.utcnow())
    since = (now - timedelta(minutes=window)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db().execute(
        "SELECT nas, COUNT(*) AS c, COUNT(DISTINCT username) AS u FROM radpostauth "
        "WHERE tenant_id=? AND reply != 'Access-Accept' AND replace(authdate,'T',' ') >= ? "
        "GROUP BY nas HAVING COUNT(*) >= ? ORDER BY c DESC LIMIT ?",
        (int(tenant_id), since, threshold, MAX_ITEMS)).fetchall()
    if not rows:
        return None
    return {"type": "repeated_rejects",
            "data": {"window_minutes": window, "threshold": threshold,
                     "nas": [{"nas": r["nas"] or "", "rejects": int(r["c"]),
                              "distinct_usernames": int(r["u"])} for r in rows]}}


def low_card_stock(tenant_id: int, *, batch_ok=None) -> Optional[dict]:
    """Plans whose UNUSED cards (active batches) fell below the threshold."""
    threshold = _setting(tenant_id, "ops_assistant.low_card_threshold")
    rows = db().execute(
        "SELECT b.id AS batch_id, b.plan_id, p.name AS plan_name, "
        "SUM(CASE WHEN c.used=0 AND c.revoked=0 AND c.deleted_at IS NULL THEN 1 ELSE 0 END) AS unused "
        "FROM card_batches b JOIN access_plans p ON p.id=b.plan_id AND p.tenant_id=b.tenant_id "
        "LEFT JOIN cards c ON c.batch_id=b.id AND c.tenant_id=b.tenant_id "
        "WHERE b.tenant_id=? AND b.deleted_at IS NULL AND p.deleted_at IS NULL "
        "AND COALESCE(b.status,'active')='active' GROUP BY b.id",
        (int(tenant_id),)).fetchall()
    per_plan: dict[int, dict] = {}
    for r in rows:
        if batch_ok is not None and not batch_ok(int(r["batch_id"])):
            continue
        slot = per_plan.setdefault(int(r["plan_id"]), {"plan_id": int(r["plan_id"]),
                                                        "plan_name": r["plan_name"],
                                                        "unused_cards": 0})
        slot["unused_cards"] += int(r["unused"] or 0)
    low = sorted((p for p in per_plan.values() if p["unused_cards"] < threshold),
                 key=lambda p: p["unused_cards"])
    if not low:
        return None
    return {"type": "low_card_stock", "data": {"threshold": threshold, "plans": low[:MAX_ITEMS]}}


def plan_without_offers(tenant_id: int) -> Optional[dict]:
    """Enabled, non-archived plans that no ACTIVE card offer sells."""
    rows = db().execute(
        "SELECT p.id, p.name FROM access_plans p WHERE p.tenant_id=? AND p.enabled=1 "
        "AND p.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM card_offers o "
        "WHERE o.tenant_id=p.tenant_id AND o.plan_id=p.id AND o.active=1) "
        "ORDER BY p.name LIMIT ?", (int(tenant_id), MAX_ITEMS)).fetchall()
    if not rows:
        return None
    return {"type": "plan_without_offers",
            "data": {"plans": [{"plan_id": int(r["id"]), "plan_name": r["name"]} for r in rows]}}


def detect(tenant_id: int, *, full_access: bool, owner: bool, in_scope=None, batch_ok=None,
           now_utc: Optional[datetime] = None, types: tuple[str, ...] = EVENT_TYPES) -> list[dict]:
    out: list[dict] = []
    for t in types:
        if t == "expiring_tomorrow":
            ev = expiring_tomorrow(tenant_id, now_utc=now_utc, in_scope=in_scope)
        elif t == "repeated_rejects":
            ev = repeated_rejects(tenant_id, now_utc=now_utc) if full_access else None
        elif t == "low_card_stock":
            ev = low_card_stock(tenant_id, batch_ok=batch_ok)
        elif t == "plan_without_offers":
            ev = plan_without_offers(tenant_id) if owner else None
        else:
            ev = None
        if ev:
            out.append(ev)
    return out


def issued_from_event(event: dict) -> dict[str, list]:
    """kind → values a proposal may reference for this event."""
    data = event.get("data") or {}
    t = event.get("type")
    if t == "expiring_tomorrow":
        return {"subscriber": [s["username"] for s in data.get("subscribers", [])],
                "plan": [s["plan_id"] for s in data.get("subscribers", []) if s.get("plan_id")]}
    if t in ("low_card_stock", "plan_without_offers"):
        return {"plan": [p["plan_id"] for p in data.get("plans", [])]}
    return {}


__all__ = ["EVENT_TYPES", "detect", "expiring_tomorrow", "repeated_rejects", "low_card_stock",
           "plan_without_offers", "issued_from_event"]
