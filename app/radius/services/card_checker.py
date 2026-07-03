"""Read-only Card Checker service.

The service answers product/support questions about a card without mutating
RADIUS state and without exposing the stored card password.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..db.helpers import parse_dt
from ..db.repos import cards_repo, device_fingerprints_repo
from .card_accounting import (
    MODE_BY_SECONDS,
    MODE_FROM_FIRST_CONNECT,
    budget_seconds,
)
from .card_accounting import remaining_seconds as _remaining_by_mode
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


def _resolve_accounting(record: dict) -> tuple[str, int]:
    """Resolve the card's *accounting mode* and *time budget* from its batch.

    Precedence (most specific → most general): per-card override (none exists
    on the schema today, reserved for the future) > card_batch flags >
    offer/plan. The batch is therefore the authoritative source: the source
    "Hobe Hub" system stamps «طريقة الإحتساب» + «صلاحية الكارت بعد أول اتصال»
    at the batch level, and migration carries them there.

    * ``count_from_first_connect`` (the source's «من أول اتصال») →
      MODE_FROM_FIRST_CONNECT: countdown from the first connection.
    * else ``count_by_seconds`` → MODE_BY_SECONDS: usage-seconds budget.
    * else a legacy calendar card → MODE_BY_SECONDS with the plain
      ``expire_at`` fallback that :func:`remaining_seconds` applies when the
      budget is 0.
    """
    if bool(record.get("batch_count_from_first_connect")):
        mode = MODE_FROM_FIRST_CONNECT
    elif bool(record.get("batch_count_by_seconds")):
        mode = MODE_BY_SECONDS
    else:
        mode = MODE_BY_SECONDS
    budget = budget_seconds(
        validity_after_first_login_days=record.get(
            "batch_validity_after_first_login_days") or 0,
        time_value=record.get("batch_time_value") or 0,
        time_unit=record.get("batch_time_unit") or "days",
        duration_minutes=record.get("profile_duration_minutes") or 0,
        validity_days=record.get("profile_validity_days") or 0,
    )
    return mode, budget


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
    # Estimated unit price — direct price_per_card if set, otherwise
    # the total_price spread across generated count.
    ppc       = float(row.get("batch_price_per_card") or 0)
    total_pr  = float(row.get("batch_total_price")    or 0)
    gen       = int(row.get("batch_generated") or 0)
    if ppc > 0:
        unit_price = ppc
    elif total_pr > 0 and gen > 0:
        unit_price = total_pr / gen
    else:
        unit_price = 0.0
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
        # Price hooks for the Card Checker hero:
        # `price_per_card` is the explicit unit price; `unit_price` is
        # the same value with the total_price-fallback applied;
        # `price_bulk` is the wholesale tier price (when set).
        "price_per_card": ppc,
        "total_price":    total_pr,
        "unit_price":     unit_price,
        "price_bulk":     float(row.get("batch_price_bulk") or 0),
        # Manager names — prefer the joined full_name; fall back to the
        # username string, and only then to the raw id / created_by
        # string. Either may be empty if the JOIN didn't match.
        "manager_name": (row.get("batch_manager_full_name")
                          or row.get("batch_manager_username")
                          or (str(row.get("batch_manager_id"))
                              if row.get("batch_manager_id") else "")),
        "created_by_name": (row.get("batch_created_by_full_name")
                             or row.get("batch_created_by") or ""),
        # Distributor / seller — display_name then name; empty if the
        # batch wasn't assigned to a distributor (the JOIN missed).
        "distributor_name": (row.get("batch_distributor_display_name")
                              or row.get("batch_distributor_name") or ""),
    }


def _profile(row: dict) -> dict | None:
    if row.get("profile_name") is None:
        return None
    return {
        "id": row.get("plan_id"),
        "name": row.get("profile_name"),
        "code": row.get("profile_code") or "",
        "currency": row.get("profile_currency") or "",
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


def _dhcp_device(fp: dict | None) -> dict | None:
    """Compact display payload for a device_fingerprints row.

    Returns None when the fingerprint is empty / missing — callers
    decide whether to fall back to OUI-based inference.
    """
    if not fp:
        return None
    hostname = fp.get("hostname") or ""
    os_family = fp.get("os_family") or ""
    os_version = fp.get("os_version") or ""
    brand = fp.get("device_brand") or ""
    # Human label, prefers the hostname which is the most recognizable
    # to operators (e.g. "Redmi-Note-12-Pro · Android 11").
    parts = []
    if hostname:
        parts.append(hostname)
    if os_family:
        os_label = os_family.capitalize()
        if os_version:
            os_label += f" {os_version}"
        parts.append(os_label)
    label = " · ".join(parts) if parts else ""
    return {
        "hostname":   hostname,
        "class_id":   fp.get("dhcp_class_id") or "",
        "os_family":  os_family,
        "os_version": os_version,
        "brand":      brand,
        "model":      fp.get("device_model") or "",
        "label":      label,           # human one-liner
        "ip":         fp.get("ip_address") or "",
        "last_seen":  fp.get("last_seen_at") or "",
    }


def _trigger_oncheck_refresh(tenant_id: int, macs: list[str]) -> None:
    """Fire-and-forget MT refresh for the card's MACs. Never blocks the
    page render — failures are logged but invisible to the user."""
    import logging
    import threading
    log = logging.getLogger(__name__)

    def _run():
        try:
            from .device_fingerprint_sync import sync_macs_for_tenant
            sync_macs_for_tenant(tenant_id, macs)
        except Exception:  # noqa: BLE001
            log.exception("oncheck dhcp refresh failed tenant=%s", tenant_id)

    t = threading.Thread(target=_run, daemon=True, name="cc-dhcp-refresh")
    t.start()


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
                # DHCP fingerprint (migration 026) — set by check_card
                # after this _summary builds the macs list. Passing
                # through here so the lock-MAC picker can show the
                # device hostname/OS next to each MAC.
                "dhcp_device": item.get("dhcp_device"),
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

    # Safety net: list_card_macs occasionally returns empty even when
    # sessions clearly contain MAC addresses (case-folded usernames,
    # NULL-as-empty bookkeeping rows, etc.). The user-visible symptom
    # was the lock-MAC picker showing "لا يوجد سجل سابق" while the
    # sessions table right above it listed MACs. Re-derive from the
    # session list whenever the dedicated query came back empty.
    if not macs and sessions:
        _agg: dict[str, dict] = {}
        for s in sessions:
            raw = (s.get("mac_address") or "").strip()
            if not raw:
                continue
            key = raw.upper()
            entry = _agg.setdefault(key, {
                "mac":             key,
                "sessions_count":  0,
                "online_sessions": 0,
                "last_seen_at":    None,
            })
            entry["sessions_count"] += 1
            if s.get("online"):
                entry["online_sessions"] += 1
            ls = s.get("stopped_at") or s.get("started_at")
            if ls and (entry["last_seen_at"] is None or ls > entry["last_seen_at"]):
                entry["last_seen_at"] = ls
        macs = sorted(
            _agg.values(),
            key=lambda x: (x["last_seen_at"] or ""),
            reverse=True,
        )

    # ── DHCP-lease device fingerprints (migration 026) ─────────────
    # Collect every MAC this card has ever touched, look them all up in
    # one query, and inject the `dhcp_device` payload into each session
    # and each MAC-history entry. Also fire a background refresh that
    # tops up the cache from the live router — the response itself
    # never blocks on MT.
    #
    # Wrapped in a defensive try/except: if migration 026 didn't run,
    # if the table is corrupt, or if the repo throws for any reason,
    # we MUST NOT 500 the Card Checker — the operator's flow has to
    # survive a degraded fingerprint layer. We log and continue with
    # empty dhcp_device on every row.
    _all_macs: list[str] = []
    for s in sessions:
        if s.get("mac_address"):
            _all_macs.append(s["mac_address"])
    for m in macs:
        if m.get("mac"):
            _all_macs.append(m["mac"])
    if record.get("used_by_mac"):
        _all_macs.append(record["used_by_mac"])
    _fp_by_mac: dict[str, dict] = {}
    try:
        if _all_macs:
            _fp_by_mac = device_fingerprints_repo.get_many_by_macs(
                tenant_id, _all_macs,
            )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "check_card: DHCP fingerprint lookup failed (continuing without)"
        )
        _fp_by_mac = {}
    for s in sessions:
        mac_key = (s.get("mac_address") or "").lower()
        s["dhcp_device"] = _dhcp_device(_fp_by_mac.get(mac_key))
    for m in macs:
        mac_key = (m.get("mac") or "").lower()
        m["dhcp_device"] = _dhcp_device(_fp_by_mac.get(mac_key))
    if _all_macs:
        try:
            _trigger_oncheck_refresh(tenant_id, _all_macs)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "check_card: oncheck refresh dispatch failed (non-fatal)"
            )
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

    # ── Accounting mode + remaining time, resolved from the batch ──────
    # The card's BATCH is the source of truth for HOW its time is counted
    # (count-from-first-connect vs by-seconds) and for the validity budget.
    # Both the «طريقة الاحتساب» label and remaining_seconds derive from this
    # single resolution — never again from "has it connected?" alone.
    acct_mode, acct_budget = _resolve_accounting(record)
    first_connection_at = (
        parse_dt(record.get("first_used_at"))
        or parse_dt(accounting_summary.get("first_session_at"))
    )
    used_seconds = _seconds(accounting_summary.get("total_session_seconds"))
    remaining = _remaining_by_mode(
        mode=acct_mode,
        budget=acct_budget,
        now=now,
        first_connection_at=first_connection_at,
        accounted_seconds=used_seconds,
        expire_at=parse_dt(record.get("card_expire_at")),
    )

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
        # 'started_at' = the first time this card actually connected.
        # When FreeRADIUS native rlm_sql does the auth (not our HTTP
        # policy engine), cards.first_used_at never gets stamped, but
        # radacct still records the session start. Fall back to the
        # earliest acctstarttime in radacct so the Card Checker hero
        # always shows the real first connection.
        "started_at": (
            _iso(record.get("first_used_at"))
            or accounting_summary.get("first_session_at")
        ),
        "expires_at": _iso(record.get("card_expire_at")),
        # remaining_seconds honours the batch-resolved accounting mode:
        # a count-from-first-connect card counts DOWN from its first
        # connection (first_connection + budget − now); a by-seconds card
        # burns its budget only while online; a legacy calendar card falls
        # back to the plain expire_at date.
        "remaining_seconds": remaining,
        # accounting_mode: the resolved mode string the checker UI reads to
        # label «طريقة الاحتساب» (from_first_connect ⇒ «تبدأ من أول اتصال»,
        # by_seconds ⇒ «بالثانية») instead of guessing from started_at.
        "accounting_mode": acct_mode,
        "accounting_budget_seconds": acct_budget,
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
            # can_disconnect must reflect "any device currently online",
            # not "the single latest radacct row is open". After a
            # per-session kick (multi-device card), the kicked row's
            # acctstoptime = now() makes it the newest row by timestamp
            # → get_latest_card_accounting returns the CLOSED row even
            # though sibling sessions are still active. We instead
            # count rows with acctstoptime IS NULL across all sessions
            # (already aggregated by summarize_card_accounting). The
            # button stays enabled as long as >0 devices are live.
            "can_disconnect": int(accounting_summary.get("online_sessions") or 0) > 0,
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
