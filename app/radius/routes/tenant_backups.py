"""MT24 — مسارات النسخ الاحتياطي المعزول لكل شبكة.

يراها مدير الشبكة في لوحته (/[slug]/admin/radius/my-backups) وتعمل على
جهته فقط (g.tenant_id). مستقلّة عن نسخ المزوّد للقاعدة كاملة.
"""
from __future__ import annotations

import io

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, send_file, session, url_for)

from ..services import tenant_backup


def register_tenant_backup_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/my-backups", "my_backups", my_backups, methods=["GET"])
    bp.add_url_rule("/my-backups/create", "my_backups_create",
                    my_backups_create, methods=["POST"])
    bp.add_url_rule("/my-backups/download/<name>", "my_backups_download",
                    my_backups_download, methods=["GET"])
    bp.add_url_rule("/my-backups/delete", "my_backups_delete",
                    my_backups_delete, methods=["POST"])
    bp.add_url_rule("/my-backups/restore", "my_backups_restore",
                    my_backups_restore, methods=["POST"])


def _tid() -> int:
    from ..core.tenant import DEFAULT_TENANT_ID
    return int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "network-admin"


def my_backups():
    tid = _tid()
    items = tenant_backup.list_tenant_backups(tid)
    tables = tenant_backup.tenant_tables()
    return render_template("radius/my_backups.html",
                           items=items, table_count=len(tables))


def my_backups_create():
    tid = _tid()
    try:
        info = tenant_backup.export_tenant(tid, actor=_actor())
        flash(f"تم إنشاء نسخة احتياطية لشبكتك ({info['rows']} صفًّا، "
              f"{info['tables']} جدولًا).", "success")
    except Exception as e:  # noqa: BLE001
        flash(f"تعذّر إنشاء النسخة: {e}", "error")
    return redirect(url_for("radius.my_backups"))


def my_backups_download(name: str):
    tid = _tid()
    data = tenant_backup.read_backup_bytes(tid, name)
    if data is None:
        abort(404)
    return send_file(io.BytesIO(data), mimetype="application/gzip",
                     as_attachment=True, download_name=name)


def my_backups_delete():
    tid = _tid()
    name = request.form.get("name") or ""
    if tenant_backup.delete_tenant_backup(tid, name):
        flash("حُذفت النسخة.", "success")
    else:
        flash("النسخة غير موجودة.", "error")
    return redirect(url_for("radius.my_backups"))


def my_backups_restore():
    tid = _tid()
    name = request.form.get("name") or ""
    confirm = (request.form.get("confirm") or "").strip()
    if confirm != "استعادة":
        flash("للاستعادة اكتب كلمة «استعادة» للتأكيد.", "error")
        return redirect(url_for("radius.my_backups"))
    try:
        res = tenant_backup.restore_tenant(tid, name, actor=_actor())
        flash(f"تمّت الاستعادة: {res['restored_rows']} صفًّا من "
              f"{res['tables']} جدولًا. بيانات شبكتك عادت لحالة النسخة.", "success")
    except Exception as e:  # noqa: BLE001
        flash(f"تعذّرت الاستعادة: {e}", "error")
    return redirect(url_for("radius.my_backups"))
