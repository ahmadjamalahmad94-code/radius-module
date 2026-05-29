"""Recharge Cards (بطاقات الشحن المسبق) — admin routes.

Wallet top-up vouchers that the card user enters on the customer
portal (/portal/card/redeem) to credit their wallet. Each card
carries its own ``wallet_value`` so a single batch can mix
denominations.

URL tree:
  GET  /cards/recharge                    → list batches
  GET  /cards/recharge/new                → multi-denom generate form
  POST /cards/recharge/new                → handle generate
  GET  /cards/recharge/<batch_id>         → cards inside a batch
  POST /cards/recharge/<batch_id>/delete  → soft-delete a batch
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, make_response, redirect,
    render_template, request, session as flask_session, url_for,
)

from ..core.errors import RadiusError, RadiusValidationError
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


# Default denomination ladder operators see on the generate form.
DEFAULT_DENOMS = [5, 10, 20, 50, 100]


def register_cards_recharge_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/recharge", "cards_recharge_list",
                    cards_recharge_list, methods=["GET"])
    bp.add_url_rule("/cards/recharge/new", "cards_recharge_new",
                    cards_recharge_new, methods=["GET", "POST"])
    bp.add_url_rule("/cards/recharge/<int:batch_id>", "cards_recharge_batch",
                    cards_recharge_batch, methods=["GET"])
    bp.add_url_rule("/cards/recharge/<int:batch_id>/delete",
                    "cards_recharge_batch_delete",
                    cards_recharge_batch_delete, methods=["POST"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return (flask_session.get("admin_name")
            or flask_session.get("admin_user")
            or "anonymous")


def cards_recharge_list():
    svc = get_cards_service()
    batches = svc.list_recharge_batches(limit=200)
    total = svc.count_recharge_batches()
    operations_service = get_operations_service()
    print_templates = operations_service.list_print_templates(
        tenant_id=_tid(), limit=500,
    )
    default_print_template_id = operations_service.get_default_print_template_id(
        tenant_id=_tid(),
    )
    return render_template(
        "radius/cards_recharge_list.html",
        batches=batches,
        total=total,
        print_templates=print_templates,
        default_print_template_id=default_print_template_id,
    )


def cards_recharge_new():
    if request.method == "GET":
        html = render_template(
            "radius/cards_recharge_new.html",
            default_denoms=DEFAULT_DENOMS,
            form=request.form,
        )
        resp = make_response(html)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    package_name = (request.form.get("package_name") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    # The form posts parallel arrays: denom_value[] + denom_count[].
    values = request.form.getlist("denom_value")
    counts = request.form.getlist("denom_count")
    denominations: list[dict] = []
    for v, c in zip(values, counts):
        try:
            v_num = float(v or 0)
            c_num = int(c or 0)
        except (TypeError, ValueError):
            continue
        if v_num > 0 and c_num > 0:
            denominations.append({"value": v_num, "count": c_num})

    if not package_name:
        flash("اسم الحزمة مطلوب.", "error")
        return _render_new(), 422
    if not denominations:
        flash("أدخل فئة واحدة على الأقل بقيمة وعدد أكبر من صفر.", "error")
        return _render_new(), 422

    try:
        result = get_cards_service().generate_recharge_batch(
            actor=_actor(),
            package_name=package_name,
            denominations=denominations,
            notes=notes,
        )
    except RadiusValidationError as exc:
        flash(exc.message, "error")
        return _render_new(), 422
    except RadiusError as exc:
        flash(exc.message, "error")
        return _render_new(), 500

    batch = result["batch"]
    flash(
        f"تم توليد {result['inserted_count']} بطاقة شحن "
        f"بإجمالي {result['total_value']:.2f} داخل «{batch.package_name}».",
        "success",
    )
    return redirect(url_for("radius.cards_recharge_batch", batch_id=batch.id))


def _render_new():
    return render_template(
        "radius/cards_recharge_new.html",
        default_denoms=DEFAULT_DENOMS,
        form=request.form,
    )


def cards_recharge_batch(batch_id: int):
    svc = get_cards_service()
    batch = svc.get_recharge_batch(batch_id)
    if not batch:
        abort(404)

    # Pagination — same ladder as the rest of the admin (10/20/50/100).
    try:
        per_page = int(request.args.get("per_page") or "20")
    except ValueError:
        per_page = 20
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1
    total = svc.count_recharge_cards(batch_id)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    offset = (page - 1) * per_page
    cards = svc.list_recharge_cards(batch_id, limit=per_page, offset=offset)

    # Parse the denominations breakdown from metadata for the UI.
    import json
    denominations: list[dict] = []
    try:
        meta = json.loads(batch.get("metadata") or "{}")
        denominations = meta.get("denominations") or []
    except (ValueError, TypeError):
        denominations = []

    operations_service = get_operations_service()
    print_templates = operations_service.list_print_templates(
        tenant_id=_tid(), limit=500,
    )
    default_print_template_id = operations_service.get_default_print_template_id(
        tenant_id=_tid(),
    )

    return render_template(
        "radius/cards_recharge_batch.html",
        batch=batch,
        cards=cards,
        total_cards=total,
        page=page,
        per_page=per_page,
        pages_count=pages_count,
        denominations=denominations,
        print_templates=print_templates,
        default_print_template_id=default_print_template_id,
    )


def cards_recharge_batch_delete(batch_id: int):
    ok = get_cards_service().delete_recharge_batch(
        actor=_actor(), batch_id=batch_id,
    )
    if ok:
        flash("تم حذف حزمة الشحن.", "success")
    else:
        flash("الحزمة غير موجودة أو غير قابلة للحذف.", "error")
    return redirect(url_for("radius.cards_recharge_list"))
