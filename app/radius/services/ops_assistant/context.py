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

from . import catalog, store, units
from .dispatch import call

MAX_CHOICES = 10          # subscribers (catalog: per_page ≤ 10)
MAX_CATALOG_CHOICES = 50  # plans / offers — short catalogs; `query` narrows


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
    if action == "create_card_batch":
        from ....api.v1.cards import _manager_cardgen_denial
        return _manager_cardgen_denial("generate") is None
    return True


def effective_permissions() -> list[str]:
    keys = set()
    for action, (key, _ep, _m) in catalog.ACTION_PERMISSION.items():
        if action_permitted(action):
            keys.add(key)
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


def list_plans(conv: dict, query: str = "") -> dict:
    res = call("GET", "/plans/options")
    if not res.ok:
        raise _from_api(res)
    q = units.latin_digits(query or "").strip().lower()
    items = []
    for p in (res.data or {}).get("items", []):
        if q and q not in str(p.get("name") or "").lower():
            continue
        items.append({"id": int(p["id"]), "name": p.get("name") or "",
                      "price": p.get("price"), "currency": p.get("currency"),
                      "duration_minutes": p.get("duration_minutes"),
                      "period_minutes": p.get("period_minutes"),
                      "plan_type": p.get("plan_type")})
    truncated = len(items) > MAX_CATALOG_CHOICES
    items = _numbered(items, MAX_CATALOG_CHOICES)
    store.issue(conv["id"], conv["tenant_id"], "plan", [i["id"] for i in items], "list_plans")
    return {"source": "list_plans", "items": items, "truncated": truncated}


def list_offers(conv: dict, query: str = "") -> dict:
    res = call("GET", "/cards/offers")
    if not res.ok:
        raise _from_api(res)
    q = units.latin_digits(query or "").strip().lower()
    items = []
    for o in (res.data or {}).get("items", []):
        if q and q not in str(o.get("name") or "").lower():
            continue
        row = {"id": int(o["id"]), "name": o.get("name") or "", "plan_id": o.get("plan_id"),
               "duration_minutes": o.get("duration_minutes"), "selling": o.get("selling"),
               "currency": o.get("currency")}
        if o.get("wholesale") is not None:
            row["wholesale"] = o.get("wholesale")
        items.append(row)
    truncated = len(items) > MAX_CATALOG_CHOICES
    items = _numbered(items, MAX_CATALOG_CHOICES)
    store.issue(conv["id"], conv["tenant_id"], "offer", [i["id"] for i in items], "list_offers")
    return {"source": "list_offers", "items": items, "truncated": truncated}


_SUB_FIELDS = ("username", "full_name", "mobile", "status", "plan_id", "expire_at",
               "online")


def find_subscriber(conv: dict, query: str = "", status: str = "") -> dict:
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
           "plan_direction", "tool_message", "ChoiceError", "MAX_CHOICES"]
