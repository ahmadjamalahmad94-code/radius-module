"""System operations API for Flutter/Web parity."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.connection import db, transaction
from ...radius.db.helpers import now_iso
from ...radius.db.repos import sync_queue_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _job(tenant_id: int, job_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM sync_queue WHERE tenant_id = ? AND id = ?",
        (tenant_id, job_id),
    ).fetchone()
    if not row:
        return None
    # Reuse the public list serializer from the repo to keep shape stable.
    for item in sync_queue_repo.list_jobs(tenant_id, limit=1000):
        if int(item["id"]) == int(job_id):
            return item
    return {"id": job_id, "tenant_id": tenant_id}


def _limit(default: int = 200, maximum: int = 1000) -> int:
    raw = request.args.get("limit")
    if not raw:
        return default
    try:
        return min(max(int(raw), 1), maximum)
    except ValueError:
        return default


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/system/status",
        "system_status",
        require_api_token(system_status),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/diagnostics",
        "system_diagnostics",
        require_api_token(system_diagnostics),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/sync",
        "system_sync_list",
        require_api_token(system_sync_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/sync/<int:job_id>/retry",
        "system_sync_retry",
        require_api_token(system_sync_retry),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/sync/<int:job_id>/cancel",
        "system_sync_cancel",
        require_api_token(system_sync_cancel),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/reconcile",
        "system_reconcile",
        require_api_token(system_reconcile),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/license-file",
        "system_license_file",
        require_api_token(system_license_file),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/usage-report",
        "system_admin_bridge_usage_report",
        require_api_token(system_admin_bridge_usage_report),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/capacity-status",
        "system_admin_bridge_capacity_status",
        require_api_token(system_admin_bridge_capacity_status),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/license-sync",
        "system_admin_bridge_license_sync",
        require_api_token(system_admin_bridge_license_sync),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/identity-sync",
        "system_admin_bridge_identity_sync",
        require_api_token(system_admin_bridge_identity_sync),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/heartbeat",
        "system_admin_bridge_heartbeat",
        require_api_token(system_admin_bridge_heartbeat),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/backups/upload-latest",
        "system_admin_bridge_backup_upload_latest",
        require_api_token(system_admin_bridge_backup_upload_latest),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/restore/poll",
        "system_admin_bridge_restore_poll",
        require_api_token(system_admin_bridge_restore_poll),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/restore/<reference>/snapshot",
        "system_admin_bridge_restore_snapshot",
        require_api_token(system_admin_bridge_restore_snapshot),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/service-activations/poll",
        "system_admin_bridge_service_activations_poll",
        require_api_token(system_admin_bridge_service_activations_poll),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/admin-bridge/events",
        "system_admin_bridge_events",
        require_api_token(system_admin_bridge_events),
        methods=["GET"],
    )


def system_status():
    from ...radius.routes.status import _gather_status

    return ok(_gather_status(_tid()))


def system_diagnostics():
    from ...radius.services import mt_diagnostics

    return ok(mt_diagnostics.diagnose_tenant(_tid()))


def system_sync_list():
    status = request.args.get("status") or None
    tenant_id = _tid()
    return ok(
        {
            "items": sync_queue_repo.list_jobs(
                tenant_id,
                status=status,
                limit=_limit(default=300),
            ),
            "stats": sync_queue_repo.stats(tenant_id),
            "status": status or "all",
        }
    )


def system_sync_retry(job_id: int):
    tenant_id = _tid()
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE sync_queue
            SET status='queued', next_attempt_at=?, last_error=''
            WHERE tenant_id = ? AND id = ?
            """,
            (now_iso(), tenant_id, job_id),
        )
        if cur.rowcount == 0:
            return fail("not_found", "Sync job not found", status=404)
    return ok({"job": _job(tenant_id, job_id), "action": "retry"})


def system_sync_cancel(job_id: int):
    tenant_id = _tid()
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE sync_queue
            SET status='failed', last_error='canceled by admin'
            WHERE tenant_id = ? AND id = ? AND status IN ('queued', 'retrying')
            """,
            (tenant_id, job_id),
        )
        if cur.rowcount == 0:
            return fail("not_found", "Sync job not found or not cancellable", status=404)
    return ok({"job": _job(tenant_id, job_id), "action": "cancel"})


def system_reconcile():
    try:
        from app.workers import mt_reconciler

        stats = mt_reconciler.reconcile_once()
    except Exception as exc:  # noqa: BLE001
        return fail("reconcile_failed", str(exc), status=500)
    return ok({"stats": stats})


def system_license_file():
    """Read-only license bridge state for native clients.

    This mirrors the web "license file" page without exposing bridge secrets.
    Manual sync/actions remain in the dedicated bridge endpoints below.
    """
    from ...radius.services.admin_panel_client import (
        SNAPSHOT_CAPACITY,
        SNAPSHOT_IDENTITY,
        SNAPSHOT_LICENSE,
        AdminBridgeConfig,
        LicenseAdminSnapshotStore,
        bridge_flag,
        bridge_setting,
        sanitize_bridge_payload,
    )
    from ...radius.services.admin_panel_client import _server_fingerprint
    from ...radius.services.license_admin_capacity import CapacityEnforcementService

    tenant_id = _tid()
    config = AdminBridgeConfig.from_env()
    store = LicenseAdminSnapshotStore()
    runtime_sync = bridge_flag(
        "HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC",
        "license_admin_bridge.runtime_contract_sync",
    )
    identity_sync = bridge_flag(
        "HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED",
        "license_admin_bridge.identity_sync_enabled",
    )
    identity_on_login = bridge_flag(
        "HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN",
        "license_admin_bridge.identity_sync_on_login",
    )
    worker_enabled = bridge_flag(
        "HOBERADIUS_ADMIN_BRIDGE_WORKER",
        "license_admin_bridge.worker_enabled",
    )
    saved_fingerprint = bridge_setting("license_admin_bridge.server_fingerprint", "")
    latest_license = store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_LICENSE)
    latest_contract = store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
    latest_identity = store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_IDENTITY)
    contract_payload = {}
    if latest_contract:
        payload = latest_contract.get("payload_json")
        if isinstance(payload, dict):
            contract_payload = payload.get("contract") if isinstance(payload.get("contract"), dict) else payload

    missing = config.missing_fields()
    if not config.shared_secret:
        missing.append("HOBERADIUS_ADMIN_SHARED_SECRET")
    capacity_status = CapacityEnforcementService().capacity_status(tenant_id=tenant_id)
    contract_services = contract_payload.get("services", {})
    if isinstance(capacity_status, dict) and isinstance(capacity_status.get("services"), dict):
        contract_services = capacity_status.get("services") or {}

    return ok(
        {
            "config": {
                "enabled": config.enabled,
                "base_url": config.base_url,
                "https_ready": str(config.base_url or "").lower().startswith("https://"),
                "license_key_configured": bool(config.license_key),
                "license_key_masked": sanitize_bridge_payload({"license_key": config.license_key}).get("license_key"),
                "shared_secret_configured": bool(config.shared_secret),
                "timeout_seconds": config.timeout_seconds,
                "retry_count": config.retry_count,
                "runtime_contract_sync": runtime_sync,
                "identity_sync_enabled": identity_sync,
                "identity_sync_on_login": identity_on_login,
                "worker_enabled": worker_enabled,
                "sync_interval_seconds": bridge_setting(
                    "license_admin_bridge.sync_interval_seconds",
                    "300",
                ),
                "server_fingerprint": {
                    "saved": saved_fingerprint,
                    "active": _server_fingerprint(),
                    "stable": bool(saved_fingerprint),
                },
            },
            "missing": missing,
            "snapshots": {
                "license": _snapshot_summary(latest_license),
                "runtime_contract": _snapshot_summary(latest_contract),
                "identity_sync": _snapshot_summary(latest_identity),
            },
            "contract": sanitize_bridge_payload(
                {
                    "license": contract_payload.get("license", {}),
                    "services": contract_services,
                    "limits": contract_payload.get("limits", {}),
                    "features": contract_payload.get("features", {}),
                }
            ),
            "capacity": sanitize_bridge_payload(
                capacity_status
            ),
        }
    )


def _snapshot_summary(snapshot: dict | None) -> dict:
    if not snapshot:
        return {
            "available": False,
            "status": "missing",
            "fetched_at": "",
            "stale_after_seconds": 0,
            "source_url": "",
            "error": {},
        }
    return {
        "available": True,
        "status": snapshot.get("normalized_status") or "unknown",
        "fetched_at": snapshot.get("fetched_at") or "",
        "created_at": snapshot.get("created_at") or "",
        "stale_after_seconds": int(snapshot.get("stale_after_seconds") or 0),
        "source_url": snapshot.get("source_url") or "",
        "error": snapshot.get("error_json") or {},
    }


def system_admin_bridge_usage_report():
    """Manual one-shot V40 usage report.

    Defaults to dry-run. Sending to admin requires explicit JSON
    `{"dry_run": false}` and valid bridge env config.
    """
    from ...radius.services.license_admin_usage_metering import UsageMeteringService

    payload = request.get_json(silent=True) or {}
    dry_run = payload.get("dry_run", True) is not False
    report_window = payload.get("report_window") or None
    result = UsageMeteringService().send_usage_report(
        tenant_id=_tid(),
        report_window=report_window,
        dry_run=dry_run,
    )
    return ok(result)


def system_admin_bridge_capacity_status():
    """Read-only local capacity state for future UI clients.

    This endpoint does not fetch from radius-module-admin and does not create
    upgrade/payment/service requests.
    """
    from ...radius.services.license_admin_capacity import CapacityEnforcementService

    return ok(CapacityEnforcementService().capacity_status(tenant_id=_tid()))


def system_admin_bridge_license_sync():
    """Pull the live license approval and derive local capacity/services state.

    The sync reads the signed license-check contract from radius-module-admin.
    It does not configure WireGuard, MikroTik, Linux tc, FreeRADIUS, or CoA.
    """
    from ...radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    result = LicenseAdminRuntimeSyncService().sync_once(tenant_id=_tid())
    return ok(result)


def system_admin_bridge_identity_sync():
    """Pull customer admin identities from radius-module-admin.

    This applies only password hashes and managed metadata. Plaintext
    passwords are rejected by the sync service.
    """
    from ...radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    payload = request.get_json(silent=True) or {}
    result = LicenseAdminIdentitySyncService().sync_once(
        tenant_id=_tid(),
        disable_missing=payload.get("disable_missing") is True,
    )
    return ok(result)


def system_admin_bridge_heartbeat():
    """Manual one-shot V40 instance heartbeat.

    Defaults to dry-run. Sending to admin requires explicit JSON
    `{"dry_run": false}` and valid bridge env config.
    """
    from ...radius.services.license_admin_instance_health import InstanceHealthService

    payload = request.get_json(silent=True) or {}
    dry_run = payload.get("dry_run", True) is not False
    result = InstanceHealthService().send_heartbeat(tenant_id=_tid(), dry_run=dry_run)
    return ok(result)


def system_admin_bridge_backup_upload_latest():
    """Manual V40 backup upload foundation.

    Defaults to dry-run and metadata-only. Content upload requires explicit
    request payload and explicit server-side env enablement.
    """
    from ...radius.services.license_admin_backup_upload import BackupUploadService

    payload = request.get_json(silent=True) or {}
    dry_run = payload.get("dry_run", True) is not False
    include_content = payload.get("include_content") is True
    result = BackupUploadService().upload_latest_backup(
        tenant_id=_tid(),
        dry_run=dry_run,
        include_content=include_content,
    )
    return ok(result)


def system_admin_bridge_restore_poll():
    from ...radius.services.license_admin_restore import RestoreWorkflowService

    result = RestoreWorkflowService().poll_once(tenant_id=_tid())
    return ok(result)


def system_admin_bridge_restore_snapshot(reference: str):
    from ...radius.services.license_admin_restore import RestoreWorkflowService

    try:
        result = RestoreWorkflowService().create_local_snapshot(
            tenant_id=_tid(),
            reference=reference,
        )
    except ValueError as exc:
        return fail("not_found", str(exc), status=404)
    return ok({"request": result})


def system_admin_bridge_service_activations_poll():
    """Manual one-shot V40 service activation poll.

    Defaults to dry-run. Unsupported jobs are recorded locally and reported;
    no live service execution is registered by default.
    """
    from ...radius.services.license_admin_service_activation import ServiceActivationService

    payload = request.get_json(silent=True) or {}
    dry_run = payload.get("dry_run", True) is not False
    result = ServiceActivationService().poll_once(tenant_id=_tid(), dry_run=dry_run)
    return ok(result)


def system_admin_bridge_events():
    """Read-only local V40 bridge event viewer."""
    from ...radius.services.license_admin_bridge_events import BridgeEventService

    service = BridgeEventService()
    event_type = request.args.get("event_type") or None
    return ok(
        {
            "items": service.list_events(
                tenant_id=_tid(),
                event_type=event_type,
                limit=_limit(default=100, maximum=500),
            ),
            "summary": service.summary(tenant_id=_tid()),
            "admin_callback": service.admin_callback_status(),
        }
    )
