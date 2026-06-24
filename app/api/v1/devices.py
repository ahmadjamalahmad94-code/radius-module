"""Devices endpoints — DHCP fingerprint lookups + push-ingest.

Two ingest paths supplying the `device_fingerprints` table (migration
026), used because routers across the public internet often have
their MT API port firewalled even when outbound HTTPS works fine:

  PULL mode (device_fingerprint_worker):
    VPS → MT API on 8728. Requires inbound TCP open on MT.

  PUSH mode (this endpoint):
    MT → VPS via /tool fetch. Requires only outbound HTTPS on MT.
    The operator pastes a scheduler script (generated on the setup
    page) that POSTs DHCP leases here every ~2 minutes.

Endpoints
  GET  /api/v1/devices/by-mac/<mac>    → one fingerprint
  GET  /api/v1/devices                 → list (filters: os, limit, offset)
  POST /api/v1/devices/sync            → trigger PULL sync now
  POST /api/v1/devices/ingest          → PUSH from MT script (batch)

All endpoints scoped to the caller's tenant via the API token.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/devices/by-mac/<mac>", "devices_by_mac",
        require_api_token(devices_by_mac), methods=["GET"],
    )
    bp.add_url_rule(
        "/devices", "devices_list",
        require_api_token(devices_list), methods=["GET"],
    )
    bp.add_url_rule(
        "/devices/sync", "devices_sync",
        require_api_token(devices_sync), methods=["POST"],
    )
    bp.add_url_rule(
        "/devices/ingest", "devices_ingest",
        require_api_token(devices_ingest), methods=["POST"],
    )
    # FCM push-token registration (Flutter app): register/upsert on login,
    # unregister on logout. Tenant + admin scoped via require_api_token.
    bp.add_url_rule(
        "/devices/push-token", "devices_push_token_register",
        require_api_token(push_token_register), methods=["POST"],
    )
    bp.add_url_rule(
        "/devices/push-token", "devices_push_token_unregister",
        require_api_token(push_token_unregister), methods=["DELETE"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _aid() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


# ─── FCM push-token registration ───────────────────────────────────────────

_ALLOWED_PLATFORMS = {"android", "ios", "web", ""}


def push_token_register():
    """POST /api/v1/devices/push-token — register/upsert this device's FCM token.

    Body (JSON): {token (required), platform (android|ios|web), app_version}.
    Idempotent on (tenant_id, token): re-posting the same token just refreshes
    last_seen/platform. The token is the device's secret, stored so the server
    can push the same notifications the bell shows."""
    from ...radius.db.repos import device_push_tokens_repo

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("invalid_shape", "أرسل كائن JSON.", status=400)
    token = str(body.get("token") or "").strip()
    if not token:
        return fail("missing_token", "رمز الجهاز (token) مطلوب.", status=400)
    platform = str(body.get("platform") or "").strip().lower()
    if platform not in _ALLOWED_PLATFORMS:
        return fail("invalid_platform",
                    "المنصّة يجب أن تكون android أو ios أو web.", status=400)
    app_version = str(body.get("app_version") or "").strip()[:40]

    device_push_tokens_repo.register(
        _tid(), token, admin_id=_aid(), platform=platform, app_version=app_version)
    return ok({"registered": True, "platform": platform,
               "count": device_push_tokens_repo.count_for_tenant(_tid())})


def push_token_unregister():
    """DELETE /api/v1/devices/push-token — unregister this device (logout).

    Body (JSON): {token (required)}. Idempotent — deleting a missing token
    returns removed=0, not an error."""
    from ...radius.db.repos import device_push_tokens_repo

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("invalid_shape", "أرسل كائن JSON.", status=400)
    token = str(body.get("token") or "").strip()
    if not token:
        return fail("missing_token", "رمز الجهاز (token) مطلوب.", status=400)
    removed = device_push_tokens_repo.unregister(_tid(), token)
    return ok({"unregistered": True, "removed": removed})


def devices_by_mac(mac: str):
    from ...radius.db.repos import device_fingerprints_repo
    fp = device_fingerprints_repo.get_by_mac(_tid(), mac)
    if not fp:
        return fail("not_found", "لا توجد بصمة جهاز لهذا العنوان.", status=404)
    return ok({"device": fp})


def devices_list():
    from ...radius.db.repos import device_fingerprints_repo
    os_family = (request.args.get("os") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit") or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    items = device_fingerprints_repo.list_for_tenant(
        _tid(), limit=limit, offset=offset, os_family=os_family,
    )
    return ok({
        "items": items,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "total": device_fingerprints_repo.count_for_tenant(_tid()),
    })


def devices_sync():
    """On-demand: pull DHCP leases for this tenant right now.

    Useful right after adding a new MikroTik, or for testing — the
    background worker normally handles this every 2 minutes.
    """
    from ...radius.services import device_fingerprint_sync
    macs_seen = device_fingerprint_sync.sync_tenant(_tid())
    return ok({"macs_seen": macs_seen})


def devices_ingest():
    """Push-mode bulk ingest from a MikroTik /tool fetch script.

    Accepts either:
      • JSON array of lease dicts            → [{mac, hostname, ...}, ...]
      • JSON object with `leases` key        → {"leases": [...]}
      • Plain text body of JSON array        → same as first form
        (MT's /tool fetch with http-data sends content-type=text/plain
        by default unless headers are crafted; we accept both).

    Each lease dict can have:
      mac        (required, normalized to lower)
      hostname   (optional, "Redmi-Note-12-Pro")
      class_id   (optional, DHCP option 60, "android-dhcp-11")
      ip         (optional)
      nas_id     (optional — kept for the source NAS, not required)

    Returns {ingested, skipped, total}. Bad rows are silently skipped
    so a single malformed lease doesn't fail the whole batch — the
    MikroTik script must be resilient.
    """
    import json
    from ...radius.db.repos import device_fingerprints_repo
    from ...radius.services.device_fingerprint_sync import (
        parse_class_id, parse_hostname,
    )

    raw = request.get_data(as_text=True) or ""
    raw = raw.strip()
    if not raw:
        return fail("empty_body", "بيانات الأجهزة مطلوبة.", status=400)

    # Accept both JSON and text-as-JSON
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return fail("invalid_json", "بيانات الطلب ليست JSON صالحًا.", status=400)

    if isinstance(body, dict) and "leases" in body:
        leases = body["leases"]
    elif isinstance(body, list):
        leases = body
    else:
        return fail("invalid_shape",
                    "أرسل مصفوفة JSON أو كائنًا يحتوي leases.",
                    status=400)

    if not isinstance(leases, list):
        return fail("invalid_shape", "قائمة leases يجب أن تكون مصفوفة.", status=400)

    tenant_id = _tid()
    ingested = 0
    skipped = 0
    for row in leases:
        if not isinstance(row, dict):
            skipped += 1
            continue
        mac = (row.get("mac") or row.get("active-mac-address") or "").strip()
        if not mac:
            skipped += 1
            continue
        hostname = (row.get("hostname") or row.get("active-host-name") or "").strip()
        class_id = (row.get("class_id")
                    or row.get("active-client-id")
                    or row.get("class-id") or "").strip()
        ip_addr  = (row.get("ip") or row.get("active-address") or "").strip()

        os_family, os_version = parse_class_id(class_id)
        brand, model = parse_hostname(hostname)
        try:
            device_fingerprints_repo.upsert(
                tenant_id=tenant_id,
                mac=mac,
                hostname=hostname,
                dhcp_class_id=class_id,
                os_family=os_family,
                os_version=os_version,
                device_brand=brand,
                device_model=model,
                ip_address=ip_addr,
                nas_id=None,
            )
            ingested += 1
        except Exception:  # noqa: BLE001
            skipped += 1

    return ok({
        "ingested": ingested,
        "skipped":  skipped,
        "total":    len(leases),
    })
