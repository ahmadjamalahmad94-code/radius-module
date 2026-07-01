"""مسارات معالج ترحيل بيانات العملاء — «ترحيل / استيراد عميل» (للمالك فقط).

تدفّق «حلّل أولًا، نفّذ بعد المراجعة» (نظير mt_import) عبر مخزن مهامّ خادميّ:

  • GET  /admin/radius/migrate          — صفحة المعالج.
  • POST /admin/radius/migrate/analyze  — رفع ملف → فحص+تصنيف (لا كتابة) →
                                          مهمّة + ملخّص الأقسام.
  • POST /admin/radius/migrate/plan     — token+اختيارات → خطّة (للقراءة):
                                          جديد/دمج/تخطٍّ/غير صالح + أسباب.
  • POST /admin/radius/migrate/commit   — token+اختيارات(+dry_run) → تنفيذ
                                          ذرّيّ idempotent + تقرير.
  • GET  /admin/radius/migrate/jobs     — آخر العمليّات.

أمان: **المالك الرئيسي وحده** (``is_primary_owner``) — 403 لأيّ غيره على كل
method. الملف الخام يبقى خادميًّا؛ التحليل يُعاد من القرص في كل خطوة (لا نثق
ببيانات المتصفّح، ولا تجول كلمات المرور للواجهة).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from ..auth.decorators import login_required
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db_path

_LOG = logging.getLogger(__name__)

# سقف حجم الملف = 500MB (يطابق MAX_CONTENT_LENGTH؛ قابل للضبط بنفس المتغيّر).
# werkzeug يَحدّ الجسم قبل الوصول هنا فيردّ 413→JSON عبر مُعالِج التطبيق؛ هذا
# حارس احتياطيّ للبثّ حين لا يوجد Content-Length. تُبَثّ للقرص لا للذاكرة.
def _max_upload_bytes() -> int:
    try:
        return int(os.environ.get("HOBERADIUS_MAX_UPLOAD_MB", "500")) * 1024 * 1024
    except ValueError:
        return 500 * 1024 * 1024


_MAX_UPLOAD = _max_upload_bytes()


def register_migration_routes(bp: Blueprint) -> None:
    # strict_slashes=False: يخدم /migrate و /migrate/ كليهما (المتصفّح وكتلة
    # nginx للمسار يُنتجان الشرطة الأخيرة) فلا يقع 404 على الشكل ذي الشرطة.
    bp.add_url_rule("/migrate", "migration_index",
                    login_required(migration_index), methods=["GET"],
                    strict_slashes=False)
    bp.add_url_rule("/migrate/analyze", "migration_analyze",
                    login_required(migration_analyze), methods=["POST"])
    bp.add_url_rule("/migrate/plan", "migration_plan",
                    login_required(migration_plan), methods=["POST"])
    bp.add_url_rule("/migrate/commit", "migration_commit",
                    login_required(migration_commit), methods=["POST"])
    bp.add_url_rule("/migrate/commit_status", "migration_commit_status",
                    login_required(migration_commit_status), methods=["GET"])
    bp.add_url_rule("/migrate/jobs", "migration_jobs",
                    login_required(migration_jobs), methods=["GET"])


# ── helpers ──────────────────────────────────────────────────────────

def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "owner"


def _require_owner():
    """يُجهض بـ403 لغير المالك الرئيسي. يُستدعى في رأس كل معالِج (دفاع عميق
    فوق حارس الدخول)."""
    from ..auth.session_helpers import current_admin_id
    from ..db.repos import admins_repo
    if not admins_repo.is_primary_owner(current_admin_id()):
        abort(403)


def _upload_dir() -> Path:
    d = Path(db_path()).resolve().parent / "migration_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _selections():
    data = request.get_json(silent=True) or {}
    sels = data.get("selections")
    return sels if isinstance(sels, list) else None


# ── routes ───────────────────────────────────────────────────────────

def migration_index():
    _require_owner()
    import json as _json
    from ..services.migration import ENGINE_VERSION, ENGINE_BUILD_NOTE
    from ..services.migration.sections import SECTIONS
    from ..db.repos import migration_jobs_repo
    jobs = migration_jobs_repo.list_for_tenant(_tid(), limit=15)
    labels = {s.key: s.label_ar for s in SECTIONS}
    # حقول كل قسم (لواجهة الربط اليدويّ الكامل: كل حقل ← أيّ عمود مصدر).
    fields = {s.key: [{"target": f.target, "required": f.required,
                       "is_password": f.is_password} for f in s.fields]
              for s in SECTIONS}
    return render_template("radius/migration_wizard.html",
                           engine_version=ENGINE_VERSION,
                           engine_build_note=ENGINE_BUILD_NOTE,
                           recent_jobs=jobs,
                           section_labels_json=_json.dumps(labels, ensure_ascii=False),
                           section_fields_json=_json.dumps(fields, ensure_ascii=False))


def migration_analyze():
    _require_owner()
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"ok": False, "error": "لم يُرفَع أيّ ملف."}), 400

    from ..services.migration import engine
    from ..db.repos import migration_jobs_repo

    token = migration_jobs_repo.new_token()
    path = _upload_dir() / f"{token}.bin"
    # بَثّ الملف للقرص على دفعات (لا تحميل كامل في الذاكرة) مع حدّ حجم.
    try:
        size = _stream_to_disk(f, path)
    except _UploadTooLarge:
        _safe_unlink(path)
        _mb = _MAX_UPLOAD // (1024 * 1024)
        return jsonify({"ok": False, "status": "too_large",
                        "error": (f"الملفّ أكبر من الحدّ المسموح ({_mb}MB). "
                                  "ارفع النسخة المضغوطة .gz أو تفريغًا أصغر.")}), 413
    except OSError as exc:
        _safe_unlink(path)
        return jsonify({"ok": False, "error": f"تعذّر حفظ الملف: {exc}"}), 500
    if size == 0:
        _safe_unlink(path)
        return jsonify({"ok": False, "error": "الملف فارغ."}), 400

    try:
        result = engine.analyze_path(str(path), f.filename)
        analysis = result.public_dict()
    except Exception as exc:  # noqa: BLE001 — أعِد JSON دومًا لا صفحة HTML 500.
        _LOG.exception("migration analyze failed")
        _safe_unlink(path)
        return jsonify({"ok": False,
                        "error": f"تعذّر تحليل الملف على الخادم: {exc}"}), 500
    migration_jobs_repo.create_job(
        tenant_id=_tid(), token=token, filename=f.filename,
        fmt=result.dataset.fmt, file_path=str(path), size_bytes=size,
        analysis=analysis, created_by=_actor())
    return jsonify({"ok": True, "token": token, "analysis": analysis})


class _UploadTooLarge(Exception):
    pass


def _stream_to_disk(filestorage, path) -> int:
    """يكتب الملف المرفوع على دفعات 1MB ويُعيد حجمه؛ يُجهض عند تجاوز الحدّ."""
    total = 0
    with open(path, "wb") as out:
        while True:
            chunk = filestorage.stream.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_UPLOAD:
                raise _UploadTooLarge()
            out.write(chunk)
    return total


def _safe_unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def migration_plan():
    _require_owner()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    from ..db.repos import migration_jobs_repo
    job = migration_jobs_repo.get_by_token(_tid(), token)
    if not job:
        return jsonify({"ok": False, "error": "المهمّة غير موجودة."}), 404
    path = job.get("file_path") or ""
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "تعذّر قراءة الملف المصدر."}), 410

    from ..services.migration import engine
    res = engine.analyze_path(path, job.get("filename") or "")
    plan = engine.build_plan(_tid(), res.dataset, res.matches,
                             selections=_selections())
    return jsonify({"ok": True, "plan": plan.public_dict()})


def migration_commit():
    """يبدأ التنفيذ في خيط خلفيّ ويعود فورًا (running) — يتجنّب طلبًا طويلًا
    (تحليل ملفّ ضخم + دمج آلاف السجلّات) قد يتجاوز مهلة الخادم/الوسيط، ويتيح
    للواجهة عرض تقدّم حقيقيّ عبر ``/migrate/commit_status``. idempotent:
    إعادة التشغيل تطابق بالمفتاح الطبيعيّ فلا تُكرّر."""
    import threading
    from flask import current_app
    _require_owner()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    dry_run = bool(data.get("dry_run", False))
    selections = _selections()
    from ..db.repos import migration_jobs_repo
    tid = _tid()
    job = migration_jobs_repo.get_by_token(tid, token)
    if not job:
        return jsonify({"ok": False, "error": "المهمّة غير موجودة."}), 404
    if job.get("status") == "running":
        return jsonify({"ok": True, "running": True, "token": token})
    path = job.get("file_path") or ""
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "تعذّر قراءة الملف المصدر."}), 410

    filename = job.get("filename") or ""
    actor = _actor()
    app = current_app._get_current_object()
    migration_jobs_repo.set_report(
        tid, token, {"progress": {"phase": "analyzing", "done": 0, "total": 0}},
        status="running")

    def _worker():
        import time as _t
        with app.app_context():
            last = [0.0]

            def cb(done, total, section, phase):
                now = _t.time()
                if now - last[0] < 0.7 and done < total:
                    return                     # خنق كتابة DB (~مرّة/ثانية)
                last[0] = now
                migration_jobs_repo.set_report(
                    tid, token,
                    {"progress": {"phase": phase, "done": done, "total": total,
                                  "section": section}}, status="running")
            try:
                from ..services.migration import engine
                res = engine.analyze_path(path, filename)
                report = engine.commit(tid, res.dataset, res.matches,
                                        selections=selections, dry_run=dry_run,
                                        actor=actor, progress_cb=cb)
                rd = report.public_dict()
                st = "failed" if report.status == "failed" else (
                    "dry_done" if dry_run else "committed")
                migration_jobs_repo.set_report(tid, token, rd, status=st)
            except Exception as exc:  # noqa: BLE001 — أعِد خطأً واضحًا، لا انهيار
                _LOG.exception("migration commit worker failed")
                migration_jobs_repo.set_report(
                    tid, token,
                    {"status": "failed", "error": f"تعذّر التنفيذ: {exc}"},
                    status="failed")

    threading.Thread(target=_worker, name=f"migrate-commit-{token[:8]}",
                     daemon=True).start()
    return jsonify({"ok": True, "running": True, "token": token})


def migration_commit_status():
    """حالة التنفيذ الخلفيّ: running + تقدّم، أو committed/dry_done/failed + التقرير."""
    _require_owner()
    token = str(request.args.get("token", "")).strip()
    from ..db.repos import migration_jobs_repo
    job = migration_jobs_repo.get_by_token(_tid(), token)
    if not job:
        return jsonify({"ok": False, "error": "المهمّة غير موجودة."}), 404
    status = job.get("status") or ""
    data = migration_jobs_repo.parsed_report(job)
    if status == "running":
        return jsonify({"ok": True, "status": "running",
                        "progress": data.get("progress", {})})
    return jsonify({"ok": True, "status": status,
                    "dry_run": status == "dry_done",
                    "report": data})


def migration_jobs():
    _require_owner()
    from ..db.repos import migration_jobs_repo
    return jsonify({"ok": True,
                    "jobs": migration_jobs_repo.list_for_tenant(_tid(), limit=25)})


__all__ = ["register_migration_routes"]
