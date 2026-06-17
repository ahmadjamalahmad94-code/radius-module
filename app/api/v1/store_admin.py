"""store-admin — v1 JSON API (feat/api-first-endpoints).

Mirrors the admin store-support page (`routes/store_support.py`,
`/admin/radius/store-support`): review/confirm/reject deposit & withdrawal
requests, manage receiving payment-methods, and the support chat (list
threads, post admin reply, set thread status). Reuses the store services
(DepositRequestService / WithdrawalRequestService / StoreChatService) — no
duplicated logic.

Admin-authed via require_api_token (NOT the card-user store token used by the
customer-facing `store.py`). Tenant-scoped via g.tenant_id.

Bodies accept JSON or form/multipart (so image uploads — qr/logo/chat image —
work via multipart while pure-data clients can send JSON).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.store_chat import StoreChatError, StoreChatService
from ...radius.services.store_deposits import DepositRequestService, StoreDepositError
from ...radius.services.store_uploads import StoreUploadError, save_store_image
from ...radius.services.store_withdrawals import (
    StoreWithdrawalError, WithdrawalRequestService,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return str(getattr(g, "admin_name", "") or getattr(g, "admin_user", "") or "api")


def _deposits() -> DepositRequestService:
    return DepositRequestService(tenant_id=_tid())


def _withdrawals() -> WithdrawalRequestService:
    return WithdrawalRequestService(tenant_id=_tid())


def _chat() -> StoreChatService:
    return StoreChatService(tenant_id=_tid())


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _field(key: str, default: str = "") -> str:
    b = _body()
    if key in b:
        return b[key]
    return request.form.get(key, default)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/store/admin/support", "store_admin_support",
                    require_api_token(support_dashboard), methods=["GET"])
    bp.add_url_rule("/store/admin/deposits/<int:req_id>/confirm",
                    "store_admin_deposit_confirm",
                    require_api_token(deposit_confirm), methods=["POST"])
    bp.add_url_rule("/store/admin/deposits/<int:req_id>/reject",
                    "store_admin_deposit_reject",
                    require_api_token(deposit_reject), methods=["POST"])
    bp.add_url_rule("/store/admin/withdrawals/<int:req_id>/confirm",
                    "store_admin_withdrawal_confirm",
                    require_api_token(withdrawal_confirm), methods=["POST"])
    bp.add_url_rule("/store/admin/withdrawals/<int:req_id>/reject",
                    "store_admin_withdrawal_reject",
                    require_api_token(withdrawal_reject), methods=["POST"])
    bp.add_url_rule("/store/admin/payment-methods", "store_admin_pm_list",
                    require_api_token(pm_list), methods=["GET"])
    bp.add_url_rule("/store/admin/payment-methods", "store_admin_pm_create",
                    require_api_token(pm_create), methods=["POST"])
    bp.add_url_rule("/store/admin/payment-methods/<int:method_id>",
                    "store_admin_pm_update",
                    require_api_token(pm_update), methods=["PATCH", "PUT"])
    bp.add_url_rule("/store/admin/payment-methods/<int:method_id>",
                    "store_admin_pm_delete",
                    require_api_token(pm_delete), methods=["DELETE"])
    bp.add_url_rule("/store/admin/chat/<int:card_user_id>", "store_admin_chat_get",
                    require_api_token(chat_thread), methods=["GET"])
    bp.add_url_rule("/store/admin/chat/<int:card_user_id>", "store_admin_chat_post",
                    require_api_token(chat_post), methods=["POST"])
    bp.add_url_rule("/store/admin/chat/<int:card_user_id>/status",
                    "store_admin_chat_status",
                    require_api_token(chat_status), methods=["POST"])


def _ordered(requests: list[dict]) -> tuple[list[dict], list[dict]]:
    """المعلّقات أولًا (الأقدم أولًا) ثم المحسومة (الأحدث أولًا) — يطابق الويب."""
    pending = [r for r in requests if (r.get("status") or "") == "pending"]
    resolved = [r for r in requests if (r.get("status") or "") != "pending"]
    pending.sort(key=lambda r: int(r.get("id") or 0))
    resolved.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
    return pending, resolved


# ───────────────────────── لوحة الدعم ─────────────────────────

def support_dashboard():
    """GET /store/admin/support — كل ما تعرضه صفحة دعم المتجر."""
    dep = _deposits()
    wd = _withdrawals()
    chat = _chat()
    dep_pending, dep_resolved = _ordered(dep.list_requests(limit=200))
    wd_pending, wd_resolved = _ordered(wd.list_requests(limit=200))
    threads = chat.list_threads(limit=200)
    return ok({
        "deposits": {"pending": dep_pending, "resolved": dep_resolved,
                     "pending_count": dep.pending_count()},
        "withdrawals": {"pending": wd_pending, "resolved": wd_resolved,
                        "pending_count": wd.pending_count()},
        "chat_threads": threads,
        "chat_unread_count": sum(int(t.get("unread_admin_count") or 0) for t in threads),
        "payment_methods": dep.list_payment_methods(),
    })


# ───────────────────────── الإيداع ─────────────────────────

def deposit_confirm(req_id: int):
    raw = str(_field("confirmed_amount", "")).strip()
    try:
        _deposits().confirm(int(req_id), actor=_actor(),
                            confirmed_amount=(raw or None),
                            note=_field("note", ""))
    except (StoreDepositError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"request_id": int(req_id), "status": "confirmed"})


def deposit_reject(req_id: int):
    try:
        _deposits().reject(int(req_id), actor=_actor(), note=_field("note", ""))
    except (StoreDepositError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"request_id": int(req_id), "status": "rejected"})


# ───────────────────────── السحب ─────────────────────────

def withdrawal_confirm(req_id: int):
    try:
        _withdrawals().confirm(int(req_id), actor=_actor(), note=_field("note", ""))
    except (StoreWithdrawalError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"request_id": int(req_id), "status": "confirmed"})


def withdrawal_reject(req_id: int):
    try:
        _withdrawals().reject(int(req_id), actor=_actor(), note=_field("note", ""))
    except (StoreWithdrawalError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"request_id": int(req_id), "status": "rejected"})


# ───────────────────────── محافظ الاستلام ─────────────────────────

def pm_list():
    return ok({"payment_methods": _deposits().list_payment_methods()})


def _saved_image(field: str, subdir: str) -> str:
    up = request.files.get(field)
    if up is not None and getattr(up, "filename", ""):
        return save_store_image(up, subdir=subdir)["path"]
    return str(_field(f"{field}_path", "") or "")


def pm_create():
    try:
        m = _deposits().create_payment_method(
            method=_field("method", "other") or "other",
            label=_field("label", ""),
            account_name=_field("account_name", ""),
            account_number=_field("account_number", ""),
            instructions=_field("instructions", ""),
            qr_image_path=_saved_image("qr_image", "qr"),
            logo_image_path=_saved_image("logo_image", "logo"),
            sort_order=int(_field("sort_order", 0) or 0),
        )
    except (StoreDepositError, StoreUploadError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"payment_method": m}, status=201)


def pm_update(method_id: int):
    fields: dict = {}
    for key in ("method", "label", "account_name", "account_number", "instructions"):
        if key in _body() or key in request.form:
            fields[key] = _field(key, "")
    if "sort_order" in _body() or "sort_order" in request.form:
        fields["sort_order"] = int(_field("sort_order", 0) or 0)
    if "active" in _body() or "active" in request.form:
        fields["active"] = 1 if str(_field("active", "")) in ("1", "on", "true", "True", "yes") else 0
    qr = _saved_image("qr_image", "qr")
    if qr:
        fields["qr_image_path"] = qr
    logo = _saved_image("logo_image", "logo")
    if logo:
        fields["logo_image_path"] = logo
    try:
        m = _deposits().update_payment_method(int(method_id), **fields)
    except (StoreDepositError, StoreUploadError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"payment_method": m})


def pm_delete(method_id: int):
    try:
        _deposits().delete_payment_method(int(method_id))
    except (StoreDepositError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"deleted": True, "method_id": int(method_id)})


# ───────────────────────── الشات ─────────────────────────

def chat_thread(card_user_id: int):
    """GET /store/admin/chat/<cu> — رسائل الخيط + حالته. يُعلّم رسائل الزبون
    مقروءة (مثل صفحة الويب)."""
    chat = _chat()
    try:
        thread = chat.thread_for_admin(card_user_id=int(card_user_id))
        chat.mark_read(card_user_id=int(card_user_id), reader="admin")
    except StoreChatError as exc:
        return fail("store_error", str(exc), status=422)
    meta = chat.get_thread_meta(card_user_id=int(card_user_id))
    return ok({"thread": thread, "status": meta.get("status") or "open", "meta": meta})


def chat_post(card_user_id: int):
    """POST /store/admin/chat/<cu> — ردّ المدير (نص + صورة اختيارية)."""
    image_path = _saved_image("image", "chat")
    try:
        msg = _chat().post_message(
            card_user_id=int(card_user_id), sender="admin",
            body=_field("body", ""), image_path=image_path, admin_actor=_actor())
    except (StoreChatError, StoreUploadError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"message": msg}, status=201)


def chat_status(card_user_id: int):
    """POST /store/admin/chat/<cu>/status — ضبط حالة الخيط (open|resolved)."""
    status = str(_field("status", "resolved")).strip().lower()
    try:
        new = _chat().set_status(card_user_id=int(card_user_id), status=status,
                                 actor=_actor())
    except (StoreChatError, ValueError) as exc:
        return fail("store_error", str(exc), status=422)
    return ok({"card_user_id": int(card_user_id), "status": new})
