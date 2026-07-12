"""
Accounts endpoints — REAL implementation.

Every write goes through UsersService (same path the Flask web form uses), so
audit + RADIUS sync + MikroTik push happen identically regardless of caller.

Field intake is whitelisted. The whitelist covers the full RM-H1 Subscriber
DTO except read-only counters and ids; `metadata` is accepted as either a
JSON object or a string and stored as a string.

Serialization flattens `metadata` to a parsed dict on the way out, so Flutter
can read both flat fields and meta groups in one round trip.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.core.types import Subscriber
from ...radius.services.license_admin_capacity import (
    CapacityEnforcementService,
    capacity_error_response,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


# Whitelist of patchable / creatable subscriber fields (mirrors the DTO).
# id, tenant_id, password, username, used_*, last_*, created_*, updated_*
# are NOT in this list — they are handled explicitly.
_EDITABLE = (
    # identity & links
    "user_type", "service_type", "plan_id",
    # pppoe specifics
    "pppoe_username", "pppoe_password", "pppoe_ip",
    # personal
    "full_name", "father_name", "mobile", "email", "address", "city",
    "district", "state", "zip", "coordinates", "national_id", "account_type",
    "photo_url",
    # balance / status / management
    "balance", "auto_renewal", "status", "manager_id", "group", "pool",
    # network
    "mac_lock", "static_ip", "vlan_id", "override_concurrent",
    # RM-H1 bandwidth overrides
    "bandwidth_control_enabled", "download_speed_kbps", "upload_speed_kbps",
    "custom_speed", "temporary_speed",
    # RM-H1 connection metadata
    "caller_id", "primary_dns_ppp", "secondary_dns_ppp", "device_connection_file",
    # RM-H1 personal extras
    "nationality", "country", "payment_method", "payment_reference",
    # RM-H1 time/quota overrides
    "total_connection_time_min", "daily_connection_time_min",
    "download_quota_mb", "upload_quota_mb", "combined_quota_mb",
    "connection_time_limit_enabled", "quota_limit_enabled",
    "equal_share_download", "equal_share_upload",
    # RM-H1 device control + working days
    "working_days", "device_count", "allowed_macs",
    # misc
    "beneficiary_ref", "remark",
)

_DATETIME_FIELDS = ("expire_at", "first_login_at", "last_login_at", "last_seen_at")


def _parse_dt(v):
    if v in (None, "", 0):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", ""))
    except (TypeError, ValueError):
        return None


def _normalize_metadata(raw) -> str:
    """Accept either a dict/list or a JSON string, always store a JSON string.

    Invalid input raises RadiusValidationError so the caller can surface a
    422 and the request fails atomically — important because the alternative
    (silently normalising to "{}") would wipe existing metadata on PATCH,
    which is destructive.

    Whitespace/empty string is treated as "no change requested": returns "{}"
    only for the create path; on patch the caller should omit the key.
    """
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        if not raw.strip():
            return "{}"
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError(f"بيانات metadata ليست JSON صالحًا: {e}")
        if not isinstance(parsed, (dict, list)):
            raise RadiusValidationError(
                "بيانات metadata يجب أن تتحول إلى كائن أو قائمة JSON.")
        return raw
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError(f"تعذّر تحويل metadata إلى JSON: {e}")
    raise RadiusValidationError(
        f"metadata يجب أن تكون قاموسًا أو قائمة أو نص JSON، والقيمة الحالية من نوع {type(raw).__name__}.")


def _coerce(field_name: str, value):
    """Light type coercion based on the destination field name. Errors raise
    RadiusValidationError so the caller can return a 422 cleanly."""
    if field_name in _DATETIME_FIELDS:
        return _parse_dt(value)
    if field_name == "metadata":
        return _normalize_metadata(value)
    if field_name == "plan_id" or field_name == "manager_id":
        if value in (None, "", 0):
            return None if field_name == "plan_id" else 0
        try:
            return int(value)
        except (TypeError, ValueError):
            raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمًا صحيحًا.")
    # leave strings/booleans/numbers to the dataclass — replace() won't coerce
    # but it does accept whatever is on the right type. We do a few common
    # coercions for numerics that often arrive as strings.
    if field_name == "balance":
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمية.")
    if field_name in {
        "download_speed_kbps", "upload_speed_kbps",
        "vlan_id", "override_concurrent",
        "total_connection_time_min", "daily_connection_time_min",
        "download_quota_mb", "upload_quota_mb", "combined_quota_mb",
        "device_count",
    }:
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمية.")
    if field_name in {
        "bandwidth_control_enabled", "custom_speed", "temporary_speed",
        "auto_renewal",
        "connection_time_limit_enabled", "quota_limit_enabled",
        "equal_share_download", "equal_share_upload",
    }:
        return bool(value)
    return value


def _apply_body(sub: Subscriber, body: dict) -> Subscriber:
    """Apply whitelisted fields from body to sub via dataclasses.replace.
    expire_at + other datetimes accepted as ISO strings."""
    changes: dict = {}
    for k in _EDITABLE:
        if k in body:
            changes[k] = _coerce(k, body[k])
    if "expire_at" in body:
        changes["expire_at"] = _parse_dt(body["expire_at"])
    if "metadata" in body:
        changes["metadata"] = _normalize_metadata(body["metadata"])
    if "connection_schedule" in body:
        # Unified access schedule (days + time windows). Normalise via
        # access_schedule so storage is canonical and keep the legacy
        # ``working_days`` cache in sync. Invalid input is ignored (never
        # destructive on PATCH).
        from ...radius.core import access_schedule as _asch
        try:
            _sched = _asch.parse(body["connection_schedule"])
            changes["connection_schedule"] = _asch.serialize(_sched)
            changes.setdefault("working_days", _asch.derive_working_days(_sched))
        except Exception:  # noqa: BLE001 — invalid schedule → leave unchanged
            pass
    return replace(sub, **changes)


def _serialize(sub: Subscriber) -> dict:
    """Serialise + parse metadata to a dict for convenience. Never leaks the
    raw password — clients must use /reset_password to change it."""
    d = asdict(sub)
    for k in ("first_login_at", "expire_at", "last_login_at", "last_seen_at",
              "created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    d.pop("password", None)
    # Convert metadata string → parsed dict so Flutter can use it directly.
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            d["metadata"] = json.loads(meta or "{}")
        except (TypeError, ValueError):
            d["metadata"] = {}
    # Convert allowed_days tuple → list for JSON friendliness (Plan only — kept
    # here so the helper covers both DTOs if reused).
    return d


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/accounts", "accounts_list",
                    require_api_token(accounts_list), methods=["GET"])
    bp.add_url_rule("/accounts", "accounts_create",
                    require_api_token(accounts_create), methods=["POST"])
    bp.add_url_rule("/accounts/<username>", "accounts_get",
                    require_api_token(accounts_get), methods=["GET"])
    bp.add_url_rule("/accounts/<username>", "accounts_patch",
                    require_api_token(accounts_patch), methods=["PATCH"])
    bp.add_url_rule("/accounts/<username>", "accounts_delete",
                    require_api_token(accounts_delete), methods=["DELETE"])
    bp.add_url_rule("/accounts/<username>/reset_password", "accounts_reset_pw",
                    require_api_token(accounts_reset_pw), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/extend_time", "accounts_extend",
                    require_api_token(accounts_extend), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/disable", "accounts_disable",
                    require_api_token(accounts_disable), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/enable", "accounts_enable",
                    require_api_token(accounts_enable), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/usage", "accounts_usage",
                    require_api_token(accounts_usage), methods=["GET"])
    bp.add_url_rule("/accounts/<username>/360", "accounts_360",
                    require_api_token(accounts_360), methods=["GET"])


def _svc():
    from ...radius.services.users import get_users_service
    return get_users_service()


# ─────────────── views ───────────────

def accounts_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم limit و offset يجب أن تكون أرقامًا صحيحة.", status=422)
    status = request.args.get("status")
    search = request.args.get("search") or ""
    plan_id = request.args.get("plan_id")
    plan_id = int(plan_id) if (plan_id and plan_id.isdigit()) else None
    items = _svc().list(status=status, plan_id=plan_id, search=search,
                        limit=limit, offset=offset)
    return ok({"items": [_serialize(s) for s in items], "count": len(items)})


def accounts_create():
    body = request.get_json(silent=True) or {}
    if not body.get("username") or not body.get("password"):
        return fail("validation_error", "username + password مطلوبان", status=422)
    capacity = CapacityEnforcementService().check_create(
        tenant_id=_tid(),
        feature_key="subscribers",
        limit_path="subscribers.max_total",
        usage_metric="subscribers_total",
    )
    if not capacity.allowed:
        return capacity_error_response(capacity)

    # Seed a default Subscriber, then apply whitelisted fields.
    seed = Subscriber(
        id=None, tenant_id=_tid(),
        username=str(body["username"]).strip(),
        password=str(body["password"]),
    )
    try:
        sub = _apply_body(seed, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)

    try:
        saved = _svc().create(actor=_actor(), sub=sub)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("conflict", e.message, status=409)
    # Fire-and-forget WhatsApp activation/OTP notice (gated + fail-safe). The
    # create() path produces no OTP/activation code, so we send no code value —
    # the operator's opt-in just yields a templated activation notice. This call
    # can NEVER break account creation: notify_whatsapp swallows every error.
    _notify_account_created(saved)
    return ok(_serialize(saved), status=201)


def _account_nonce(sub) -> str:
    """Stable-per-record nonce for idempotency keys (created/updated time)."""
    for attr in ("created_at", "updated_at"):
        value = getattr(sub, attr, None)
        if value is not None:
            try:
                return value.isoformat()
            except AttributeError:
                return str(value)
    return "0"


def _notify_account_created(sub) -> None:
    """Enqueue the gated 'otp' WhatsApp activation notice for a new account.

    Wrapped so a notify failure can never turn a successful create into an
    error response. No OTP code is invented — create() does not produce one.
    """
    try:
        from ...radius.services.whatsapp_notify import notify_whatsapp

        phone = str(getattr(sub, "mobile", "") or "").strip()
        username = str(getattr(sub, "username", "") or "")
        notify_whatsapp(
            _tid(),
            "otp",
            gate="otp",
            recipient_phone=phone,
            template_key="otp",
            subscriber_id=getattr(sub, "id", None),
            idempotency_key=f"otp:{_tid()}:{username}:{_account_nonce(sub)}",
        )
    except Exception:  # noqa: BLE001 — notification must never break account create
        pass


def accounts_get(username: str):
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", f"الحساب {username} غير موجود.", status=404)
    return ok(_serialize(sub))


def accounts_patch(username: str):
    body = request.get_json(silent=True) or {}
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    try:
        new_sub = _apply_body(sub, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        _svc().update(actor=_actor(), sub=new_sub)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(username)))


def accounts_delete(username: str):
    try:
        _svc().delete(actor=_actor(), username=username)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": username, "archived": True})


def accounts_reset_pw(username: str):
    body = request.get_json(silent=True) or {}
    pw = body.get("new_password")
    if not pw:
        return fail("validation_error", "new_password مطلوب", status=422)
    try:
        _svc().reset_password(actor=_actor(), username=username, new_password=str(pw))
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    # Fire-and-forget WhatsApp 'password changed' notice (gated + fail-safe).
    # The new password is NEVER put in the payload — only a heads-up that it
    # changed. This call can NEVER break the password reset.
    _notify_password_changed(username)
    return ok({"username": username, "reset": True})


def _notify_password_changed(username: str) -> None:
    """Enqueue the gated 'password_changed' WhatsApp notice (no password sent).

    Re-reads the subscriber for its phone + id + a version nonce; any lookup or
    notify failure is swallowed so the reset itself always succeeds.
    """
    try:
        from ...radius.services.whatsapp_notify import notify_whatsapp

        try:
            sub = _svc().get(username)
        except Exception:  # noqa: BLE001 — lookup is best-effort
            sub = None
        phone = str(getattr(sub, "mobile", "") or "").strip() if sub else ""
        notify_whatsapp(
            _tid(),
            "password_changed",
            gate="password",
            recipient_phone=phone,
            template_key="password_changed",
            subscriber_id=getattr(sub, "id", None) if sub else None,
            idempotency_key=f"pwd:{_tid()}:{username}:{_account_nonce(sub) if sub else '0'}",
        )
    except Exception:  # noqa: BLE001 — notification must never break the reset
        pass


def accounts_extend(username: str):
    body = request.get_json(silent=True) or {}
    try:
        minutes = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "قيمة minutes يجب أن تكون رقمًا صحيحًا.", status=422)
    if minutes <= 0:
        return fail("validation_error", "قيمة minutes يجب أن تكون أكبر من صفر.", status=422)
    try:
        saved = _svc().extend_time(actor=_actor(), username=username, minutes=minutes)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"username": username, "extended_minutes": minutes,
               "new_expire_at": saved.expire_at.isoformat() + "Z" if saved.expire_at else None})


def accounts_disable(username: str):
    try:
        _svc().disable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({"username": username, "status": "disabled"})


def accounts_enable(username: str):
    try:
        _svc().enable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({"username": username, "status": "enabled"})


def accounts_usage(username: str):
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({
        "username": sub.username,
        "used_seconds": sub.used_seconds,
        "used_bytes_in": sub.used_bytes_in,
        "used_bytes_out": sub.used_bytes_out,
        "online_count": sub.online_count,
        "last_seen_at": sub.last_seen_at.isoformat() + "Z" if sub.last_seen_at else None,
        "expire_at": sub.expire_at.isoformat() + "Z" if sub.expire_at else None,
    })


def accounts_360(username: str):
    from ...radius.services.subscriber_360 import Subscriber360Service

    try:
        payload = Subscriber360Service(tenant_id=_tid()).get_by_username(username)
    except KeyError:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok(_safe_360_payload(payload))


_SENSITIVE_360_KEYS = {
    "password",
    "pass",
    "password_hash",
    "secret",
    "shared_secret",
    "radius_secret",
    "private_key",
}


def _safe_360_payload(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_360_KEYS:
                continue
            safe[key] = _safe_360_payload(item)
        return safe
    if isinstance(value, list):
        return [_safe_360_payload(item) for item in value]
    return value
