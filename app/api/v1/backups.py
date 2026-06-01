"""Backup readiness endpoints."""
from __future__ import annotations

from flask import Blueprint, g

from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/backups/status", "backups_status",
                    require_api_token(backups_status), methods=["GET"])
    bp.add_url_rule("/backups/run", "backups_run",
                    require_api_token(backups_run), methods=["POST"])
    bp.add_url_rule("/backups/google-drive/connect",
                    "backups_google_drive_connect",
                    require_api_token(backups_google_drive_connect), methods=["POST"])


def backups_status():
    return ok(_svc().backup_status(tenant_id=_tid()))


def backups_run():
    result = _svc().run_local_backup(tenant_id=_tid(), actor=_actor())
    return ok(result, status=201 if result.get("verified") else 500)


def backups_google_drive_connect():
    return fail(
        "not_implemented",
        "ربط Google Drive عبر OAuth غير مفعل حاليًا من هذا المسار.",
        status=501,
        details={
            "domain": "backups",
            "operation": "google_drive_connect",
            "status": "planned",
        },
    )
