"""Web admin screens for distributor operations."""
from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..core.system_config import default_currency
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


def register_distributors_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/distributors", "distributors_list", distributors_list, methods=["GET"])
    bp.add_url_rule("/distributors/new", "distributors_new", distributors_new, methods=["GET"])
    bp.add_url_rule("/distributors", "distributors_create", distributors_create, methods=["POST"])
    bp.add_url_rule("/distributors/<int:distributor_id>", "distributors_detail", distributors_detail, methods=["GET"])
    bp.add_url_rule(
        "/distributors/<int:distributor_id>/edit",
        "distributors_edit",
        distributors_edit,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/distributors/<int:distributor_id>/edit",
        "distributors_update",
        distributors_update,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/distributors/<int:distributor_id>/assign-batch",
        "distributors_assign_batch",
        distributors_assign_batch,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/distributors/<int:distributor_id>/settle",
        "distributors_settle",
        distributors_settle,
        methods=["POST"],
    )


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _svc():
    return get_operations_service()


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _float_field(name: str, default: float = 0.0) -> float:
    raw = _field(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RadiusValidationError(f"{name} must be numeric") from None


def _permissions(raw: str) -> list[str]:
    return [
        item.strip()
        for part in raw.replace("\n", ",").split(",")
        for item in [part.strip()]
        if item
    ]


def _permissions_from_request() -> list[str]:
    raw_items = request.form.getlist("permissions")
    checked = [
        item.strip()
        for raw in raw_items
        for part in raw.replace("\n", ",").split(",")
        for item in [part.strip()]
        if item
    ]
    if checked:
        return checked
    return _permissions(_field("permissions"))


def _scope(raw: str) -> dict:
    if not raw:
        return {"card_batches": "assigned"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise RadiusValidationError("نطاق البيانات غير صالح.") from None
    if not isinstance(parsed, dict):
        raise RadiusValidationError("نطاق البيانات يجب أن يكون كائن إعدادات صحيحًا.")
    return parsed


def _form_payload() -> dict:
    return {
        "name": _field("name"),
        "display_name": _field("display_name"),
        "email": _field("email"),
        "phone": _field("phone"),
        "status": _field("status") or "active",
        "permissions": _permissions_from_request(),
        "scope": _scope(_field("scope_json")),
        "balance": _float_field("balance"),
        "credit_limit": _float_field("credit_limit"),
        "debt_balance": _float_field("debt_balance"),
        "notes": _field("notes"),
    }


def distributors_list():
    status = (request.args.get("status") or "").strip() or None
    items = _svc().list_distributors(tenant_id=_tid(), status=status, limit=500)
    return render_template(
        "radius/distributors_list.html",
        distributors=items,
        status=status or "",
        # ?new=1 يفتح الصندوق العائم «إضافة موزع» تلقائيًا (رابط /distributors/new القديم)
        open_new_modal=(request.args.get("new") == "1"),
    )


def distributors_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.distributors_list", new=1))


def distributors_create():
    try:
        saved = _svc().create_distributor(
            tenant_id=_tid(),
            actor=_actor(),
            data=_form_payload(),
        )
    except RadiusValidationError as e:
        flash(e.message, "error")
        return render_template(
            "radius/distributors_form.html",
            form=request.form,
            is_new=True,
        ), 400
    flash("تم إنشاء الموزع.", "success")
    return redirect(url_for("radius.distributors_detail", distributor_id=saved["id"]))


def _distributor_form_values(distributor: dict) -> dict:
    return {
        "name": distributor.get("name") or "",
        "display_name": distributor.get("display_name") or "",
        "email": distributor.get("email") or "",
        "phone": distributor.get("phone") or "",
        "status": distributor.get("status") or "active",
        "permissions": ", ".join(distributor.get("permissions_json") or []),
        "scope_json": json.dumps(
            distributor.get("scope_json") or {}, ensure_ascii=False
        ),
        "balance": distributor.get("balance", 0),
        "credit_limit": distributor.get("credit_limit", 0),
        "debt_balance": distributor.get("debt_balance", 0),
        "notes": distributor.get("notes") or "",
    }


def distributors_edit(distributor_id: int):
    try:
        distributor = _svc().get_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
        )
    except RadiusNotFound:
        abort(404)
    return render_template(
        "radius/distributors_form.html",
        form=_distributor_form_values(distributor),
        distributor=distributor,
        is_new=False,
    )


def distributors_update(distributor_id: int):
    try:
        _svc().update_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            actor=_actor(),
            data=_form_payload(),
        )
    except RadiusNotFound:
        abort(404)
    except RadiusValidationError as e:
        flash(e.message, "error")
        return render_template(
            "radius/distributors_form.html",
            form=request.form,
            distributor={"id": distributor_id},
            is_new=False,
        ), 400
    flash("تم تحديث الموزع.", "success")
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))


def _detail_context(distributor_id: int) -> dict:
    try:
        summary = _svc().distributor_summary(
            tenant_id=_tid(),
            distributor_id=distributor_id,
        )
        assigned_batches = _svc().list_distributor_batches(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            limit=500,
        )
    except RadiusNotFound:
        abort(404)
    all_batches = get_cards_service().list_batches(limit=500)
    assigned_ids = {int(item["id"]) for item in assigned_batches if item.get("id")}
    available_batches = [batch for batch in all_batches if batch.id not in assigned_ids]
    return {
        "summary": summary,
        "distributor": summary["distributor"],
        "assigned_batches": assigned_batches,
        "available_batches": available_batches,
    }


def distributors_detail(distributor_id: int):
    return render_template(
        "radius/distributors_detail.html",
        **_detail_context(distributor_id),
    )


def distributors_assign_batch(distributor_id: int):
    try:
        batch_id = int(request.form.get("batch_id") or 0)
    except (TypeError, ValueError):
        batch_id = 0
    if batch_id <= 0:
        flash("اختر حزمة كروت صحيحة.", "error")
        return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))
    try:
        _svc().assign_batch(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            batch_id=batch_id,
            actor=_actor(),
            notes=_field("notes"),
        )
        flash("تم ربط الحزمة بالموزع.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))


def distributors_settle(distributor_id: int):
    try:
        _svc().settle_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            actor=_actor(),
            data={
                "amount": _float_field("amount", 0.0),
                "direction": _field("direction") or "credit",
                "entry_type": _field("entry_type") or "settlement",
                "currency": _field("currency") or default_currency(),
                "notes": _field("notes"),
            },
        )
        flash("تم تسجيل حركة الموزع.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))
