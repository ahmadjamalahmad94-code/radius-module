"""Backup / Google Drive readiness API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "backup job table",
    "credential storage policy",
    "scheduler integration",
    "restore-test workflow",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/backups/status", "backups_status",
                       methods=["GET"], domain="backups",
                       operation="status",
                       planned_slice="R4 backup readiness",
                       required_work=_WORK)
    add_contract_route(bp, "/backups/run", "backups_run",
                       methods=["POST"], domain="backups",
                       operation="run",
                       planned_slice="R4 backup readiness",
                       required_work=_WORK)
    add_contract_route(bp, "/backups/google-drive/connect",
                       "backups_google_drive_connect", methods=["POST"],
                       domain="backups", operation="google_drive_connect",
                       planned_slice="R4 backup readiness",
                       required_work=_WORK)
