"""Card pricing and batch financial costing routes."""
from __future__ import annotations
from ..core.system_config import default_currency

from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from ..db.connection import db
from ..db.helpers import row_to_dict
from ..services.business_os_finance import minor_to_money
from ..services.card_pricing import CardPricingError, CardPricingService


def register_card_pricing_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/card-pricing", "card_pricing", card_pricing, methods=["GET"])
    bp.add_url_rule("/card-pricing/packages/<int:package_id>", "card_pricing_update_package", card_pricing_update_package, methods=["POST"])
    bp.add_url_rule("/card-pricing/batches", "card_pricing_create_batch", card_pricing_create_batch, methods=["POST"])
    bp.add_url_rule("/card-pricing/batches/<int:batch_id>", "card_pricing_batch_detail", card_pricing_batch_detail, methods=["GET"])
    bp.add_url_rule("/card-pricing/summary.json", "card_pricing_summary_json", card_pricing_summary_json, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _service() -> CardPricingService:
    return CardPricingService(tenant_id=_tid())


def _float_money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _priced_batch_rows(tenant_id: int, *, limit: int = 80) -> list[dict[str, Any]]:
    _cur = default_currency()
    _cur = _cur if _cur.isalpha() else "ILS"
    rows = db().execute(
        f"""
        SELECT
            b.id,
            b.batch_code,
            b.package_name,
            b.generated,
            b.count,
            b.price_per_card,
            b.price_bulk,
            b.total_price,
            b.created_at,
            p.name AS plan_name,
            COALESCE(NULLIF(p.currency, ''), '{_cur}') AS currency
        FROM card_batches b
        LEFT JOIN access_plans p
          ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
        WHERE b.tenant_id = ?
          AND b.deleted_at IS NULL
          AND (
            COALESCE(b.price_per_card, 0) > 0
            OR COALESCE(b.price_bulk, 0) > 0
            OR COALESCE(b.total_price, 0) > 0
          )
        ORDER BY b.id DESC
        LIMIT ?
        """,
        (tenant_id, int(limit)),
    ).fetchall()
    batches = [row_to_dict(row) for row in rows]
    for batch in batches:
        generated = int(batch.get("generated") or batch.get("count") or 0)
        unit_price = _float_money(batch.get("price_per_card"))
        if unit_price <= 0 and generated > 0:
            unit_price = _float_money(batch.get("total_price")) / generated
        wholesale = _float_money(batch.get("price_bulk"))
        batch["unit_price"] = f"{unit_price:.2f}"
        batch["wholesale_price"] = f"{wholesale:.2f}"
        batch["margin"] = f"{max(unit_price - wholesale, 0):.2f}"
        batch["cards_count"] = generated
        batch["display_name"] = batch.get("package_name") or batch.get("plan_name") or batch.get("batch_code")
    return batches


def _pricing_overview(
    packages: list[dict[str, Any]],
    summary: dict[str, Any],
    priced_batches: list[dict[str, Any]],
) -> dict[str, Any]:
    priced = [
        package for package in packages
        if _float_money(package.get("retail_price")) > 0 and _float_money(package.get("wholesale_price")) > 0
    ]
    package_margins = [
        max(_float_money(package.get("retail_price")) - _float_money(package.get("wholesale_price")), 0)
        for package in priced
    ]
    batch_margins = [_float_money(batch.get("margin")) for batch in priced_batches]
    margins = package_margins + batch_margins
    return {
        "packages": len(packages),
        "priced_packages": len(priced) + len(priced_batches),
        "unpriced_packages": max(len(packages) - len(priced), 0),
        "avg_margin": f"{(sum(margins) / len(margins)):.2f}" if margins else "0.00",
        "total_cards": int(summary.get("total_cards") or 0),
        "unused_cards": int(summary.get("unused_cards") or 0),
        "sold_cards": int(summary.get("sold_cards") or 0),
        "revenue_total": summary.get("revenue_total") or "0.00",
    }


def _manager_options(tenant_id: int) -> list[dict[str, Any]]:
    _cur = default_currency()
    _cur = _cur if _cur.isalpha() else "ILS"
    rows = db().execute(
        f"""
        SELECT
            a.id,
            COALESCE(NULLIF(a.full_name, ''), a.username) AS display_name,
            a.username,
            COALESCE(w.balance_minor, 0) AS balance_minor,
            COALESCE(NULLIF(w.currency, ''), '{_cur}') AS currency
        FROM admins a
        LEFT JOIN tenant_memberships tm
          ON tm.admin_id = a.id AND tm.tenant_id = ?
        LEFT JOIN wallets w
          ON w.owner_type = 'manager'
         AND w.owner_id = a.id
         AND w.tenant_id = ?
        WHERE a.enabled = 1
          AND (tm.tenant_id IS NOT NULL OR a.is_super_admin = 1)
        GROUP BY a.id
        ORDER BY a.id DESC
        LIMIT 300
        """,
        (tenant_id, tenant_id),
    ).fetchall()
    managers = [row_to_dict(row) for row in rows]
    for manager in managers:
        manager["balance"] = minor_to_money(manager.get("balance_minor") or 0)
    return managers


def _recent_costed_batches(tenant_id: int, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = db().execute(
        """
        SELECT
            c.*,
            b.batch_code,
            b.package_name,
            COALESCE(NULLIF(a.full_name, ''), a.username, CAST(c.responsible_id AS TEXT)) AS manager_name
        FROM card_batch_financial_costs c
        LEFT JOIN card_batches b
          ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN admins a
          ON a.id = c.responsible_id
        WHERE c.tenant_id = ?
        ORDER BY c.id DESC
        LIMIT ?
        """,
        (tenant_id, int(limit)),
    ).fetchall()
    items = [row_to_dict(row) for row in rows]
    for item in items:
        item["retail_total"] = minor_to_money(item.get("total_retail_minor") or 0)
        item["wholesale_total"] = minor_to_money(item.get("total_wholesale_minor") or 0)
        item["unit_retail"] = minor_to_money(item.get("retail_price_minor") or 0)
        item["unit_wholesale"] = minor_to_money(item.get("wholesale_price_minor") or 0)
    return items


def card_pricing():
    service = _service()
    summary = service.cards_summary()
    packages = service.list_packages(limit=200)
    priced_batches = _priced_batch_rows(_tid())
    return render_template(
        "radius/card_pricing.html",
        packages=packages,
        priced_batches=priced_batches,
        summary=summary,
        pricing_overview=_pricing_overview(packages, summary, priced_batches),
        managers=_manager_options(_tid()),
        costed_batches=_recent_costed_batches(_tid()),
    )


def card_pricing_update_package(package_id: int):
    try:
        _service().set_package_pricing(
            package_id=package_id,
            retail_price=request.form.get("retail_price") or "0",
            wholesale_price=request.form.get("wholesale_price") or "0",
            min_price=request.form.get("min_price") or "0",
            max_discount=request.form.get("max_discount") or "0",
            allowed_manager_ids=_parse_ids(request.form.get("allowed_manager_ids") or ""),
            allowed_distributor_ids=_parse_ids(request.form.get("allowed_distributor_ids") or ""),
        )
        flash("تم تحديث أسعار الباقة.", "success")
    except (CardPricingError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.card_pricing"))


def card_pricing_create_batch():
    from ..auth.session_helpers import is_super_admin
    from ..services.manager_credit import (
        ManagerCreditConfirmRequired,
        ManagerCreditError,
    )

    package_id = int(request.form.get("package_id") or 0)
    count = int(request.form.get("count") or 0)
    manager_id = int(request.form.get("responsible_manager_id") or 0)
    actor_is_super = is_super_admin()
    allow_super_debt = str(request.form.get("confirm_manager_debt") or "").strip() in ("1", "true", "on", "yes")
    try:
        result = _service().create_costed_batch(
            package_id=package_id,
            count=count,
            responsible_manager_id=manager_id,
            creator_type="admin",
            creator_id=session.get("admin_id"),
            actor=_actor(),
            actor_is_super=actor_is_super,
            allow_super_debt=allow_super_debt,
        )
        flash("تم إنشاء سجل تكلفة الدفعة وخصم محفظة المدير.", "success")
        return redirect(url_for("radius.card_pricing_batch_detail", batch_id=result["batch"]["id"]))
    except ManagerCreditConfirmRequired as confirm:
        # Super-admin linked a package to a manager who can't cover it. Re-render
        # the page with a design-system CONFIRM modal; on confirm the same form
        # re-POSTs with confirm_manager_debt=1 (super override → manager debt).
        packages = _service().list_packages()
        summary = _service().cards_summary()
        priced_batches = _priced_batch_rows(_tid())
        return render_template(
            "radius/card_pricing.html",
            packages=packages,
            priced_batches=priced_batches,
            summary=summary,
            pricing_overview=_pricing_overview(packages, summary, priced_batches),
            managers=_manager_options(_tid()),
            costed_batches=_recent_costed_batches(_tid()),
            confirm_manager_debt={
                "message": confirm.message,
                "shortfall": minor_to_money(confirm.shortfall_minor),
                "package_id": package_id,
                "count": count,
                "responsible_manager_id": manager_id,
            },
        )
    except ManagerCreditError as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.card_pricing"))
    except (CardPricingError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.card_pricing"))


def card_pricing_batch_detail(batch_id: int):
    try:
        result = _service().get_batch_financial(batch_id)
    except CardPricingError:
        return redirect(url_for("radius.card_pricing"))
    return render_template("radius/card_pricing_batch.html", result=result)


def card_pricing_summary_json():
    return jsonify({"status": "ok", "summary": _service().cards_summary()})


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out
