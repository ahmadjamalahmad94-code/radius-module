"""Card pricing and batch financial costing routes."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

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


def card_pricing():
    service = _service()
    return render_template(
        "radius/card_pricing.html",
        packages=service.list_packages(limit=200),
        summary=service.cards_summary(),
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
    try:
        result = _service().create_costed_batch(
            package_id=int(request.form.get("package_id") or 0),
            count=int(request.form.get("count") or 0),
            responsible_manager_id=int(request.form.get("responsible_manager_id") or 0),
            creator_type="admin",
            creator_id=session.get("admin_id"),
            actor=_actor(),
        )
        flash("تم إنشاء سجل تكلفة الدفعة وخصم محفظة المدير.", "success")
        return redirect(url_for("radius.card_pricing_batch_detail", batch_id=result["batch"]["id"]))
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
