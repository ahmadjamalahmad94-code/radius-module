"""Read-only Card Checker service.

The service answers product/support questions about a card without mutating
RADIUS state and without exposing the stored card password.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..db.helpers import parse_dt
from ..db.repos import cards_repo
from .device_fingerprint import infer_device


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(raw: Any) -> str | None:
    dt = parse_dt(raw) if isinstance(raw, str) or raw is None else raw
    if not dt:
        return None
    return dt.isoformat() + "Z"


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _remaining_seconds(raw: Any, now: datetime) -> int | None:
    expires = parse_dt(raw)
    if not expires:
        return None
    return max(0, int((expires - now).total_seconds()))


def _bytes(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _status(row: dict, now: datetime) -> str:
    batch_status = (row.get("batch_status") or "").strip().lower()
    if bool(row.get("card_revoked")) or row.get("batch_deleted_at") or batch_status in {
        "revoked",
        "cancelled",
        "canceled",
        "deleted",
    }:
        return "revoked"
    expires_at = parse_dt(row.get("card_expire_at"))
    if expires_at and expires_at < now:
        return "expired"
    if bool(row.get("card_used")):
        return "active"
    return "available"


def _batch(row: dict) -> dict | None:
    if row.get("batch_id") is None:
        return None
    return {
        "id": row.get("batch_id"),
        "batch_code": row.get("batch_code"),
        "package_name": row.get("batch_package_name") or "",
        "status": row.get("batch_status"),
        "count": row.get("batch_count"),
        "generated": row.get("batch_generated"),
        "used": row.get("batch_used"),
        "manager_id": row.get("batch_manager_id"),
        "created_by": row.get("batch_created_by") or None,
        "created_at": _iso(row.get("batch_created_at")),
        "expires_at": _iso(row.get("batch_expire_at")),
        "deleted_at": _iso(row.get("batch_deleted_at")),
    }


def _profile(row: dict) -> dict | None:
    if row.get("profile_name") is None:
        return None
    return {
        "id": row.get("plan_id"),
        "name": row.get("profile_name"),
        "code": row.get("profile_code") or "",
        "service_type": row.get("profile_service_type"),
        "plan_type": row.get("profile_plan_type"),
        "speed_down_kbps": row.get("profile_speed_down_kbps"),
        "speed_up_kbps": row.get("profile_speed_up_kbps"),
        "quota_total_mb": row.get("profile_quota_total_mb"),
        "quota_daily_mb": row.get("profile_quota_daily_mb"),
        "quota_monthly_mb": row.get("profile_quota_monthly_mb"),
        "duration_minutes": row.get("profile_duration_minutes"),
        "validity_days": row.get("profile_validity_days"),
    }


def _session(row: dict, now: datetime) -> dict:
    start = parse_dt(row.get("acctstarttime"))
    stop = parse_dt(row.get("acctstoptime"))
    update = parse_dt(row.get("acctupdatetime"))
    online = stop is None
    duration = _seconds(row.get("acctsessiontime"))
    if online and start:
        duration = max(duration, int((now - start).total_seconds()))
    return {
        "id": row.get("radacctid"),
        "session_id": row.get("acctsessionid") or "",
        "unique_id": row.get("acctuniqueid") or "",
        "started_at": _iso(row.get("acctstarttime")),
        "updated_at": _iso(row.get("acctupdatetime")),
        "stopped_at": _iso(row.get("acctstoptime")),
        "online": online,
        "duration_seconds": duration,
        "upload_bytes": _bytes(row.get("acctinputoctets")),
        "download_bytes": _bytes(row.get("acctoutputoctets")),
        "mac_address": row.get("callingstationid") or None,
        "called_station": row.get("calledstationid") or None,
        "ip_address": row.get("framedipaddress") or None,
        "ipv6_address": row.get("framedipv6address") or None,
        "nas_address": row.get("nasipaddress") or None,
        "nas_port": row.get("nasportid") or None,
        "nas_port_type": row.get("nasporttype") or None,
        "service_type": row.get("servicetype") or None,
        "framed_protocol": row.get("framedprotocol") or None,
        "connect_info_start": row.get("connectinfo_start") or None,
        "connect_info_stop": row.get("connectinfo_stop") or None,
        "terminate_cause": row.get("acctterminatecause") or None,
        "device_hint": row.get("connectinfo_start") or row.get("nasporttype") or None,
        # R13.A.6: device fingerprint per session — vendor + category +
        # icon + connection medium. Always present; "unknown" for missing
        # data. UI uses `device.icon` + `device.label`.
        "device": infer_device(
            mac=row.get("callingstationid"),
            nas_port_type=row.get("nasporttype"),
            connect_info=row.get("connectinfo_start"),
        ),
    }


def _summary(raw: dict, sessions: list[dict], macs: list[dict]) -> dict:
    return {
        "sessions_count": _seconds(raw.get("sessions_count")),
        "online_sessions": _seconds(raw.get("online_sessions")),
        "unique_macs": _seconds(raw.get("unique_macs")),
        "unique_ips": _seconds(raw.get("unique_ips")),
        "unique_nas": _seconds(raw.get("unique_nas")),
        "total_session_seconds": _seconds(raw.get("total_session_seconds")),
        "total_upload_bytes": _bytes(raw.get("total_upload_bytes")),
        "total_download_bytes": _bytes(raw.get("total_download_bytes")),
        "first_session_at": _iso(raw.get("first_session_at")),
        "last_session_at": _iso(raw.get("last_session_at")),
        "macs": [
            {
                "mac": item.get("mac"),
                "sessions_count": _seconds(item.get("sessions_count")),
                "online_sessions": _seconds(item.get("online_sessions")),
                "last_seen_at": _iso(item.get("last_seen_at")),
                # R13.A.6: device fingerprint per MAC. We don't carry
                # nas_port_type at this aggregate level, so the inference
                # leans on the OUI alone — still high-confidence for
                # well-known vendors like Apple / Samsung.
                "device": infer_device(mac=item.get("mac")),
            }
            for item in macs
        ],
        "latest_sessions": sessions,
    }


def _assigned_to(row: dict) -> dict | None:
    if not row.get("subscriber_username"):
        return None
    return {
        "subscriber_id": row.get("used_by_subscriber_id"),
        "username": row.get("subscriber_username"),
        "full_name": row.get("subscriber_full_name") or "",
        "mobile": row.get("subscriber_mobile") or "",
        "status": row.get("subscriber_status"),
    }


def _latest_seen(row: dict, acct: dict | None) -> str | None:
    if acct:
        return _iso(
            acct.get("acctupdatetime")
            or acct.get("acctstoptime")
            or acct.get("acctstarttime")
        )
    return _iso(row.get("subscriber_last_seen_at") or row.get("subscriber_last_login_at"))


def _missing_fields(card: dict) -> list[str]:
    missing = ["cancelled_at", "deleted_at", "sold_by"]
    for key in ("assigned_to", "last_seen_at", "mac_address", "ip_address"):
        if card.get(key) in (None, "", {}):
            missing.append(key)
    if card.get("batch") is None:
        missing.append("batch")
    if card.get("profile") is None:
        missing.append("profile")
    return sorted(set(missing))


def _available_fields(card: dict) -> list[str]:
    fields = []
    for key, value in card.items():
        if key in {"available_fields", "missing_fields"}:
            continue
        if value not in (None, "", {}, []):
            fields.append(key)
    return sorted(fields)


def check_card(tenant_id: int, query: str) -> dict:
    """Return a stable, Flutter-ready Card Checker payload."""
    record = cards_repo.get_card_check_record(tenant_id, query)
    if not record:
        return {
            "exists": False,
            "status": "not_found",
            "query": query,
            "data_sources": [],
            "available_fields": ["exists", "query", "status"],
            "missing_fields": [
                "assigned_to",
                "batch",
                "created_at",
                "expires_at",
                "last_seen_at",
                "mac_address",
                "profile",
                "sold_by",
            ],
        }

    now = _utcnow()
    acct = cards_repo.get_latest_card_accounting(tenant_id, record["username"])
    session_rows = cards_repo.list_card_accounting(tenant_id, record["username"], limit=100)
    sessions = [_session(row, now) for row in session_rows]
    accounting_summary = cards_repo.summarize_card_accounting(tenant_id, record["username"])
    macs = cards_repo.list_card_macs(tenant_id, record["username"], limit=30)
    data_sources = ["cards"]
    if record.get("batch_code") is not None:
        data_sources.append("card_batches")
    if record.get("profile_name") is not None:
        data_sources.append("access_plans")
    if record.get("subscriber_username"):
        data_sources.append("subscribers")
    if acct:
        data_sources.append("radacct")

    mac_address = (
        record.get("used_by_mac")
        or (acct or {}).get("callingstationid")
        or record.get("subscriber_mac_lock")
        or None
    )
    ip_address = (acct or {}).get("framedipaddress") or record.get("subscriber_static_ip")
    card = {
        "exists": True,
        "status": _status(record, now),
        "query": query,
        "id": record.get("card_id"),
        "username": record.get("username"),
        "has_password": bool(record.get("password")),
        "used": bool(record.get("card_used")),
        "revoked": bool(record.get("card_revoked")),
        "locked_mac": record.get("locked_mac") or None,
        "disabled_reason": record.get("disabled_reason") or "",
        "disabled_at": _iso(record.get("disabled_at")),
        "disabled_by": record.get("disabled_by") or "",
        "created_at": _iso(record.get("card_created_at")),
        "started_at": _iso(record.get("first_used_at")),
        "expires_at": _iso(record.get("card_expire_at")),
        "remaining_seconds": _remaining_seconds(record.get("card_expire_at"), now),
        "batch": _batch(record),
        "profile": _profile(record),
        "created_by": record.get("batch_created_by") or None,
        "assigned_to": _assigned_to(record),
        "sold_by": None,
        "last_seen_at": _latest_seen(record, acct),
        "mac_address": mac_address,
        "ip_address": ip_address,
        "nas_address": (acct or {}).get("nasipaddress") or None,
        "active_session": None if not acct else not bool(acct.get("acctstoptime")),
        "last_session_seconds": _int_or_none((acct or {}).get("acctsessiontime")),
        # R13.A.6: current/most-recent device fingerprint. Uses the latest
        # acct row's full context (MAC + NAS-Port-Type + Connect-Info) so
        # we can distinguish iPhone-on-WiFi from MacBook-on-WiFi etc.
        "current_device": infer_device(
            mac=mac_address,
            nas_port_type=(acct or {}).get("nasporttype"),
            connect_info=(acct or {}).get("connectinfo_start"),
        ),
        "operations": {
            "can_disconnect": bool((acct or {}).get("acctstoptime") is None and acct),
            "can_lock_mac": bool(mac_address),
            "can_reset_usage": True,
            "can_disable": not bool(record.get("card_revoked")),
            "can_enable": bool(record.get("card_revoked")),
            "can_delete_permanently": True,
        },
        "accounting_summary": _summary(accounting_summary, sessions, macs),
        "data_sources": data_sources,
    }
    card["missing_fields"] = _missing_fields(card)
    card["available_fields"] = _available_fields(card)
    return card
