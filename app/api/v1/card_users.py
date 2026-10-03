"""Card users and electronic-card marketplace API contracts."""
from __future__ import annotations
from app.i18n_text import N_, _tr
from ...radius.core.system_config import default_currency

from typing import Any

from flask import Blueprint, g, request

from ...radius.services.card_users_marketplace import (
    CardMarketplaceError,
    CardUsersMarketplaceService,
    arabic_error_message,
    int_input,
)
from ..auth import require_api_token
from ..responses import fail, ok


_ERRORS = {
    "display_name is required": ("validation_error", N_("اسم مستخدم الكروت مطلوب"), 422),
    "password must be at least 4 characters": (
        "validation_error",
        N_("كلمة المرور يجب أن تكون 4 أحرف على الأقل"),
        422,
    ),
    "card user not found": ("not_found", N_("مستخدم الكروت غير موجود"), 404),
    # الخدمة ترفع النصّ العربيّ مباشرةً — كان يسقط إلى 422 بدل 404.
    "مستخدم الكروت غير موجود.": ("not_found", N_("مستخدم الكروت غير موجود"), 404),
    "name is required": ("validation_error", N_("اسم الباقة مطلوب"), 422),
    "price must be positive": ("validation_error", N_("السعر يجب أن يكون أكبر من صفر"), 422),
    "plan not found": ("not_found", N_("العرض المرتبط غير موجود"), 404),
    "package not found": ("not_found", N_("باقة الكروت غير موجودة"), 404),
    "package is inactive": ("conflict", N_("باقة الكروت غير مفعلة"), 409),
    "insufficient wallet balance": ("insufficient_balance", N_("رصيد المحفظة غير كاف"), 402),
}


def register(bp: Blueprint) -> None:
    routes = [
        ("/card-users", "card_users_list", card_users_list, ["GET"]),
        ("/card-users", "card_users_create", card_users_create, ["POST"]),
        ("/card-users/<int:card_user_id>/360", "card_user_360", card_user_360, ["GET"]),
        (
            "/card-users/<int:card_user_id>/recharge",
            "card_user_recharge",
            card_user_recharge,
            ["POST"],
        ),
        (
            "/card-users/<int:card_user_id>/purchase",
            "card_user_purchase",
            card_user_purchase,
            ["POST"],
        ),
        (
            "/card-users/<int:card_user_id>/password",
            "card_user_password",
            card_user_password,
            ["POST"],
        ),
        (
            "/card-marketplace/packages",
            "card_marketplace_packages",
            card_marketplace_packages,
            ["GET"],
        ),
        (
            "/card-marketplace/packages",
            "card_marketplace_package_create",
            card_marketplace_package_create,
            ["POST"],
        ),
    ]
    for rule, endpoint, view, methods in routes:
        bp.add_url_rule(rule, endpoint, require_api_token(view), methods=methods)


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _service() -> CardUsersMarketplaceService:
    return CardUsersMarketplaceService(tenant_id=_tid())


def _payload() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _limit(default: int = 100, maximum: int = 500) -> int:
    try:
        return min(max(int(request.args.get("limit") or default), 1), maximum)
    except (TypeError, ValueError):
        return default


def _bool_arg(name: str, default: bool = True) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _marketplace_error(exc: Exception):
    raw = str(exc)
    # f05-M5: لا نصّ بايثون/إنجليزيّ للمستخدم («invalid literal for int()…»).
    code, message, status = _ERRORS.get(
        raw, ("validation_error", arabic_error_message(exc) if raw else N_("تعذر تنفيذ العملية"), 422))
    return fail(code, message, status=status)


def _public_card_user(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    password_hash = str(item.pop("password_hash", "") or "")
    item["has_portal_password"] = bool(password_hash)
    return item


def _public_package(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.pop("metadata_json", None)
    return item


def _public_purchase(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.pop("metadata_json", None)
    return item


def _public_wallet(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.pop("metadata_json", None)
    return item


def _summary(users: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "users": len(users),
        "active": sum(1 for user in users if (user.get("status") or "active") == "active"),
        "cards": sum(int(user.get("owned_cards_count") or 0) for user in users),
        "purchases": sum(int(user.get("purchase_count") or 0) for user in users),
        "balance": round(sum(float(user.get("balance") or 0) for user in users), 2),
        "currency": next((user.get("wallet_currency") for user in users if user.get("wallet_currency")), default_currency()),
    }


def card_users_list():
    status = (request.args.get("status") or "").strip()
    users = _service().list_card_users(status=status, limit=_limit())
    items = [_public_card_user(user) for user in users]
    return ok({"items": items, "count": len(items), "summary": _summary(items)})


def card_users_create():
    body = _payload()
    try:
        user = _service().create_card_user(
            display_name=str(body.get("display_name") or ""),
            mobile=str(body.get("mobile") or ""),
            email=str(body.get("email") or ""),
            password=str(body.get("password") or ""),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        )
    except CardMarketplaceError as exc:
        return _marketplace_error(exc)
    return ok({"card_user": _public_card_user(user)}, status=201)


def card_user_360(card_user_id: int):
    try:
        data = _service().card_user_360(card_user_id)
    except CardMarketplaceError as exc:
        return _marketplace_error(exc)
    payload = {
        "card_user": _public_card_user(data.get("card_user") or {}),
        "wallet": _public_wallet(data.get("wallet") or {}),
        "purchases": [_public_purchase(item) for item in data.get("purchases") or []],
        "cards": data.get("cards") or [],
        "usage": data.get("usage") or {},
        "financial_history": data.get("financial_history") or [],
        "timeline": data.get("timeline") or [],
        "messages": [
            {
                "status": "not_configured",
                "message": _tr("لم يتم ربط مزود الرسائل بعد."),
            }
        ],
        "events": data.get("events") or [],
    }
    return ok(payload)


def card_user_recharge(card_user_id: int):
    body = _payload()
    try:
        result = _service().recharge_wallet(
            card_user_id=card_user_id,
            amount=body.get("amount"),
            actor=_actor(),
        )
    except (CardMarketplaceError, ValueError) as exc:
        return _marketplace_error(exc)
    if isinstance(result, dict):
        result["wallet"] = _public_wallet(result.get("wallet") or {})
    return ok(result, status=201)


def card_user_purchase(card_user_id: int):
    body = _payload()
    try:
        purchase = _service().purchase_package(
            card_user_id=card_user_id,
            package_id=int_input(body.get("package_id"), label=N_("الباقة")),
            actor=_actor(),
        )
    except (CardMarketplaceError, ValueError) as exc:
        return _marketplace_error(exc)
    return ok({"purchase": _public_purchase(purchase)}, status=201)


def card_user_password(card_user_id: int):
    body = _payload()
    try:
        user = _service().set_card_user_password(
            card_user_id=card_user_id,
            password=str(body.get("password") or ""),
        )
    except CardMarketplaceError as exc:
        return _marketplace_error(exc)
    return ok({"card_user": _public_card_user(user)})


def card_marketplace_packages():
    packages = _service().list_packages(active_only=_bool_arg("active", True), limit=_limit())
    items = [_public_package(item) for item in packages]
    return ok({"items": items, "count": len(items)})


def card_marketplace_package_create():
    body = _payload()
    try:
        package = _service().create_package(
            name=str(body.get("name") or ""),
            plan_id=int_input(body.get("plan_id"), label=N_("العرض")),
            price=body.get("price"),
            duration_minutes=int_input(body.get("duration_minutes"), label=N_("المدّة بالدقائق"),
                                       maximum=525600),
            speed_down_kbps=int_input(body.get("speed_down_kbps"), label=N_("سرعة التنزيل"),
                                      maximum=10_000_000),
            speed_up_kbps=int_input(body.get("speed_up_kbps"), label=N_("سرعة الرفع"),
                                    maximum=10_000_000),
            currency=str(body.get("currency") or default_currency()),
            card_color=str(body.get("card_color") or "#14b8a6"),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        )
    except (CardMarketplaceError, ValueError) as exc:
        return _marketplace_error(exc)
    return ok({"package": _public_package(package)}, status=201)
