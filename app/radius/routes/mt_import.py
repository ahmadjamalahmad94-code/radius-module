"""mt_import routes — واجهة «استيراد المشتركين من المايكروتيك» (الزيادة 5).

ثلاث نقاط AJAX على صفحة أجهزة الشبكة، تربط طبقات الزيادات 2-4:

  • POST /devices/<id>/import/preview  → جلب + معاينة (بلا كتابة).
  • POST /devices/<id>/import/run      → جلب + معاينة + تنفيذ + سجلّ.
  • GET  /devices/<id>/import/logs     → آخر عمليات الاستيراد لهذا الراوتر.

التدفّق في الواجهة: اختر راوترًا → «استيراد المشتركين» → هوتسبوت/نطاق عريض
→ اتصال + جلب → معاينة → تأكيد → نتيجة. «User-Manager» غير مدعوم بعد.

أمان: تُعاد المعاينة بلا كلمات مرور (public_dict)؛ التنفيذ يُعيد الجلب
خادمِيًّا (لا نُرسل الكلمات للمتصفّح ونستعيدها). اعتماد الراوتر يبقى خادمِيًّا.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, request, session

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def register_mt_import_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/devices/<int:nas_id>/import/preview",
                    "mt_import_preview", mt_import_preview, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/import/run",
                    "mt_import_run", mt_import_run, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/import/logs",
                    "mt_import_logs", mt_import_logs, methods=["GET"])


# ── helpers ──────────────────────────────────────────────────────────

def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _actor_id() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


def _param(name: str, default: str = "") -> str:
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return str(data.get(name, default) or default).strip()
    return str(request.form.get(name, default) or default).strip()


def _flag(name: str) -> bool:
    return _param(name).lower() in ("1", "on", "true", "yes")


def _load_nas(nas_id: int) -> dict | None:
    """صفّ الراوتر الكامل (مع اعتماد API + وضع الاتصال) لهذا المستأجر."""
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    return dict(row) if row else None


_USERMANAGER = {"usermanager", "user-manager", "user_manager", "um"}


def _import_type() -> str:
    return _param("import_type").lower()


# ── routes ───────────────────────────────────────────────────────────

def mt_import_preview(nas_id: int):
    itype = _import_type()
    if itype in _USERMANAGER:
        return jsonify({"ok": False, "not_implemented": True,
                        "error": "استيراد «User-Manager» غير مدعوم بعد."}), 400
    nas = _load_nas(nas_id)
    if not nas:
        return jsonify({"ok": False, "error": "الراوتر غير موجود"}), 404

    from ..services import mt_import_fetch as fetcher
    from ..services import mt_import_service as mapper

    try:
        fetched = fetcher.fetch_users(nas, itype, transport=_param("transport"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not fetched.ok:
        return jsonify({"ok": False, "error": fetched.error,
                        "attempted": fetched.attempted}), 502

    preview = mapper.build_preview(_tid(), itype, fetched.records,
                                   transport=fetched.transport)
    return jsonify({"ok": True,
                    "transport": fetched.transport,
                    "attempted": fetched.attempted,
                    "preview": preview.public_dict()})


def mt_import_run(nas_id: int):
    itype = _import_type()
    if itype in _USERMANAGER:
        return jsonify({"ok": False, "not_implemented": True,
                        "error": "استيراد «User-Manager» غير مدعوم بعد."}), 400
    nas = _load_nas(nas_id)
    if not nas:
        return jsonify({"ok": False, "error": "الراوتر غير موجود"}), 404

    from ..services import mt_import_fetch as fetcher
    from ..services import mt_import_runner as runner
    from ..services import mt_import_service as mapper

    # إعادة الجلب خادمِيًّا — لا نثق ببيانات يرسلها المتصفّح (وفيها كلمات مرور).
    try:
        fetched = fetcher.fetch_users(nas, itype, transport=_param("transport"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not fetched.ok:
        return jsonify({"ok": False, "error": fetched.error,
                        "attempted": fetched.attempted}), 502

    preview = mapper.build_preview(_tid(), itype, fetched.records,
                                   transport=fetched.transport)
    result = runner.run_import(
        tenant_id=_tid(), nas=nas, preview=preview,
        duplicate_mode=_param("duplicate_mode", "skip"),
        actor=_actor(), actor_name=_actor(), actor_id=_actor_id(),
        create_missing_plans=_flag("create_missing_plans"),
        dry_run=_flag("dry_run"))
    return jsonify({"ok": True, "result": result.to_dict()})


def mt_import_logs(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return jsonify({"ok": False, "error": "الراوتر غير موجود"}), 404
    from ..db.repos import mikrotik_import_logs_repo as logs
    rows = logs.list_for_tenant(_tid(), nas_id=nas_id, limit=20)
    return jsonify({"ok": True, "logs": rows})


__all__ = ["register_mt_import_routes"]
