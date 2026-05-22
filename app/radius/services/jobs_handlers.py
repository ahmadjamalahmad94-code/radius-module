"""jobs_handlers — concrete handlers for the S1 background runner.

This module's only job is to register handlers at import time.
Importing it has the side effect of populating the runner's
registry, so the blueprint just imports it once and forgets it.

Handlers live here grouped by domain. Each takes `(job, payload)`,
runs the work, and returns a dict result (the runner stores the
result through the repo's redaction pipeline).
"""
from __future__ import annotations

from typing import Any

from ..db.connection import db
from . import mikrotik_admin_client as mac
from . import mt_health
from .jobs_runner import progress, register_handler


# ─── shared helpers ──────────────────────────────────────────


def _load_nas(nas_id: int, tenant_id: int) -> dict | None:
    """Mirror of the per-route `_load_nas` helpers — kept in the
    handler module so a worker process can call it without
    importing the route layer."""
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, enabled, connection_mode "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), int(tenant_id)),
    ).fetchone()
    return dict(row) if row else None


def _nas_to_admin_client_shape(nas: dict) -> dict[str, Any]:
    """Translate a `nas_devices` row into the mapping the
    `mikrotik_admin_client` family expects (host/port/username/
    password/use_tls)."""
    return {
        "id":          nas["id"],
        "name":        nas["name"],
        "host":        nas["address"],
        "port":        int(nas.get("api_port") or 8728),
        "username":    nas.get("api_user") or "admin",
        "password":    nas.get("api_password") or "",
        "use_tls":     bool(nas.get("api_use_tls")),
        "verify_tls":  True,
        "timeout_sec": 10,
    }


# ─── mt.diag.scan ─────────────────────────────────────────────


@register_handler("mt.diag.scan")
def handle_mt_diag_scan(job: dict, payload: dict) -> dict[str, Any]:
    """Background equivalent of P7's per-router diagnostics scan.

    Reuses the cached K4 readers — no extra RouterOS calls beyond
    what the existing sync path would have made. Safe on offline
    routers (mt_health.scan_router returns fetch_errors and the
    handler treats them as a partial-but-OK outcome, not a
    runner-level failure)."""
    progress(job["id"], 10, "تحميل بيانات الراوتر")
    tenant_id = int(job.get("tenant_id") or 1)
    nas_id = job.get("router_id") or payload.get("router_id")
    if not nas_id:
        raise ValueError("router_id مطلوب لتشغيل التشخيص.")
    nas = _load_nas(int(nas_id), tenant_id)
    if not nas:
        raise ValueError(
            f"الراوتر #{nas_id} غير موجود في هذا المستأجر.")
    if not nas.get("enabled"):
        # Disabled routers aren't a runner failure — they're an
        # operator state. Returning a "skipped" result keeps the
        # job row green so the UI doesn't surface a misleading
        # "failed" badge.
        return {
            "router_id": int(nas_id),
            "router_name": nas.get("name"),
            "skipped": True,
            "reason": "الراوتر معطّل من الإعدادات — لا فحص.",
        }

    progress(job["id"], 40, "استعلام عن الواجهات والعناوين")
    report = mt_health.scan_router(_nas_to_admin_client_shape(nas))
    progress(job["id"], 90, "تجميع التقرير")
    return {
        "router_id":    int(nas_id),
        "router_name":  nas.get("name"),
        "signals":      report.get("signals", []),
        "summary":      report.get("summary", {}),
        "fetch_errors": report.get("fetch_errors", []),
    }


__all__ = ["handle_mt_diag_scan"]
