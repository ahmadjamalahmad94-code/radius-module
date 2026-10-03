"""Web UI for operational backups: local backup, upload to the license panel,
download, and a heavily-gated in-app restore."""
from __future__ import annotations
from app.i18n_text import N_, _tr

import os
from ..core import env_settings

from flask import (
    Blueprint, flash, g, jsonify, redirect, render_template, request, send_file,
    session, url_for,
)

from ..services.operations import get_operations_service


def register_backup_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/backups", "backups", backups, methods=["GET"])
    bp.add_url_rule("/backups/run", "backups_run", backups_run, methods=["POST"])
    bp.add_url_rule("/backups/run-all", "backups_run_all", backups_run_all, methods=["POST"])
    bp.add_url_rule("/backups/prune-logs", "backups_prune_logs", backups_prune_logs, methods=["POST"])
    bp.add_url_rule("/backups/upload-computer", "backups_upload_computer", backups_upload_computer, methods=["POST"])
    bp.add_url_rule("/backups/schedule", "backups_schedule", backups_schedule, methods=["POST"])
    bp.add_url_rule("/backups/upload-panel", "backups_upload_panel", backups_upload_panel, methods=["POST"])
    bp.add_url_rule("/backups/download/<path:name>", "backups_download", backups_download, methods=["GET"])
    bp.add_url_rule("/backups/content/<path:name>", "backups_content", backups_content, methods=["GET"])
    bp.add_url_rule("/backups/restore", "backups_restore", backups_restore, methods=["POST"])
    bp.add_url_rule("/backups/delete", "backups_delete", backups_delete, methods=["POST"])
    bp.add_url_rule("/backups/settings", "backups_settings", backups_settings, methods=["POST"])
    bp.add_url_rule("/backups/gdrive/save", "backups_gdrive_save", backups_gdrive_save, methods=["POST"])
    bp.add_url_rule("/backups/gdrive/start", "backups_gdrive_start", backups_gdrive_start, methods=["POST"])
    bp.add_url_rule("/backups/gdrive/poll", "backups_gdrive_poll", backups_gdrive_poll, methods=["GET"])
    bp.add_url_rule("/backups/gdrive/disconnect", "backups_gdrive_disconnect", backups_gdrive_disconnect, methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _restore_enabled() -> bool:
    # In-app restore is available by default (commercial deployments must not
    # need terminal access). Set HOBERADIUS_LOCAL_RESTORE_DISABLED=1 to hard-off.
    return str(env_settings.env("HOBERADIUS_LOCAL_RESTORE_DISABLED", "")).strip().lower() not in {"1", "true", "yes", "on"}


def _backup_service_state(tid: int) -> dict:
    """Read the paid «backups» service state from the latest runtime contract.

    Panel-side backup upload is a paid service: only allowed when the
    customer's `backups` service is active in the contract delivered by the
    license panel. Returns {enabled, status, name}.
    """
    try:
        from ..services.admin_panel_client import LicenseAdminSnapshotStore, SNAPSHOT_CAPACITY
        snap = LicenseAdminSnapshotStore().latest(tenant_id=tid, snapshot_type=SNAPSHOT_CAPACITY)
        services = {}
        if snap and isinstance(snap.get("payload_json"), dict):
            pj = snap["payload_json"]
            services = (pj.get("contract") or {}).get("services") or pj.get("services") or {}
        svc = services.get("backups") or {}
        return {
            "enabled": bool(svc.get("enabled")),
            "status": str(svc.get("status") or "disabled"),
            "name": N_("النسخ الاحتياطي السحابي"),
        }
    except Exception:  # noqa: BLE001 — never break the page on contract read
        return {"enabled": False, "status": "unknown", "name": N_("النسخ الاحتياطي السحابي")}


def backups():
    svc = get_operations_service()
    try:
        svc.prune_local_backups()  # enforce 30-day retention on view
    except Exception:  # noqa: BLE001
        pass
    status = svc.backup_status(tenant_id=_tid())
    local_files = svc.list_local_backups(tenant_id=_tid())
    return render_template(
        "radius/backups.html",
        status=status,
        local_files=local_files,
        restore_enabled=_restore_enabled(),
        panel_backup=_backup_service_state(_tid()),
        retention_days=get_operations_service().LOCAL_BACKUP_RETENTION_DAYS,
        backup_max_count=get_operations_service().backup_max_count(tenant_id=_tid()),
        backup_max_from_panel=get_operations_service().backup_max_count_from_panel(tenant_id=_tid()),
        backup_schedule=get_operations_service().get_backup_schedule(tenant_id=_tid()),
        gdrive=_gdrive_status(_tid()),
    )


def _wants_full_archive() -> bool:
    """A backup is lean ("core") by default; the operator can request a full
    archive (including the high-volume log/telemetry tables) with mode=full."""
    raw = (request.values.get("mode") or request.values.get("scope") or "").strip().lower()
    return raw in {"full", "archive", "full_archive"}


def backups_run_all():
    """Unified manual backup: local → panel (if paid) → Drive. Returns JSON steps."""
    lean = not _wants_full_archive()
    result = get_operations_service().run_full_backup(tenant_id=_tid(), actor=_actor(), lean=lean)
    return jsonify(result)


def backups_prune_logs():
    """Manually run log/accounting retention now (prune high-volume tables +
    VACUUM) to reclaim database space immediately, without waiting for the daily
    background worker. Returns JSON when called via AJAX, else flashes + redirects."""
    from ..services import log_retention
    result = log_retention.run_retention(actor=_actor())
    deleted = int(result.get("total_deleted") or 0)
    reclaimed_mb = round(int(result.get("reclaimed_bytes") or 0) / (1024 * 1024), 2)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
            "application/json" in (request.headers.get("Accept") or ""):
        return jsonify(result)
    if deleted:
        flash(
            _tr('تم تنظيف السجلّات: حُذف %(deleted)s سجلًّا قديمًا', deleted=deleted)
            + (_tr(' واستُرجع %(reclaimed_mb)s م.ب من مساحة قاعدة البيانات.', reclaimed_mb=reclaimed_mb) if reclaimed_mb else "."),
            "success",
        )
    else:
        flash(_tr("لا توجد سجلّات قديمة لحذفها — قاعدة البيانات ضمن نافذة الاحتفاظ."), "info")
    return redirect(url_for("radius.backups"))


def backups_upload_computer():
    """Accept a backup file uploaded from the user's computer → store locally."""
    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash(_tr("اختر ملف نسخة بصيغة .sqlite3 أو .sqlite3.gz للرفع."), "error")
        return redirect(url_for("radius.backups"))
    low = str(f.filename).lower()
    if not (low.endswith(".sqlite3") or low.endswith(".sqlite3.gz")):
        flash(_tr("صيغة الملف يجب أن تكون .sqlite3 أو .sqlite3.gz"), "error")
        return redirect(url_for("radius.backups"))
    result = get_operations_service().import_uploaded_backup(
        tenant_id=_tid(), actor=_actor(), fileobj=f, filename=f.filename)
    flash(result.get("message") or (_tr("تم الرفع.") if result.get("ok") else _tr("تعذّر الرفع.")),
          "success" if result.get("ok") else "error")
    return redirect(url_for("radius.backups"))


def backups_schedule():
    """Save the automatic-backup schedule (enable + interval)."""
    enabled = (request.form.get("enabled") or "").strip().lower() in {"1", "true", "yes", "on"}
    interval = (request.form.get("interval") or "daily").strip()
    sched = get_operations_service().set_backup_schedule(tenant_id=_tid(), enabled=enabled, interval=interval)
    labels = {"6h": N_("كل 6 ساعات"), "12h": N_("كل 12 ساعة"), "daily": N_("يوميًا"), "weekly": N_("أسبوعيًا")}
    if sched["enabled"]:
        flash(_tr('تم تفعيل الجدولة التلقائية (%(v)s).', v=labels.get(sched['interval'], sched['interval'])), "success")
    else:
        flash(_tr("تم إيقاف الجدولة التلقائية."), "info")
    return redirect(url_for("radius.backups"))


def _gdrive_status(tid: int) -> dict:
    """اتصال جوجل درايف يعيش في لوحة التراخيص لكل عميل.
    نقرأه عبر جسر الربط حتى تعرض صفحة الريدياس حالته الفعلية.
    (المصدر الواحد للويب والـAPI: services.backup_drive_link.)"""
    from ..services.backup_drive_link import panel_google_drive_status
    return panel_google_drive_status()


def backups_run():
    lean = not _wants_full_archive()
    result = get_operations_service().run_local_backup(tenant_id=_tid(), actor=_actor(), lean=lean)
    if result.get("verified"):
        flash(_tr("تم إنشاء نسخة احتياطية محلية والتحقق منها."), "success")
    else:
        message = result.get("run", {}).get("message") or _tr("تعذر التحقق من النسخة الاحتياطية.")
        flash(message, "error")
    return redirect(url_for("radius.backups"))


def backups_upload_panel():
    """Upload the latest local backup (with content) to the license panel."""
    if not _backup_service_state(_tid()).get("enabled"):
        flash(_tr("خدمة النسخ على لوحة التراخيص غير مفعّلة. أرسل «طلب تفعيل» أولاً (خدمة مدفوعة)."), "error")
        return redirect(url_for("radius.backups"))
    from ..services.license_admin_backup_upload import BackupUploadService

    result = BackupUploadService().upload_latest_backup(
        tenant_id=_tid(),
        dry_run=False,
        include_content=True,
    )
    if result.get("ok") and not result.get("dry_run"):
        content_included = bool((result.get("payload") or {}).get("content_included"))
        if content_included:
            flash(_tr("تم رفع النسخة الاحتياطية (بالملف الكامل) إلى لوحة التراخيص وتخزينها في ملف العميل."), "success")
        else:
            reason = (result.get("payload") or {}).get("content_omitted_reason") or ""
            if reason == "content_too_large":
                hint = _tr("حجم النسخة يتجاوز الحد المسموح للرفع.")
            elif reason == "backup_file_missing":
                hint = _tr("تعذّر العثور على ملف النسخة محليًا.")
            else:
                hint = _tr("تم تسجيل البيانات الوصفية فقط.")
            flash(_tr('تم تسجيل النسخة في ملف العميل بلوحة التراخيص. %(hint)s', hint=hint), "warning")
    elif result.get("status") == "no_backup_found":
        flash(_tr("لا توجد نسخة محلية ناجحة لرفعها. شغّل نسخة محلية أولاً."), "error")
    else:
        from ..services.license_admin_backup_upload import friendly_panel_backup_error
        flash(friendly_panel_backup_error(result), "error")
    return redirect(url_for("radius.backups"))


def backups_gdrive_save():
    """احفظ بيانات عميل جوجل اللازمة لربط درايف."""
    from ..services import google_drive as gd
    gd.save_client(_tid(), request.form.get("client_id") or "", request.form.get("client_secret") or "")
    flash(_tr("تم حفظ بيانات جوجل. اضغط «ربط جوجل درايف» للبدء."), "success")
    return redirect(url_for("radius.backups"))


def backups_gdrive_start():
    """Begin the device flow — returns a code + google.com/device link."""
    from ..services import google_drive as gd
    result = gd.start_device_flow(_tid())
    if not result.get("ok"):
        if result.get("error") == "not_configured":
            flash(_tr("أدخل معرّف العميل وسر العميل من جوجل أولاً."), "error")
        else:
            flash(_tr('تعذّر بدء الربط مع جوجل: %(v)s %(v2)s', v=result.get('error'), v2=result.get('detail', '')), "error")
        return redirect(url_for("radius.backups"))
    # show the pairing instructions on the backups page
    flash(
        _tr('افتح %(verification_url)s وأدخل الرمز: %(user_code)s ثم اضغط «تحقّقت، أكمل الربط».', verification_url=result['verification_url'], user_code=result['user_code']),
        "info",
    )
    session["gdrive_pairing"] = {"user_code": result["user_code"], "url": result["verification_url"]}
    return redirect(url_for("radius.backups"))


def backups_gdrive_poll():
    """Polled (by JS or button) to complete the device flow once authorised."""
    from ..services import google_drive as gd
    result = gd.poll_device_flow(_tid())
    if request.args.get("ajax"):
        return jsonify(result)
    if result.get("ok"):
        session.pop("gdrive_pairing", None)
        flash(_tr('تم ربط جوجل درايف بنجاح (%(v)s). ستُرفع نسخك إلى درايفك تلقائيًا.', v=result.get('email') or ''), "success")
    elif result.get("pending"):
        flash(_tr("لم يكتمل التفويض بعد. أكمل الموافقة على google.com/device ثم أعد المحاولة."), "warning")
    else:
        flash(_tr('تعذّر إكمال الربط: %(v)s %(v2)s', v=result.get('error'), v2=result.get('detail', '')), "error")
    return redirect(url_for("radius.backups"))


def backups_gdrive_disconnect():
    from ..services import google_drive as gd
    gd.disconnect(_tid())
    session.pop("gdrive_pairing", None)
    flash(_tr("تم فصل جوجل درايف."), "info")
    return redirect(url_for("radius.backups"))


def backups_content(name: str):
    """JSON content summary (row counts) for one local backup — loaded on demand."""
    return jsonify(get_operations_service().summarize_local_backup(name=name))


def backups_download(name: str):
    path = get_operations_service().resolve_local_backup_path(name=name)
    if not path:
        flash(_tr("ملف النسخة غير موجود."), "error")
        return redirect(url_for("radius.backups"))
    # Carry the correct content-type so the browser/CDN treats it as a gzip
    # archive (not text) — matters for the compressed .sqlite3.gz backups.
    mimetype = "application/gzip" if path.name.lower().endswith(".gz") else "application/x-sqlite3"
    return send_file(str(path), as_attachment=True, download_name=path.name, mimetype=mimetype)


def backups_restore():
    ajax = (request.form.get("ajax") or request.args.get("ajax") or "").strip() == "1"

    def _fail(message: str, code: str = "invalid"):
        if ajax:
            return jsonify({"ok": False, "code": code, "message": message}), 200
        flash(message, "error")
        return redirect(url_for("radius.backups"))

    if not _restore_enabled():
        return _fail(_tr("الاستعادة داخل التطبيق معطّلة على هذا الخادم."), "restore_disabled")
    if (request.form.get("ack") or "").strip() != "1":
        return _fail(_tr("يجب الإقرار بأن الاستعادة ستستبدل قاعدة البيانات الحالية."), "ack")
    confirm_value = (request.form.get("confirm") or "").strip()
    if confirm_value != "استعادة النسخة" and confirm_value.upper() != "RESTORE":
        return _fail(_tr("لإتمام الاستعادة يجب كتابة عبارة التأكيد بشكل صحيح."), "confirm")
    name = (request.form.get("name") or "").strip()
    result = get_operations_service().restore_local_backup(tenant_id=_tid(), actor=_actor(), name=name)
    if ajax:
        ok = bool(result.get("ok"))
        # synthesize the staged steps the operation performed for the progress UI
        steps = [
            {"key": "snapshot", "label": _tr("نسخة احترازية قبل الاستعادة"), "status": "success" if ok else ("success" if result.get("code") not in {"not_found", "restore_disabled"} else "failed"), "message": _tr("تم أخذ نسخة احترازية.") if ok else ""},
            {"key": "apply", "label": _tr("استبدال قاعدة البيانات"), "status": "success" if ok else "failed", "message": result.get("message") or (_tr("تمت الاستعادة.") if ok else _tr("فشلت الاستعادة."))},
            {"key": "verify", "label": _tr("التحقق"), "status": "success" if ok else "skipped", "message": _tr("تم التحقق من القاعدة المستعادة.") if ok else ""},
        ]
        return jsonify({"ok": ok, "message": result.get("message") or (_tr("تمت الاستعادة بنجاح.") if ok else _tr("تعذّرت الاستعادة.")), "steps": steps})
    flash(result.get("message") or (_tr("تمت الاستعادة بنجاح.") if result.get("ok") else _tr("تعذّرت الاستعادة.")),
          "success" if result.get("ok") else "error")
    return redirect(url_for("radius.backups"))


def backups_delete():
    """Delete a single local backup file behind an explicit typed confirmation."""
    confirm_value = (request.form.get("confirm") or "").strip()
    if confirm_value != "حذف النسخة" and confirm_value.upper() != "DELETE":
        flash(_tr("لحذف النسخة يجب كتابة عبارة التأكيد بشكل صحيح."), "error")
        return redirect(url_for("radius.backups"))
    name = (request.form.get("name") or "").strip()
    result = get_operations_service().delete_local_backup(tenant_id=_tid(), actor=_actor(), name=name)
    flash(
        result.get("message") or (_tr("تم الحذف.") if result.get("ok") else _tr("تعذّر الحذف.")),
        "success" if result.get("ok") else "error",
    )
    return redirect(url_for("radius.backups"))


def backups_settings():
    """Save the backup retention cap (max number of kept backups) + enforce it."""
    raw = (request.form.get("max_count") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        flash(_tr("أدخل عددًا صحيحًا لحدّ عدد النسخ."), "error")
        return redirect(url_for("radius.backups"))
    svc = get_operations_service()
    saved = svc.set_backup_max_count(tenant_id=_tid(), value=value)
    removed = svc.prune_local_backups_by_count(tenant_id=_tid(), max_count=saved)
    if removed:
        flash(_tr('تم ضبط حدّ النسخ إلى %(saved)s، وحُذفت %(v)s نسخة قديمة زائدة.', saved=saved, v=len(removed)), "success")
    else:
        flash(_tr('تم ضبط حدّ النسخ إلى %(saved)s نسخة.', saved=saved), "success")
    return redirect(url_for("radius.backups"))
