"""S8 — Backup center routes.

  GET  /mt/<id>/backups            list backups for one router
  POST /mt/<id>/backups/save       create new backup
  POST /mt/<id>/backups/restore-plan  ← S8.4 planner only;
                                      actual restore stays
                                      blocked behind a UI guard.

Restore planning lets the operator upload a file, inspect
metadata, see warnings — and STOPS there. Actually applying a
restore is a higher-risk operation that requires a separate
audited workflow not built in this commit. The planner shows a
clear "ميزة التطبيق معطّلة حاليًا" reason.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import router_backups_repo
from ..integration.mikrotik.client import MikrotikClient
from ..services import mikrotik_admin_client as mac
from ..services.audit import get_audit_service
from ..services.nas_connection import resolve_connection_address
from ..services.mt_permissions import (
    PERM_BACKUP, PERM_RESTORE, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, "
        "       api_password, api_use_tls, enabled, "
        "       connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    return dict(row) if row else None


def _storage_root() -> str:
    """Where backup files live on disk. Defaults to the
    instance folder under `backups/`; overridable for tests."""
    path = os.environ.get("HOBERADIUS_BACKUP_DIR") or "/tmp/hr-backups"
    os.makedirs(path, exist_ok=True)
    return path


def register_mt_backups_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/backups",
        "mt_backups_list",
        requires_perm(PERM_BACKUP)(mt_backups_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/backups/save",
        "mt_backups_save",
        requires_perm(PERM_BACKUP)(mt_backups_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/backups/restore-plan",
        "mt_backups_restore_plan",
        requires_perm(PERM_RESTORE)(mt_backups_restore_plan),
        methods=["POST"],
    )


# ─── List ────────────────────────────────────────────────────


def mt_backups_list(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    rows = router_backups_repo.list_for_router(_tid(), nas_id)
    return render_template(
        "radius/mt_backups.html",
        nas=nas, backups=rows,
        restore_disabled_reason=(
            "تطبيق الاستعادة مقفل حاليًا لحماية الراوتر. "
            "يمكنك فحص الملف ومراجعة الخطة دون تنفيذ أي تغيير."
        ),
    )


# ─── Save ────────────────────────────────────────────────────


def mt_backups_save(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    if not nas.get("enabled"):
        flash("الراوتر معطّل — لا يمكن أخذ نسخة احتياطية.", "error")
        return redirect(url_for("radius.mt_backups_list", nas_id=nas_id))

    actor = getattr(g, "admin_id", None)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    # اسم النسخة: نبني اسماً «نظيفاً بلا امتداد» للراوتر (هو يضيف
    # .backup تلقائياً)، ونحتفظ بـfilename (مع .backup) للسجل/التخزين
    # المحلي فقط. لو أعطى المستخدم اسماً اختيارياً نستعمله بعد التنظيف،
    # وإلا نرجع للاسم التلقائي. التنظيف الفعلي + الرفض العربي يجريان
    # داخل mac.backup_save عبر _sanitize_backup_name.
    user_name = (request.form.get("backup_name") or "").strip()
    backup_name = user_name or f"nas-{nas_id}-{ts}"   # للراوتر (بلا امتداد)
    filename = f"{backup_name}.backup"                 # للسجل/التخزين المحلي
    storage_path = os.path.join(_storage_root(), filename)

    error = ""            # النص الخام (يُسجَّل في التدقيق فقط)
    user_error = ""       # رسالة عربية ودودة تُعرض للمستخدم
    status = "success"
    size = 0
    checksum = ""
    backup_id = None

    client = MikrotikClient(
        host=resolve_connection_address(nas),
        port=int(nas.get("api_port") or 8728),
        username=nas.get("api_user") or "admin",
        password=nas.get("api_password") or "",
        use_tls=bool(nas.get("api_use_tls")),
        verify_tls=True, timeout=15.0,
    )
    try:
        client.connect()
        # Reuse the K8 backup helper to ask the router to
        # produce the file. The router writes it to its own
        # filesystem; for now we record the metadata + leave
        # downloading the bytes for a follow-up (file-stream
        # endpoint already exists at K8.1b).
        result = mac.backup_save(_nas_for_mac(nas), name=backup_name)
        if not getattr(result, "ok", False):
            status = "failed"
            # رسالة الخدمة عربية أصلاً (تشمل رفض الاسم من
            # _sanitize_backup_name)، فنعرضها كما هي للمستخدم.
            error = getattr(result, "error", "") or "تعذّر إنشاء النسخة على الراوتر."
            user_error = error
    except Exception as e:  # noqa: BLE001
        status = "failed"
        # نص الاستثناء خام (إنجليزي/تقني) → نسجّله للتدقيق فقط
        # ونعرض للمستخدم رسالة عربية عامة بدل الخطأ الخام.
        error = str(e)
        user_error = "تعذّر الاتصال بالراوتر أو حفظ النسخة. تأكد من توفّر الجهاز وحاول مجددًا."
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    # We don't actually have the bytes locally yet (router holds
    # them); leave size/checksum at 0/empty for now. Future
    # commit downloads + fills them in.
    backup_id = router_backups_repo.record(
        tenant_id=_tid(), router_id=nas_id,
        backup_type="binary",
        filename=filename, storage_path="",
        size_bytes=size, checksum=checksum,
        sensitive=True,
        status=status, error_message=error,
        created_by=int(actor) if actor else None,
    )

    get_audit_service().record(
        actor=str(actor or "ui"),
        action="mt.backup.save",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        severity=("info" if status == "success" else "critical"),
        result_status=status,
        router_id=nas_id,
        error_message=error,
        payload={"filename": filename, "backup_id": backup_id},
    )

    if status == "success":
        flash("تم حفظ النسخة الاحتياطية على الراوتر.", "success")
    else:
        flash(f"فشل حفظ النسخة الاحتياطية: {user_error or error}", "error")
    return redirect(url_for("radius.mt_backups_list", nas_id=nas_id))


def _nas_for_mac(nas: dict) -> dict:
    return {
        "id": nas["id"], "name": nas.get("name"),
        "host": resolve_connection_address(nas),
        "port": int(nas.get("api_port") or 8728),
        "username": nas.get("api_user") or "admin",
        "password": nas.get("api_password") or "",
        "use_tls": bool(nas.get("api_use_tls")),
        "verify_tls": True, "timeout_sec": 15,
    }


def _restore_plan_rows(metadata: dict) -> list[dict]:
    if not metadata:
        return []
    labels = {
        "filename": "اسم الملف",
        "head_size_bytes": "حجم عينة الفحص",
        "appears_binary": "نوع الملف",
        "checksum_prefix": "بصمة الفحص",
    }
    rows: list[dict] = []
    for key in ("filename", "head_size_bytes", "appears_binary", "checksum_prefix"):
        if key not in metadata:
            continue
        value = metadata.get(key)
        if key == "appears_binary":
            rendered = "ملف ثنائي" if value else "ملف نصي أو غير ثنائي"
        elif key == "head_size_bytes":
            rendered = f"{int(value or 0)} بايت"
        else:
            rendered = str(value or "—")
        rows.append({"key": key, "label": labels[key], "value": rendered})
    return rows


# ─── Restore planner (S8.4 — gated) ──────────────────────────


def mt_backups_restore_plan(nas_id: int):
    """Show a planned restore — does NOT actually restore.

    This commit ships the metadata-inspection + warning UI so
    the operator can see what's in a backup file. Actually
    applying a restore is destructive (overwrites router config)
    and not safe to expose without a dedicated audited workflow.
    The button is rendered as disabled with a clear reason.
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    upload = request.files.get("backup_file")
    metadata = {}
    error = ""
    if upload is None:
        error = "لم يتم اختيار ملف."
    else:
        # Inspect: read first ~4 KB so we can show file shape
        # without ever applying it.
        head = upload.stream.read(4096)
        upload.stream.seek(0)
        metadata = {
            "filename": upload.filename,
            "head_size_bytes": len(head),
            "appears_binary":
                any(b == 0 for b in head[:512]),
            "checksum_prefix": hashlib.sha256(head).hexdigest()[:16],
        }
    return render_template(
        "radius/mt_backups.html",
        nas=nas,
        backups=router_backups_repo.list_for_router(_tid(), nas_id),
        restore_plan=metadata,
        restore_plan_rows=_restore_plan_rows(metadata),
        restore_error=error,
        restore_disabled_reason=(
            "هذه خطة فحص فقط — تطبيق الاستعادة مقفل لحماية الراوتر "
            "ويتطلب موافقة تشغيلية منفصلة قبل التنفيذ."
        ),
    )
