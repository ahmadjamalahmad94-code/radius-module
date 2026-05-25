"""Card users and card marketplace web routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.card_users_marketplace import CardMarketplaceError, CardUsersMarketplaceService


def register_card_users_marketplace_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/card-users", "card_users_list", card_users_list, methods=["GET"])
    bp.add_url_rule("/card-users", "card_users_create", card_users_create, methods=["POST"])
    bp.add_url_rule("/card-users/<int:card_user_id>", "card_user_360", card_user_360, methods=["GET"])
    bp.add_url_rule(
        "/card-users/<int:card_user_id>/recharge",
        "card_user_recharge",
        card_user_recharge,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/card-users/<int:card_user_id>/purchase",
        "card_user_purchase",
        card_user_purchase,
        methods=["POST"],
    )
    bp.add_url_rule("/card-marketplace", "card_marketplace", card_marketplace, methods=["GET"])
    bp.add_url_rule("/card-marketplace/packages", "card_marketplace_package_create", card_marketplace_package_create, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _service() -> CardUsersMarketplaceService:
    return CardUsersMarketplaceService(tenant_id=_tid())


def card_users_list():
    service = _service()
    return render_template(
        "radius/card_users.html",
        card_users=service.list_card_users(limit=200),
        packages=service.list_packages(limit=200),
    )


def card_users_create():
    try:
        card_user = _service().create_card_user(
            display_name=request.form.get("display_name") or "",
            mobile=request.form.get("mobile") or "",
            email=request.form.get("email") or "",
        )
        flash("تم إنشاء مستخدم كروت مع محفظة تشغيلية.", "success")
        return redirect(url_for("radius.card_user_360", card_user_id=card_user["id"]))
    except CardMarketplaceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.card_users_list"))


def card_user_360(card_user_id: int):
    try:
        payload = _service().card_user_360(card_user_id)
    except CardMarketplaceError:
        return redirect(url_for("radius.card_users_list"))
    return render_template(
        "radius/card_user_360.html",
        card360=payload,
        packages=_service().list_packages(limit=200),
    )


def card_user_recharge(card_user_id: int):
    try:
        _service().recharge_wallet(
            card_user_id=card_user_id,
            amount=request.form.get("amount") or "0",
            actor=_actor(),
        )
        flash("تم شحن محفظة مستخدم الكروت.", "success")
    except (CardMarketplaceError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.card_user_360", card_user_id=card_user_id))


def card_user_purchase(card_user_id: int):
    try:
        purchase = _service().purchase_package(
            card_user_id=card_user_id,
            package_id=int(request.form.get("package_id") or 0),
            actor=_actor(),
        )
        flash(f"تم شراء كرت رقم {purchase['card_id']} وخصم المحفظة.", "success")
    except (CardMarketplaceError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.card_user_360", card_user_id=card_user_id))


def card_marketplace():
    service = _service()
    return render_template(
        "radius/card_marketplace.html",
        packages=service.list_packages(limit=200),
        purchases=service.list_purchases(limit=100),
    )


def card_marketplace_package_create():
    try:
        _service().create_package(
            name=request.form.get("name") or "",
            plan_id=int(request.form.get("plan_id") or 0),
            price=request.form.get("price") or "0",
            duration_minutes=int(request.form.get("duration_minutes") or 0),
            speed_down_kbps=int(request.form.get("speed_down_kbps") or 0),
            speed_up_kbps=int(request.form.get("speed_up_kbps") or 0),
        )
        flash("تم إنشاء باقة Marketplace.", "success")
    except (CardMarketplaceError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.card_marketplace"))
