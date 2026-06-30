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

import os
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from ..auth.decorators import login_required
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db_path

# سقف حجم الملف (الذاكرة + أمان). 64MB يكفي لقواعد متوسّطة؛ القواعد الأكبر
# يُفضَّل تصديرها SQL/CSV.
_MAX_UPLOAD = 64 * 1024 * 1024


def register_migration_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/migrate", "migration_index",
                    login_required(migration_index), methods=["GET"])
    bp.add_url_rule("/migrate/analyze", "migration_analyze",
                    login_required(migration_analyze), methods=["POST"])
    bp.add_url_rule("/migrate/plan", "migration_plan",
                    login_required(migration_plan), methods=["POST"])
    bp.add_url_rule("/migrate/commit", "migration_commit",
                    login_required(migration_commit), methods=["POST"])
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


def _load_bytes(job: dict) -> bytes:
    path = job.get("file_path") or ""
    if not path or not os.path.exists(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


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
    return render_template("radius/migration_wizard.html",
                           engine_version=ENGINE_VERSION,
                           engine_build_note=ENGINE_BUILD_NOTE,
                           recent_jobs=jobs,
                           section_labels_json=_json.dumps(labels, ensure_ascii=False))


def migration_analyze():
    _require_owner()
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"ok": False, "error": "لم يُرفَع أيّ ملف."}), 400
    raw = f.read(_MAX_UPLOAD + 1)
    if len(raw) > _MAX_UPLOAD:
        return jsonify({"ok": False,
                        "error": "الملف أكبر من الحدّ (64MB). صدّر جزءًا أو "
                                 "استخدم CSV/SQL."}), 400
    if not raw:
        return jsonify({"ok": False, "error": "الملف فارغ."}), 400

    from ..services.migration import engine
    from ..db.repos import migration_jobs_repo

    result = engine.analyze(raw, f.filename)
    token = migration_jobs_repo.new_token()
    path = _upload_dir() / f"{token}.bin"
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"تعذّر حفظ الملف: {exc}"}), 500

    analysis = result.public_dict()
    migration_jobs_repo.create_job(
        tenant_id=_tid(), token=token, filename=f.filename,
        fmt=result.dataset.fmt, file_path=str(path), size_bytes=len(raw),
        analysis=analysis, created_by=_actor())
    return jsonify({"ok": True, "token": token, "analysis": analysis})


def migration_plan():
    _require_owner()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    from ..db.repos import migration_jobs_repo
    job = migration_jobs_repo.get_by_token(_tid(), token)
    if not job:
        return jsonify({"ok": False, "error": "المهمّة غير موجودة."}), 404
    raw = _load_bytes(job)
    if not raw:
        return jsonify({"ok": False, "error": "تعذّر قراءة الملف المصدر."}), 410

    from ..services.migration import engine
    res = engine.analyze(raw, job.get("filename") or "")
    plan = engine.build_plan(_tid(), res.dataset, res.matches,
                             selections=_selections())
    return jsonify({"ok": True, "plan": plan.public_dict()})


def migration_commit():
    _require_owner()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    dry_run = bool(data.get("dry_run", False))
    from ..db.repos import migration_jobs_repo
    job = migration_jobs_repo.get_by_token(_tid(), token)
    if not job:
        return jsonify({"ok": False, "error": "المهمّة غير موجودة."}), 404
    raw = _load_bytes(job)
    if not raw:
        return jsonify({"ok": False, "error": "تعذّر قراءة الملف المصدر."}), 410

    from ..services.migration import engine
    res = engine.analyze(raw, job.get("filename") or "")
    report = engine.commit(_tid(), res.dataset, res.matches,
                           selections=_selections(), dry_run=dry_run,
                           actor=_actor())
    rd = report.public_dict()
    if not dry_run:
        migration_jobs_repo.set_report(_tid(), token, rd,
                                       status=report.status if report.status
                                       == "failed" else "committed")
    return jsonify({"ok": True, "report": rd})


def migration_jobs():
    _require_owner()
    from ..db.repos import migration_jobs_repo
    return jsonify({"ok": True,
                    "jobs": migration_jobs_repo.list_for_tenant(_tid(), limit=25)})


__all__ = ["register_migration_routes"]
