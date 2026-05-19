"""Operational ISP foundations: distributors, schedules, printing, backups."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..core.errors import RadiusNotFound, RadiusValidationError
from ..db.connection import db, db_path
from ..db.repos import cards_repo, operations_repo, plans_repo, subscribers_repo
from .audit import RadiusAuditService

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_SERVICE_SCOPES = {"hotspot", "broadband", "both"}
_SESSION_FROZEN_STATUSES = {"disabled", "suspended", "frozen", "banned"}
_PRINT_ORIENTATIONS = {"portrait", "landscape"}


def _int_field(data: dict, key: str, *, minimum: int = 0, default: int = 0) -> int:
    raw = data.get(key, default)
    if raw in (None, ""):
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{key} must be integer")
    if value < minimum:
        raise RadiusValidationError(f"{key} must be >= {minimum}")
    return value


def _float_field(data: dict, key: str, *, minimum: float = 0.0,
                 default: float = 0.0) -> float:
    raw = data.get(key, default)
    if raw in (None, ""):
        raw = default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{key} must be numeric")
    if value < minimum:
        raise RadiusValidationError(f"{key} must be >= {minimum:g}")
    return value


def validate_service_scope(value: str) -> str:
    scope = (value or "both").strip().lower()
    if scope not in _SERVICE_SCOPES:
        raise RadiusValidationError(
            "service_scope must be one of hotspot, broadband, both"
        )
    return scope


def _validate_time(value: str, field: str) -> str:
    raw = (value or "").strip()
    if not _TIME_RE.match(raw):
        raise RadiusValidationError(f"{field} must be HH:MM")
    hour, minute = [int(part) for part in raw.split(":", 1)]
    if hour > 23 or minute > 59:
        raise RadiusValidationError(f"{field} must be a valid time")
    return raw


def classify_online_state(*, account_status: str = "",
                          expire_at: Any = None,
                          is_online: bool = True) -> dict:
    """Normalize UI-facing live states without mutating RADIUS sessions."""
    if not is_online:
        return {"state": "disconnected", "state_label": "disconnected", "state_color": "gray"}
    status = (account_status or "").strip().lower()
    if status in _SESSION_FROZEN_STATUSES:
        return {"state": "frozen", "state_label": "frozen", "state_color": "blue"}
    if status == "expired":
        return {"state": "expired", "state_label": "expired", "state_color": "orange"}
    if expire_at is not None:
        from datetime import datetime
        if hasattr(expire_at, "replace"):
            if expire_at < datetime.utcnow():
                return {"state": "expired", "state_label": "expired", "state_color": "orange"}
    if status in {"enabled", "active", ""}:
        return {"state": "online", "state_label": "online", "state_color": "green"}
    return {"state": "active", "state_label": status or "active", "state_color": "cyan"}


class OperationsService:
    def __init__(self, audit: RadiusAuditService) -> None:
        self._audit = audit

    def create_distributor(self, *, tenant_id: int, actor: str, data: dict) -> dict:
        name = (data.get("name") or data.get("username") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        normalized = {
            "name": name,
            "display_name": (data.get("display_name") or name).strip(),
            "email": (data.get("email") or "").strip(),
            "phone": (data.get("phone") or "").strip(),
            "status": (data.get("status") or "active").strip().lower(),
            "permissions": data.get("permissions") or [],
            "scope": data.get("scope") or {},
            "balance": _float_field(data, "balance", default=0),
            "credit_limit": _float_field(data, "credit_limit", default=0),
            "debt_balance": _float_field(data, "debt_balance", default=0),
            "notes": (data.get("notes") or "")[:500],
            "metadata": data.get("metadata") or {},
        }
        try:
            saved = operations_repo.create_distributor(tenant_id, normalized, actor=actor)
        except sqlite3.IntegrityError:
            raise RadiusValidationError("distributor name already exists")
        self._audit.record(
            actor=actor,
            action="distributor.create",
            target_type="distributor",
            target_id=str(saved.get("id")),
            payload={"name": saved.get("name")},
        )
        return saved

    def list_distributors(self, *, tenant_id: int, status: Optional[str] = None,
                          limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_distributors(
            tenant_id, status=status, limit=limit, offset=offset
        )

    def get_distributor(self, *, tenant_id: int, distributor_id: int) -> dict:
        distributor = operations_repo.get_distributor(tenant_id, distributor_id)
        if not distributor:
            raise RadiusNotFound("distributor not found")
        return distributor

    def assign_batch(self, *, tenant_id: int, distributor_id: int, batch_id: int,
                     actor: str, notes: str = "") -> dict:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        if not cards_repo.get_batch(tenant_id, batch_id):
            raise RadiusNotFound("batch not found")
        assignment = operations_repo.assign_batch(
            tenant_id, distributor_id=distributor_id, batch_id=batch_id,
            actor=actor, notes=notes[:300],
        )
        self._audit.record(
            actor=actor,
            action="card_batch.assign_distributor",
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"distributor_id": distributor_id},
        )
        return assignment

    def list_distributor_batches(self, *, tenant_id: int, distributor_id: int,
                                 limit: int = 200, offset: int = 0) -> list[dict]:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        return operations_repo.list_assigned_batches(
            tenant_id, distributor_id, limit=limit, offset=offset
        )

    def distributor_summary(self, *, tenant_id: int, distributor_id: int) -> dict:
        summary = operations_repo.distributor_summary(tenant_id, distributor_id)
        if not summary:
            raise RadiusNotFound("distributor not found")
        return summary

    def settle_distributor(self, *, tenant_id: int, distributor_id: int,
                           actor: str, data: dict) -> dict:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        amount = _float_field(data, "amount", minimum=0.01)
        direction = (data.get("direction") or "credit").strip().lower()
        if direction not in {"credit", "debit"}:
            raise RadiusValidationError("direction must be credit or debit")
        entry_type = (data.get("entry_type") or "settlement").strip().lower()
        entry = operations_repo.post_distributor_ledger(
            tenant_id,
            distributor_id,
            entry_type=entry_type,
            direction=direction,
            amount=amount,
            currency=(data.get("currency") or "JOD").strip().upper(),
            actor=actor,
            notes=(data.get("notes") or "")[:500],
            related_type=(data.get("related_type") or "").strip(),
            related_id=data.get("related_id"),
            metadata=data.get("metadata") or {},
        )
        self._audit.record(
            actor=actor,
            action="distributor.ledger_post",
            target_type="distributor",
            target_id=str(distributor_id),
            payload={"entry_id": entry.get("id"), "amount": amount, "direction": direction},
        )
        return entry

    def create_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        plan_id = _int_field(data, "plan_id", minimum=1)
        if not plans_repo.get_plan(tenant_id, plan_id):
            raise RadiusNotFound("plan not found")
        normalized = {
            "plan_id": plan_id,
            "name": name,
            "starts_at_time": _validate_time(data.get("starts_at_time"), "starts_at_time"),
            "ends_at_time": _validate_time(data.get("ends_at_time"), "ends_at_time"),
            "speed_down_kbps": _int_field(data, "speed_down_kbps"),
            "speed_up_kbps": _int_field(data, "speed_up_kbps"),
            "cir_down_kbps": _int_field(data, "cir_down_kbps"),
            "cir_up_kbps": _int_field(data, "cir_up_kbps"),
            "restore_mode": (data.get("restore_mode") or "profile_default").strip(),
            "enabled": bool(data.get("enabled", True)),
            "notes": (data.get("notes") or "")[:500],
            "metadata": data.get("metadata") or {},
        }
        saved = operations_repo.create_bandwidth_schedule(
            tenant_id, normalized, actor=actor
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.create",
            target_type="bandwidth_schedule",
            target_id=str(saved.get("id")),
            payload={"plan_id": plan_id, "name": name},
        )
        return saved

    def list_bandwidth_schedules(self, *, tenant_id: int,
                                 plan_id: int | None = None,
                                 limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_bandwidth_schedules(
            tenant_id, plan_id=plan_id, limit=limit, offset=offset
        )

    def apply_bandwidth_schedule(self, *, tenant_id: int, schedule_id: int,
                                 actor: str) -> dict:
        schedule = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not schedule:
            raise RadiusNotFound("schedule not found")
        # Foundation only: no realtime RADIUS mutation here.
        log = operations_repo.log_bandwidth_schedule(
            tenant_id, schedule_id, action="dry_run_apply", status="planned",
            message="Validated schedule. Real-time RADIUS apply worker is not enabled in this slice.",
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.apply_planned",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"log_id": log.get("id")},
        )
        return {"schedule": schedule, "log": log, "applied_to_radius": False}

    def create_print_template(self, *, tenant_id: int, actor: str, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        orientation = (data.get("orientation") or "portrait").strip().lower()
        if orientation not in _PRINT_ORIENTATIONS:
            raise RadiusValidationError("orientation must be portrait or landscape")
        normalized = {
            "name": name,
            "orientation": orientation,
            "cards_per_row": _int_field(data, "cards_per_row", minimum=1, default=2),
            "cards_per_column": _int_field(data, "cards_per_column", minimum=1, default=5),
            "page_size": (data.get("page_size") or "A4").strip(),
            "show_qr": bool(data.get("show_qr", True)),
            "username_x": _float_field(data, "username_x", default=0),
            "username_y": _float_field(data, "username_y", default=0),
            "password_x": _float_field(data, "password_x", default=0),
            "password_y": _float_field(data, "password_y", default=0),
            "qr_x": _float_field(data, "qr_x", default=0),
            "qr_y": _float_field(data, "qr_y", default=0),
            "font_size": _int_field(data, "font_size", minimum=6, default=12),
            "color": (data.get("color") or "#1f2937").strip(),
            "layout": data.get("layout") or {},
        }
        try:
            saved = operations_repo.create_print_template(
                tenant_id, normalized, actor=actor
            )
        except sqlite3.IntegrityError:
            raise RadiusValidationError("print template name already exists")
        self._audit.record(
            actor=actor,
            action="card_print_template.create",
            target_type="card_print_template",
            target_id=str(saved.get("id")),
            payload={"name": name},
        )
        return saved

    def list_print_templates(self, *, tenant_id: int,
                             limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_print_templates(tenant_id, limit=limit, offset=offset)

    def render_print_template_preview(self, *, tenant_id: int, template_id: int,
                                      sample: Optional[dict] = None) -> dict:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")
        return {
            "template": template,
            "preview": {
                "renderer": "json_layout_preview",
                "cards_per_page": int(template.get("cards_per_row") or 0)
                                  * int(template.get("cards_per_column") or 0),
                "qr_supported": bool(template.get("show_qr")),
                "sample": sample or {
                    "username": "CARD1234",
                    "has_password": True,
                    "qr_payload": "CARD1234",
                },
            },
            "export_generated": False,
        }

    def backup_status(self, *, tenant_id: int) -> dict:
        return operations_repo.backup_status(tenant_id)

    def run_local_backup(self, *, tenant_id: int, actor: str) -> dict:
        source = Path(db_path())
        job = operations_repo.ensure_backup_job(tenant_id, actor=actor)
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        target = backup_dir / f"hoberadius-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        try:
            with sqlite3.connect(str(target)) as dest:
                db().backup(dest)
            verified = target.exists() and target.stat().st_size > 0
            status = "success" if verified else "failed"
            message = "Local SQLite backup verified." if verified else "Backup file was not created."
        except sqlite3.Error as exc:
            verified = False
            status = "failed"
            message = f"SQLite backup failed: {exc}"
        log = operations_repo.record_backup_run(
            tenant_id,
            job_id=job.get("id"),
            status=status,
            path=str(target) if target.exists() else "",
            message=message,
        )
        self._audit.record(
            actor=actor,
            action="backup.local_run",
            target_type="backup_job",
            target_id=str(job.get("id")),
            payload={"status": status, "verified": verified},
        )
        return {"job": operations_repo.ensure_backup_job(tenant_id), "run": log, "verified": verified}


def get_operations_service() -> OperationsService:
    from .audit import get_audit_service
    return OperationsService(get_audit_service())
