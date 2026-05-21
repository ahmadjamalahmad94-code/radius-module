"""Lifecycle retention service.

This module implements automatic archiving only. It does not hard-delete data
and it never calls RADIUS, MikroTik, CoA, or disconnect paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.radius.db.connection import transaction
from app.radius.db.helpers import json_dump, now_iso, parse_dt
from app.radius.db.repos import lifecycle_repo


ENTITY_CARD = "card"
ENTITY_SUBSCRIBER = "subscriber"
ENTITY_CARD_BATCH = "card_batch"
ENTITY_EXTERNAL_FILE = "external_file"
TRIGGER_EXPIRED = "expired_at"
ACTION_ARCHIVE = "archive"

ALLOWED_ENTITIES = {ENTITY_CARD, ENTITY_SUBSCRIBER, ENTITY_CARD_BATCH, ENTITY_EXTERNAL_FILE}
ALLOWED_TRIGGERS = {TRIGGER_EXPIRED, "disabled_since", "inactive_since"}
ALLOWED_UNITS = {"minutes", "hours", "days", "months"}


@dataclass(frozen=True)
class LifecycleValidationError(ValueError):
    message: str
    code: str = "invalid_lifecycle_policy"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _delta(value: int, unit: str) -> timedelta:
    if value < 0:
        raise LifecycleValidationError("مدة السياسة لا يمكن أن تكون سالبة.")
    if unit == "minutes":
        return timedelta(minutes=value)
    if unit == "hours":
        return timedelta(hours=value)
    if unit == "days":
        return timedelta(days=value)
    if unit == "months":
        return timedelta(days=value * 30)
    raise LifecycleValidationError("وحدة المدة غير مدعومة.")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def validate_policy_payload(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {}
    defaults = {
        "entity_type": ENTITY_CARD,
        "trigger_type": TRIGGER_EXPIRED,
        "delay_value": 0,
        "delay_unit": "days",
        "action": ACTION_ARCHIVE,
        "retention_value": 90,
        "retention_unit": "days",
        "enabled": True,
    }
    source = payload if partial else {**defaults, **payload}
    for key in defaults:
        if key not in source and partial:
            continue
        value = source.get(key, defaults[key])
        if key in {"delay_value", "retention_value"}:
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise LifecycleValidationError("قيمة المدة يجب أن تكون رقمًا صحيحًا.") from exc
            if value < 0:
                raise LifecycleValidationError("قيمة المدة لا يمكن أن تكون سالبة.")
        elif key == "enabled":
            value = 1 if _normalise_bool(value) else 0
        elif isinstance(value, str):
            value = value.strip()
        data[key] = value
    entity = data.get("entity_type")
    trigger = data.get("trigger_type")
    delay_unit = data.get("delay_unit")
    retention_unit = data.get("retention_unit")
    action = data.get("action")
    if entity and entity not in ALLOWED_ENTITIES:
        raise LifecycleValidationError("نوع العنصر غير مدعوم.")
    if trigger and trigger not in ALLOWED_TRIGGERS:
        raise LifecycleValidationError("شرط الأرشفة غير مدعوم.")
    if delay_unit and delay_unit not in ALLOWED_UNITS:
        raise LifecycleValidationError("وحدة التأخير غير مدعومة.")
    if retention_unit and retention_unit not in ALLOWED_UNITS:
        raise LifecycleValidationError("وحدة الاحتفاظ غير مدعومة.")
    if action and action != ACTION_ARCHIVE:
        raise LifecycleValidationError("النسخة الحالية تدعم الأرشفة فقط.")
    return data


def policy_supported(policy: dict[str, Any]) -> bool:
    return (
        policy.get("enabled") in {1, True}
        and policy.get("action") == ACTION_ARCHIVE
        and policy.get("trigger_type") == TRIGGER_EXPIRED
        and policy.get("entity_type") in {ENTITY_CARD, ENTITY_SUBSCRIBER}
    )


def create_policy(tenant_id: int, payload: dict[str, Any], *, actor: str = "") -> dict:
    data = validate_policy_payload(payload)
    return lifecycle_repo.create_policy(tenant_id, actor=actor, **data)


def update_policy(tenant_id: int, policy_id: int, payload: dict[str, Any], *, actor: str = "") -> dict | None:
    data = validate_policy_payload(payload, partial=True)
    return lifecycle_repo.update_policy(tenant_id, policy_id, data, actor=actor)


def list_policies(tenant_id: int, *, entity_type: str = "") -> list[dict]:
    return lifecycle_repo.list_policies(tenant_id, entity_type=entity_type)


def disable_policy(tenant_id: int, policy_id: int, *, actor: str = "") -> dict | None:
    return lifecycle_repo.disable_policy(tenant_id, policy_id, actor=actor)


def _cutoff_for(policy: dict[str, Any], now: datetime | None = None) -> str:
    now = now or _utc_now()
    return _iso(now - _delta(int(policy.get("delay_value") or 0), str(policy.get("delay_unit") or "days")))


def _retention_for(policy: dict[str, Any], now: datetime | None = None) -> str:
    now = now or _utc_now()
    return _iso(now + _delta(int(policy.get("retention_value") or 0), str(policy.get("retention_unit") or "days")))


def _summarise_policy(tenant_id: int, policy: dict[str, Any], *, limit: int = 500) -> dict:
    cutoff = _cutoff_for(policy)
    supported = policy_supported(policy)
    cards: list[dict] = []
    subscribers: list[dict] = []
    batch_impacts: list[dict] = []
    if supported and policy.get("entity_type") == ENTITY_CARD:
        cards = lifecycle_repo.due_cards(tenant_id, cutoff, limit=limit)
        batch_impacts = lifecycle_repo.pending_cards_by_batch(tenant_id, cutoff)
    elif supported and policy.get("entity_type") == ENTITY_SUBSCRIBER:
        subscribers = lifecycle_repo.due_subscribers(tenant_id, cutoff, limit=limit)
    return {
        "policy": policy,
        "supported": supported,
        "cutoff_at": cutoff,
        "cards_count": len(cards),
        "subscribers_count": len(subscribers),
        "batch_impacts": batch_impacts,
        "sample_items": [
            {
                "entity_type": policy.get("entity_type"),
                "id": row.get("id"),
                "label": row.get("username") or row.get("name") or row.get("batch_name") or str(row.get("id")),
                "expires_at": row.get("expire_at"),
            }
            for row in (cards[:10] if cards else subscribers[:10])
        ],
    }


def preview(tenant_id: int, *, limit: int = 500) -> dict:
    policies = lifecycle_repo.list_policies(tenant_id, enabled=True)
    summaries = [_summarise_policy(tenant_id, policy, limit=limit) for policy in policies]
    cards_count = sum(item["cards_count"] for item in summaries)
    subscribers_count = sum(item["subscribers_count"] for item in summaries)
    batches = {
        impact.get("batch_id")
        for item in summaries
        for impact in item.get("batch_impacts", [])
        if impact.get("batch_id") is not None
    }
    return {
        "dry_run": True,
        "policies": summaries,
        "totals": {
            "cards": cards_count,
            "subscribers": subscribers_count,
            "batches_impacted": len(batches),
            "pending_archive": cards_count + subscribers_count,
        },
    }


def _snapshot(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if key in row}


def run(tenant_id: int, *, actor: str = "system:lifecycle", limit: int = 500) -> dict:
    policies = lifecycle_repo.list_policies(tenant_id, enabled=True)
    changed = 0
    skipped = 0
    failed = 0
    items: list[dict] = []
    for policy in policies:
        if not policy_supported(policy):
            skipped += 1
            items.append({
                "policy_id": policy.get("id"),
                "entity_type": policy.get("entity_type"),
                "status": "skipped",
                "reason": "policy_not_supported_in_current_worker",
            })
            continue
        cutoff = _cutoff_for(policy)
        retention = _retention_for(policy)
        reason = f"أرشفة تلقائية حسب السياسة #{policy.get('id')}"
        if policy.get("entity_type") == ENTITY_CARD:
            candidates = lifecycle_repo.due_cards(tenant_id, cutoff, limit=limit)
            entity_type = ENTITY_CARD
        else:
            candidates = lifecycle_repo.due_subscribers(tenant_id, cutoff, limit=limit)
            entity_type = ENTITY_SUBSCRIBER
        for row in candidates:
            entity_id = int(row["id"])
            try:
                with transaction() as conn:
                    if entity_type == ENTITY_CARD:
                        ok = lifecycle_repo.archive_card(
                            conn,
                            tenant_id=tenant_id,
                            card_id=entity_id,
                            policy_id=int(policy["id"]),
                            actor=actor,
                            reason=reason,
                            retention_expires_at=retention,
                        )
                        snap = _snapshot(row, ("id", "username", "batch_id", "expire_at", "batch_original_count"))
                    else:
                        ok = lifecycle_repo.archive_subscriber(
                            conn,
                            tenant_id=tenant_id,
                            subscriber_id=entity_id,
                            policy_id=int(policy["id"]),
                            actor=actor,
                            reason=reason,
                            retention_expires_at=retention,
                        )
                        snap = _snapshot(row, ("id", "username", "plan_id", "expire_at", "status"))
                    lifecycle_repo.record_event(
                        conn,
                        tenant_id=tenant_id,
                        policy_id=int(policy["id"]),
                        entity_type=entity_type,
                        entity_id=entity_id,
                        action=ACTION_ARCHIVE,
                        scheduled_for=cutoff,
                        executed_at=now_iso(),
                        status="done" if ok else "skipped",
                        reason=reason if ok else "already_archived",
                        snapshot=snap,
                    )
                    if ok:
                        changed += 1
                        conn.execute(
                            """
                            INSERT INTO audit_log(
                                tenant_id, actor, action, target_type, target_id,
                                payload_json, ip_address, user_agent, created_at
                            )
                            VALUES(?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                tenant_id,
                                actor,
                                "lifecycle.archive",
                                entity_type,
                                str(entity_id),
                                json_dump({"policy_id": policy["id"], "retention_expires_at": retention}),
                                "",
                                "",
                                now_iso(),
                            ),
                        )
                    else:
                        skipped += 1
                items.append({
                    "policy_id": policy.get("id"),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "status": "done" if ok else "skipped",
                })
            except Exception as exc:  # pragma: no cover - defensive worker path
                failed += 1
                with transaction() as conn:
                    lifecycle_repo.record_event(
                        conn,
                        tenant_id=tenant_id,
                        policy_id=int(policy["id"]),
                        entity_type=entity_type,
                        entity_id=entity_id,
                        action=ACTION_ARCHIVE,
                        scheduled_for=cutoff,
                        executed_at=now_iso(),
                        status="failed",
                        reason=reason,
                        snapshot=_snapshot(row, ("id", "username", "expire_at")),
                        error=str(exc)[:500],
                    )
                items.append({
                    "policy_id": policy.get("id"),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "status": "failed",
                })
    return {
        "changed": changed,
        "skipped": skipped,
        "failed": failed,
        "items": items,
    }


def retention_status(row: dict[str, Any]) -> dict[str, Any]:
    expires = row.get("retention_expires_at")
    if not expires:
        return {"restore_allowed": True, "retention_expired": False}
    parsed = parse_dt(str(expires))
    expired = bool(parsed and parsed < datetime.utcnow())
    return {"restore_allowed": not expired, "retention_expired": expired}
