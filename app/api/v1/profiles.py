"""
Profiles (Plans) endpoints — full CUD parity for the Flutter client.

Every write delegates to `PlansService` (the same code path the Flask web
form uses), so validation, audit, and adapter sync stay identical across
clients. The Flask web admin form is untouched.

Field intake is a whitelist mirroring `AccessPlan`. `metadata` is accepted
as either a dict or a JSON string and stored as a string. The response
serialiser parses metadata back to a dict so Flutter doesn't have to.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.core.types import AccessPlan
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


# Whitelist of patchable / creatable plan fields. `id`, `tenant_id`,
# `created_at`, `updated_at` are not editable here.
_STR_FIELDS = (
    "name", "code", "plan_type", "service_type", "typebp", "limit_type",
    "duration_unit", "validity_unit", "data_unit",
    "quota_reset_strategy", "burst_raw",
    "address_pool", "framed_pool", "ipv6_pool",
    "allowed_hours_from", "allowed_hours_to",
    "on_login", "on_logout",
    "currency", "plan_tier", "project", "description", "color",
    "offer_hours_from", "offer_hours_to",
)
_INT_FIELDS = (
    "duration_value", "duration_minutes",
    "validity_value", "validity_days",
    "max_daily_minutes", "max_weekly_minutes", "max_monthly_minutes",
    "session_timeout_sec", "idle_timeout_sec",
    "data_value",
    "quota_total_mb", "quota_daily_mb", "quota_monthly_mb",
    "bandwidth_id",
    "speed_up_kbps", "speed_down_kbps",
    "burst_up_kbps", "burst_down_kbps", "burst_threshold_kbps", "burst_time_sec",
    "concurrent_sessions", "pool_id", "vlan_id", "allowed_devices_count",
    "priority",
    # RM-H3
    "cir_down_kbps", "cir_up_kbps",
    "monthly_download_quota_mb", "monthly_upload_quota_mb", "monthly_combined_quota_mb",
    "daily_download_quota_mb", "daily_upload_quota_mb", "daily_combined_quota_mb",
    "max_consumption_times", "ticket_validity_days", "working_hours_limit",
)
_FLOAT_FIELDS = ("price_card", "price_bulk", "price")
_BOOL_FIELDS = (
    "bind_mac", "bind_ip", "force_mac_address",
    "auto_renew", "prepaid", "enabled",
    # RM-H3
    "speed_control_enabled", "burst_enabled", "nightly_unlimited_enabled",
    "single_use_once", "hotspot_enabled", "ppp_enabled",
)
_TUPLE_FIELDS = ("allowed_days", "router_ids")

_VALID_DAYS = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


def _normalize_metadata(raw) -> str:
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        if not raw.strip():
            return "{}"
        try:
            json.loads(raw)
            return raw
        except (TypeError, ValueError):
            return "{}"
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return "{}"
    return "{}"


def _coerce_int(name: str, v: Any) -> int:
    if v in (None, ""):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{name} must be integer")


def _coerce_float(name: str, v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{name} must be numeric")


def _coerce_days(v: Any) -> tuple[str, ...]:
    """Accept list/tuple of day codes or CSV string. Validates against
    {sun..sat}. Empty input → empty tuple (caller decides default)."""
    if v in (None, ""):
        return ()
    if isinstance(v, str):
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
    elif isinstance(v, (list, tuple)):
        parts = [str(p).strip().lower() for p in v if str(p).strip()]
    else:
        raise RadiusValidationError("allowed_days must be list or CSV string")
    bad = [p for p in parts if p not in _VALID_DAYS]
    if bad:
        raise RadiusValidationError(f"allowed_days has invalid entries: {bad}")
    # de-dup, preserve canonical order
    canonical_order = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    seen = set(parts)
    return tuple(d for d in canonical_order if d in seen)


def _coerce_router_ids(v: Any) -> tuple[int, ...]:
    if v in (None, ""):
        return ()
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",") if p.strip()]
    elif isinstance(v, (list, tuple)):
        parts = [str(p).strip() for p in v if str(p).strip() != ""]
    else:
        raise RadiusValidationError("router_ids must be list or CSV string")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            raise RadiusValidationError(f"router_ids entry not int: {p!r}")
    return tuple(out)


def _apply_body(plan: AccessPlan, body: dict) -> AccessPlan:
    changes: dict = {}
    for k in _STR_FIELDS:
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    for k in _INT_FIELDS:
        if k in body:
            changes[k] = _coerce_int(k, body[k])
    for k in _FLOAT_FIELDS:
        if k in body:
            changes[k] = _coerce_float(k, body[k])
    for k in _BOOL_FIELDS:
        if k in body:
            changes[k] = bool(body[k])
    if "allowed_days" in body:
        days = _coerce_days(body["allowed_days"])
        # Service expects at least one day; treat empty as "all 7".
        if not days:
            days = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
        changes["allowed_days"] = days
    if "router_ids" in body:
        changes["router_ids"] = _coerce_router_ids(body["router_ids"])
    if "metadata" in body:
        changes["metadata"] = _normalize_metadata(body["metadata"])
    return replace(plan, **changes)


def _serialize(plan: AccessPlan) -> dict:
    d = asdict(plan)
    for k in ("created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    # Tuples → lists for JSON cleanliness.
    if isinstance(d.get("allowed_days"), tuple):
        d["allowed_days"] = list(d["allowed_days"])
    if isinstance(d.get("router_ids"), tuple):
        d["router_ids"] = list(d["router_ids"])
    # Metadata string → parsed dict for client convenience.
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            d["metadata"] = json.loads(meta or "{}")
        except (TypeError, ValueError):
            d["metadata"] = {}
    return d


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/profiles", "profiles_list",
                    require_api_token(profiles_list), methods=["GET"])
    bp.add_url_rule("/profiles", "profiles_create",
                    require_api_token(profiles_create), methods=["POST"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_get",
                    require_api_token(profiles_get), methods=["GET"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_patch",
                    require_api_token(profiles_patch), methods=["PATCH"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_delete",
                    require_api_token(profiles_delete), methods=["DELETE"])


def _svc():
    from ...radius.services.plans import get_plans_service
    return get_plans_service()


# ─────────────── views ───────────────

def profiles_list():
    try:
        limit = min(int(request.args.get("limit") or 200), 1000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    items = _svc().list(limit=limit, offset=offset)
    return ok({"items": [_serialize(p) for p in items], "count": len(items)})


def profiles_get(profile_id: int):
    try:
        plan = _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"profile {profile_id} غير موجود", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(plan))


def profiles_create():
    body = request.get_json(silent=True) or {}
    if not (body.get("name") or "").strip():
        return fail("validation_error", "name مطلوب", status=422)

    # Seed a minimal plan, then apply body fields.
    seed = AccessPlan(
        id=None,
        tenant_id=_tid(),
        name=str(body["name"]).strip(),
    )
    try:
        plan = _apply_body(seed, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        saved = _svc().create(actor=_actor(), plan=plan)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(saved), status=201)


def profiles_patch(profile_id: int):
    body = request.get_json(silent=True) or {}
    try:
        existing = _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"profile {profile_id} غير موجود", status=404)
    try:
        new_plan = _apply_body(existing, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        _svc().update(actor=_actor(), plan=new_plan)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(profile_id)))


def profiles_delete(profile_id: int):
    # Adapter delete is silent on missing rows, so check existence first to
    # give callers a clean 404 instead of a misleading 200.
    try:
        _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"profile {profile_id} غير موجود", status=404)
    try:
        _svc().delete(actor=_actor(), plan_id=profile_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": profile_id})
