"""CONTEXT (second system message) and CHOICES lists for the model.

Both are built by the executor from the CURRENT credential — never by the
model: tenant from the token, the admin's role and effective permissions from
the existing RBAC (the same ``permission_guard.decide`` the API runs), CHOICES
through the real /api/v1 list endpoints (tenant + distributor scoping exactly
like the mobile app). Every id / username shown is recorded as *issued* for
the conversation; proposals may only reference issued values.
"""
from __future__ import annotations

from typing import Any, Optional

from flask import g

from . import catalog, fuzzy, store, units
from .dispatch import call

MAX_CHOICES = 10          # subscribers (catalog: per_page ≤ 10)
MAX_CATALOG_CHOICES = 50  # plans / offers — short catalogs; `query` narrows
MAX_SUGGESTIONS = fuzzy.MAX_CANDIDATES   # «هل تقصد…؟» rows (never issued)


# ─────────────────────────── identity ────────────────────────────

def admin_id() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def tenant_id() -> int:
    return int(getattr(g, "tenant_id", 1) or 1)


def _admin():
    from ....api.access_control import token_admin
    return token_admin()


def is_owner() -> bool:
    adm = _admin()
    if adm is None:
        return False
    from ...auth.owner import is_owner_like
    return bool(is_owner_like(adm))


def role() -> str:
    from ....api.access_control import current_distributor
    if is_owner():
        return "owner"
    if current_distributor():
        return "distributor"
    return "manager"


def action_permitted(action: str, fields: Optional[dict] = None) -> bool:
    """Would the API let this admin run ``action``? Same decision as the
    central guard (+ the in-handler checks of offers / direct generation)."""
    if action in catalog.CONTROL_ACTIONS:
        return True
    adm = _admin()
    if adm is None or not getattr(adm, "enabled", False):
        return False
    if action in catalog.OWN_ROWS_ACTIONS:
        return True                       # the admin's OWN rows only (info.recent_activity)
    if is_owner():
        return True
    if action == "list_offers":
        return True                       # visibility filtered in the handler
    if action == "create_offer":
        from ....api.access_control import token_bypasses_rbac
        from ...services import manager_grants
        return bool(token_bypasses_rbac() or manager_grants.action_allowed(
            admin_id(), "offer", "create", tenant_id=tenant_id()))
    spec = catalog.ACTION_PERMISSION.get(action)
    if not spec:
        return False
    _key, endpoint, method = spec
    if action == "temporary_speed" and (fields or {}).get("operation") == "cancel":
        endpoint = "v1.accounts_action_temp_speed_cancel"
    from ....api.permission_guard import decide
    try:
        if decide(endpoint, method, adm, tenant_id=tenant_id()) is not None:
            return False
    except Exception:  # noqa: BLE001 — never grant on an error
        return False
    if action == "create_card_batch" and str((fields or {}).get("source") or "plan") == "plan":
        # a plan-source batch = direct generation (owner, or the explicit grant)
        return direct_generation_permitted()
    return True


def direct_generation_permitted() -> bool:
    """``cards.generate_direct``: the in-handler guard of /cards/generate on
    top of the role key (a manager without the grant generates from offers)."""
    if is_owner():
        return True
    from ....api.v1.cards import _manager_cardgen_denial
    try:
        return _manager_cardgen_denial("generate") is None
    except Exception:  # noqa: BLE001 — never grant on an error
        return False


def effective_permissions() -> list[str]:
    """The CONTEXT permission keys (SPEC_DATA_v3 §11 vocabulary)."""
    keys = set()
    for action, (key, _ep, _m) in catalog.ACTION_PERMISSION.items():
        probe = {"source": "offer"} if action == "create_card_batch" else None
        if action_permitted(action, probe):
            keys.add(key)
    if "cards.generate" in keys and direct_generation_permitted():
        keys.add(catalog.DIRECT_GENERATION_KEY)
    return sorted(keys)


def build_context(event: Optional[dict] = None) -> dict:
    from ...core.system_config import default_currency
    tid = tenant_id()
    return {
        "today_local": units.local_today(tid).isoformat(),
        "tz": units.tz_name(tid),
        "currency": (default_currency() or "ILS").strip().upper(),
        "admin": {"role": role(), "permissions": effective_permissions()},
        "event": event,
    }


# ─────────────────────────── CHOICES ────────────────────────────

class ChoiceError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _from_api(res) -> ChoiceError:
    err = res.error
    return ChoiceError(res.status, err.get("code") or "api_error", err.get("message") or "",
                       err.get("details"))


def _numbered(items: list[dict], limit: int = MAX_CHOICES) -> list[dict]:
    return [{"n": i, **it} for i, it in enumerate(items[:limit], start=1)]


def suggestions(source: str, query: str, items: list[dict]) -> dict:
    """«هل تقصد…؟» CHOICES: ``match: "fuzzy"``. The ids are deliberately NOT
    issued — nothing can be executed on a suggestion until the admin picks it
    (``pick`` → an exact lookup that issues the one record he chose)."""
    return {"source": source, "match": "fuzzy", "query": str(query or "")[:100],
            "items": _numbered(items, MAX_SUGGESTIONS), "truncated": False}


def _pick_id(pick: Any) -> Optional[int]:
    try:
        return int(pick) if pick not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _catalog_rows(all_items: list[dict], query: str, pick: Any) -> tuple[list[dict], bool]:
    """(rows, fuzzy?) — exact pick by id, else «contains» on the name, else the
    similar names (Arabic folding / typos) as suggestions."""
    pid = _pick_id(pick)
    if pid is not None:
        return [it for it in all_items if it["id"] == pid], False
    q = units.latin_digits(query or "").strip().lower()
    if not q:
        return all_items, False
    rows = [it for it in all_items if q in str(it.get("name") or "").lower()]
    if rows:
        return rows, False
    cands = fuzzy.catalog_candidates(query, all_items)
    return [all_items[c.key] for c in cands], bool(cands)


def list_plans(conv: dict, query: str = "", pick: Any = None) -> dict:
    res = call("GET", "/plans/options")
    if not res.ok:
        raise _from_api(res)
    all_items = []
    for p in (res.data or {}).get("items", []):
        all_items.append({"id": int(p["id"]), "name": p.get("name") or "",
                          "price": p.get("price"), "currency": p.get("currency"),
                          "duration_minutes": p.get("duration_minutes"),
                          "period_minutes": p.get("period_minutes"),
                          "plan_type": p.get("plan_type")})
    items, is_fuzzy = _catalog_rows(all_items, query, pick)
    if is_fuzzy:
        return suggestions("list_plans", query, items)
    truncated = len(items) > MAX_CATALOG_CHOICES
    items = _numbered(items, MAX_CATALOG_CHOICES)
    store.issue(conv["id"], conv["tenant_id"], "plan", [i["id"] for i in items], "list_plans")
    return {"source": "list_plans", "items": items, "truncated": truncated}


def list_offers(conv: dict, query: str = "", pick: Any = None) -> dict:
    res = call("GET", "/cards/offers")
    if not res.ok:
        raise _from_api(res)
    all_items = []
    for o in (res.data or {}).get("items", []):
        row = {"id": int(o["id"]), "name": o.get("name") or "", "plan_id": o.get("plan_id"),
               "duration_minutes": o.get("duration_minutes"), "selling": o.get("selling"),
               "currency": o.get("currency")}
        if o.get("wholesale") is not None:
            row["wholesale"] = o.get("wholesale")
        all_items.append(row)
    items, is_fuzzy = _catalog_rows(all_items, query, pick)
    if is_fuzzy:
        return suggestions("list_offers", query, items)
    truncated = len(items) > MAX_CATALOG_CHOICES
    items = _numbered(items, MAX_CATALOG_CHOICES)
    store.issue(conv["id"], conv["tenant_id"], "offer", [i["id"] for i in items], "list_offers")
    return {"source": "list_offers", "items": items, "truncated": truncated}


def local_str(value: Any, tid: int) -> Optional[str]:
    """API UTC timestamp → tenant-zone (Asia/Gaza) wall clock ``YYYY-MM-DDTHH:MM``."""
    if not value:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(value).replace(" ", "T").rstrip("Z")[:19])
        return units.to_local(dt, tid).strftime("%Y-%m-%dT%H:%M")
    except Exception:  # noqa: BLE001
        return None


def batch_status(row: dict) -> str:
    """Operational status of a batch row (the repo's own rule: deleted /
    cancelled / revoked as stored, exhausted when no unused card is left)."""
    status = str(row.get("status") or "active").strip().lower() or "active"
    if row.get("deleted_at"):
        return "deleted"
    if status in {"deleted", "cancelled", "canceled", "revoked", "archived"}:
        return status
    if int(row.get("total_cards") or 0) and int(row.get("available_count") or 0) == 0:
        return "exhausted"
    return status


def batch_item(b: dict, tid: int) -> Optional[dict]:
    """One /cards/batches row → the CHOICES item (never the cards / codes)."""
    try:
        bid = int(b.get("id"))
    except (TypeError, ValueError):
        return None
    name = str(b.get("package_name") or "").strip() or str(b.get("batch_code") or "")
    row = {"id": bid, "name": name, "code": b.get("batch_code"),
           "origin": b.get("source_type") or "generated", "plan_name": b.get("plan_name"),
           "created_local": local_str(b.get("created_at"), tid),
           "total_cards": int(b.get("total_cards") or 0),
           "available_count": int(b.get("available_count") or 0),
           "status": batch_status(b)}
    return {k: v for k, v in row.items() if v is not None}


def _batch_by_code(code: str, batch_id: Optional[int] = None) -> list[dict]:
    """Exact code lookup through the API (its own batch scope check)."""
    if not code:
        return []
    res = call("GET", "/cards/batches", query={"code": code})
    if not res.ok:
        return []
    rows = list((res.data or {}).get("items", []) or [])
    if batch_id is not None:
        rows = [b for b in rows if str(b.get("id")) == str(batch_id)]
    return rows


def _batch_scope() -> Optional[int]:
    """The SAME scope as GET /cards/batches (None = every batch)."""
    from ....api.access_control import is_owner_level
    if is_owner_level():
        return None
    from ...services.card_batch_scope import batch_scope_admin_id
    return batch_scope_admin_id(admin_id() or None, tenant_id=tenant_id())


def _batch_code_of(batch_id: int) -> str:
    from ...db.connection import db
    row = db().execute("SELECT batch_code FROM card_batches WHERE tenant_id=? AND id=?",
                       (tenant_id(), int(batch_id))).fetchone()
    return str(row[0] or "") if row else ""


def _batch_of_card(number: str) -> Optional[int]:
    """The batch of ONE card whose number the admin typed EXACTLY (Arabic
    digits folded). Card numbers are never suggested fuzzily: near-miss
    suggestions would let anyone discover valid voucher codes."""
    from ...db.connection import db
    num = fuzzy.latin(number).strip()
    if not num or len(num) > 64:
        return None
    row = db().execute("SELECT batch_id FROM cards WHERE tenant_id=? AND username=? "
                       "AND batch_id IS NOT NULL LIMIT 1", (tenant_id(), num)).fetchone()
    return int(row[0]) if row and row[0] else None


def list_card_batches(conv: dict, query: str = "", limit: Any = None, pick: Any = None) -> dict:
    """Card batches (حزم البطاقات) through GET /cards/batches in the admin's
    scope — never the cards themselves. A query matching nothing is retried
    as an exact batch code, then as an exact CARD number (→ its batch,
    ``match: "card"``), then as similar batch names/codes (``match: "fuzzy"``,
    not issued)."""
    try:
        n = max(1, min(int(limit or MAX_CHOICES), MAX_CHOICES))
    except (TypeError, ValueError):
        n = MAX_CHOICES
    tid = int(conv["tenant_id"])
    pid = _pick_id(pick)
    if pid is not None:
        rows = _batch_by_code(_batch_code_of(pid), pid)
        items = _numbered([i for i in (batch_item(b, tid) for b in rows) if i], 1)
        store.issue(conv["id"], tid, "batch", [i["id"] for i in items], "pick")
        return {"source": "list_card_batches", "items": items, "truncated": False}
    q = units.latin_digits(query or "").strip()[:100]
    res = call("GET", "/cards/batches", query={"q": q or None, "per_page": 10, "page": 1})
    if not res.ok:
        raise _from_api(res)
    rows = list((res.data or {}).get("items", []) or [])
    total = int((res.data or {}).get("total") or len(rows))
    match = None
    if q and not rows:
        rows = _batch_by_code(q)
        total = len(rows)
    if q and not rows:
        bid = _batch_of_card(q)
        if bid is not None:
            rows = _batch_by_code(_batch_code_of(bid), bid)
            total, match = len(rows), ("card" if rows else None)
    if q and not rows:
        sugg = []
        for c in fuzzy.batch_candidates(tid, _batch_scope(), q):
            for b in _batch_by_code(_batch_code_of(int(c.key)), int(c.key)):
                it = batch_item(b, tid)
                if it:
                    sugg.append(it)
        if sugg:
            return suggestions("list_card_batches", q, sugg)
    items = _numbered([i for i in (batch_item(b, tid) for b in rows) if i], n)
    store.issue(conv["id"], conv["tenant_id"], "batch", [i["id"] for i in items],
                "list_card_batches")
    out = {"source": "list_card_batches", "items": items, "truncated": total > len(items)}
    if match:
        out["match"] = match
    return out


_SUB_FIELDS = ("username", "full_name", "mobile", "status", "plan_id", "expire_at",
               "online")


def _subscriber_row(username: str) -> Optional[dict]:
    """ONE subscriber through GET /accounts/<username> — the API's own
    users.view + scope check — reduced to the CHOICES whitelist."""
    from urllib.parse import quote
    res = call("GET", "/accounts/" + quote(str(username), safe=""))
    if not res.ok or not isinstance(res.data, dict):
        return None
    s = res.data
    row = {k: s.get(k) for k in _SUB_FIELDS if k in s}
    row["created_at"] = s.get("created_at")
    return row


def _subscriber_suggestions(conv: dict, q: str) -> Optional[dict]:
    """The fuzzy fallback of ``find_subscriber`` (exact search found nothing)."""
    from ....api.access_control import subscriber_scope_admin_id
    tid = int(conv["tenant_id"])
    match, cands = fuzzy.subscriber_candidates(tid, subscriber_scope_admin_id(), q)
    rows = []
    for c in cands:
        row = _subscriber_row(str(c.key))
        if row is None:
            continue
        row["matched"] = c.matched
        if c.matched == "national_id":
            row["national_id_tail"] = fuzzy.mask_tail(c.extra.get("value"))
        if c.matched != "phone":
            row.pop("mobile", None)       # the phone is shown only when it IS the match
        rows.append(row)
    if not rows:
        return None
    if match == "normalized":
        # the very number the admin typed, written differently (+970, spaces, ٠٥٩…)
        items = _numbered(rows, MAX_SUGGESTIONS)
        store.issue(conv["id"], tid, "subscriber", [i["username"] for i in items],
                    "find_subscriber")
        return {"source": "find_subscriber", "items": items, "truncated": False}
    return suggestions("find_subscriber", q, rows)


def find_subscriber(conv: dict, query: str = "", status: str = "", pick: Any = None) -> dict:
    if pick not in (None, ""):
        row = _subscriber_row(str(pick))
        items = _numbered([row] if row else [], 1)
        store.issue(conv["id"], conv["tenant_id"], "subscriber",
                    [i["username"] for i in items], "pick")
        return {"source": "find_subscriber", "items": items, "truncated": False}
    q = units.latin_digits(query or "").strip()
    if not q:
        raise ChoiceError(422, "validation_error", "query required")
    res = call("GET", "/accounts", query={"q": q[:100], "status": status or None,
                                           "per_page": MAX_CHOICES, "page": 1})
    if not res.ok:
        raise _from_api(res)
    items = []
    for s in (res.data or {}).get("items", []):
        # never pass secrets/money to the model (balance, pppoe_password, …)
        items.append({k: s.get(k) for k in _SUB_FIELDS if k in s})
    total = int((res.data or {}).get("total") or len(items))
    if not items and not status:
        sugg = _subscriber_suggestions(conv, q)
        if sugg is not None:
            return sugg
    items = _numbered(items)
    store.issue(conv["id"], conv["tenant_id"], "subscriber",
                [i["username"] for i in items], "find_subscriber")
    return {"source": "find_subscriber", "items": items, "truncated": total > len(items)}


_POLICIES = {
    "lower": ("lower_compensate", "lower_keep_expiry"),
    "higher": ("higher_debt", "higher_reduce_days", "higher_keep_expiry"),
    "neutral": ("neutral_keep_expiry",),
}


def plan_direction(username: str, plan_id: int) -> str:
    """lower / higher / neutral by price PER MINUTE — the users service's own
    comparison (``plan_change_direction``)."""
    from ...db.repos import plans_repo, subscribers_repo
    from ...services.users import plan_change_direction
    tid = tenant_id()
    sub = subscribers_repo.get_subscriber(tid, username)
    old_plan = None
    if sub is not None and getattr(sub, "plan_id", None):
        try:
            old_plan = plans_repo.get_plan(tid, int(sub.plan_id), include_deleted=True)
        except Exception:  # noqa: BLE001
            old_plan = None
    new_plan = plans_repo.get_plan(tid, int(plan_id))
    if new_plan is None:
        raise ChoiceError(404, "not_found", "plan not found")
    return plan_change_direction(old_plan, new_plan)


def change_plan_policies(conv: dict, username: str, plan_id: Any) -> dict:
    if not store.is_issued(conv["id"], conv["tenant_id"], "subscriber", username) or \
            not store.is_issued(conv["id"], conv["tenant_id"], "plan", plan_id):
        raise ChoiceError(422, "invented_id",
                          "username and plan_id must come from CHOICES of this conversation")
    direction = plan_direction(username, int(plan_id))
    items = _numbered([{"id": p, "name": p, "direction": direction}
                       for p in _POLICIES.get(direction, ())])
    return {"source": "change_plan_policies", "direction": direction, "items": items}


def tool_message(choices: dict) -> str:
    import json
    return "CHOICES " + json.dumps(choices, ensure_ascii=False)


__all__ = ["build_context", "effective_permissions", "action_permitted", "role", "is_owner",
           "list_plans", "list_offers", "find_subscriber", "change_plan_policies",
           "list_card_batches", "direct_generation_permitted", "local_str", "batch_status",
           "plan_direction", "tool_message", "ChoiceError", "MAX_CHOICES", "suggestions",
           "batch_item", "MAX_SUGGESTIONS"]
