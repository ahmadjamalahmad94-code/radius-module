"""Fleet-level read-only provisioning dashboard helpers for Setup Wizard."""
from __future__ import annotations

import ipaddress
import os
from typing import Any

from ..db.connection import db
from ..db.helpers import row_to_dict
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_recovery import SetupWizardRecoveryService
from .setup_wizard_router_provisioning import (
    SETUP_SERVER_IP_ENV,
    SETUP_VPN_POOL_ENV,
)
from .setup_wizard_support import mask_secrets
from .wg_peer_manager import SERVER_IP_DEFAULT, SERVER_IP_ENV, SUBNET_DEFAULT, SUBNET_ENV


ACTION_STATES = {
    "waiting_router_key",
    "peer_ready",
    "failed",
    "radius_pending",
    "api_pending",
}


class RouterFleetProvisioningService:
    def __init__(self, *, recovery_service: SetupWizardRecoveryService | None = None) -> None:
        self.recovery_service = recovery_service

    def summary(
        self,
        *,
        tenant_id: int,
        status: str = "",
        lifecycle_state: str = "",
        failed_only: bool = False,
        include_retired: bool = True,
        search: str = "",
    ) -> dict[str, Any]:
        routers = self.list_routers(
            tenant_id=tenant_id,
            status=status,
            lifecycle_state=lifecycle_state,
            failed_only=failed_only,
            include_retired=include_retired,
            search=search,
        )
        return mask_secrets(
            {
                "filters": {
                    "status": status,
                    "lifecycle_state": lifecycle_state,
                    "failed_only": failed_only,
                    "include_retired": include_retired,
                    "search": search,
                },
                "metrics": self._metrics(routers),
                "allocation_usage": self.allocation_usage(tenant_id=tenant_id),
                "health_summary": self._health_summary(routers),
                "recent_failures": [r for r in routers if r["lifecycle_state"] == "failed"][:10],
                "action_needed": [r for r in routers if r["needs_action"]][:25],
                "routers": routers,
            }
        )

    def list_routers(
        self,
        *,
        tenant_id: int,
        status: str = "",
        lifecycle_state: str = "",
        failed_only: bool = False,
        include_retired: bool = True,
        search: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["r.tenant_id=?"]
        params: list[Any] = [int(tenant_id)]
        if status:
            clauses.append("r.status=?")
            params.append(status)
        if lifecycle_state:
            clauses.append("COALESCE(NULLIF(r.lifecycle_state, ''), r.status)=?")
            params.append(lifecycle_state)
        if failed_only:
            clauses.append("(r.status='failed' OR r.lifecycle_state='failed' OR r.failure_reason <> '')")
        if not include_retired:
            clauses.append("r.status <> 'retired' AND COALESCE(NULLIF(r.lifecycle_state, ''), r.status) <> 'retired'")
        if search:
            clauses.append("(r.router_label LIKE ? OR r.router_identity LIKE ? OR r.router_vpn_ip LIKE ? OR r.wireguard_peer_name LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern, pattern])
        sql = f"""
            SELECT
              r.id, r.tenant_id, r.wizard_run_id, r.router_label, r.router_identity,
              r.status, r.vpn_pool_cidr, r.router_vpn_ip, r.server_vpn_ip,
              r.wireguard_interface_name, r.wireguard_peer_name,
              r.api_username, r.allocation_index, r.created_at, r.updated_at,
              r.retired_at, r.lifecycle_state, r.failure_reason, r.lifecycle_updated_at,
              r.tentative_started_at, r.tentative_expires_at,
              r.tentative_reclaimed_at, r.tentative_reclaim_reason,
              p.status AS peer_status,
              p.router_public_key_masked,
              p.allowed_ips,
              w.verification_status_json,
              w.current_step,
              w.last_error
            FROM router_provisioning_registry r
            LEFT JOIN prepared_wireguard_peers p
              ON p.id = (
                SELECT id FROM prepared_wireguard_peers
                WHERE tenant_id=r.tenant_id AND registry_id=r.id
                ORDER BY id DESC LIMIT 1
              )
            LEFT JOIN setup_wizard_runs w
              ON w.tenant_id=r.tenant_id AND w.id=r.wizard_run_id
            WHERE {" AND ".join(clauses)}
            ORDER BY r.id DESC
        """
        rows = db().execute(sql, tuple(params)).fetchall()
        return [self._row_to_router(row) for row in rows]

    def router_detail(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        routers = self.list_routers(tenant_id=tenant_id, include_retired=True)
        router = next((item for item in routers if int(item["id"]) == int(registry_id)), None)
        if not router:
            raise SetupWizardValidationError("router provisioning entry not found")
        events = db().execute(
            """
            SELECT from_state, to_state, event_type, actor, reason, metadata_json, created_at
            FROM router_lifecycle_events
            WHERE tenant_id=? AND registry_id=?
            ORDER BY id ASC
            """,
            (int(tenant_id), int(registry_id)),
        ).fetchall()
        recovery = None
        if self.recovery_service and router.get("wizard_run_id"):
            recovery = self.recovery_service.analyze(
                tenant_id=tenant_id,
                run_id=int(router["wizard_run_id"]),
            )
        return mask_secrets(
            {
                "router": router,
                "lifecycle_events": [row_to_dict(row) for row in events],
                "recovery": recovery,
            }
        )

    def resume_router(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        detail = self.router_detail(tenant_id=tenant_id, registry_id=registry_id)
        run_id = int((detail.get("router") or {}).get("wizard_run_id") or 0)
        if not run_id:
            return {"status": "blocked", "reason": "wizard_run_missing"}
        if not self.recovery_service:
            return {"status": "blocked", "reason": "recovery_service_unavailable"}
        return self.recovery_service.resume(tenant_id=tenant_id, run_id=run_id)

    def retire_router(self, *, tenant_id: int, registry_id: int, reason: str) -> dict[str, Any]:
        detail = self.router_detail(tenant_id=tenant_id, registry_id=registry_id)
        run_id = int((detail.get("router") or {}).get("wizard_run_id") or 0)
        if not run_id:
            return {"status": "blocked", "reason": "wizard_run_missing"}
        if not self.recovery_service:
            return {"status": "blocked", "reason": "recovery_service_unavailable"}
        return self.recovery_service.retire_router(tenant_id=tenant_id, run_id=run_id, reason=reason)

    def allocation_usage(self, *, tenant_id: int) -> dict[str, Any]:
        pool = _pool_from_env()
        server_ip = _server_ip_from_env(pool)
        rows = db().execute(
            """
            SELECT ip_address FROM router_ip_allocations
            WHERE tenant_id=? AND pool_name=? AND status IN ('reserved', 'active')
            ORDER BY id ASC
            """,
            (int(tenant_id), str(pool)),
        ).fetchall()
        used = {str(row["ip_address"]) for row in rows}
        next_available = ""
        for host in pool.hosts():
            ip_text = str(host)
            if host == server_ip or ip_text in used:
                continue
            next_available = ip_text
            break
        usable_router_ips = max(pool.num_addresses - 3, 0)
        used_count = len(used)
        return {
            "vpn_pool_cidr": str(pool),
            "server_vpn_ip": str(server_ip),
            "used": used_count,
            "remaining": max(usable_router_ips - used_count, 0),
            "capacity": usable_router_ips,
            "next_available": next_available,
        }

    def _row_to_router(self, row: Any) -> dict[str, Any]:
        data = row_to_dict(row)
        lifecycle = str(data.get("lifecycle_state") or data.get("status") or "reserved")
        peer_status = str(data.get("peer_status") or "")
        health = _health_for(lifecycle, peer_status)
        needs_action = lifecycle in ACTION_STATES or bool(data.get("failure_reason") or data.get("last_error"))
        return mask_secrets(
            {
                "id": int(data["id"]),
                "wizard_run_id": int(data["wizard_run_id"]) if data.get("wizard_run_id") is not None else None,
                "router_label": data.get("router_label") or f"Router #{data['id']}",
                "router_identity": data.get("router_identity") or "",
                "status": data.get("status") or "",
                "lifecycle_state": lifecycle,
                "router_vpn_ip": data.get("router_vpn_ip") or "",
                "server_vpn_ip": data.get("server_vpn_ip") or "",
                "wireguard_interface_name": data.get("wireguard_interface_name") or "",
                "wireguard_peer_name": data.get("wireguard_peer_name") or "",
                "peer_status": peer_status,
                "router_public_key_masked": data.get("router_public_key_masked") or "",
                "allowed_ips": data.get("allowed_ips") or "",
                "api_username": data.get("api_username") or "",
                "allocation_index": int(data.get("allocation_index") or 0),
                "created_at": data.get("created_at") or "",
                "updated_at": data.get("updated_at") or "",
                "retired_at": data.get("retired_at") or "",
                "lifecycle_updated_at": data.get("lifecycle_updated_at") or "",
                "failure_reason": data.get("failure_reason") or data.get("last_error") or "",
                "last_verification": _last_verification(data.get("verification_status_json")),
                "current_step": data.get("current_step") or "",
                "health": health,
                "needs_action": needs_action,
                "next_action": _next_action(lifecycle, peer_status, needs_action),
                "tentative_started_at": data.get("tentative_started_at") or "",
                "tentative_expires_at": data.get("tentative_expires_at") or "",
                "tentative_reclaimed_at": data.get("tentative_reclaimed_at") or "",
                "tentative_reclaim_reason": data.get("tentative_reclaim_reason") or "",
                "is_tentative": bool(
                    data.get("tentative_expires_at")
                    and not data.get("tentative_reclaimed_at")
                ),
                "is_reclaimed": bool(data.get("tentative_reclaimed_at")),
            }
        )

    @staticmethod
    def _metrics(routers: list[dict[str, Any]]) -> dict[str, int]:
        metrics = {
            "total_routers": len(routers),
            "reserved": 0,
            "waiting_key": 0,
            "peer_ready": 0,
            "vpn_verified": 0,
            "fully_onboarded": 0,
            "failed": 0,
            "retired": 0,
        }
        for router in routers:
            state = str(router.get("lifecycle_state") or "")
            status = str(router.get("status") or "")
            if state == "reserved" or status == "reserved":
                metrics["reserved"] += 1
            if state == "waiting_router_key":
                metrics["waiting_key"] += 1
            if state == "peer_ready":
                metrics["peer_ready"] += 1
            if state == "vpn_verified":
                metrics["vpn_verified"] += 1
            if state == "fully_onboarded":
                metrics["fully_onboarded"] += 1
            if state == "failed" or status == "failed":
                metrics["failed"] += 1
            if state == "retired" or status == "retired":
                metrics["retired"] += 1
        return metrics

    @staticmethod
    def _health_summary(routers: list[dict[str, Any]]) -> dict[str, int]:
        out = {"healthy": 0, "stale": 0, "missing_handshake": 0, "not_verified": 0}
        for router in routers:
            status = str((router.get("health") or {}).get("status") or "not_verified")
            if status in out:
                out[status] += 1
            else:
                out["not_verified"] += 1
        return out


def _pool_from_env() -> ipaddress.IPv4Network:
    raw = os.environ.get(SETUP_VPN_POOL_ENV) or os.environ.get(SUBNET_ENV) or SUBNET_DEFAULT
    return ipaddress.ip_network(str(raw).strip(), strict=False)


def _server_ip_from_env(pool: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    raw = os.environ.get(SETUP_SERVER_IP_ENV) or os.environ.get(SERVER_IP_ENV) or SERVER_IP_DEFAULT
    return ipaddress.IPv4Address(str(raw).strip())


def _last_verification(value: Any) -> dict[str, Any]:
    try:
        data = value if isinstance(value, dict) else __import__("json").loads(value or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict) or not data:
        return {}
    latest_key = ""
    latest_value: dict[str, Any] = {}
    for key, item in data.items():
        if isinstance(item, dict):
            latest_key = str(key)
            latest_value = item
    return {"step_key": latest_key, **latest_value} if latest_key else {}


def _health_for(lifecycle_state: str, peer_status: str) -> dict[str, Any]:
    if lifecycle_state in {"fully_onboarded", "api_verified", "radius_verified", "vpn_verified"}:
        return {"status": "healthy", "score": 90, "label_ar": "سليم"}
    if lifecycle_state == "failed":
        return {"status": "stale", "score": 20, "label_ar": "متعثر"}
    if peer_status in {"ready_to_apply", "applied"} or lifecycle_state == "peer_ready":
        return {"status": "missing_handshake", "score": 45, "label_ar": "ينتظر handshake"}
    return {"status": "not_verified", "score": 10, "label_ar": "لم يتم التحقق"}


def _next_action(lifecycle_state: str, peer_status: str, needs_action: bool) -> str:
    if lifecycle_state == "waiting_router_key":
        return "submit_router_public_key"
    if lifecycle_state == "failed":
        return "open_recovery"
    if lifecycle_state == "peer_ready" or peer_status in {"ready_to_apply", "applied"}:
        return "verify_peer"
    if needs_action:
        return "review"
    return "continue"
