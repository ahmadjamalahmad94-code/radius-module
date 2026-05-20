"""Web UI for operational backup status and manual local backup."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, session, url_for

from ..services.operations import get_operations_service


def register_backup_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/backups", "backups", backups, methods=["GET"])
    bp.add_url_rule("/backups/run", "backups_run", backups_run, methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def backups():
    status = get_operations_service().backup_status(tenant_id=_tid())
    return render_template("radius/backups.html", status=status)


def backups_run():
    result = get_operations_service().run_local_backup(tenant_id=_tid(), actor=_actor())
    if result.get("verified"):
        flash("تم إنشاء نسخة احتياطية محلية والتحقق منها.", "success")
    else:
        message = result.get("run", {}).get("message") or "تعذر التحقق من النسخة الاحتياطية."
        flash(message, "error")
    return redirect(url_for("radius.backups"))
