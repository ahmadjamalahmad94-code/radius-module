"""مزامنة الترخيص والتخصيصات من لوحة التراخيص المركزية.

يستدعي ``POST /api/integration/hoberadius/service-activations/poll``
بنفس آلية admin_panel_client ويحدّث جدولَي ``license_snapshot``
و ``service_allocation_mirror`` في SQLite المحلية.

الاستخدام:
    flask sync-license              # من cron/systemd (كل 5 دقائق)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)

SERVICE_ACTIVATIONS_POLL_PATH = "/api/integration/hoberadius/service-activations/poll"
USAGE_SNAPSHOT_PUSH_PATH      = "/api/integration/hoberadius/usage-snapshot/push"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync(tenant_id: int = 1) -> dict[str, Any]:
    """يزامن license_snapshot + service_allocation_mirror.

    يُعيد ``{"ok": True, "allocations_synced": N}`` أو
    ``{"ok": False, "reason": "..."}``.
    """
    from .admin_panel_client import AdminBridgeConfig, AdminPanelClient, INSTANCE_HEARTBEAT_PATH

    config = AdminBridgeConfig.from_env()
    if not config.enabled:
        return {"ok": False, "reason": "bridge_disabled"}
    missing = config.missing_fields()
    if missing:
        return {"ok": False, "reason": "bridge_not_configured", "missing": missing}

    client = AdminPanelClient(config=config)
    url = f"{config.base_url.rstrip('/')}{SERVICE_ACTIVATIONS_POLL_PATH}"
    try:
        response = client.transport.request_json(
            method="POST",
            url=url,
            headers=client._headers(),
            json_body=client._license_check_payload(),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception as exc:
        LOG.warning("license_sync: unreachable: %s", exc)
        return {"ok": False, "reason": "unreachable", "detail": str(exc)}

    if not isinstance(response, dict) or not response.get("ok"):
        LOG.warning("license_sync: bad response status=%s", response.get("status") if isinstance(response, dict) else "?")
        return {"ok": False, "reason": (response.get("status") if isinstance(response, dict) else "bad_response")}

    # ── license_snapshot ──
    snap = response.get("license_snapshot") or {}
    if snap:
        _upsert_license_snapshot(tenant_id, snap)

    # ── service_allocation_mirror ──
    allocs = response.get("allocations") or []
    synced = _sync_allocations(tenant_id, allocs)

    # ── heartbeat (بعد التزامن) ──
    _send_heartbeat(client, config)

    # ── usage snapshot push (غير حرج — فشله لا يوقف المزامنة) ──
    _push_usage_snapshot(client, config, tenant_id)

    return {
        "ok": True,
        "allocations_synced": synced,
        "license_status": response.get("status"),
    }


def _upsert_license_snapshot(tenant_id: int, snap: dict) -> None:
    from ..db.connection import db, transaction

    payload_hash = hashlib.sha256(
        json.dumps(snap, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    now = _now()

    existing = db().execute(
        "SELECT id FROM license_snapshot WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
        (tenant_id,),
    ).fetchone()

    if existing:
        with transaction() as conn:
            conn.execute(
                """UPDATE license_snapshot SET
                   remote_license_id=?, plan_name=?,
                   max_subscribers=?, max_cards=?, max_active_users=?, max_routers=?,
                   license_status=?, starts_at=?, expires_at=?,
                   synced_at=?, payload_hash=?, updated_at=?
                   WHERE id=?""",
                (
                    snap.get("remote_license_id", 0),
                    snap.get("plan_name", ""),
                    snap.get("max_subscribers"),
                    snap.get("max_cards"),
                    snap.get("max_active_users"),
                    snap.get("max_routers"),
                    snap.get("license_status", "active"),
                    snap.get("starts_at"),
                    snap.get("expires_at"),
                    now, payload_hash, now, existing["id"],
                ),
            )
    else:
        with transaction() as conn:
            conn.execute(
                """INSERT INTO license_snapshot
                   (tenant_id, remote_license_id, plan_name,
                    max_subscribers, max_cards, max_active_users, max_routers,
                    license_status, starts_at, expires_at,
                    synced_at, payload_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    snap.get("remote_license_id", 0),
                    snap.get("plan_name", ""),
                    snap.get("max_subscribers"),
                    snap.get("max_cards"),
                    snap.get("max_active_users"),
                    snap.get("max_routers"),
                    snap.get("license_status", "active"),
                    snap.get("starts_at"),
                    snap.get("expires_at"),
                    now, payload_hash, now, now,
                ),
            )


def _sync_allocations(tenant_id: int, allocs: list[dict]) -> int:
    """يزامن service_allocation_mirror ويُعيد عدد التخصيصات التي تغيّرت."""
    from ..db.connection import db, transaction

    changed = 0
    now = _now()
    for a in allocs:
        remote_id = a.get("id")
        if not remote_id:
            continue
        payload_hash = hashlib.sha256(
            json.dumps(a, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

        existing = db().execute(
            "SELECT id, payload_hash FROM service_allocation_mirror "
            "WHERE tenant_id=? AND remote_allocation_id=?",
            (tenant_id, remote_id),
        ).fetchone()

        if existing:
            if existing["payload_hash"] == payload_hash:
                continue
            with transaction() as conn:
                conn.execute(
                    """UPDATE service_allocation_mirror SET
                       service_type=?, status=?, chr_node_name=?, chr_node_public_ip=?,
                       speed_limit_mbps=?, max_accounts=?, max_peers=?,
                       transfer_limit_bytes=?, expires_at=?,
                       synced_at=?, payload_hash=?, updated_at=?
                       WHERE id=?""",
                    (
                        a.get("service_type", ""),
                        a.get("status", "pending"),
                        a.get("chr_node_name", ""),
                        a.get("chr_node_public_ip", ""),
                        a.get("speed_limit_mbps", 0),
                        a.get("max_accounts", 0),
                        a.get("max_peers", 0),
                        a.get("transfer_limit_bytes"),
                        a.get("expires_at"),
                        now, payload_hash, now, existing["id"],
                    ),
                )
        else:
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO service_allocation_mirror
                       (tenant_id, remote_allocation_id, service_type, status,
                        chr_node_name, chr_node_public_ip,
                        speed_limit_mbps, max_accounts, max_peers,
                        transfer_limit_bytes, expires_at,
                        synced_at, payload_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tenant_id, remote_id,
                        a.get("service_type", ""),
                        a.get("status", "pending"),
                        a.get("chr_node_name", ""),
                        a.get("chr_node_public_ip", ""),
                        a.get("speed_limit_mbps", 0),
                        a.get("max_accounts", 0),
                        a.get("max_peers", 0),
                        a.get("transfer_limit_bytes"),
                        a.get("expires_at"),
                        now, payload_hash, now, now,
                    ),
                )
        changed += 1
    return changed


def _push_usage_snapshot(client: Any, config: Any, tenant_id: int) -> None:
    """يُرسل usage snapshot إلى لوحة التراخيص (fire-and-forget).

    لا يُطلق استثناءات للخارج أبدًا — فشله لا يوقف المزامنة الرئيسية.

    ما يحتويه الـ payload (آمن — لا أسرار):
      - remote_allocation_id, service_type, active_accounts, active_peers
      - used_transfer_bytes, current_mbps, health_status, overall_health
    لا يُرسَل: كلمات مرور، مفاتيح WireGuard، أسرار HMAC، بيانات مستخدمين.
    """
    url = f"{config.base_url.rstrip('/')}{USAGE_SNAPSHOT_PUSH_PATH}"
    try:
        from .monitoring_service import build_usage_snapshot_payload
        snapshot_data = build_usage_snapshot_payload(tenant_id)
        payload = client._license_check_payload(snapshot_data)
        client.transport.request_json(
            method="POST",
            url=url,
            headers=client._headers(),
            json_body=payload,
            timeout_seconds=config.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        # Non-critical: admin panel may be unreachable, DB may be unready.
        # Log at DEBUG so operators can diagnose without log noise in prod.
        LOG.debug("_push_usage_snapshot: non-critical failure: %s", exc)


def _send_heartbeat(client: Any, config: Any) -> None:
    from .admin_panel_client import INSTANCE_HEARTBEAT_PATH
    url = f"{config.base_url.rstrip('/')}{INSTANCE_HEARTBEAT_PATH}"
    try:
        client.transport.request_json(
            method="POST",
            url=url,
            headers=client._headers(),
            json_body=client._license_check_payload(),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception:
        pass  # heartbeat failure is non-critical


def get_current_snapshot(tenant_id: int = 1) -> dict | None:
    from ..db.connection import db
    row = db().execute(
        "SELECT * FROM license_snapshot WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
        (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def get_active_allocations(tenant_id: int = 1, service_type: str | None = None) -> list[dict]:
    from ..db.connection import db
    if service_type:
        rows = db().execute(
            "SELECT * FROM service_allocation_mirror "
            "WHERE tenant_id=? AND status='active' AND service_type=? "
            "ORDER BY id",
            (tenant_id, service_type),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM service_allocation_mirror "
            "WHERE tenant_id=? AND status='active' ORDER BY id",
            (tenant_id,),
        ).fetchall()
    return [dict(r) for r in rows]
