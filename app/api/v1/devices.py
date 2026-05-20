"""Devices endpoints — DHCP fingerprint lookups.

Single-MAC and list views over the `device_fingerprints` table
(migration 026). The table is populated by the background DHCP-lease
worker pulling `/ip/dhcp-server/lease/print` from every enabled
MikroTik router every ~2 minutes.

  GET  /api/v1/devices/by-mac/<mac>    → one fingerprint
  GET  /api/v1/devices                 → list (filters: os, limit, offset)
  POST /api/v1/devices/sync            → trigger on-demand sync now

All endpoints scoped to the caller's tenant.
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


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def devices_by_mac(mac: str):
    from ...radius.db.repos import device_fingerprints_repo
    fp = device_fingerprints_repo.get_by_mac(_tid(), mac)
    if not fp:
        return fail("not_found", "no fingerprint for this MAC", status=404)
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
