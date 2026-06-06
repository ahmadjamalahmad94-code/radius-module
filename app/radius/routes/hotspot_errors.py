"""رسائل أخطاء الهوتسبوت — لوحة التحكم بنصوص errors.txt.

Routes:
  GET  /admin/radius/hotspot-errors          — جدول المفاتيح القابل للتحرير
  POST /admin/radius/hotspot-errors/save     — حفظ كل الرسائل + حالات التفعيل
  POST /admin/radius/hotspot-errors/reset     — استعادة الكل / مفتاح واحد

التخزين عام على مستوى المستأجر (router_id=0) — الصف العام يكفي
في v1؛ تجاوز لكل راوتر محجوز للمستقبل. النصوص تُبنى إلى
hotspot/errors.txt وتُرفع للراوتر تلقائيًا عند نشر صفحة الدخول
(انظر mt_login_designer.deploy + download.zip).
"""
from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import hotspot_error_messages_repo as err_repo
from ..services import hotspot_error_messages as hem
from ..services.audit import get_audit_service
from ..services.mt_permissions import (
    PERM_DEPLOY_LOGIN, PERM_VIEW, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_hotspot_errors_routes(bp: Blueprint) -> None:
    # العرض: صلاحية عرض الميكروتك. الحفظ/الاستعادة: نفس صلاحية نشر
    # صفحة الدخول (PERM_DEPLOY_LOGIN) لأن هذه النصوص تُرفع للراوتر.
    bp.add_url_rule(
        "/hotspot-errors",
        "hotspot_errors_page",
        requires_perm(PERM_VIEW)(hotspot_errors_page),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/hotspot-errors/save",
        "hotspot_errors_save",
        requires_perm(PERM_DEPLOY_LOGIN)(hotspot_errors_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/hotspot-errors/reset",
        "hotspot_errors_reset",
        requires_perm(PERM_DEPLOY_LOGIN)(hotspot_errors_reset),
        methods=["POST"],
    )


def _rows_for_render():
    """صفوف الجدول: لكل مفتاح قياسي — التعريف + القيمة المخزّنة
    (أو الافتراضية) + حالة التفعيل. يزرع الافتراضيات أولًا فتظهر
    كل المفاتيح مهيّأة عند أول فتح."""
    tid = _tid()
    err_repo.seed_defaults(tid)
    stored = err_repo.list_messages(tid)
    defaults = hem.default_messages()
    rows = []
    for e in hem.ERROR_KEYS:
        row = stored.get(e.key) or {}
        rows.append({
            "key": e.key,
            "name_ar": e.name_ar,
            "when_ar": e.when_ar,
            "default_ar": e.default_ar,
            "message_ar": row.get("message_ar") or defaults[e.key],
            "enabled": row.get("enabled", True),
            "is_custom": (row.get("message_ar") or defaults[e.key])
                         != defaults[e.key],
        })
    return rows


def _render(*, saved: bool = False, error: str = "",
            flash_ok: str = ""):
    return render_template(
        "radius/hotspot_errors.html",
        rows=_rows_for_render(),
        allowed_vars=hem.ALLOWED_VARS,
        msg_max_len=hem.MESSAGE_MAX_LEN,
        saved=saved,
        error=error,
        flash_ok=flash_ok,
    )


def hotspot_errors_page():
    return _render()


def hotspot_errors_save():
    """يحفظ نصّ كل مفتاح وحالة تفعيله من النموذج. كل مفتاح يُفحص
    عبر validate_message؛ أول قيمة غير صالحة توقف الحفظ برسالة
    عربية تشير إلى المفتاح."""
    tid = _tid()
    err_repo.seed_defaults(tid)
    error = ""
    saved = False
    # نجمع القيم أولًا ونتحقق منها كلها قبل أي كتابة — فلا يُحفظ
    # نصف النموذج عند خطأ في حقل واحد.
    pending: list[tuple[str, str, bool]] = []
    for e in hem.ERROR_KEYS:
        raw = request.form.get("msg__" + e.key)
        if raw is None:
            continue  # مفتاح غائب من النموذج — نتركه كما هو
        enabled = request.form.get("en__" + e.key) == "1"
        try:
            msg = hem.validate_message(raw)
        except ValueError as ex:
            error = f"«{e.name_ar}»: {ex}"
            break
        if not msg:
            # نص فارغ → نسقط للافتراضي بدل تخزين سطر فارغ.
            msg = e.default_ar
        pending.append((e.key, msg, enabled))
    if not error:
        for key, msg, enabled in pending:
            err_repo.save_message(
                tid, key, message_ar=msg, enabled=enabled)
        saved = True
        get_audit_service().record(
            actor=str(getattr(g, "admin_id", None) or "ui"),
            action="hotspot.error_messages.save",
            target_type="tenant",
            target_id=str(tid),
            severity="info",
            result_status="success",
            payload={"count": len(pending)},
        )
    return _render(saved=saved, error=error)


def hotspot_errors_reset():
    """استعادة الافتراضي — مفتاح واحد (key في النموذج) أو الكل."""
    tid = _tid()
    err_repo.seed_defaults(tid)
    key = (request.form.get("key") or "").strip()
    if key and key in hem.ERROR_KEYS_BY_KEY:
        err_repo.reset_message(tid, key)
        flash_ok = (f"استُعيد النص الافتراضي للرسالة "
                    f"«{hem.ERROR_KEYS_BY_KEY[key].name_ar}».")
        _action_payload = {"key": key}
    else:
        err_repo.reset_all(tid)
        flash_ok = "استُعيدت كل الرسائل إلى نصوصها الافتراضية."
        _action_payload = {"key": "*"}
    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action="hotspot.error_messages.reset",
        target_type="tenant",
        target_id=str(tid),
        severity="info",
        result_status="success",
        payload=_action_payload,
    )
    return _render(flash_ok=flash_ok)


__all__ = ["register_hotspot_errors_routes"]
