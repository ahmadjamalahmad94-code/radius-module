"""Web admin screens for distributor operations."""
from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..auth.session_helpers import current_admin_id, is_super_admin
from ..core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..core.messages_ar import error_message_ar
from ..core.system_config import default_currency
from ..db.repos import admins_repo
from ..services.cards import get_cards_service
from ..services.manager_distributor_ops import ManagerDistributorOpsService
from ..services.operations import get_operations_service
from ..core.numbers import NonFiniteNumber, strict_float  # Infinity/NaN → ValueError (422/flash)


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


def _can_manage_distributors() -> bool:
    """من يَملك إنشاء/إدارة موزّعين؟ المالك الرئيسي دائمًا؛ والمدير المحدود
    فقط إن مُنِح صلاحية ``can_manage_distributors`` من صفحة المشغّل."""
    if is_super_admin():
        return True
    me = current_admin_id()
    if not me:
        return False
    return ManagerDistributorOpsService(tenant_id=_tid()).has_permission(
        entity_type="manager", entity_id=int(me), permission="can_manage_distributors"
    )


def _sees_all() -> bool:
    """المالك/الشريك أو دور «مدير عام» (كل الصلاحيات غير المالكيّة) يرى ويدير كل
    الموزّعين؛ غيره موزّعيه فقط، ودخول الموزّع لا يتّسع أبدًا."""
    if is_super_admin():
        return True
    from ..services.distributor_scope import sees_all_distributors
    return sees_all_distributors(current_admin_id(), tenant_id=_tid())


def _owner_admin_id() -> int | None:
    """المالك المُسنَد للموزّع عند الإنشاء/التعديل.

    محدود → نفسه دائمًا (مقفل، يَتجاهل أيّ admin_id مُرسَل بالنموذج).
    سوبر/«مدير عام» → القيمة المختارة من النموذج (مدير بعينه) أو None (بلا مالك؛
    في التعديل None = يبقى المالك الحاليّ)."""
    if not _sees_all():
        return current_admin_id()
    raw = (request.form.get("admin_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _assert_distributor_access(distributor: dict) -> None:
    """يَمنع المدير المحدود من لمس موزّعٍ لا يَتبع له (مِلكية admin_id).
    السوبر/«مدير عام» يَصل للكل. عدم التطابق → 403 (لا 404 حتى لا نُفشي وجوده)."""
    if _sees_all():
        return
    me = current_admin_id()
    owner = int(distributor.get("admin_id") or 0)
    if not me or owner != int(me):
        abort(403)


def _managers_for_form() -> list:
    """قائمة المدراء لاختيار مالك الموزّع — للسوبر/«مدير عام». المحدود يَرى نفسه."""
    if _sees_all():
        return admins_repo.list_admins()
    me = current_admin_id()
    return [a for a in admins_repo.list_admins() if a.id == me]


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _float_field(name: str, default: float = 0.0) -> float:
    raw = _field(name)
    if not raw:
        return default
    try:
        return strict_float(raw)
    except NonFiniteNumber:
        # Infinity/NaN/1e400: same wording as the shared service check.
        raise RadiusValidationError(f"قيمة الحقل «{name}» خارج النطاق المسموح.") from None
    except ValueError:
        raise RadiusValidationError(f"قيمة الحقل «{name}» يجب أن تكون رقمية.") from None


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
        "admin_id": _owner_admin_id(),
    }


def distributors_list():
    status = (request.args.get("status") or "").strip() or None
    # عزل المِلكية: المدير المحدود يَرى موزّعيه فقط؛ السوبر/«مدير عام» يَرى الكل.
    scope_admin = None if _sees_all() else current_admin_id()
    items = _svc().list_distributors(
        tenant_id=_tid(), status=status, admin_id=scope_admin, limit=500
    )
    return render_template(
        "radius/distributors_list.html",
        distributors=items,
        status=status or "",
        # ?new=1 يفتح الصندوق العائم «إضافة موزع» تلقائيًا (رابط /distributors/new القديم)
        open_new_modal=(request.args.get("new") == "1"),
        is_super=_sees_all(),
        can_manage_distributors=_can_manage_distributors(),
        managers=_managers_for_form(),
        current_manager_id=current_admin_id(),
    )


def distributors_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.distributors_list", new=1))


def distributors_create():
    # بوّابة خادميّة: المدير المحدود بلا صلاحية «إدارة الموزعين» يُرفَض (403).
    if not _can_manage_distributors():
        flash("لا تملك صلاحية إدارة الموزعين. اطلب من المالك تفعيلها.", "error")
        abort(403)
    try:
        saved = _svc().create_distributor(
            tenant_id=_tid(),
            actor=_actor(),
            data=_form_payload(),
        )
    except RadiusValidationError as e:
        flash(error_message_ar(e), "error")
        return render_template(
            "radius/distributors_form.html",
            form=request.form,
            is_new=True,
            is_super=_sees_all(),
            managers=_managers_for_form(),
            current_manager_id=current_admin_id(),
        ), 400
    _maybe_set_portal_password(saved["id"])
    flash("تم إنشاء الموزع.", "success")
    return redirect(url_for("radius.distributors_detail", distributor_id=saved["id"]))


def _maybe_set_portal_password(distributor_id: int) -> None:
    """يحفظ hash كلمة مرور بوابة «فحص كروت» إن أُدخلت (فارغة = لا تغيير).

    الحقل اختياري في نموذجي الإنشاء والتعديل؛ لا يُخزَّن نص صريح أبدًا."""
    raw = (request.form.get("portal_password") or "").strip()
    if not raw:
        return
    from werkzeug.security import generate_password_hash

    from ..db.repos import operations_repo
    operations_repo.set_distributor_portal_password(
        _tid(), int(distributor_id), generate_password_hash(raw)
    )


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
    if not _can_manage_distributors():
        abort(403)
    try:
        distributor = _svc().get_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
        )
    except RadiusNotFound:
        abort(404)
    _assert_distributor_access(distributor)
    return render_template(
        "radius/distributors_form.html",
        form=_distributor_form_values(distributor),
        distributor=distributor,
        is_new=False,
        is_super=_sees_all(),
        managers=_managers_for_form(),
        current_manager_id=current_admin_id(),
    )


def distributors_update(distributor_id: int):
    if not _can_manage_distributors():
        abort(403)
    try:
        existing = _svc().get_distributor(tenant_id=_tid(), distributor_id=distributor_id)
        _assert_distributor_access(existing)
        _svc().update_distributor(
            tenant_id=_tid(),
            distributor_id=distributor_id,
            actor=_actor(),
            data=_form_payload(),
        )
    except RadiusNotFound:
        abort(404)
    except RadiusValidationError as e:
        flash(error_message_ar(e), "error")
        return render_template(
            "radius/distributors_form.html",
            form=request.form,
            distributor={"id": distributor_id},
            is_new=False,
            is_super=_sees_all(),
            managers=_managers_for_form(),
            current_manager_id=current_admin_id(),
        ), 400
    _maybe_set_portal_password(distributor_id)
    flash("تم تحديث الموزع.", "success")
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))


def _detail_context(distributor_id: int) -> dict:
    # عزل المِلكية قبل أيّ عرض/إجراء على الموزّع.
    try:
        _assert_distributor_access(
            _svc().get_distributor(tenant_id=_tid(), distributor_id=distributor_id)
        )
    except RadiusNotFound:
        abort(404)
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
    from uuid import uuid4
    return render_template(
        "radius/distributors_detail.html",
        # مفتاح تكرار لكلّ عرضٍ للنموذج: نقرتان/إعادة إرسال = حركة واحدة.
        idem_nonce=uuid4().hex,
        **_detail_context(distributor_id),
    )


def distributors_assign_batch(distributor_id: int):
    try:
        _assert_distributor_access(
            _svc().get_distributor(tenant_id=_tid(), distributor_id=distributor_id)
        )
    except RadiusNotFound:
        abort(404)
    # رقم الحزمة أو رمزها الظاهر (B-…) — نفس محلّل الـAPI.
    ref = request.form.get("batch_id") or ""
    is_code = False
    if not ref and request.form.get("batch_code"):
        ref, is_code = request.form.get("batch_code") or "", True
    try:
        batch_id = _svc().resolve_batch_ref(_tid(), ref, is_code=is_code)
    except RadiusError as e:
        flash(e.message if ref else "اختر حزمة كروت صحيحة.", "error")
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
        flash(error_message_ar(e), "error")
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))


def distributors_settle(distributor_id: int):
    try:
        _assert_distributor_access(
            _svc().get_distributor(tenant_id=_tid(), distributor_id=distributor_id)
        )
    except RadiusNotFound:
        abort(404)
    # منع التكرار (نفس منطق الـAPI): المفتاح المخفيّ في النموذج يُحجز قبل
    # التسجيل؛ نقرةٌ ثانية/إعادة إرسال بالمفتاح نفسه لا تُسجّل حركةً ثانية.
    from ..services import idempotency as _idem
    idem_key = (request.form.get("client_request_id") or "").strip()[:_idem.MAX_KEY]
    idem_scope = f"WEB POST {request.path}"
    if idem_key:
        _state, _row = _idem.claim(
            _tid(), idem_key, idem_scope,
            _idem.fingerprint("POST", request.path, request.form.to_dict(flat=True)))
        if _state == _idem.REPLAY:
            flash("سُجِّلت هذه الحركة مسبقًا — لم تُكرَّر.", "warning")
            return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))
        if _state in (_idem.IN_PROGRESS, _idem.MISMATCH):
            flash("طلبٌ بنفس النموذج قيد التنفيذ أو استُخدم لحركةٍ أخرى — حدّث الصفحة.", "error")
            return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))
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
                # «إضافة للرصيد» / «خصم من الدين» — فارغ = الافتراضيّ في الخدمة.
                "apply_to": _field("apply_to"),
            },
        )
        flash("تم تسجيل حركة الموزع.", "success")
        if idem_key:
            _idem.finish(_tid(), idem_key, idem_scope, 302, "{}")
    except RadiusError as e:
        if idem_key:
            _idem.release(_tid(), idem_key, idem_scope)
        flash(error_message_ar(e), "error")
    except Exception:
        if idem_key:
            _idem.release(_tid(), idem_key, idem_scope)
        raise
    return redirect(url_for("radius.distributors_detail", distributor_id=distributor_id))
