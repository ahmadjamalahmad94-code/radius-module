"""Operational tools API for Flutter parity.

These endpoints mirror the existing web admin tools while keeping dangerous
maintenance actions behind a preview + confirmation token.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, current_app, g, request

from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.connection import db, transaction
from ...radius.db.repos import audit_repo, plans_repo
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/tools/set-speeds",
        "tools_set_speeds",
        require_api_token(set_speeds),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tools/general-adjustments",
        "tools_general_adjustments",
        require_api_token(general_adjustments),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tools/test-auth",
        "tools_test_auth",
        require_api_token(test_auth),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tools/radius-log",
        "tools_radius_log",
        require_api_token(radius_log),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/tools/maintenance/preview",
        "tools_maintenance_preview",
        require_api_token(maintenance_preview),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tools/maintenance/run",
        "tools_maintenance_run",
        require_api_token(maintenance_run),
        methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    token_id = getattr(g, "api_token_id", None)
    admin_id = getattr(g, "admin_id", None)
    if admin_id:
        return f"api-admin:{admin_id}"
    if token_id:
        return f"api-token:{token_id}"
    return "api-token:env"


def _payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _limit(default: int = 80, maximum: int = 500) -> int:
    try:
        return min(max(int(request.args.get("limit") or default), 1), maximum)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def set_speeds():
    data = _payload()
    plan_ids = data.get("plan_ids") or []
    if not isinstance(plan_ids, list):
        return fail("validation_error", "قائمة الباقات يجب أن تكون مصفوفة.", status=422)
    try:
        ids = [int(pid) for pid in plan_ids]
    except (TypeError, ValueError):
        return fail("validation_error", "معرّفات الباقات يجب أن تكون أرقامًا صحيحة.", status=422)
    if not ids:
        return fail("validation_error", "اختر باقة واحدة على الأقل.", status=422)

    from ...radius.core.errors import RadiusValidationError
    from ...radius.services.bulk_speeds import new_speed, parse_bulk_speed_params
    try:
        mult_down, mult_up, set_down, set_up = parse_bulk_speed_params(
            data.get("mult_down"), data.get("mult_up"),
            data.get("set_down"), data.get("set_up"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)

    # `preview` is the name the app sends for «معاينة»; before
    # this fix only `dry_run` was honoured, so `preview: true` APPLIED the change.
    dry_run = _bool_value(data.get("dry_run", False)) or _bool_value(data.get("preview", False))
    changes: list[dict[str, Any]] = []
    plans_to_write = []
    tenant_id = _tid()
    for plan_id in ids:
        plan = plans_repo.get_plan(tenant_id, plan_id)
        if not plan:
            continue
        try:
            new_down = new_speed(plan.speed_down_kbps, mult_down, set_down,
                                 plan_name=plan.name, direction="التنزيل")
            new_up = new_speed(plan.speed_up_kbps, mult_up, set_up,
                               plan_name=plan.name, direction="الرفع")
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        plans_to_write.append((plan, new_down, new_up))
        changes.append({
            "plan_id": plan.id,
            "name": plan.name,
            "before": {
                "speed_down_kbps": plan.speed_down_kbps,
                "speed_up_kbps": plan.speed_up_kbps,
            },
            "after": {
                "speed_down_kbps": new_down,
                "speed_up_kbps": new_up,
            },
        })

    # Validate EVERY plan first, then write — a refused plan never leaves the
    # batch half-applied.
    if not dry_run:
        for plan, new_down, new_up in plans_to_write:
            plans_repo.upsert_plan(
                replace(plan, speed_down_kbps=new_down, speed_up_kbps=new_up)
            )

    if not dry_run and changes:
        audit_repo.record(
            tenant_id=tenant_id,
            actor=_actor(),
            action="bulk_set_speeds",
            target_type="plan",
            target_id=",".join(str(item["plan_id"]) for item in changes),
            payload={
                "mult_down": mult_down,
                "mult_up": mult_up,
                "set_down": set_down,
                "set_up": set_up,
                "source": "api",
            },
        )

    return ok({
        "dry_run": dry_run,
        "changed": 0 if dry_run else len(changes),
        "matched": len(changes),
        "changes": changes,
    })


def general_adjustments():
    """POST /tools/general-adjustments — the same shared service as the web
    tool page. ``dry_run`` is a real preview: every username is resolved and
    gets the outcome the real run would have (not found / no change / new
    expiry); a bad request (unknown action, missing minutes / password) is a
    422 before anything runs."""
    from ...radius.core.errors import RadiusValidationError
    from ...radius.services import general_adjustments as ga

    data = _payload()
    usernames = ga.parse_usernames(data.get("usernames"))
    try:
        params = ga.validate_request(
            data.get("action"), usernames,
            minutes=data.get("minutes"), new_password=data.get("new_password"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    if _bool_value(data.get("dry_run", False)):
        return ok(ga.plan(_tid(), usernames, params))
    return ok(ga.run(usernames, params, actor=_actor()))


def test_auth():
    data = _payload()
    username = str(data.get("username") or "").strip()
    if not username:
        return fail("validation_error", "اسم المستخدم مطلوب.", status=422)
    from ...radius.services.policy_engine import AuthRequest, authorize

    req = AuthRequest(
        username=username,
        password=str(data.get("password") or ""),
        chap_password=str(data.get("chap_password") or ""),
        chap_challenge=str(data.get("chap_challenge") or ""),
        tenant_id=_tid(),
        calling_station_id=str(data.get("calling_station_id") or ""),
        called_station_id=str(data.get("called_station_id") or ""),
        nas_ip=str(data.get("nas_ip") or ""),
        nas_port_type=str(data.get("nas_port_type") or "Ethernet"),
    )
    try:
        decision = authorize(req)
    except Exception as exc:  # noqa: BLE001
        return ok({
            "decision": {
                "ok": False,
                "reason": "engine_error",
                "message": str(exc),
                "reply_attrs": {},
            }
        })
    return ok({
        "decision": {
            "ok": decision.ok,
            "reason": decision.reason,
            "message": decision.message,
            "reply_attrs": dict(decision.reply_attrs or {}),
        }
    })


def radius_log():
    rows = db().execute(
        """
        SELECT id, authdate, username, reply, nas, class
        FROM radpostauth
        WHERE tenant_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (_tid(), _limit()),
    ).fetchall()
    items = [{
        "id": row["id"],
        "authdate": row["authdate"],
        "username": row["username"],
        "reply": row["reply"],
        "nas": row["nas"],
        "reason": row["class"] or "",
        "ok": "Accept" in (row["reply"] or ""),
    } for row in rows]
    return ok({"items": items, "count": len(items)})


def _maintenance_cutoff(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"


def _table_count(sql: str, params: tuple[Any, ...]) -> int:
    try:
        row = db().execute(sql, params).fetchone()
        return int(row["c"] if row else 0)
    except Exception:  # noqa: BLE001
        return 0


def _maintenance_plan(action: str, days: int) -> dict[str, Any] | None:
    tenant_id = _tid()
    cutoff = _maintenance_cutoff(days)
    if action == "purge_radacct":
        count = _table_count(
            """
            SELECT COUNT(*) AS c FROM radacct
            WHERE tenant_id = ? AND acctstoptime IS NOT NULL AND acctstoptime < ?
            """,
            (tenant_id, cutoff),
        )
        return {
            "action": action,
            "days": days,
            "estimated_rows": count,
            "table": "radacct",
            "destructive": True,
        }
    if action == "purge_sync_done":
        count = _table_count(
            """
            SELECT COUNT(*) AS c FROM sync_queue
            WHERE tenant_id = ? AND status='done' AND completed_at < ?
            """,
            (tenant_id, cutoff),
        )
        return {
            "action": action,
            "days": days,
            "estimated_rows": count,
            "table": "sync_queue",
            "destructive": True,
        }
    if action == "purge_audit":
        count = _table_count(
            "SELECT COUNT(*) AS c FROM audit_log WHERE tenant_id = ? AND created_at < ?",
            (tenant_id, cutoff),
        )
        return {
            "action": action,
            "days": days,
            "estimated_rows": count,
            "table": "audit_log",
            "destructive": True,
        }
    if action == "purge_failed_webhooks":
        count = _table_count(
            """
            SELECT COUNT(*) AS c FROM webhook_deliveries
            WHERE tenant_id = ? AND status='failed'
            """,
            (tenant_id,),
        )
        return {
            "action": action,
            "days": days,
            "estimated_rows": count,
            "table": "webhook_deliveries",
            "destructive": True,
        }
    if action == "vacuum":
        return {
            "action": action,
            "days": days,
            "estimated_rows": 0,
            "table": "database",
            "destructive": False,
        }
    return None


def _confirm_token(plan: dict[str, Any]) -> str:
    secret = str(current_app.config.get("SECRET_KEY") or "dev-secret")
    raw = (
        f"{_tid()}|{plan['action']}|{plan['days']}|"
        f"{plan['estimated_rows']}|{plan['table']}"
    )
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def maintenance_preview():
    data = _payload()
    action = str(data.get("action") or "").strip()
    try:
        days = max(1, min(int(data.get("days") or 90), 3650))
    except (TypeError, ValueError):
        return fail("validation_error", "عدد الأيام يجب أن يكون رقمًا صحيحًا.", status=422)
    plan = _maintenance_plan(action, days)
    if not plan:
        return fail("validation_error", "إجراء الصيانة غير معروف.", status=422)
    plan["confirm_phrase"] = "RUN_MAINTENANCE"
    plan["confirm_token"] = _confirm_token(plan)
    return ok(plan)


def maintenance_run():
    data = _payload()
    action = str(data.get("action") or "").strip()
    try:
        days = max(1, min(int(data.get("days") or 90), 3650))
    except (TypeError, ValueError):
        return fail("validation_error", "عدد الأيام يجب أن يكون رقمًا صحيحًا.", status=422)
    plan = _maintenance_plan(action, days)
    if not plan:
        return fail("validation_error", "إجراء الصيانة غير معروف.", status=422)
    if data.get("confirm_phrase") != "RUN_MAINTENANCE":
        return fail("confirmation_required", "عبارة تأكيد الصيانة مطلوبة.", status=409)
    if not hmac.compare_digest(str(data.get("confirm_token") or ""), _confirm_token(plan)):
        return fail("confirmation_required", "رمز معاينة الصيانة غير صالح.", status=409)

    tenant_id = _tid()
    cutoff = _maintenance_cutoff(days)
    deleted = 0
    if action == "vacuum":
        db().execute("VACUUM")
    else:
        with transaction() as conn:
            if action == "purge_radacct":
                cur = conn.execute(
                    """
                    DELETE FROM radacct
                    WHERE tenant_id = ? AND acctstoptime IS NOT NULL AND acctstoptime < ?
                    """,
                    (tenant_id, cutoff),
                )
            elif action == "purge_sync_done":
                cur = conn.execute(
                    """
                    DELETE FROM sync_queue
                    WHERE tenant_id = ? AND status='done' AND completed_at < ?
                    """,
                    (tenant_id, cutoff),
                )
            elif action == "purge_audit":
                cur = conn.execute(
                    "DELETE FROM audit_log WHERE tenant_id = ? AND created_at < ?",
                    (tenant_id, cutoff),
                )
            elif action == "purge_failed_webhooks":
                cur = conn.execute(
                    """
                    DELETE FROM webhook_deliveries
                    WHERE tenant_id = ? AND status='failed'
                    """,
                    (tenant_id,),
                )
            else:  # pragma: no cover - guarded above
                cur = None
            deleted = int(cur.rowcount if cur else 0)

    audit_repo.record(
        tenant_id=tenant_id,
        actor=_actor(),
        action="maintenance.run",
        target_type=plan["table"],
        target_id=action,
        payload={
            "days": days,
            "estimated_rows": plan["estimated_rows"],
            "affected_rows": deleted,
            "source": "api",
        },
    )
    return ok({
        "action": action,
        "days": days,
        "affected_rows": deleted,
        "dry_run": False,
    })
