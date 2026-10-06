"""Card OFFERS for the API (catalog open question Q2 — list + create).

Until now offers were web-only (``/admin/radius/cards/offers``). These two
endpoints reuse the web form's service (``CardOffersService``) and the web
handler's rules verbatim:

* ``GET  /api/v1/cards/offers`` — owner/co-owner (web «is_super_admin») sees
  every offer (``include_inactive=1`` honoured for him only); a manager sees
  only ACTIVE offers explicitly shared with him; wholesale / margin are hidden
  without «can_see_wholesale» / «can_see_profit» (same server-side projection
  as ``routes/cards._offers_page_context``).
* ``POST /api/v1/cards/offers`` — owner, or a manager holding the entity grant
  offer/create (``manager_grants.action_allowed``, web ``cards_offer_create``);
  plan required + in tenant, duration > 0, selling ≥ wholesale (service).

Tenant from the credential only (``g.tenant_id``). Both endpoints sit in
``permission_guard.API_AUTH_ONLY`` because — exactly like the web routes
(``blueprint._IN_HANDLER``) — their decision is taken in the handler.
"""
from __future__ import annotations
from app.i18n_text import _tr

from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/offers", "cards_offers_list",
                    require_api_token(cards_offers_list), methods=["GET"])
    bp.add_url_rule("/cards/offers", "cards_offers_create",
                    require_api_token(cards_offers_create), methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1) or 1)


def _aid() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def _is_super() -> bool:
    """Web ``is_super_admin()`` = ``_resolve_is_super(admin)`` at login; for a
    token that is ``token_bypasses_rbac`` (unbound credential → True)."""
    from ..access_control import token_bypasses_rbac
    return bool(token_bypasses_rbac())


def _svc():
    from ...radius.services.card_offers import CardOffersService
    return CardOffersService(tenant_id=_tid())


def _money(minor: Any):
    from ...radius.services.business_os_finance import minor_to_money
    try:
        return float(minor_to_money(minor))
    except (TypeError, ValueError):
        return None


def _serialize(o: dict, *, owner: bool, see_cost: bool, see_profit: bool) -> dict:
    out = {
        "id": int(o["id"]), "name": o.get("name") or "", "plan_id": o.get("plan_id"),
        "duration_minutes": int(o.get("duration_minutes") or 0),
        "selling": _money(o.get("selling_minor")),
        "wholesale": _money(o.get("wholesale_minor")) if see_cost else None,
        "margin": _money(o.get("margin_minor")) if see_profit else None,
        "currency": (o.get("currency") or "").strip().upper(),
        "active": bool(int(o.get("active") or 0)),
        "device_count": int(o.get("device_count") or 0),
        "device_limit_mode": o.get("device_limit_mode") or "",
        "equal_share_download": bool(int(o.get("equal_share_download") or 0)),
        "equal_share_upload": bool(int(o.get("equal_share_upload") or 0)),
        "notes": o.get("notes") or "",
    }
    if owner:
        out["visible_admin_ids"] = list(o.get("visible_admin_ids") or [])
    return out


def _visibility_flags(owner: bool) -> tuple[bool, bool]:
    if owner:
        return True, True
    from ...radius.services import manager_grants as mg
    return (bool(mg.can_see(_aid(), "can_see_wholesale", tenant_id=_tid())),
            bool(mg.can_see(_aid(), "can_see_profit", tenant_id=_tid())))


def cards_offers_list():
    owner = _is_super()
    include_inactive = owner and str(request.args.get("include_inactive") or "").lower() in {
        "1", "true", "yes", "on"}
    offers = _svc().list_offers(admin_id=_aid() or None, is_super=owner,
                                include_inactive=include_inactive)
    see_cost, see_profit = _visibility_flags(owner)
    items = [_serialize(o, owner=owner, see_cost=see_cost, see_profit=see_profit)
             for o in offers]
    return ok({"items": items, "count": len(items)})


def _duration_minutes(body: dict) -> int:
    if body.get("duration_minutes") not in (None, ""):
        return int(body["duration_minutes"])
    value = int(body.get("duration_value") or 0)
    unit = str(body.get("duration_unit") or "hours").strip().lower()
    return value * {"minutes": 1, "hours": 60, "days": 1440}.get(unit, 60)


def _bool(v: Any) -> bool:
    return v is True or str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def cards_offers_create():
    from ..json_input import json_object
    body, err = json_object()
    if err is not None:
        return err
    owner = _is_super()
    if not owner:
        from ...radius.services import manager_grants as mg
        if not mg.action_allowed(_aid() or None, "offer", "create", tenant_id=_tid()):
            return fail("forbidden",
                        _tr("لا تملك صلاحية إضافة العروض. اطلب من المالك منحها لك من صفحة صلاحيّاتك."),
                        status=403, details={"reason": "offer_create_grant"})
    from ...radius.services.card_offers import CardOfferError
    try:
        visible = body.get("visible_admin_ids") or []
        if not isinstance(visible, list):
            raise ValueError("visible_admin_ids")
        offer = _svc().create_offer(
            name=str(body.get("name") or ""),
            duration_minutes=_duration_minutes(body),
            wholesale=body.get("wholesale") or 0,
            selling=body.get("selling") or 0,
            plan_id=(int(body["plan_id"]) if str(body.get("plan_id") or "").strip().isdigit()
                     else None),
            currency=str(body.get("currency") or ""),
            notes=str(body.get("notes") or ""),
            active=True,
            created_by=_actor(),
            visible_admin_ids=[int(x) for x in visible if str(x).strip().isdigit()],
            device_limit_mode=str(body.get("device_limit_mode") or ""),
            device_count=int(body.get("device_count") or 0),
            equal_share_download=_bool(body.get("equal_share_download")),
            equal_share_upload=_bool(body.get("equal_share_upload")),
        )
    except CardOfferError as e:
        return fail("validation_error", str(e), status=422)
    except (TypeError, ValueError):
        return fail("validation_error", _tr("قيم العرض غير صحيحة."), status=422)
    try:
        from ...radius.services.audit import RadiusAuditService
        RadiusAuditService().record(actor=_actor(), action="offer.create", target_type="offer",
                                    target_id=str(offer["id"]),
                                    after=_svc().snapshot(offer))
    except Exception:  # noqa: BLE001 — auditing never breaks the create
        pass
    see_cost, see_profit = _visibility_flags(owner)
    return ok(_serialize(offer, owner=owner, see_cost=see_cost, see_profit=see_profit),
              status=201)


def _actor() -> str:
    try:
        from ..access_control import token_admin
        adm = token_admin()
        if adm is not None:
            return getattr(adm, "full_name", "") or getattr(adm, "username", "") or "api"
    except Exception:  # noqa: BLE001
        pass
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


__all__ = ["register"]
