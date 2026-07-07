"""«تنظيف البيانات التجريبيّة» — إزالة حركات المال المبذورة (demo-seed) للمالك فقط.

  • GET  /admin/radius/demo-cleanup          — صفحة الأداة (معاينة العدّاد).
  • POST /admin/radius/demo-cleanup/preview  — عدّاد صفوف demo-seed (مراجعة).
  • POST /admin/radius/demo-cleanup/run      — نسخة احتياطيّة إلزاميّة ثمّ حذف
                                               ذرّيّ لصفوف demo-seed فقط + تقرير.

أمان: **المالك وحده** (``is_primary_owner`` / مجموعة المالكين). الحارس المركزيّ
(_PERM_GUARDED = __super__) يَمنع غير المالك بـ403 على كل method؛ ونُضيف
``_require_owner`` في رأس كل معالِج كدفاع عميق. النسخة الاحتياطيّة تُنشَأ **قبل**
أيّ حذف، وإن فشلت يُلغى الحذف. الحذف يطابق العلامة ``demo-seed`` بالضبط فلا يمسّ
أيّ حركة حقيقيّة. الأداة idempotent: تشغيلها ثانيةً لا يحذف شيئًا.
"""
from __future__ import annotations

import logging
import os
import threading

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from ..auth.decorators import login_required
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.demo_cleanup import get_demo_cleanup_service

_LOG = logging.getLogger(__name__)

# كلمة التأكيد التي يكتبها المالك حرفيًّا قبل الحذف (مطابقة نمط data-reset).
CONFIRM_WORD = "حذف"

# قفل عمليّة واحدة في المرّة (منع الإرسال المزدوج / التنفيذ المتزامن).
_CLEANUP_LOCK = threading.Lock()


def register_demo_cleanup_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/demo-cleanup", "demo_cleanup_page",
                    login_required(demo_cleanup_page), methods=["GET"])
    bp.add_url_rule("/demo-cleanup/preview", "demo_cleanup_preview",
                    login_required(demo_cleanup_preview), methods=["POST"])
    bp.add_url_rule("/demo-cleanup/run", "demo_cleanup_run",
                    login_required(demo_cleanup_run), methods=["POST"])


# ── helpers ──────────────────────────────────────────────────────────

def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "owner"


def _current_admin_id() -> int | None:
    from ..auth.session_helpers import current_admin_id
    return current_admin_id()


def _require_owner() -> None:
    """403 لغير المالك (دفاع عميق فوق الحارس المركزيّ)."""
    from ..db.repos import admins_repo
    if not admins_repo.is_primary_owner(_current_admin_id()):
        abort(403)


# ── views ────────────────────────────────────────────────────────────

def demo_cleanup_page():
    _require_owner()
    preview = get_demo_cleanup_service().preview(tenant_id=_tid())
    return render_template(
        "radius/demo_cleanup.html",
        preview=preview,
        confirm_word=CONFIRM_WORD,
    )


def demo_cleanup_preview():
    _require_owner()
    out = get_demo_cleanup_service().preview(tenant_id=_tid())
    return jsonify(out)


def demo_cleanup_run():
    _require_owner()
    payload = request.get_json(silent=True) or {}
    confirm = str(payload.get("confirm") or "").strip()
    if confirm != CONFIRM_WORD:
        return jsonify({"ok": False, "code": "confirm",
                        "message": f"للمتابعة اكتب كلمة التأكيد «{CONFIRM_WORD}» بالضبط."}), 200

    # قفل: عمليّة واحدة في المرّة.
    if not _CLEANUP_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "code": "busy",
                        "message": "هناك عمليّة تنظيف جارية بالفعل. انتظر انتهاءها."}), 200
    try:
        t = _tid()
        actor = _actor()

        # لا شيء للحذف؟ لا تُنشئ نسخة ولا تُنفّذ (idempotent + بلا نسخ عبثيّة).
        pre = get_demo_cleanup_service().preview(tenant_id=t)
        if not pre.get("total"):
            return jsonify({"ok": True, "code": "empty", "total_deleted": 0,
                            "report": [],
                            "message": "لا توجد صفوف تجريبيّة (demo-seed) للحذف."}), 200

        # ── (1) نسخة احتياطيّة إلزاميّة أوّلًا (أرشيف كامل مضغوط gzip) ──
        from ..services.operations import get_operations_service
        try:
            bk = get_operations_service().run_local_backup(
                tenant_id=t, actor=actor, lean=False)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("demo-cleanup backup crashed")
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة — أُلغي الحذف "
                                       "ولم يُحذف شيء.",
                            "detail": str(exc)}), 200
        if not bk.get("verified"):
            msg = (bk.get("run") or {}).get("message") or "فشل التحقّق من النسخة."
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة موثوقة — أُلغي "
                                       "الحذف ولم يُحذف شيء.",
                            "detail": msg}), 200
        backup_name = os.path.basename((bk.get("run") or {}).get("path") or "")

        # ── (2) الحذف الذرّيّ لصفوف demo-seed فقط ──
        try:
            result = get_demo_cleanup_service().purge(tenant_id=t)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("demo-cleanup purge failed")
            return jsonify({
                "ok": False, "code": "error", "backup": backup_name,
                "message": f"تعذّر الحذف — أُعيد كل شيء (لم يُحذف). {exc}",
                "detail": str(exc),
            }), 200

        # ── (3) تدقيق + تقرير ──
        try:
            from ..services.audit import get_audit_service
            get_audit_service().record(
                actor=actor, action="data.demo_cleanup",
                target_type="tenant", target_id=str(t),
                payload={"backup": backup_name,
                         "total_deleted": result.get("total_deleted", 0),
                         "marker": result.get("marker")})
        except Exception:  # noqa: BLE001 — التدقيق لا يكسر النتيجة
            pass
        result["backup"] = backup_name
        result["message"] = "تم حذف البيانات التجريبيّة بنجاح."
        return jsonify(result)
    finally:
        _CLEANUP_LOCK.release()
