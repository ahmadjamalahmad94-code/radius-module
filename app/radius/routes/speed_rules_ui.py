"""Shared helpers for embedding speed-rule controls in admin edit pages."""
from __future__ import annotations

from ..core.errors import RadiusValidationError
from ..services.operations import get_operations_service

_DAY_CODES = {"sat", "sun", "mon", "tue", "wed", "thu", "fri"}


def _days_from_form(form, prefix: str) -> str:
    """Normalize multi-value form input → canonical CSV (sat,sun,mon,...)."""
    raw = form.getlist(prefix) if hasattr(form, "getlist") else (form.get(prefix) or "").split(",")
    seen = []
    for d in raw:
        code = (d or "").strip().lower()
        if code in _DAY_CODES and code not in seen:
            seen.append(code)
    # canonical sort order
    order = ["sat", "sun", "mon", "tue", "wed", "thu", "fri"]
    return ",".join(d for d in order if d in seen)


def speed_rules_panel(
    *,
    tenant_id: int,
    target_type: str,
    return_to: str,
    title: str,
    help_text: str,
    plan_id: int | None = None,
    subscriber_username: str = "",
    card_batch_id: int | None = None,
    subscriber_group_id: int | None = None,
) -> dict:
    svc = get_operations_service()
    rules = svc.list_bandwidth_schedules(
        tenant_id=tenant_id,
        target_type=target_type,
        plan_id=plan_id if target_type == "plan" else None,
        subscriber_username=subscriber_username if target_type == "subscriber" else None,
        card_batch_id=card_batch_id if target_type == "card_batch" else None,
        subscriber_group_id=subscriber_group_id if target_type == "subscriber_group" else None,
        limit=200,
    )
    presets = svc.list_bandwidth_schedules(tenant_id=tenant_id, limit=500)
    return {
        "target_type": target_type,
        "plan_id": plan_id,
        "subscriber_username": subscriber_username,
        "card_batch_id": card_batch_id,
        "subscriber_group_id": subscriber_group_id,
        "return_to": return_to,
        "title": title,
        "help_text": help_text,
        "rules": rules,
        "presets": presets,
    }


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def create_staged_speed_rules(
    *,
    tenant_id: int,
    actor: str,
    form,
    target_type: str,
    plan_id: int | None = None,
    subscriber_username: str = "",
    card_batch_id: int | None = None,
    subscriber_group_id: int | None = None,
    metadata: dict | None = None,
) -> int:
    """Persist every sr_new_<n>_* rule staged by the embedded panel."""
    indices: set[int] = set()
    for key in form.keys():
        if not key.startswith("sr_new_"):
            continue
        rest = key[len("sr_new_"):]
        idx_part = rest.split("_", 1)[0]
        try:
            indices.add(int(idx_part))
        except ValueError:
            continue

    if not indices:
        return 0

    svc = get_operations_service()
    created = 0
    base_meta = {
        "embedded_target": target_type,
        "added_via": "speed_rules_panel_defer",
        **(metadata or {}),
    }
    for idx in sorted(indices):
        suffix = str(idx)
        starts = (form.get(f"sr_new_{suffix}_starts_at_time") or "").strip()
        ends = (form.get(f"sr_new_{suffix}_ends_at_time") or "").strip()
        if not starts or not ends:
            continue
        svc.create_bandwidth_schedule(
            tenant_id=tenant_id,
            actor=actor,
            data={
                "target_type": target_type,
                "plan_id": plan_id,
                "subscriber_username": subscriber_username,
                "card_batch_id": card_batch_id,
                "subscriber_group_id": subscriber_group_id,
                "name": (form.get(f"sr_new_{suffix}_name") or "").strip() or "قاعدة سرعة",
                "starts_at_time": starts,
                "ends_at_time": ends,
                "days_csv": form.get(f"sr_new_{suffix}_days_csv") or "",
                "speed_down_kbps": _as_int(form.get(f"sr_new_{suffix}_speed_down_kbps"), 0),
                "speed_up_kbps": _as_int(form.get(f"sr_new_{suffix}_speed_up_kbps"), 0),
                "cir_down_kbps": _as_int(form.get(f"sr_new_{suffix}_cir_down_kbps"), 0),
                "cir_up_kbps": _as_int(form.get(f"sr_new_{suffix}_cir_up_kbps"), 0),
                "restore_mode": form.get(f"sr_new_{suffix}_restore_mode") or "profile_default",
                "priority": _as_int(form.get(f"sr_new_{suffix}_priority"), 5),
                "enabled": (form.get(f"sr_new_{suffix}_enabled") or "1").lower() in {"1", "true", "on", "yes"},
                "notes": form.get(f"sr_new_{suffix}_notes") or "",
                "metadata": base_meta,
            },
        )
        created += 1
    return created


def handle_embedded_speed_rule(
    *,
    tenant_id: int,
    actor: str,
    form,
    target_type: str,
    plan_id: int | None = None,
    subscriber_username: str = "",
    card_batch_id: int | None = None,
    subscriber_group_id: int | None = None,
) -> bool:
    """Handle speed-rule actions from an edit page."""
    action = (form.get("_speed_rule_action") or "").strip()
    known_prefixes = ("update:", "toggle:", "delete:")
    if action not in {"manual", "copy", "enable_all", "disable_all"} and not action.startswith(known_prefixes):
        return False

    base = {
        "target_type": target_type,
        "plan_id": plan_id,
        "subscriber_username": subscriber_username,
        "card_batch_id": card_batch_id,
        "subscriber_group_id": subscriber_group_id,
        "priority": form.get("sr_priority") or 100,
        "enabled": True,
        "notes": form.get("sr_notes") or "",
    }
    svc = get_operations_service()

    def _schedule_id() -> int:
        try:
            return int(action.split(":", 1)[1])
        except (IndexError, TypeError, ValueError):
            raise RadiusValidationError("قاعدة السرعة غير واضحة")

    def _assert_target(rule: dict) -> None:
        if not rule or rule.get("target_type") != target_type:
            raise RadiusValidationError("قاعدة السرعة لا تتبع هذا القسم")
        if target_type == "plan" and int(rule.get("plan_id") or 0) != int(plan_id or 0):
            raise RadiusValidationError("قاعدة السرعة لا تتبع هذا العرض")
        if target_type == "subscriber" and (rule.get("subscriber_username") or "") != subscriber_username:
            raise RadiusValidationError("قاعدة السرعة لا تتبع هذا المشترك")
        if target_type == "card_batch" and int(rule.get("card_batch_id") or 0) != int(card_batch_id or 0):
            raise RadiusValidationError("قاعدة السرعة لا تتبع هذه الحزمة")
        if target_type == "subscriber_group" and int(rule.get("subscriber_group_id") or 0) != int(subscriber_group_id or 0):
            raise RadiusValidationError("قاعدة السرعة لا تتبع هذه المجموعة")

    if action in {"enable_all", "disable_all"}:
        svc.set_bandwidth_schedules_enabled_for_target(
            tenant_id=tenant_id,
            actor=actor,
            target_type=target_type,
            enabled=action == "enable_all",
            plan_id=plan_id,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            subscriber_group_id=subscriber_group_id,
        )
        return True

    if action.startswith("toggle:"):
        schedule_id = _schedule_id()
        rule = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=schedule_id)
        _assert_target(rule or {})
        svc.set_bandwidth_schedule_enabled(
            tenant_id=tenant_id,
            actor=actor,
            schedule_id=schedule_id,
            enabled=not bool(rule.get("enabled")),
        )
        return True

    if action.startswith("delete:"):
        schedule_id = _schedule_id()
        rule = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=schedule_id)
        _assert_target(rule or {})
        svc.delete_bandwidth_schedule(tenant_id=tenant_id, actor=actor, schedule_id=schedule_id)
        return True

    if action.startswith("update:"):
        schedule_id = _schedule_id()
        rule = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=schedule_id)
        _assert_target(rule or {})
        suffix = str(schedule_id)
        svc.update_bandwidth_schedule(
            tenant_id=tenant_id,
            actor=actor,
            schedule_id=schedule_id,
            data={
                "name": form.get(f"sr_edit_name_{suffix}"),
                "starts_at_time": form.get(f"sr_edit_starts_at_time_{suffix}"),
                "ends_at_time": form.get(f"sr_edit_ends_at_time_{suffix}"),
                "days_csv": _days_from_form(form, f"sr_edit_days_{suffix}"),
                "speed_down_kbps": form.get(f"sr_edit_speed_down_kbps_{suffix}") or 0,
                "speed_up_kbps": form.get(f"sr_edit_speed_up_kbps_{suffix}") or 0,
                "cir_down_kbps": form.get(f"sr_edit_cir_down_kbps_{suffix}") or 0,
                "cir_up_kbps": form.get(f"sr_edit_cir_up_kbps_{suffix}") or 0,
                "restore_mode": form.get(f"sr_edit_restore_mode_{suffix}") or "profile_default",
                "priority": form.get(f"sr_edit_priority_{suffix}") or 100,
                "enabled": form.get(f"sr_edit_enabled_{suffix}") in {"1", "true", "on", "yes"},
                "notes": form.get(f"sr_edit_notes_{suffix}") or "",
            },
        )
        return True

    if action == "copy":
        source_raw = form.get("sr_source_schedule_id")
        try:
            source_id = int(source_raw or 0)
        except (TypeError, ValueError):
            raise RadiusValidationError("اختر جدول سرعة محفوظًا أولًا")
        source = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=source_id)
        if not source:
            raise RadiusValidationError("جدول السرعة المحفوظ غير موجود")
        payload = {
            **base,
            "name": form.get("sr_copy_name") or f"نسخة من {source.get('name') or 'جدول محفوظ'}",
            "priority": form.get("sr_priority") or source.get("priority") or 100,
            "starts_at_time": source.get("starts_at_time"),
            "ends_at_time": source.get("ends_at_time"),
            "speed_down_kbps": source.get("speed_down_kbps") or 0,
            "speed_up_kbps": source.get("speed_up_kbps") or 0,
            "cir_down_kbps": source.get("cir_down_kbps") or 0,
            "cir_up_kbps": source.get("cir_up_kbps") or 0,
            "restore_mode": source.get("restore_mode") or "profile_default",
            "metadata": {
                "copied_from_schedule_id": source_id,
                "copied_from_target_type": source.get("target_type"),
                "embedded_target": target_type,
            },
        }
    else:
        # A manual rule needs an actual time window. When both clock fields are
        # blank there is nothing to schedule, so we skip silently instead of
        # tripping the HH:MM validator — the time format is only checked once an
        # explicit clock time was actually entered. (Mirrors the empty-time skip
        # in create_staged_speed_rules.)
        _starts = (form.get("sr_starts_at_time") or "").strip()
        _ends = (form.get("sr_ends_at_time") or "").strip()
        if not _starts and not _ends:
            return False
        payload = {
            **base,
            "name": form.get("sr_name") or "قاعدة سرعة",
            "starts_at_time": form.get("sr_starts_at_time"),
            "ends_at_time": form.get("sr_ends_at_time"),
            "days_csv": _days_from_form(form, "sr_days"),
            "speed_down_kbps": form.get("sr_speed_down_kbps") or 0,
            "speed_up_kbps": form.get("sr_speed_up_kbps") or 0,
            "cir_down_kbps": form.get("sr_cir_down_kbps") or 0,
            "cir_up_kbps": form.get("sr_cir_up_kbps") or 0,
            "restore_mode": form.get("sr_restore_mode") or "profile_default",
            "metadata": {"embedded_target": target_type},
        }

    svc.create_bandwidth_schedule(tenant_id=tenant_id, actor=actor, data=payload)
    return True
