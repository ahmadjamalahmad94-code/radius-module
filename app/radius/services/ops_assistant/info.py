"""Read-only INFO actions of the operations assistant (catalog ops-v2,
SPEC_DATA_v3 §4): ``card_batch_status``, ``subscriber_info``,
``online_sessions``; ops-v3 (SPEC_DATA_v4_DRAFT): ``recent_subscribers``,
``recent_card_batches``, ``recent_activity`` (default 5, max 20), ``card_info``
(ONE card by its number, through the web card checker's API).

Each one is a GET through the REAL /api/v1 handler with the logged-in
admin's own credential (``dispatch.call``) — the same RBAC, tenant and
distributor/subscriber scope as the mobile app. The answer for the model is a
``RESULT {"source": <action>, "data": {...}}`` tool line built from a
WHITELIST of keys, so passwords, PPPoE secrets, card codes, tokens and payment
rows can never leak by accident; the balance is passed only when the API
itself shows it (the «رؤية الرصيد» right). Usernames / batch ids in a RESULT
are issued to the conversation like CHOICES ids.

Errors never raise to the model loop: ``{"source": ..., "error": <code>}``
(``not_found`` / ``out_of_scope`` / ``missing_permission`` / ``unavailable``).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import store
from .context import batch_status, local_str
from .dispatch import call

MAX_ONLINE_ITEMS = 10


def _err_code(res) -> str:
    code = str((res.error or {}).get("code") or "")
    if res.status == 404 or code == "not_found":
        return "not_found"
    if res.status == 403:
        details = (res.error or {}).get("details") or {}
        reason = str(details.get("reason") or "") if isinstance(details, dict) else ""
        return "out_of_scope" if "scope" in (code + reason) else "missing_permission"
    return "unavailable"


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _plan_name(tenant_id: int, plan_id: Any) -> Optional[str]:
    if not plan_id:
        return None
    from ...db.connection import db
    try:
        row = db().execute("SELECT name FROM access_plans WHERE tenant_id=? AND id=?",
                           (int(tenant_id), int(plan_id))).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return (row["name"] or None) if row else None


# ─────────────────────────── card_batch_status ────────────────────────────

def card_batch_status(conv: dict, fields: dict) -> dict:
    tid = int(conv["tenant_id"])
    bid = int(fields["batch_id"])
    summ = call("GET", f"/cards/batches/{bid}/summary")
    if not summ.ok:
        return {"source": "card_batch_status", "error": _err_code(summ)}
    s = (summ.data or {}).get("summary") or {}
    meta = call("GET", f"/cards/batches/{bid}")
    b = meta.data if meta.ok and isinstance(meta.data, dict) else {}
    name = str(b.get("package_name") or "").strip() or str(s.get("batch_code") or "")
    counts = {k: _int(s.get(k)) for k in ("total_cards", "available_count", "active_count",
                                          "expired_count", "revoked_count", "archived_count")}
    status = str(s.get("operational_status") or "") or batch_status({**b, **counts})
    data = {"batch_id": bid, "name": name, "code": s.get("batch_code") or b.get("batch_code"),
            "plan_name": _plan_name(tid, s.get("plan_id") or b.get("plan_id")),
            "status": status, "created_local": local_str(b.get("created_at"), tid), **counts}
    return {"source": "card_batch_status",
            "data": {k: v for k, v in data.items() if v is not None}}


# ─────────────────────────── card_info ────────────────────────────

MAX_CARD_LEN = 64

# card checker status → the assistant's vocabulary
_CARD_STATUS = {"available": "unused", "active": "active", "expired": "expired",
                "revoked": "disabled"}


def card_number(raw: Any) -> str:
    """What the admin typed → the card username the checker matches: Arabic-Indic
    digits read as Latin, every space removed (printed cards are grouped «5503 9046»)."""
    from .units import latin_digits
    return "".join(latin_digits(str(raw or "")).split())[:MAX_CARD_LEN]


def _duration_text(seconds: Any) -> Optional[str]:
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return None
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m else ""]
    return " ".join(p for p in parts if p) or "0m"


def card_info(conv: dict, fields: dict) -> dict:
    """ONE card by its number — GET /cards/check, the SAME handler as the web
    «فحص البطاقة» (``card_checker.check_card``: tenant-bound, the username
    wins, read-only — it never calls the policy engine, so ``first_used_at`` /
    the card window are never stamped) + its batch scope (``batch_in_scope``,
    403 otherwise). Whitelisted: never the password / PIN, MACs, IPs or the
    assigned subscriber's phone. Not found ⇒ ``not_found``, never a «did you
    mean» (card numbers are not suggested approximately)."""
    tid = int(conv["tenant_id"])
    number = card_number(fields.get("card"))
    if not number:
        return {"source": "card_info", "error": "not_found"}
    res = call("GET", "/cards/check", query={"query": number})
    if not res.ok:
        return {"source": "card_info", "error": _err_code(res)}
    c = (res.data or {}).get("card") or {}
    if not isinstance(c, dict) or not c.get("exists"):
        return {"source": "card_info", "error": "not_found", "data": {"card": number}}
    b = c.get("batch") if isinstance(c.get("batch"), dict) else {}
    p = c.get("profile") if isinstance(c.get("profile"), dict) else {}
    summ = c.get("accounting_summary") if isinstance(c.get("accounting_summary"), dict) else {}
    status = _CARD_STATUS.get(str(c.get("status") or ""), str(c.get("status") or "") or None)
    if b.get("deleted_at"):
        status = "deleted"
    bid = _int(b.get("id")) or None
    remaining = c.get("remaining_seconds")
    budget = _int(c.get("accounting_budget_seconds"))
    price = b.get("unit_price")
    data: dict[str, Any] = {
        "card": c.get("username") or number,
        "status": status,
        "batch_id": bid,
        "batch_name": (str(b.get("package_name") or "").strip() or b.get("batch_code") or None)
        if bid else None,
        "plan_name": p.get("name") or None,
        "first_login_local": local_str(c.get("started_at"), tid),
        "expires_local": local_str(c.get("expires_at"), tid),
        "remaining": _duration_text(remaining) if remaining is not None else None,
        "card_time": _duration_text(budget) if budget > 0 else None,
        "counting": c.get("accounting_mode") or None,
        "used_time": _duration_text(c.get("used_session_seconds"))
        if c.get("started_at") else None,
        "sessions": _int(summ.get("sessions_count")) or None,
        "online_now": _int(summ.get("online_sessions")),
        "devices_used": _int(summ.get("unique_macs")) or None,
        "last_seen_local": local_str(c.get("last_seen_at"), tid),
        "price": round(float(price), 2) if isinstance(price, (int, float)) and price > 0 else None,
    }
    quota = _int(p.get("quota_total_mb"))
    if quota > 0:
        from .units import format_quota
        data["quota"] = format_quota(quota)
    if data["price"] is not None:
        from ...core.system_config import default_currency
        data["currency"] = (str(p.get("currency") or "") or default_currency() or "ILS").strip().upper()
    out = {k: v for k, v in data.items() if v is not None}
    if bid:
        # the admin may follow up with «وضع حزمتها» → card_batch_status
        store.issue(conv["id"], tid, "batch", [bid], "card_info")
    return {"source": "card_info", "data": out}


# ─────────────────────────── subscriber_info ────────────────────────────

def subscriber_info(conv: dict, fields: dict) -> dict:
    tid = int(conv["tenant_id"])
    username = str(fields["username"])
    full = call("GET", f"/accounts/{username}/360")
    if not full.ok:
        return {"source": "subscriber_info", "error": _err_code(full)}
    d = full.data or {}
    sub = d.get("subscriber") if isinstance(d.get("subscriber"), dict) else {}
    plan = d.get("plan") if isinstance(d.get("plan"), dict) else {}
    overview = d.get("overview") if isinstance(d.get("overview"), dict) else {}
    usage = call("GET", f"/accounts/{username}/usage")
    u = usage.data if usage.ok and isinstance(usage.data, dict) else {}
    online_n = _int(u.get("online_count"))
    data: dict[str, Any] = {
        "username": sub.get("username") or username,
        "full_name": sub.get("full_name") or None,
        "plan": plan.get("name") or _plan_name(tid, sub.get("plan_id")),
        "status": _derived_status(sub),
        "expires_local": local_str(sub.get("expire_at"), tid) if sub.get("expire_at") else None,
        "no_expiry": True if not sub.get("expire_at") else None,
        "online": online_n > 0,
        "online_sessions": online_n,
        "last_seen_local": local_str(u.get("last_seen_at"), tid),
    }
    # money: only what the API itself shows to THIS admin (balance_hidden otherwise)
    if sub.get("balance_hidden") or sub.get("balance") is None:
        data["balance_hidden"] = True
    else:
        data["balance"] = sub.get("balance")
        if overview.get("open_debt") is not None:
            data["open_debt"] = overview.get("open_debt")
        from ...core.system_config import default_currency
        data["currency"] = (plan.get("currency") or default_currency() or "ILS").strip().upper()
    out = {k: v for k, v in data.items() if v is not None}
    store.issue(conv["id"], conv["tenant_id"], "subscriber", [out["username"]], "subscriber_info")
    return {"source": "subscriber_info", "data": out}


def _derived_status(sub: dict) -> Optional[str]:
    """``expired`` is derived from expire_at, never stored (owner rule)."""
    st = str(sub.get("status") or "").strip().lower() or None
    exp = sub.get("expire_at")
    if st in (None, "enabled", "active") and exp:
        from datetime import datetime
        try:
            when = datetime.fromisoformat(str(exp).replace(" ", "T").rstrip("Z")[:19])
            if when < datetime.utcnow():
                return "expired"
        except ValueError:
            pass
        return "active"
    if st == "enabled":
        return "active"
    return st


# ─────────────────────────── online_sessions ────────────────────────────

def online_sessions(conv: dict, fields: dict) -> dict:
    tid = int(conv["tenant_id"])
    q = str(fields.get("query") or "").strip()[:80]
    res = call("GET", "/sessions/online", query={"q": q or None, "limit": MAX_ONLINE_ITEMS})
    if not res.ok:
        return {"source": "online_sessions", "error": _err_code(res)}
    d = res.data or {}
    types = d.get("types") if isinstance(d.get("types"), dict) else {}
    items = []
    for n, it in enumerate((d.get("items") or [])[:MAX_ONLINE_ITEMS], start=1):
        row = {"n": n, "username": it.get("username"),
               "user_type": it.get("user_type") or "subscriber",
               "started_local": local_str(it.get("started_at"), tid)}
        items.append({k: v for k, v in row.items() if v is not None})
    total = _int(d.get("total"))
    data = {"query": q, "total": total, "subscribers": _int(types.get("subscriber")),
            "cards": _int(types.get("card")), "items": items,
            "truncated": total > len(items)}
    store.issue(conv["id"], conv["tenant_id"], "subscriber",
                [i["username"] for i in items
                 if i.get("username") and i.get("user_type") == "subscriber"], "online_sessions")
    return {"source": "online_sessions", "data": data}


# ─────────────────────────── «latest records» (ops-v3) ────────────────────────────

DEFAULT_RECENT = 5
MAX_RECENT = 20


def _recent_limit(fields: dict) -> int:
    try:
        n = int(fields.get("limit") or DEFAULT_RECENT)
    except (TypeError, ValueError):
        n = DEFAULT_RECENT
    return max(1, min(n, MAX_RECENT))


def _admin_id() -> int:
    from flask import g
    return int(getattr(g, "admin_id", 0) or 0)


def _admin_login(aid: int) -> tuple[str, str]:
    from ...db.connection import db
    row = db().execute("SELECT username, full_name FROM admins WHERE id=?", (int(aid),)).fetchone()
    return (str(row["username"] or ""), str(row["full_name"] or "")) if row else ("", "")


def _my_actor_tags(aid: int, tid: int) -> set[str]:
    """How THIS admin appears as ``created_by`` / audit ``actor``: his login,
    his display name when no other admin shares it (the web stores display
    names), and ``api-token:<id>`` for the tokens bound to him (app / ops)."""
    from ...db.connection import db
    login, display = _admin_login(aid)
    tags = {login} if login else set()
    if display and display != login:
        same = db().execute("SELECT COUNT(*) FROM admins WHERE full_name=? OR username=?",
                            (display, display)).fetchone()[0]
        if int(same or 0) == 1:
            tags.add(display)
    try:
        for r in db().execute("SELECT id FROM api_tokens WHERE tenant_id=? AND created_by=?",
                              (int(tid), int(aid))):
            tags.add(f"api-token:{r[0]}")
    except Exception:  # noqa: BLE001 — tokens are an extra, never a reason to fail
        pass
    return tags


def _subscriber_item(n: int, s: dict, tid: int) -> dict:
    row = {"n": n, "username": s.get("username"), "full_name": s.get("full_name") or None,
           "plan": _plan_name(tid, s.get("plan_id")), "status": _derived_status(s),
           "created_local": local_str(s.get("created_at"), tid),
           "expires_local": local_str(s.get("expire_at"), tid) if s.get("expire_at") else None}
    return {k: v for k, v in row.items() if v is not None}


def recent_subscribers(conv: dict, fields: dict) -> dict:
    """The newest subscribers the admin may list (GET /accounts is ORDER BY id
    DESC). ``mine``: those this admin is responsible for (manager_id = him or
    his distributors' — the repo's owner-scope predicate), each re-read
    through GET /accounts/<username> (the API's own scope check)."""
    tid = int(conv["tenant_id"])
    n = _recent_limit(fields)
    mine = fields.get("mine") is True
    if mine:
        from urllib.parse import quote

        from ...db.repos import subscribers_repo
        aid = _admin_id()
        subs = subscribers_repo.list_subscribers(tid, user_type="subscriber", owner_admin_id=aid,
                                                 limit=n, order_by="id", order_dir="desc")
        total = subscribers_repo.count_subscribers(tid, user_type="subscriber",
                                                   owner_admin_id=aid)
        rows = []
        for s in subs:
            r = call("GET", "/accounts/" + quote(str(s.username), safe=""))
            if r.ok and isinstance(r.data, dict):
                rows.append(r.data)
    else:
        res = call("GET", "/accounts", query={"per_page": n, "page": 1})
        if not res.ok:
            return {"source": "recent_subscribers", "error": _err_code(res)}
        rows = list((res.data or {}).get("items") or [])[:n]
        total = _int((res.data or {}).get("total"))
    items = [_subscriber_item(i, s, tid) for i, s in enumerate(rows, start=1)]
    store.issue(conv["id"], tid, "subscriber", [i["username"] for i in items if i.get("username")],
                "recent_subscribers")
    return {"source": "recent_subscribers",
            "data": {"mine": mine, "total": total, "items": items,
                     "truncated": total > len(items)}}


def recent_card_batches(conv: dict, fields: dict) -> dict:
    """The newest card batches in the admin's scope (GET /cards/batches is
    ORDER BY id DESC) — never the cards. ``mine``: created by this admin
    (created_by = his login / display name / one of his tokens, or
    manager_id = him) among the latest 50."""
    from .context import batch_item
    tid = int(conv["tenant_id"])
    n = _recent_limit(fields)
    mine = fields.get("mine") is True
    res = call("GET", "/cards/batches", query={"per_page": 50 if mine else n, "page": 1})
    if not res.ok:
        return {"source": "recent_card_batches", "error": _err_code(res)}
    rows = list((res.data or {}).get("items") or [])
    total = _int((res.data or {}).get("total"))
    if mine:
        aid = _admin_id()
        tags = _my_actor_tags(aid, tid)
        rows = [b for b in rows if str(b.get("created_by") or "") in tags
                or _int(b.get("manager_id")) == aid]
        total = len(rows)
    items = []
    for b in rows[:n]:
        it = batch_item(b, tid)
        if it:
            items.append({"n": len(items) + 1, **it})
    store.issue(conv["id"], tid, "batch", [i["id"] for i in items], "recent_card_batches")
    return {"source": "recent_card_batches",
            "data": {"mine": mine, "total": total, "items": items,
                     "truncated": total > len(items)}}


_ACTIVITY_TARGETS = ("subscriber", "card_batch", "batch", "plan", "offer", "card_offer",
                     "nas", "router", "manager", "admin", "distributor")


def recent_activity(conv: dict, fields: dict) -> dict:
    """The admin's OWN latest actions in the panel (audit_log): his login as
    actor — or his display name when no other admin shares it (the web
    interceptor stores display names) — page visits and the assistant's own
    rows excluded. Only the action label, the target name and the outcome;
    never payload values."""
    import json as _json

    from ...db.connection import db
    from ...services.audit_format import action_label
    from app.i18n_text import _tr
    tid = int(conv["tenant_id"])
    n = _recent_limit(fields)
    aid = _admin_id()
    login, _display = _admin_login(aid)
    if not login:
        return {"source": "recent_activity", "error": "unavailable"}
    actors = sorted(_my_actor_tags(aid, tid))
    marks = ",".join("?" * len(actors))
    rows = db().execute(
        "SELECT created_at, action, target_type, target_id, payload_json, outcome, result_status "
        f"FROM audit_log WHERE tenant_id=? AND actor IN ({marks}) AND COALESCE(is_visit,0)=0 "
        "AND target_type != 'ops_assistant' AND action NOT LIKE 'ops.%' "
        "ORDER BY id DESC LIMIT ?", (tid, *actors, n * 4)).fetchall()
    items = []
    for r in rows:
        try:
            payload = _json.loads(r["payload_json"] or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("login") and str(payload.get("login")) != login:
            continue                      # another admin with the same display name
        label = payload.get("action_ar") or action_label(r["action"])
        target = payload.get("entity_name") or (
            r["target_id"] if str(r["target_type"] or "") in _ACTIVITY_TARGETS else None)
        row = {"n": len(items) + 1, "when_local": local_str(r["created_at"], tid),
               "action": _tr(str(label))[:120], "target": str(target)[:80] if target else None,
               "outcome": (r["outcome"] or r["result_status"] or "success")[:20]}
        items.append({k: v for k, v in row.items() if v is not None})
        if len(items) >= n:
            break
    return {"source": "recent_activity", "data": {"items": items}}


_RUNNERS = {"card_batch_status": card_batch_status, "subscriber_info": subscriber_info,
            "online_sessions": online_sessions, "recent_subscribers": recent_subscribers,
            "recent_card_batches": recent_card_batches, "recent_activity": recent_activity,
            "card_info": card_info}


def run(conv: dict, action: str, fields: dict) -> dict:
    try:
        return _RUNNERS[action](conv, fields)
    except Exception:  # noqa: BLE001 — a read must never break the conversation
        import logging
        logging.getLogger(__name__).warning("ops info %s failed", action, exc_info=True)
        return {"source": action, "error": "unavailable"}


def result_line(result: dict) -> str:
    return "RESULT " + json.dumps(result, ensure_ascii=False)


__all__ = ["run", "result_line", "card_batch_status", "subscriber_info", "online_sessions",
           "recent_subscribers", "recent_card_batches", "recent_activity", "card_info",
           "card_number"]
