"""Card users and card marketplace web routes."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..db.connection import db
from ..db.helpers import json_load, now_iso, row_to_dict
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
    bp.add_url_rule(
        "/card-users/<int:card_user_id>/password",
        "card_user_password",
        card_user_password,
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


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _electronic_batch_rows(tenant_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    now = now_iso()
    rows = db().execute(
        """
        WITH purchase_cards AS (
            SELECT
                card_id,
                COUNT(*) AS sold_count,
                COALESCE(SUM(amount_minor), 0) AS revenue_minor
            FROM card_user_purchases
            WHERE tenant_id = ? AND status = 'completed' AND card_id IS NOT NULL
            GROUP BY card_id
        )
        SELECT
            b.*,
            p.name AS plan_name,
            p.currency AS plan_currency,
            p.duration_minutes AS plan_duration_minutes,
            p.quota_total_mb AS plan_quota_total_mb,
            COALESCE(NULLIF(a.full_name, ''), a.username, NULLIF(b.created_by, ''), CAST(NULLIF(b.manager_id, 0) AS TEXT)) AS manager_display_name,
            COALESCE(NULLIF(d.display_name, ''), d.name) AS distributor_display_name,
            COUNT(c.id) AS total_cards,
            COALESCE(SUM(CASE
                WHEN c.deleted_at IS NULL
                 AND c.revoked = 0
                 AND c.used = 0
                 AND pc.card_id IS NULL
                 AND (c.expire_at IS NULL OR c.expire_at >= ?)
                THEN 1 ELSE 0 END), 0) AS available_count,
            COALESCE(SUM(CASE WHEN pc.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold_count,
            COALESCE(SUM(CASE WHEN c.deleted_at IS NULL AND c.used = 1 THEN 1 ELSE 0 END), 0) AS used_count,
            COALESCE(SUM(CASE
                WHEN c.deleted_at IS NULL
                 AND c.revoked = 0
                 AND c.expire_at IS NOT NULL
                 AND c.expire_at < ?
                THEN 1 ELSE 0 END), 0) AS expired_count,
            COALESCE(SUM(CASE WHEN c.revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked_count,
            COALESCE(SUM(pc.revenue_minor), 0) AS revenue_minor
        FROM card_batches b
        LEFT JOIN access_plans p
          ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
        LEFT JOIN admins a
          ON a.id = b.manager_id
        LEFT JOIN distributors d
          ON d.tenant_id = b.tenant_id AND d.id = b.distributor_id
        LEFT JOIN cards c
          ON c.tenant_id = b.tenant_id AND c.batch_id = b.id
        LEFT JOIN purchase_cards pc
          ON pc.card_id = c.id
        WHERE b.tenant_id = ?
          AND b.deleted_at IS NULL
          AND LOWER(COALESCE(b.metadata, '')) NOT LIKE '%printed%'
          AND (
              LOWER(COALESCE(b.metadata, '')) LIKE '%electronic%'
              OR LOWER(COALESCE(b.batch_code, '')) LIKE '%online%'
              OR LOWER(COALESCE(b.package_name, '')) LIKE '%online%'
              OR LOWER(COALESCE(b.package_name, '')) LIKE '%electronic%'
              OR COALESCE(b.package_name, '') LIKE '%إلكترون%'
              OR COALESCE(b.package_name, '') LIKE '%الكترون%'
          )
        GROUP BY b.id
        ORDER BY b.id DESC
        LIMIT ?
        """,
        (tenant_id, now, now, tenant_id, int(limit)),
    ).fetchall()

    batches = [row_to_dict(row) for row in rows]
    for batch in batches:
        metadata = json_load(batch.get("metadata"), {}) or {}
        generated = int(batch.get("generated") or batch.get("count") or batch.get("total_cards") or 0)
        total_cards = int(batch.get("total_cards") or generated or 0)
        unit_price = _money(batch.get("price_per_card"))
        if unit_price <= 0 and _money(batch.get("total_price")) > 0 and generated > 0:
            unit_price = _money(_money(batch.get("total_price")) / generated)
        sold_count = int(batch.get("sold_count") or 0)
        revenue = _money((int(batch.get("revenue_minor") or 0) / 100) or (sold_count * unit_price))
        batch.update(
            metadata=metadata,
            display_name=batch.get("package_name") or batch.get("plan_name") or batch.get("batch_code") or "حزمة إلكترونية",
            currency=metadata.get("currency") or "ILS",
            total_cards=total_cards,
            unit_price=unit_price,
            wholesale_price=_money(batch.get("price_bulk")),
            sold_count=sold_count,
            available_count=int(batch.get("available_count") or 0),
            used_count=int(batch.get("used_count") or 0),
            expired_count=int(batch.get("expired_count") or 0),
            revoked_count=int(batch.get("revoked_count") or 0),
            revenue=revenue,
            duration_value=int(batch.get("time_value") or 0),
            duration_unit=batch.get("time_unit") or "",
            quota_mb=int(batch.get("total_quota_mb") or batch.get("plan_quota_total_mb") or 0),
        )
    return batches


def _recent_electronic_purchases(tenant_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
    rows = db().execute(
        """
        SELECT
            cup.*,
            cu.display_name AS card_user_name,
            c.username AS card_username,
            b.batch_code,
            b.package_name
        FROM card_user_purchases cup
        LEFT JOIN card_users cu
          ON cu.tenant_id = cup.tenant_id AND cu.id = cup.card_user_id
        LEFT JOIN cards c
          ON c.tenant_id = cup.tenant_id AND c.id = cup.card_id
        LEFT JOIN card_batches b
          ON b.tenant_id = cup.tenant_id AND b.id = c.batch_id
        WHERE cup.tenant_id = ?
          AND LOWER(COALESCE(b.metadata, '')) NOT LIKE '%printed%'
          AND (
              LOWER(COALESCE(b.metadata, '')) LIKE '%electronic%'
              OR LOWER(COALESCE(b.batch_code, '')) LIKE '%online%'
              OR LOWER(COALESCE(b.package_name, '')) LIKE '%online%'
              OR LOWER(COALESCE(b.package_name, '')) LIKE '%electronic%'
              OR COALESCE(b.package_name, '') LIKE '%إلكترون%'
              OR COALESCE(b.package_name, '') LIKE '%الكترون%'
          )
        ORDER BY cup.id DESC
        LIMIT ?
        """,
        (tenant_id, int(limit)),
    ).fetchall()
    purchases = [row_to_dict(row) for row in rows]
    for purchase in purchases:
        purchase["amount"] = _money(int(purchase.get("amount_minor") or 0) / 100)
    return purchases


def _market_summary(batches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "batches": len(batches),
        "cards": sum(int(batch.get("total_cards") or 0) for batch in batches),
        "available": sum(int(batch.get("available_count") or 0) for batch in batches),
        "sold": sum(int(batch.get("sold_count") or 0) for batch in batches),
        "revenue": _money(sum(float(batch.get("revenue") or 0) for batch in batches)),
        "currency": next((batch.get("currency") for batch in batches if batch.get("currency")), "ILS"),
    }


def _marketplace_plans(tenant_id: int, *, limit: int = 300) -> list[dict[str, Any]]:
    rows = db().execute(
        """
        SELECT
            id, name, code, duration_minutes, speed_down_kbps, speed_up_kbps,
            quota_total_mb, price_card, price, currency, enabled
        FROM access_plans
        WHERE tenant_id=? AND COALESCE(enabled, 1)=1
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, int(limit)),
    ).fetchall()
    plans = [row_to_dict(row) for row in rows]
    for plan in plans:
        plan["display_name"] = plan.get("name") or plan.get("code") or f"عرض #{plan.get('id')}"
        plan["suggested_price"] = _money(plan.get("price_card") or plan.get("price") or 0)
        plan["duration_minutes"] = int(plan.get("duration_minutes") or 0)
        plan["speed_down_kbps"] = int(plan.get("speed_down_kbps") or 0)
        plan["speed_up_kbps"] = int(plan.get("speed_up_kbps") or 0)
        plan["quota_total_mb"] = int(plan.get("quota_total_mb") or 0)
        plan["currency"] = plan.get("currency") or "ILS"
    return plans


def _card_user_rows(tenant_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = db().execute(
        """
        SELECT
            cu.*,
            w.id AS wallet_id,
            w.balance_minor,
            w.pending_balance_minor,
            w.currency AS wallet_currency,
            w.status AS wallet_status,
            COUNT(cup.id) AS purchase_count,
            COUNT(cup.card_id) AS owned_cards_count,
            COALESCE(SUM(CASE WHEN cup.status = 'completed' THEN cup.amount_minor ELSE 0 END), 0) AS spent_minor,
            MAX(cup.created_at) AS last_purchase_at
        FROM card_users cu
        LEFT JOIN wallets w
          ON w.tenant_id = cu.tenant_id
         AND w.owner_type = 'card_user'
         AND w.owner_id = cu.id
        LEFT JOIN card_user_purchases cup
          ON cup.tenant_id = cu.tenant_id
         AND cup.card_user_id = cu.id
        WHERE cu.tenant_id = ?
        GROUP BY cu.id
        ORDER BY cu.id DESC
        LIMIT ?
        """,
        (tenant_id, int(limit)),
    ).fetchall()
    users = [row_to_dict(row) for row in rows]
    for user in users:
        user["balance"] = _money(int(user.get("balance_minor") or 0) / 100)
        user["pending_balance"] = _money(int(user.get("pending_balance_minor") or 0) / 100)
        user["spent"] = _money(int(user.get("spent_minor") or 0) / 100)
        user["wallet_currency"] = user.get("wallet_currency") or "ILS"
        user["purchase_count"] = int(user.get("purchase_count") or 0)
        user["owned_cards_count"] = int(user.get("owned_cards_count") or 0)
    return users


def _card_users_summary(users: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "users": len(users),
        "active": sum(1 for user in users if (user.get("status") or "active") == "active"),
        "cards": sum(int(user.get("owned_cards_count") or 0) for user in users),
        "purchases": sum(int(user.get("purchase_count") or 0) for user in users),
        "balance": _money(sum(float(user.get("balance") or 0) for user in users)),
        "currency": next((user.get("wallet_currency") for user in users if user.get("wallet_currency")), "ILS"),
    }


def card_users_list():
    service = _service()
    card_users = _card_user_rows(_tid())
    electronic_batches = _electronic_batch_rows(_tid(), limit=12)
    return render_template(
        "radius/card_users.html",
        card_users=card_users,
        users_summary=_card_users_summary(card_users),
        packages=service.list_packages(limit=200),
        electronic_batches=electronic_batches,
        market_summary=_market_summary(electronic_batches),
    )


def card_users_create():
    try:
        card_user = _service().create_card_user(
            display_name=request.form.get("display_name") or "",
            mobile=request.form.get("mobile") or "",
            password=request.form.get("password") or "",
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


def card_user_password(card_user_id: int):
    try:
        _service().set_card_user_password(
            card_user_id=card_user_id,
            password=request.form.get("password") or "",
        )
        flash("تم تحديث كلمة مرور بوابة مستخدم البطاقة.", "success")
    except CardMarketplaceError as exc:
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
    all_electronic_batches = _electronic_batch_rows(_tid())
    electronic_batches = all_electronic_batches[:4]
    marketplace_plans = _marketplace_plans(_tid())
    return render_template(
        "radius/card_marketplace.html",
        packages=service.list_packages(limit=200),
        marketplace_plans=marketplace_plans,
        purchases=_recent_electronic_purchases(_tid()),
        electronic_batches=electronic_batches,
        market_summary=_market_summary(all_electronic_batches),
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
            card_color=request.form.get("card_color") or "#14b8a6",
            metadata={
                "sale_note": request.form.get("sale_note") or "",
            },
        )
        flash("تم إنشاء باقة بيع إلكترونية، وستظهر للمستخدمين للشراء.", "success")
    except (CardMarketplaceError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.card_marketplace"))
