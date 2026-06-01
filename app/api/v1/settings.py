from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import audit_repo, tenants_repo
from ...radius.routes.settings import _SETTINGS_KEYS
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _catalog() -> dict[str, tuple[str, str]]:
    return {key: (label, default) for key, label, default in _SETTINGS_KEYS}


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/settings",
        "settings_get",
        require_api_token(settings_get),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/settings",
        "settings_patch",
        require_api_token(settings_patch),
        methods=["PATCH", "PUT"],
    )


def settings_get():
    tenant_id = _tid()
    rows = tenants_repo.list_settings(tenant_id)
    items = []
    for key, (label, default) in _catalog().items():
        items.append(
            {
                "key": key,
                "label": label,
                "value": rows.get(key, default),
                "default": default,
            }
        )
    return ok({"items": items, "settings": {item["key"]: item["value"] for item in items}})


def settings_patch():
    body = request.get_json(silent=True) or {}
    settings = body.get("settings", body)
    if not isinstance(settings, dict):
        return fail("validation_error", "الإعدادات يجب أن تكون كائنًا.", status=422)
    catalog = _catalog()
    unknown = sorted(str(k) for k in settings if str(k) not in catalog)
    if unknown:
        return fail(
            "validation_error",
            "مفتاح إعداد غير معروف.",
            status=422,
            details={"unknown": unknown},
        )
    changed: dict[str, str] = {}
    tenant_id = _tid()
    for key, value in settings.items():
        skey = str(key)
        sval = "" if value is None else str(value).strip()
        old = tenants_repo.get_setting(tenant_id, skey, catalog[skey][1])
        if sval != old:
            tenants_repo.set_setting(
                tenant_id,
                skey,
                sval,
                by=int(getattr(g, "admin_id", 0) or 0),
            )
            changed[skey] = sval
    if changed:
        audit_repo.record(
            tenant_id=tenant_id,
            actor=_actor(),
            action="settings_update",
            target_type="settings",
            target_id=",".join(changed.keys()),
            payload={"changed": list(changed.keys())},
        )
    return ok({"updated": changed, "count": len(changed)})
