"""لوحة دعم المتجر المتقدّم (للمدير) — تحت قسم سوق البطاقات.

من هذه اللوحة يدير المدير المتجر المتقدّم بكامله:
  • مراجعة وتأكيد/رفض طلبات الشحن (الإيداع) — الرصيد يُضاف عند التأكيد فقط.
  • مراجعة وتأكيد/رفض طلبات السحب — الرصيد يُخصم عند التأكيد فقط.
  • محادثة الزبائن (شات الدعم) — نص + إرفاق صورة.
  • ضبط محافظ الاستلام (قنوات الدفع التي يعرضها المتجر للزبون).

كل العمليات المالية تمرّ عبر الخدمات المبنيّة مسبقًا (DepositRequestService /
WithdrawalRequestService) بحارس idempotency صارم — لا حركة مال في هذه الطبقة.
المسارات تتبع أعراف card_users_marketplace.py: خدمات تُبنى لكل طلب،
flash + redirect، والتقاط أخطاء الخدمة + ValueError.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..services.store_chat import StoreChatError, StoreChatService
from ..services.store_deposits import DepositRequestService, StoreDepositError
from ..services.store_uploads import StoreUploadError, save_store_image
from ..services.store_withdrawals import (
    StoreWithdrawalError,
    WithdrawalRequestService,
)


def register_store_support_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/store-support", "store_support", store_support, methods=["GET"])
    bp.add_url_rule(
        "/store-support/deposits/<int:req_id>/confirm",
        "store_support_deposit_confirm",
        store_support_deposit_confirm,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/deposits/<int:req_id>/reject",
        "store_support_deposit_reject",
        store_support_deposit_reject,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/withdrawals/<int:req_id>/confirm",
        "store_support_withdrawal_confirm",
        store_support_withdrawal_confirm,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/withdrawals/<int:req_id>/reject",
        "store_support_withdrawal_reject",
        store_support_withdrawal_reject,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/payment-methods",
        "store_support_payment_method_create",
        store_support_payment_method_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/payment-methods/<int:method_id>",
        "store_support_payment_method_update",
        store_support_payment_method_update,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/store-support/chat/<int:card_user_id>",
        "store_support_chat_post",
        store_support_chat_post,
        methods=["POST"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _deposits() -> DepositRequestService:
    return DepositRequestService(tenant_id=_tid())


def _withdrawals() -> WithdrawalRequestService:
    return WithdrawalRequestService(tenant_id=_tid())


def _chat() -> StoreChatService:
    return StoreChatService(tenant_id=_tid())


def _whatsapp_configured(tenant_id: int) -> bool:
    """هل ضُبط «رقم واتساب الدعم» (SUPPORT_WHATSAPP) في أي تصميم صفحة
    دخول محفوظ؟ يُحقن الرقم في store.html عند النشر فيظهر زر واتساب
    للزبائن — إن كان فارغًا في كل التصاميم يختفي الزر، فنُظهر تلميحًا
    للمدير ليضبطه من المصمّم. أفضل-جهد: أي خطأ ⇒ True (لا نزعج بتلميح
    خاطئ)."""
    try:
        import json
        import re as _re
        from ..db.connection import db
        rows = db().execute(
            "SELECT variables_json FROM hotspot_designs WHERE tenant_id=?",
            (int(tenant_id),),
        ).fetchall()
        for r in rows:
            try:
                v = json.loads(r["variables_json"] or "{}")
            except (TypeError, ValueError):
                v = {}
            if _re.sub(r"\D", "", str(v.get("SUPPORT_WHATSAPP") or "")):
                return True
        return False
    except Exception:  # noqa: BLE001 — التلميح لا يكسر الصفحة
        return True


def _ordered(requests: list[dict]) -> list[dict]:
    """المعلّقات أولًا (الأقدم أولًا لتُعالج بالترتيب) ثم المحسوم (الأحدث أولًا)."""
    pending = [r for r in requests if (r.get("status") or "") == "pending"]
    resolved = [r for r in requests if (r.get("status") or "") != "pending"]
    pending.sort(key=lambda r: int(r.get("id") or 0))
    resolved.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
    return pending, resolved


# ───────────────────────── العرض (GET) ─────────────────────────


def store_support():
    deposits_svc = _deposits()
    withdrawals_svc = _withdrawals()
    chat_svc = _chat()

    deposit_pending, deposit_resolved = _ordered(deposits_svc.list_requests(limit=200))
    withdrawal_pending, withdrawal_resolved = _ordered(
        withdrawals_svc.list_requests(limit=200)
    )

    # شات مفتوح اختياري — ?chat=<card_user_id> يحمّل خيط زبون واحد.
    open_thread = None
    open_chat_id = None
    raw_chat = request.args.get("chat")
    if raw_chat:
        try:
            open_chat_id = int(raw_chat)
        except (TypeError, ValueError):
            open_chat_id = None
        if open_chat_id:
            try:
                open_thread = chat_svc.thread_for_admin(card_user_id=open_chat_id)
                chat_svc.mark_read(card_user_id=open_chat_id, reader="admin")
            except StoreChatError:
                open_thread = None

    chat_threads = chat_svc.list_threads(limit=200)
    # مجموع رسائل الزبائن غير المقروءة (للمدير) — شارة تبويب الشات.
    chat_unread_count = sum(
        int(t.get("unread_admin_count") or 0) for t in chat_threads)

    return render_template(
        "radius/store_support.html",
        deposit_pending=deposit_pending,
        deposit_resolved=deposit_resolved,
        withdrawal_pending=withdrawal_pending,
        withdrawal_resolved=withdrawal_resolved,
        chat_threads=chat_threads,
        chat_unread_count=chat_unread_count,
        open_thread=open_thread,
        open_chat_id=open_chat_id,
        payment_methods=deposits_svc.list_payment_methods(),
        deposit_pending_count=deposits_svc.pending_count(),
        withdrawal_pending_count=withdrawals_svc.pending_count(),
        whatsapp_configured=_whatsapp_configured(_tid()),
    )


# ───────────────────────── الإيداع (الشحن) ─────────────────────────


def store_support_deposit_confirm(req_id: int):
    raw_amount = (request.form.get("confirmed_amount") or "").strip()
    confirmed_amount = raw_amount if raw_amount else None
    try:
        _deposits().confirm(
            req_id,
            actor=_actor(),
            confirmed_amount=confirmed_amount,
            note=request.form.get("note") or "",
        )
        flash("تم تأكيد طلب الشحن وإضافة الرصيد لمحفظة الزبون.", "success")
    except (StoreDepositError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


def store_support_deposit_reject(req_id: int):
    try:
        _deposits().reject(req_id, actor=_actor(), note=request.form.get("note") or "")
        flash("تم رفض طلب الشحن.", "success")
    except (StoreDepositError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


# ───────────────────────── السحب ─────────────────────────


def store_support_withdrawal_confirm(req_id: int):
    try:
        _withdrawals().confirm(
            req_id, actor=_actor(), note=request.form.get("note") or ""
        )
        flash("تم تأكيد تنفيذ السحب وخصم الرصيد من محفظة الزبون.", "success")
    except (StoreWithdrawalError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


def store_support_withdrawal_reject(req_id: int):
    try:
        _withdrawals().reject(
            req_id, actor=_actor(), note=request.form.get("note") or ""
        )
        flash("تم رفض طلب السحب.", "success")
    except (StoreWithdrawalError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


# ───────────────────────── محافظ الاستلام (قنوات الدفع) ─────────────────────────


def store_support_payment_method_create():
    try:
        qr_path = ""
        upload = request.files.get("qr_image")
        if upload is not None and getattr(upload, "filename", ""):
            qr_path = save_store_image(upload, subdir="qr")["path"]
        logo_path = ""
        logo_upload = request.files.get("logo_image")
        if logo_upload is not None and getattr(logo_upload, "filename", ""):
            logo_path = save_store_image(logo_upload, subdir="logo")["path"]
        _deposits().create_payment_method(
            method=request.form.get("method") or "other",
            label=request.form.get("label") or "",
            account_name=request.form.get("account_name") or "",
            account_number=request.form.get("account_number") or "",
            instructions=request.form.get("instructions") or "",
            qr_image_path=qr_path,
            logo_image_path=logo_path,
            sort_order=int(request.form.get("sort_order") or 0),
        )
        flash("تمت إضافة قناة استلام جديدة.", "success")
    except (StoreDepositError, StoreUploadError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


def store_support_payment_method_update(method_id: int):
    svc = _deposits()
    if (request.form.get("action") or "").strip().lower() == "delete":
        try:
            svc.delete_payment_method(method_id)
            flash("تم حذف قناة الاستلام.", "success")
        except (StoreDepositError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("radius.store_support"))

    try:
        fields: dict = {}
        for key in ("method", "label", "account_name", "account_number",
                    "instructions"):
            if key in request.form:
                fields[key] = request.form.get(key)
        if "sort_order" in request.form:
            fields["sort_order"] = int(request.form.get("sort_order") or 0)
        if "active" in request.form:
            fields["active"] = 1 if (request.form.get("active") in ("1", "on", "true")) else 0
        upload = request.files.get("qr_image")
        if upload is not None and getattr(upload, "filename", ""):
            fields["qr_image_path"] = save_store_image(upload, subdir="qr")["path"]
        logo_upload = request.files.get("logo_image")
        if logo_upload is not None and getattr(logo_upload, "filename", ""):
            fields["logo_image_path"] = save_store_image(
                logo_upload, subdir="logo")["path"]
        svc.update_payment_method(method_id, **fields)
        flash("تم تحديث قناة الاستلام.", "success")
    except (StoreDepositError, StoreUploadError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support"))


# ───────────────────────── الشات ─────────────────────────


def store_support_chat_post(card_user_id: int):
    try:
        image_path = ""
        upload = request.files.get("image")
        if upload is not None and getattr(upload, "filename", ""):
            image_path = save_store_image(upload, subdir="chat")["path"]
        _chat().post_message(
            card_user_id=card_user_id,
            sender="admin",
            body=request.form.get("body") or "",
            image_path=image_path,
            admin_actor=_actor(),
        )
    except (StoreChatError, StoreUploadError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.store_support", chat=card_user_id))


__all__ = ["register_store_support_routes"]
