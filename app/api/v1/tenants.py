from __future__ import annotations

from flask import Blueprint, request

from ...radius.core.errors import RadiusError
from ...radius.core.system_config import default_currency
from ...radius.core.tenant import (
    TIER_LIMITS,
    TENANT_STATUS_ACTIVE,
    TENANT_STATUS_CLOSED,
    TENANT_STATUS_SUSPENDED,
    TENANT_STATUS_TRIAL,
    TENANT_TIER_ENTERPRISE,
    TENANT_TIER_PRO,
    TENANT_TIER_STARTER,
    Tenant,
)
from ...radius.services.tenants import get_tenants_service
from ..auth import require_api_token
from ..responses import fail, ok


_STATUSES = {
    TENANT_STATUS_ACTIVE,
    TENANT_STATUS_TRIAL,
    TENANT_STATUS_SUSPENDED,
    TENANT_STATUS_CLOSED,
}
_TIERS = {
    TENANT_TIER_STARTER,
    TENANT_TIER_PRO,
    TENANT_TIER_ENTERPRISE,
}


def _actor() -> str:
    from flask import g

    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _serialize_tenant(t: Tenant) -> dict:
    return {
        "id": t.id,
        "slug": t.slug,
        "name": t.name,
        "display_name": t.display_name,
        "email": t.email,
        "phone": t.phone,
        "currency": t.currency,
        "locale": t.locale,
        "timezone": t.timezone,
        "logo_url": t.logo_url,
        "primary_color": t.primary_color,
        "status": t.status,
        "plan_tier": t.plan_tier,
        "max_subscribers": t.max_subscribers,
        "max_nas": t.max_nas,
        "api_rpm": t.api_rpm,
        "trial_ends_at": t.trial_ends_at.isoformat() + "Z" if t.trial_ends_at else None,
        "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
    }


def _int_value(body: dict, key: str, default: int = 0) -> int:
    raw = body.get(key, default)
    if raw in ("", None):
        return default
    return int(raw)


def _tenant_from_body(body: dict) -> Tenant:
    slug = str(body.get("slug") or "").strip().lower()
    name = str(body.get("name") or "").strip()
    if not slug or not name:
        raise ValueError("slug and name are required")
    tier = str(body.get("plan_tier") or TENANT_TIER_STARTER).strip()
    status = str(body.get("status") or TENANT_STATUS_ACTIVE).strip()
    if tier not in _TIERS:
        raise ValueError("unknown plan_tier")
    if status not in _STATUSES:
        raise ValueError("unknown status")
    return Tenant(
        id=None,
        slug=slug,
        name=name,
        display_name=str(body.get("display_name") or "").strip(),
        email=str(body.get("email") or "").strip(),
        phone=str(body.get("phone") or "").strip(),
        currency=str(body.get("currency") or default_currency()).strip(),
        locale=str(body.get("locale") or "ar").strip(),
        timezone=str(body.get("timezone") or "Asia/Amman").strip(),
        logo_url=str(body.get("logo_url") or "").strip(),
        primary_color=str(body.get("primary_color") or "#2BAACC").strip(),
        status=status,
        plan_tier=tier,
        max_subscribers=_int_value(body, "max_subscribers"),
        max_nas=_int_value(body, "max_nas"),
        api_rpm=_int_value(body, "api_rpm"),
    )


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/tenants",
        "tenants_list",
        require_api_token(tenants_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/tenants",
        "tenants_create",
        require_api_token(tenants_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tenants/<int:tenant_id>",
        "tenants_get",
        require_api_token(tenants_get),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/tenants/<int:tenant_id>",
        "tenants_patch",
        require_api_token(tenants_patch),
        methods=["PATCH"],
    )


def tenants_list():
    items = [_serialize_tenant(t) for t in get_tenants_service().list()]
    return ok({"items": items, "count": len(items), "tier_limits": TIER_LIMITS})


def tenants_get(tenant_id: int):
    item = get_tenants_service().get(tenant_id)
    if not item:
        return fail("not_found", "tenant not found", status=404)
    return ok(_serialize_tenant(item))


def tenants_create():
    body = request.get_json(silent=True) or {}
    try:
        tenant = _tenant_from_body(body)
        saved = get_tenants_service().create(actor=_actor(), tenant=tenant)
    except (ValueError, RadiusError) as exc:
        return fail("validation_error", str(getattr(exc, "message", exc)), status=422)
    except Exception as exc:  # noqa: BLE001
        return fail("conflict", str(exc), status=409)
    return ok(_serialize_tenant(saved), status=201)


def tenants_patch(tenant_id: int):
    if not get_tenants_service().get(tenant_id):
        return fail("not_found", "tenant not found", status=404)
    body = request.get_json(silent=True) or {}
    allowed = {
        "name",
        "display_name",
        "email",
        "phone",
        "currency",
        "locale",
        "timezone",
        "logo_url",
        "primary_color",
        "status",
        "plan_tier",
        "max_subscribers",
        "max_nas",
        "api_rpm",
    }
    changes = {key: body[key] for key in allowed if key in body}
    if "status" in changes and changes["status"] not in _STATUSES:
        return fail("validation_error", "unknown status", status=422)
    if "plan_tier" in changes and changes["plan_tier"] not in _TIERS:
        return fail("validation_error", "unknown plan_tier", status=422)
    for key in ("max_subscribers", "max_nas", "api_rpm"):
        if key in changes:
            try:
                changes[key] = int(changes[key])
            except (TypeError, ValueError):
                return fail("validation_error", f"{key} must be integer", status=422)
    try:
        saved = get_tenants_service().update(
            actor=_actor(),
            tenant_id=tenant_id,
            **changes,
        )
    except RadiusError as exc:
        return fail("validation_error", exc.message, status=422)
    return ok(_serialize_tenant(saved))
