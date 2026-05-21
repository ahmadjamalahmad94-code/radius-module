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
_PRINT_PRESETS: dict[str, dict[str, Any]] = {
    "modern": {
        "label": "حديث",
        "gradient_start": "#0f172a",
        "gradient_end": "#22a7bd",
        "accent_color": "#f59e0b",
        "text_color": "#ffffff",
        "surface_color": "#e8f7fb",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة إنترنت",
        "footer_text": "احتفظ ببيانات الدخول حتى نهاية الصلاحية",
    },
    "dark": {
        "label": "داكن احترافي",
        "gradient_start": "#111827",
        "gradient_end": "#334155",
        "accent_color": "#38bdf8",
        "text_color": "#ffffff",
        "surface_color": "#dbeafe",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "Hotspot Voucher",
        "footer_text": "الدعم الفني متاح عبر نقطة البيع",
    },
    "gold": {
        "label": "ذهبي",
        "gradient_start": "#3b2f1c",
        "gradient_end": "#b7791f",
        "accent_color": "#facc15",
        "text_color": "#fff7ed",
        "surface_color": "#fff7d6",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة مميزة",
        "footer_text": "سرعة ثابتة وتجربة أفضل",
    },
    "minimal": {
        "label": "بسيط",
        "gradient_start": "#ffffff",
        "gradient_end": "#f8fafc",
        "accent_color": "#0ea5e9",
        "text_color": "#0f172a",
        "surface_color": "#eff6ff",
        "qr_style": "clean",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة دخول",
        "footer_text": "اسم المستخدم وكلمة المرور للاستخدام مرة واحدة",
    },
    "telecom": {
        "label": "اتصالات",
        "gradient_start": "#083344",
        "gradient_end": "#0891b2",
        "accent_color": "#67e8f9",
        "text_color": "#ecfeff",
        "surface_color": "#cffafe",
        "qr_style": "rounded",
        "brand_name": "HobeRadius",
        "card_title": "شبكة لاسلكية",
        "footer_text": "امسح QR أو أدخل البيانات يدويًا",
    },
    "neon": {
        "label": "نيون",
        "gradient_start": "#240046",
        "gradient_end": "#00b4d8",
        "accent_color": "#c8ff00",
        "text_color": "#ffffff",
        "surface_color": "#e0f2fe",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة سرعة",
        "footer_text": "مررها للعميل بعد الدفع مباشرة",
    },
}
_PRINT_BOOL_FIELDS = {
    "show_username",
    "show_password",
    "show_price",
    "show_hotspot",
    "show_validity",
    "show_serial",
    "show_guides",
    "show_brand",
}


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


def _safe_hex(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", raw):
        return raw if raw.startswith("#") else f"#{raw}"
    return default


def _boolish(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _template_layout(data: dict) -> dict:
    layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}
    merged = {**layout, **data}
    preset_name = str(merged.get("design_preset") or "modern").strip()
    if preset_name not in _PRINT_PRESETS:
        preset_name = "modern"
    preset = _PRINT_PRESETS[preset_name]

    def _text(key: str, default: str = "", max_len: int = 140) -> str:
        return str(merged.get(key) or default).strip()[:max_len]

    normalized = {
        **layout,
        "preview_mode": "visual_design_room",
        "design_preset": preset_name,
        "card_width_mm": _float_field(merged, "card_width_mm", minimum=1, default=85),
        "card_height_mm": _float_field(merged, "card_height_mm", minimum=1, default=54),
        "gradient_start": _safe_hex(merged.get("gradient_start"), preset["gradient_start"]),
        "gradient_end": _safe_hex(merged.get("gradient_end"), preset["gradient_end"]),
        "accent_color": _safe_hex(merged.get("accent_color"), preset["accent_color"]),
        "text_color": _safe_hex(merged.get("text_color") or merged.get("color"), preset["text_color"]),
        "surface_color": _safe_hex(merged.get("surface_color"), preset["surface_color"]),
        "qr_style": _text("qr_style", preset["qr_style"], 30),
        "brand_name": _text("brand_name", preset["brand_name"], 80),
        "card_title": _text("card_title", preset["card_title"], 80),
        "footer_text": _text("footer_text", preset["footer_text"], 180),
        "hotspot_address": _text("hotspot_address", "hotspot.local", 120),
        "price_text": _text("price_text", "", 60),
        "validity_text": _text("validity_text", "", 60),
        "instructions_text": _text(
            "instructions_text",
            "استخدم اسم المستخدم وكلمة المرور أو QR للدخول.",
            180,
        ),
        "background_style": _text("background_style", "gradient", 30),
        "bleed_marks": _boolish(merged.get("bleed_marks"), False),
    }
    defaults = {
        "show_username": True,
        "show_password": True,
        "show_price": False,
        "show_hotspot": True,
        "show_validity": True,
        "show_serial": True,
        "show_guides": False,
        "show_brand": True,
    }
    for key, default in defaults.items():
        normalized[key] = _boolish(merged.get(key), default)
    return normalized


def _print_presets_list() -> list[dict]:
    return [
        {"key": key, "label": value["label"], "layout": {**value, "design_preset": key}}
        for key, value in _PRINT_PRESETS.items()
    ]


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
        layout = _template_layout(data)
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
            "color": _safe_hex(data.get("color") or layout.get("text_color"), "#1f2937"),
            "layout": layout,
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

    def update_print_template(self, *, tenant_id: int, actor: str,
                              template_id: int, data: dict) -> dict:
        current = operations_repo.get_print_template(tenant_id, template_id)
        if not current:
            raise RadiusNotFound("print template not found")
        merged = {**current, **data}
        if isinstance(current.get("layout_json"), dict):
            merged["layout"] = {**current["layout_json"], **(data.get("layout") or {})}
        if "name" in data and not str(data.get("name") or "").strip():
            raise RadiusValidationError("name is required")
        orientation = str(merged.get("orientation") or "portrait").strip().lower()
        if orientation not in _PRINT_ORIENTATIONS:
            raise RadiusValidationError("orientation must be portrait or landscape")
        layout = _template_layout(merged)
        normalized = {
            "name": str(merged.get("name") or "").strip(),
            "orientation": orientation,
            "cards_per_row": _int_field(merged, "cards_per_row", minimum=1, default=2),
            "cards_per_column": _int_field(merged, "cards_per_column", minimum=1, default=5),
            "page_size": str(merged.get("page_size") or "A4").strip(),
            "show_qr": _boolish(merged.get("show_qr"), True),
            "username_x": _float_field(merged, "username_x", default=0),
            "username_y": _float_field(merged, "username_y", default=0),
            "password_x": _float_field(merged, "password_x", default=0),
            "password_y": _float_field(merged, "password_y", default=0),
            "qr_x": _float_field(merged, "qr_x", default=0),
            "qr_y": _float_field(merged, "qr_y", default=0),
            "font_size": _int_field(merged, "font_size", minimum=6, default=12),
            "color": _safe_hex(merged.get("color") or layout.get("text_color"), "#1f2937"),
            "layout": layout,
        }
        try:
            saved = operations_repo.update_print_template(
                tenant_id, template_id, normalized, actor=actor
            )
        except sqlite3.IntegrityError:
            raise RadiusValidationError("print template name already exists")
        self._audit.record(
            actor=actor,
            action="card_print_template.update",
            target_type="card_print_template",
            target_id=str(template_id),
            payload={"name": saved.get("name")},
        )
        return saved

    def list_print_templates(self, *, tenant_id: int,
                             limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_print_templates(tenant_id, limit=limit, offset=offset)

    def list_print_template_presets(self) -> list[dict]:
        return _print_presets_list()

    def list_print_jobs(self, *, tenant_id: int,
                        limit: int = 50, offset: int = 0) -> list[dict]:
        return operations_repo.list_print_jobs(tenant_id, limit=limit, offset=offset)

    def render_print_template_preview(self, *, tenant_id: int, template_id: int,
                                      sample: Optional[dict] = None) -> dict:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")
        layout = template.get("layout_json")
        if not isinstance(layout, dict):
            layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}
        layout = _template_layout({**template, "layout": layout})
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

        raw_sample = sample if isinstance(sample, dict) else {}
        sample_username = str(raw_sample.get("username") or "CARD1234")
        sample_payload = {
            "username": sample_username,
            "has_password": bool(raw_sample.get("has_password", True)),
            "qr_payload": str(raw_sample.get("qr_payload") or sample_username),
            "price": str(raw_sample.get("price") or layout.get("price_text") or ""),
            "validity": str(raw_sample.get("validity") or layout.get("validity_text") or ""),
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
                    "layout": layout,
                },
                "design": {
                    "preset": layout.get("design_preset"),
                    "brand_name": layout.get("brand_name"),
                    "card_title": layout.get("card_title"),
                    "gradient_start": layout.get("gradient_start"),
                    "gradient_end": layout.get("gradient_end"),
                    "accent_color": layout.get("accent_color"),
                    "text_color": layout.get("text_color"),
                    "surface_color": layout.get("surface_color"),
                    "qr_style": layout.get("qr_style"),
                    "footer_text": layout.get("footer_text"),
                    "hotspot_address": layout.get("hotspot_address"),
                    "price_text": layout.get("price_text"),
                    "validity_text": layout.get("validity_text"),
                },
                "placements": {
                    "username": _placement("username"),
                    "password": _placement("password"),
                    "qr": _placement("qr"),
                },
                "sample": sample_payload,
                "capabilities": {
                    "sample_pdf": True,
                    "batch_pdf": True,
                    "csv": True,
                    "excel": False,
                    "png": False,
                },
            },
            "export_generated": False,
        }

    def export_print_template_pdf(self, *, tenant_id: int, template_id: int,
                                  sample: Optional[dict] = None,
                                  batch_id: int | None = None,
                                  actor: str = "system") -> bytes:
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
        layout = _template_layout({**template, "layout": layout})

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
        text_color = _reportlab_color(str(layout.get("text_color") or template.get("color") or "#1f2937"))
        show_qr = bool(template.get("show_qr"))

        sample_payload = sample or {}
        export_type = "sample_pdf"
        batch = None
        cards: list[dict]
        if batch_id:
            batch = cards_repo.get_batch(tenant_id, batch_id, include_deleted=True)
            if not batch:
                raise RadiusNotFound("card batch not found")
            raw_cards = cards_repo.list_cards(tenant_id, batch_id=batch_id, limit=20000, offset=0)
            cards = [
                {
                    "username": c.username,
                    "password": c.password,
                    "qr_payload": c.username,
                    "serial": str(c.id or ""),
                    "price": layout.get("price_text") or "",
                    "validity": layout.get("validity_text") or "",
                    "batch_code": batch.batch_code,
                    "package_name": batch.package_name,
                    "expire_at": c.expire_at.isoformat() if c.expire_at else "",
                }
                for c in raw_cards
            ]
            export_type = "batch_pdf"
        else:
            cards = sample_payload.get("cards") if isinstance(sample_payload.get("cards"), list) else []
            if not cards:
                cards = [{
                    "username": sample_payload.get("username") or "CARD1234",
                    "password": sample_payload.get("password") or "********",
                    "qr_payload": sample_payload.get("qr_payload") or sample_payload.get("username") or "CARD1234",
                    "serial": "SAMPLE",
                    "price": sample_payload.get("price") or layout.get("price_text") or "",
                    "validity": sample_payload.get("validity") or layout.get("validity_text") or "",
                    "batch_code": "DEMO",
                    "package_name": layout.get("card_title") or "",
                }]
        if not cards:
            raise RadiusValidationError("selected batch has no cards")

        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=pagesize)
        pdf.setTitle(f"Card print template - {template.get('name') or template_id}")
        pdf.setAuthor("HobeRadius")

        cards_per_page = rows * cols
        file_name = f"cards-template-{template_id}.pdf"
        if batch_id:
            file_name = f"cards-batch-{batch_id}-template-{template_id}.pdf"
        job = operations_repo.create_print_job(
            tenant_id,
            template_id=template_id,
            batch_id=batch_id,
            export_type=export_type,
            status="started",
            card_count=len(cards),
            file_name=file_name,
            metadata={"template_name": template.get("name"), "batch_code": getattr(batch, "batch_code", "") if batch else ""},
            actor=actor,
        )
        try:
            for idx, card in enumerate(cards):
                if idx and idx % cards_per_page == 0:
                    pdf.showPage()
                slot = idx % cards_per_page
                row = slot // cols
                col = slot % cols
                x = margin + col * (card_width + gap)
                y = page_height - margin - card_height - row * (card_height + gap)
                _draw_template_card(
                    pdf,
                    x=x,
                    y=y,
                    width=card_width,
                    height=card_height,
                    template=template,
                    layout=layout,
                    card=card if isinstance(card, dict) else {},
                    font_size=font_size,
                    text_color=text_color,
                    show_qr=show_qr,
                    mm_unit=mm,
                )

            pdf.showPage()
            pdf.save()
            payload = output.getvalue()
            operations_repo.finish_print_job(
                tenant_id,
                int(job.get("id") or 0),
                status="success",
                card_count=len(cards),
                file_name=file_name,
                message=f"Generated {len(cards)} card(s).",
                metadata={
                    "template_name": template.get("name"),
                    "batch_id": batch_id,
                    "cards_per_page": cards_per_page,
                    "bytes": len(payload),
                },
            )
            self._audit.record(
                actor=actor,
                action="card_print_template.export_pdf",
                target_type="card_print_template",
                target_id=str(template_id),
                payload={"batch_id": batch_id, "card_count": len(cards), "job_id": job.get("id")},
            )
            return payload
        except Exception as exc:
            operations_repo.finish_print_job(
                tenant_id,
                int(job.get("id") or 0),
                status="failed",
                card_count=len(cards),
                file_name=file_name,
                message=str(exc),
                metadata={"template_name": template.get("name"), "batch_id": batch_id},
            )
            raise

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
                        template: dict, layout: dict, card: dict, font_size: int,
                        text_color, show_qr: bool, mm_unit: float) -> None:
    from reportlab.lib import colors
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    bg = _reportlab_color(str(layout.get("gradient_start") or "#0f172a"))
    bg2 = _reportlab_color(str(layout.get("gradient_end") or "#22a7bd"))
    accent = _reportlab_color(str(layout.get("accent_color") or "#f59e0b"))
    surface = _reportlab_color(str(layout.get("surface_color") or "#e8f7fb"))

    radius = 7
    pdf.setStrokeColor(colors.Color(1, 1, 1, alpha=0.35))
    pdf.setFillColor(bg)
    pdf.roundRect(x, y, width, height, radius, stroke=0, fill=1)
    pdf.setFillColor(bg2)
    pdf.roundRect(x + width * 0.55, y, width * 0.45, height, radius, stroke=0, fill=1)
    pdf.setFillColor(accent)
    pdf.roundRect(x + 4 * mm_unit, y + height - 6 * mm_unit, width - 8 * mm_unit, 2 * mm_unit, 1.2, stroke=0, fill=1)

    if layout.get("show_guides"):
        pdf.setStrokeColor(colors.Color(1, 1, 1, alpha=0.22))
        pdf.setLineWidth(0.3)
        pdf.line(x + width / 2, y + 2 * mm_unit, x + width / 2, y + height - 2 * mm_unit)
        pdf.line(x + 2 * mm_unit, y + height / 2, x + width - 2 * mm_unit, y + height / 2)

    def _coord(prefix: str) -> tuple[float, float]:
        x_mm = float(template.get(f"{prefix}_x") or 0)
        y_mm = float(template.get(f"{prefix}_y") or 0)
        return x + x_mm * mm_unit, y + height - y_mm * mm_unit

    username = str(card.get("username") or "CARD1234")
    password = str(card.get("password") or "********")
    qr_payload = str(card.get("qr_payload") or username)

    pdf.setFillColor(text_color)
    pdf.setFont("Helvetica-Bold", max(font_size + 1, 7))
    if layout.get("show_brand"):
        pdf.drawString(x + 7 * mm_unit, y + height - 12 * mm_unit, str(layout.get("brand_name") or "HobeRadius")[:38])
    pdf.setFont("Helvetica-Bold", max(font_size, 7))
    pdf.drawString(x + 7 * mm_unit, y + height - 20 * mm_unit, str(layout.get("card_title") or "Internet Card")[:44])

    label_size = max(font_size - 3, 5)
    pdf.setFont("Helvetica", label_size)
    ux, uy = _coord("username")
    if layout.get("show_username", True):
        pdf.setFillColor(surface)
        pdf.roundRect(ux - 2 * mm_unit, uy - 4 * mm_unit, width * 0.42, 8 * mm_unit, 3, stroke=0, fill=1)
        pdf.setFillColor(colors.HexColor("#0f172a"))
        pdf.drawString(ux, uy + 1 * mm_unit, "USER")
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawString(ux + 13 * mm_unit, uy + 1 * mm_unit, username[:24])
    px, py = _coord("password")
    if layout.get("show_password", True):
        pdf.setFillColor(surface)
        pdf.roundRect(px - 2 * mm_unit, py - 4 * mm_unit, width * 0.42, 8 * mm_unit, 3, stroke=0, fill=1)
        pdf.setFillColor(colors.HexColor("#0f172a"))
        pdf.setFont("Helvetica", label_size)
        pdf.drawString(px, py + 1 * mm_unit, "PASS")
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawString(px + 13 * mm_unit, py + 1 * mm_unit, password[:24])

    if show_qr:
        qx, qy = _coord("qr")
        size = 16 * mm_unit
        pdf.setFillColor(colors.white)
        pdf.roundRect(qx - 1.5 * mm_unit, qy - size - 1.5 * mm_unit,
                      size + 3 * mm_unit, size + 3 * mm_unit, 4, stroke=0, fill=1)
        qr = QrCodeWidget(qr_payload)
        bounds = qr.getBounds()
        qr_width = bounds[2] - bounds[0]
        qr_height = bounds[3] - bounds[1]
        drawing = Drawing(size, size, transform=[size / qr_width, 0, 0, size / qr_height, 0, 0])
        drawing.add(qr)
        renderPDF.draw(drawing, pdf, qx, qy - size)

    pdf.setFillColor(text_color)
    pdf.setFont("Helvetica", max(font_size - 3, 5))
    meta_parts = []
    if layout.get("show_price") and (card.get("price") or layout.get("price_text")):
        meta_parts.append(str(card.get("price") or layout.get("price_text"))[:22])
    if layout.get("show_validity") and (card.get("validity") or layout.get("validity_text")):
        meta_parts.append(str(card.get("validity") or layout.get("validity_text"))[:22])
    if layout.get("show_hotspot") and layout.get("hotspot_address"):
        meta_parts.append(str(layout.get("hotspot_address"))[:28])
    if meta_parts:
        pdf.drawString(x + 7 * mm_unit, y + 8 * mm_unit, "  |  ".join(meta_parts)[:86])
    if layout.get("show_serial") and card.get("serial"):
        pdf.drawRightString(x + width - 6 * mm_unit, y + 8 * mm_unit, f"#{card.get('serial')}")
    footer = str(layout.get("footer_text") or "")[:80]
    if footer:
        pdf.setFillColor(colors.Color(1, 1, 1, alpha=0.82))
        pdf.drawString(x + 7 * mm_unit, y + 3.2 * mm_unit, footer[:70])


def get_operations_service() -> OperationsService:
    from .audit import get_audit_service
    return OperationsService(get_audit_service())
