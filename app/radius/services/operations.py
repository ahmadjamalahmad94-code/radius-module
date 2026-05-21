"""Operational ISP foundations: distributors, schedules, printing, backups."""
from __future__ import annotations

import re
import sqlite3
import os
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


def _rate_limit_from_schedule(schedule: dict | None) -> str:
    schedule = schedule or {}
    up = int(schedule.get("speed_up_kbps") or 0)
    down = int(schedule.get("speed_down_kbps") or 0)
    return f"{up}k/{down}k"


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
        target_type = (data.get("target_type") or "plan").strip().lower()
        if target_type not in {"plan", "subscriber", "card_batch", "subscriber_group"}:
            raise RadiusValidationError(
                "target_type must be plan, subscriber, card_batch, or subscriber_group")

        plan_id = _int_field(data, "plan_id", minimum=0, default=0) or None
        subscriber_username = ""
        card_batch_id = None
        subscriber_group_id = None
        if target_type == "plan":
            if not plan_id:
                raise RadiusValidationError("plan_id is required")
            if not plans_repo.get_plan(tenant_id, plan_id):
                raise RadiusNotFound("plan not found")
        elif target_type == "subscriber":
            from ..db.repos import subscribers_repo
            subscriber_username = (data.get("subscriber_username") or data.get("username") or "").strip()
            if not subscriber_username:
                raise RadiusValidationError("subscriber_username is required")
            sub = subscribers_repo.get_subscriber(tenant_id, subscriber_username)
            if not sub:
                raise RadiusNotFound("subscriber not found")
            plan_id = sub.plan_id or plan_id
            if not plan_id:
                raise RadiusValidationError("subscriber has no plan_id; set plan_id first")
        elif target_type == "card_batch":
            from ..db.repos import cards_repo
            card_batch_id = _int_field(data, "card_batch_id", minimum=1)
            batch = cards_repo.get_batch(tenant_id, card_batch_id, include_deleted=True)
            if not batch:
                raise RadiusNotFound("card batch not found")
            plan_id = batch.plan_id or plan_id
        else:  # subscriber_group
            from ..db.repos import subscriber_groups_repo
            subscriber_group_id = _int_field(data, "subscriber_group_id", minimum=1)
            grp = subscriber_groups_repo.get(tenant_id, subscriber_group_id)
            if not grp:
                raise RadiusNotFound("subscriber group not found")
            plan_id = grp.get("default_plan_id") or plan_id
        normalized = {
            "plan_id": plan_id,
            "target_type": target_type,
            "subscriber_username": subscriber_username,
            "card_batch_id": card_batch_id,
            "subscriber_group_id": subscriber_group_id,
            "priority": _int_field(data, "priority", minimum=1, default=100),
            "name": name,
            "starts_at_time": _validate_time(data.get("starts_at_time"), "starts_at_time"),
            "ends_at_time": _validate_time(data.get("ends_at_time"), "ends_at_time"),
            "days_csv": (data.get("days_csv") or "").strip(),
            "speed_down_kbps": _int_field(data, "speed_down_kbps"),
            "speed_up_kbps": _int_field(data, "speed_up_kbps"),
            "cir_down_kbps": _int_field(data, "cir_down_kbps"),
            "cir_up_kbps": _int_field(data, "cir_up_kbps"),
            "restore_mode": (data.get("restore_mode") or "profile_default").strip(),
            "enabled": bool(data.get("enabled", True)),
            "notes": (data.get("notes") or "")[:500],
            "metadata": data.get("metadata") or {},
        }
        if not (normalized["speed_down_kbps"] or normalized["speed_up_kbps"]) \
                and normalized["restore_mode"] != "disconnect":
            raise RadiusValidationError(
                "أدخلي سرعة التنزيل أو سرعة الرفع (واحدة على الأقل). "
                "إذا كان الغرض من القاعدة فصل الجلسة فقط، اختاري «فصل الجلسة» في «بعد الانتهاء»."
            )
        saved = operations_repo.create_bandwidth_schedule(
            tenant_id, normalized, actor=actor
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.create",
            target_type="bandwidth_schedule",
            target_id=str(saved.get("id")),
            payload={
                "plan_id": plan_id,
                "target_type": target_type,
                "subscriber_username": subscriber_username,
                "card_batch_id": card_batch_id,
                "name": name,
            },
        )
        return saved

    def list_bandwidth_schedules(self, *, tenant_id: int,
                                 plan_id: int | None = None,
                                 target_type: str | None = None,
                                 subscriber_username: str | None = None,
                                 card_batch_id: int | None = None,
                                 subscriber_group_id: int | None = None,
                                 limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_bandwidth_schedules(
            tenant_id,
            plan_id=plan_id,
            target_type=target_type,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            subscriber_group_id=subscriber_group_id,
            limit=limit,
            offset=offset,
        )

    def get_bandwidth_schedule(self, *, tenant_id: int, schedule_id: int) -> dict | None:
        return operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)

    def update_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  schedule_id: int, data: dict) -> dict:
        current = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not current:
            raise RadiusNotFound("schedule not found")
        normalized = {
            "name": (data.get("name") or current.get("name") or "قاعدة سرعة").strip(),
            "starts_at_time": _validate_time(data.get("starts_at_time"), "starts_at_time"),
            "ends_at_time": _validate_time(data.get("ends_at_time"), "ends_at_time"),
            "days_csv": (data.get("days_csv") or "").strip(),
            "speed_down_kbps": _int_field(data, "speed_down_kbps"),
            "speed_up_kbps": _int_field(data, "speed_up_kbps"),
            "cir_down_kbps": _int_field(data, "cir_down_kbps"),
            "cir_up_kbps": _int_field(data, "cir_up_kbps"),
            "restore_mode": (data.get("restore_mode") or "profile_default").strip(),
            "priority": _int_field(data, "priority", minimum=1, default=100),
            "enabled": bool(data.get("enabled", True)),
            "notes": (data.get("notes") or "")[:500],
        }
        if not (normalized["speed_down_kbps"] or normalized["speed_up_kbps"]) \
                and normalized["restore_mode"] != "disconnect":
            raise RadiusValidationError(
                "أدخلي سرعة التنزيل أو سرعة الرفع (واحدة على الأقل). "
                "إذا كان الغرض من القاعدة فصل الجلسة فقط، اختاري «فصل الجلسة» في «بعد الانتهاء»."
            )
        saved = operations_repo.update_bandwidth_schedule(tenant_id, schedule_id, normalized)
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.update",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"name": saved.get("name"), "enabled": saved.get("enabled")},
        )
        return saved

    def set_bandwidth_schedule_enabled(self, *, tenant_id: int, actor: str,
                                       schedule_id: int, enabled: bool) -> dict:
        saved = operations_repo.set_bandwidth_schedule_enabled(tenant_id, schedule_id, enabled)
        if not saved:
            raise RadiusNotFound("schedule not found")
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.enable" if enabled else "bandwidth_schedule.disable",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"enabled": enabled},
        )
        return saved

    def set_bandwidth_schedules_enabled_for_target(
        self,
        *,
        tenant_id: int,
        actor: str,
        target_type: str,
        enabled: bool,
        plan_id: int | None = None,
        subscriber_username: str = "",
        card_batch_id: int | None = None,
        subscriber_group_id: int | None = None,
    ) -> int:
        count = operations_repo.set_bandwidth_schedules_enabled_for_target(
            tenant_id,
            target_type=target_type,
            enabled=enabled,
            plan_id=plan_id,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            subscriber_group_id=subscriber_group_id,
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.bulk_enable" if enabled else "bandwidth_schedule.bulk_disable",
            target_type=target_type,
            target_id=str(plan_id or subscriber_username or card_batch_id or subscriber_group_id or ""),
            payload={"enabled": enabled, "count": count},
        )
        return count

    def delete_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  schedule_id: int) -> bool:
        current = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not current:
            raise RadiusNotFound("schedule not found")
        deleted = operations_repo.delete_bandwidth_schedule(tenant_id, schedule_id)
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.delete",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"name": current.get("name"), "deleted": deleted},
        )
        return deleted

    def apply_bandwidth_schedule(self, *, tenant_id: int, schedule_id: int,
                                 actor: str, live: bool = False) -> dict:
        schedule = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not schedule:
            raise RadiusNotFound("schedule not found")
        rate = _rate_limit_from_schedule(schedule)
        live_enabled = os.environ.get("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY") == "1"
        if not live or not live_enabled:
            message = (
                "Live RADIUS apply is disabled; dry-run only."
                if live and not live_enabled
                else "Validated schedule. Real-time RADIUS apply was not requested."
            )
            log = operations_repo.log_bandwidth_schedule(
                tenant_id, schedule_id, action="dry_run_apply", status="planned",
                message=message,
            )
            self._audit.record(
                actor=actor,
                action="bandwidth_schedule.apply_planned",
                target_type="bandwidth_schedule",
                target_id=str(schedule_id),
                payload={"log_id": log.get("id"), "live_requested": bool(live)},
            )
            return {
                "schedule": schedule,
                "log": log,
                "rate_limit": rate,
                "applied_to_radius": False,
                "dry_run": True,
                "live_requested": bool(live),
                "live_enabled": live_enabled,
            }

        usernames = operations_repo.usernames_for_bandwidth_schedule(
            tenant_id,
            schedule,
            limit=1000,
        )
        results: list[dict] = []
        applied = 0
        from ..integration import radius_coa
        for username in usernames:
            coa = radius_coa.change_user_rate(
                tenant_id,
                username,
                new_rate_limit=rate,
            )
            if coa.ok:
                applied += 1
            results.append({
                "username": username,
                "ok": bool(coa.ok),
                "code": coa.code_name,
                "message": coa.reply_message,
            })
        status = "applied" if applied else "no_active_sessions"
        if not usernames:
            status = "no_targets"
        log = operations_repo.log_bandwidth_schedule(
            tenant_id,
            schedule_id,
            action="live_apply",
            status=status,
            message=f"Applied {applied}/{len(usernames)} active sessions.",
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.apply_live",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={
                "log_id": log.get("id"),
                "rate_limit": rate,
                "target_count": len(usernames),
                "applied_count": applied,
            },
        )
        return {
            "schedule": schedule,
            "log": log,
            "rate_limit": rate,
            "applied_to_radius": applied > 0,
            "dry_run": False,
            "live_requested": True,
            "live_enabled": True,
            "target_count": len(usernames),
            "applied_count": applied,
            "results": results,
        }

    def resolve_effective_bandwidth_schedule(
        self,
        *,
        tenant_id: int,
        subscriber_username: str = "",
        card_batch_id: int | None = None,
        plan_id: int | None = None,
    ) -> dict:
        rule = operations_repo.resolve_effective_bandwidth_schedule(
            tenant_id,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            plan_id=plan_id,
        )
        return {
            "effective_rule": rule,
            "has_rule": bool(rule),
            "rate_limit": _rate_limit_from_schedule(rule) if rule else "",
            "source": (rule or {}).get("target_type") or "none",
            "precedence": ["subscriber", "card_batch", "plan"],
            "input": {
                "subscriber_username": subscriber_username,
                "card_batch_id": card_batch_id,
                "plan_id": plan_id,
            },
        }

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
        layout = template.get("layout_json")
        if not isinstance(layout, dict):
            layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}
        width_mm = max(float(layout.get("card_width_mm") or 85), 1.0)
        height_mm = max(float(layout.get("card_height_mm") or 54), 1.0)

        def _placement(prefix: str) -> dict:
            x_mm = float(template.get(f"{prefix}_x") or 0)
            y_mm = float(template.get(f"{prefix}_y") or 0)
            return {
                "x_mm": x_mm,
                "y_mm": y_mm,
                "x_percent": max(0, min(100, round((x_mm / width_mm) * 100, 2))),
                "y_percent": max(0, min(100, round((y_mm / height_mm) * 100, 2))),
            }

        sample_payload = sample or {
            "username": "CARD1234",
            "has_password": True,
            "qr_payload": "CARD1234",
        }
        return {
            "template": template,
            "preview": {
                "renderer": "visual_card_preview",
                "cards_per_page": int(template.get("cards_per_row") or 0)
                                  * int(template.get("cards_per_column") or 0),
                "qr_supported": bool(template.get("show_qr")),
                "card": {
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "font_size": int(template.get("font_size") or 12),
                    "color": template.get("color") or "#1f2937",
                },
                "placements": {
                    "username": _placement("username"),
                    "password": _placement("password"),
                    "qr": _placement("qr"),
                },
                "sample": sample_payload,
            },
            "export_generated": False,
        }

    def export_print_template_pdf(self, *, tenant_id: int, template_id: int,
                                  sample: Optional[dict] = None) -> bytes:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")

        from io import BytesIO
        from reportlab.lib.pagesizes import A4, letter, landscape, portrait
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        layout = template.get("layout_json")
        if not isinstance(layout, dict):
            layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}

        page_size = str(template.get("page_size") or "A4").strip().lower()
        base_size = letter if page_size == "letter" else A4
        orientation = str(template.get("orientation") or "portrait").lower()
        pagesize = landscape(base_size) if orientation == "landscape" else portrait(base_size)
        page_width, page_height = pagesize

        rows = max(int(template.get("cards_per_column") or 1), 1)
        cols = max(int(template.get("cards_per_row") or 1), 1)
        margin = 10 * mm
        gap = 4 * mm
        fit_width = (page_width - (margin * 2) - (gap * (cols - 1))) / cols
        fit_height = (page_height - (margin * 2) - (gap * (rows - 1))) / rows
        card_width = min(max(float(layout.get("card_width_mm") or 85), 1.0) * mm, fit_width)
        card_height = min(max(float(layout.get("card_height_mm") or 54), 1.0) * mm, fit_height)
        font_size = max(min(int(template.get("font_size") or 12), 36), 6)
        text_color = _reportlab_color(str(template.get("color") or "#1f2937"))
        show_qr = bool(template.get("show_qr"))

        sample_payload = sample or {}
        cards = sample_payload.get("cards")
        if not isinstance(cards, list) or not cards:
            cards = [{
                "username": sample_payload.get("username") or "CARD1234",
                "password": sample_payload.get("password") or "********",
                "qr_payload": sample_payload.get("qr_payload") or sample_payload.get("username") or "CARD1234",
            }]
        cards_per_page = rows * cols
        while len(cards) < cards_per_page:
            index = len(cards) + 1
            cards.append({
                "username": f"CARD{index:04d}",
                "password": "********",
                "qr_payload": f"CARD{index:04d}",
            })

        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=pagesize)
        pdf.setTitle(f"Card print template - {template.get('name') or template_id}")
        pdf.setAuthor("HobeRadius")

        for idx, card in enumerate(cards[:cards_per_page]):
            row = idx // cols
            col = idx % cols
            x = margin + col * (card_width + gap)
            y = page_height - margin - card_height - row * (card_height + gap)
            _draw_template_card(
                pdf,
                x=x,
                y=y,
                width=card_width,
                height=card_height,
                template=template,
                card=card if isinstance(card, dict) else {},
                font_size=font_size,
                text_color=text_color,
                show_qr=show_qr,
                mm_unit=mm,
            )

        pdf.showPage()
        pdf.save()
        return output.getvalue()

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


def _reportlab_color(value: str):
    from reportlab.lib import colors

    raw = (value or "#1f2937").strip()
    try:
        return colors.HexColor(raw if raw.startswith("#") else f"#{raw}")
    except Exception:
        return colors.HexColor("#1f2937")


def _draw_template_card(pdf, *, x: float, y: float, width: float, height: float,
                        template: dict, card: dict, font_size: int,
                        text_color, show_qr: bool, mm_unit: float) -> None:
    from reportlab.lib import colors

    pdf.setStrokeColor(colors.HexColor("#22a7bd"))
    pdf.setFillColor(colors.HexColor("#ffffff"))
    pdf.roundRect(x, y, width, height, 7, stroke=1, fill=1)
    pdf.setStrokeColor(colors.HexColor("#d8edf3"))
    pdf.line(x + 5 * mm_unit, y + height - 10 * mm_unit,
             x + width - 5 * mm_unit, y + height - 10 * mm_unit)

    def _coord(prefix: str) -> tuple[float, float]:
        x_mm = float(template.get(f"{prefix}_x") or 0)
        y_mm = float(template.get(f"{prefix}_y") or 0)
        return x + x_mm * mm_unit, y + height - y_mm * mm_unit

    username = str(card.get("username") or "CARD1234")
    password = str(card.get("password") or "********")
    qr_payload = str(card.get("qr_payload") or username)

    pdf.setFillColor(text_color)
    pdf.setFont("Helvetica-Bold", font_size)
    ux, uy = _coord("username")
    pdf.drawString(ux, uy, username)
    px, py = _coord("password")
    pdf.setFont("Helvetica", max(font_size - 1, 6))
    pdf.drawString(px, py, password)

    if show_qr:
        qx, qy = _coord("qr")
        size = 16 * mm_unit
        pdf.setStrokeColor(text_color)
        pdf.rect(qx, qy - size, size, size, stroke=1, fill=0)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawCentredString(qx + size / 2, qy - size / 2 - 2, "QR")
        pdf.setFont("Helvetica", 5)
        pdf.drawCentredString(qx + size / 2, qy - size - 5, qr_payload[:18])


def get_operations_service() -> OperationsService:
    from .audit import get_audit_service
    return OperationsService(get_audit_service())
