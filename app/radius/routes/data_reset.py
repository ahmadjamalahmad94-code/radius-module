"""مسارات «تصفير / تنظيف البيانات» — للمالك فقط.

  • GET  /admin/radius/data-reset          — صفحة الأداة (فئات + عدّادات).
  • POST /admin/radius/data-reset/summary  — عدّادات الفئات المُختارة (مراجعة).
  • POST /admin/radius/data-reset/run      — نسخة احتياطيّة إلزاميّة ثمّ تصفير
                                             ذرّيّ + تقرير.

أمان: **المالك وحده** (``is_primary_owner`` / مجموعة المالكين المعيَّنة). الحارس
المركزيّ (_PERM_GUARDED = __super__) يَمنع غير المالك بـ403 على كل method؛ ونُضيف
``_require_owner`` في رأس كل معالِج كدفاع عميق. النسخة الاحتياطيّة تُنشَأ **قبل**
أيّ حذف، وإن فشلت يُلغى التصفير. عمليّة واحدة في المرّة (قفل) لمنع الإرسال المزدوج.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from ..auth.decorators import login_required
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.data_reset import CONFIRM_WORD, get_data_reset_service

_LOG = logging.getLogger(__name__)

# قفل عمليّة واحدة لكل عمليّة (منع الإرسال المزدوج / التنفيذ المتزامن).
_WIPE_LOCK = threading.Lock()


def register_data_reset_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/data-reset", "data_reset_page",
                    login_required(data_reset_page), methods=["GET"])
    bp.add_url_rule("/data-reset/summary", "data_reset_summary",
                    login_required(data_reset_summary), methods=["POST"])
    bp.add_url_rule("/data-reset/run", "data_reset_run",
                    login_required(data_reset_run), methods=["POST"])


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


def _valid_keys(raw) -> list[str]:
    """يصفّي المفاتيح المُدخَلة إلى مجموعة الفئات المعروفة فقط."""
    known = set(get_data_reset_service().category_map().keys())
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    for k in raw:
        k = str(k or "").strip()
        if k in known and k not in seen:
            seen.append(k)
    return seen


# ── views ────────────────────────────────────────────────────────────

def data_reset_page():
    _require_owner()
    svc = get_data_reset_service()
    cats = svc.categories()
    # عدّادات أوّليّة لكل الفئات (تُعرَض مباشرةً كي يرى المالك حجم بياناته).
    summary = svc.summarize(
        tenant_id=_tid(), keys=[c.key for c in cats],
        current_admin_id=_current_admin_id())
    counts = {c["key"]: c["count"] for c in summary["categories"]}
    # جمّع الفئات حسب المجموعة للعرض.
    groups: dict[str, list] = {}
    for c in cats:
        groups.setdefault(c.group, []).append(c)
    group_labels = {
        "core": "البيانات الأساسيّة",
        "network": "الشبكة والأجهزة",
        "money": "المال",
        "logs": "السجلّات والجلسات",
    }
    return render_template(
        "radius/data_reset.html",
        categories=cats,
        counts=counts,
        groups=groups,
        group_labels=group_labels,
        confirm_word=CONFIRM_WORD,
    )


def data_reset_summary():
    _require_owner()
    payload = request.get_json(silent=True) or {}
    keys = _valid_keys(payload.get("keys"))
    if not keys:
        return jsonify({"ok": False, "message": "اختر فئة واحدة على الأقلّ."}), 200
    out = get_data_reset_service().summarize(
        tenant_id=_tid(), keys=keys, current_admin_id=_current_admin_id())
    return jsonify(out)


def data_reset_run():
    _require_owner()
    payload = request.get_json(silent=True) or {}
    keys = _valid_keys(payload.get("keys"))
    confirm = str(payload.get("confirm") or "").strip()

    if not keys:
        return jsonify({"ok": False, "code": "no_keys",
                        "message": "اختر فئة واحدة على الأقلّ للتصفير."}), 200
    # تأكيد صريح: يجب كتابة كلمة التأكيد حرفيًّا (تصفير) — أو «حذف» كبديل.
    if confirm != CONFIRM_WORD and confirm != "حذف":
        return jsonify({"ok": False, "code": "confirm",
                        "message": f"للمتابعة اكتب كلمة التأكيد «{CONFIRM_WORD}» بالضبط."}), 200

    # قفل: عمليّة واحدة في المرّة (منع الإرسال المزدوج).
    if not _WIPE_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "code": "busy",
                        "message": "هناك عمليّة تصفير جارية بالفعل. انتظر انتهاءها."}), 200
    try:
        t = _tid()
        actor = _actor()
        # ── (1) نسخة احتياطيّة إلزاميّة أوّلًا (أرشيف كامل مضغوط gzip) ──
        from ..services.operations import get_operations_service
        try:
            bk = get_operations_service().run_local_backup(
                tenant_id=t, actor=actor, lean=False)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("data-reset backup crashed")
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة — أُلغي التصفير "
                                       "ولم يُحذف شيء.",
                            "detail": str(exc)}), 200
        if not bk.get("verified"):
            msg = (bk.get("run") or {}).get("message") or "فشل التحقّق من النسخة."
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة موثوقة — أُلغي "
                                       "التصفير ولم يُحذف شيء.",
                            "detail": msg}), 200
        backup_name = os.path.basename((bk.get("run") or {}).get("path") or "")

        # ── (2) التصفير الذرّيّ ──
        try:
            result = get_data_reset_service().wipe(
                tenant_id=t, keys=keys, current_admin_id=_current_admin_id())
        except sqlite3.IntegrityError as exc:
            _LOG.warning("data-reset integrity block: %s", exc)
            return jsonify({
                "ok": False, "code": "integrity", "backup": backup_name,
                "message": "تعذّر الحذف بسبب ارتباط مرجعيّ بين البيانات — لم "
                           "يُحذف شيء (أُعيد كل شيء). اختر الفئات المرتبطة معًا "
                           "(مثلًا «الباقات» مع «الكروت» و«المشتركون»).",
                "detail": str(exc),
            }), 200
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("data-reset wipe failed")
            return jsonify({
                "ok": False, "code": "error", "backup": backup_name,
                "message": f"تعذّر التصفير — أُعيد كل شيء (لم يُحذف). {exc}",
                "detail": str(exc),
            }), 200

        # ── (3) تدقيق + تقرير ──
        try:
            from ..services.audit import get_audit_service
            get_audit_service().record(
                actor=actor, action="data.reset",
                target_type="tenant", target_id=str(t),
                payload={"keys": keys, "backup": backup_name,
                         "total_rows": result.get("total_rows", 0)})
        except Exception:  # noqa: BLE001 — التدقيق لا يكسر النتيجة
            pass
        result["backup"] = backup_name
        result["message"] = "تم التصفير بنجاح."
        return jsonify(result)
    finally:
        _WIPE_LOCK.release()
