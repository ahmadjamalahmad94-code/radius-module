"""Router provisioning registry for Setup Wizard VPN/RADIUS onboarding.

This layer only reserves identifiers and masked secret references. It does not
write WireGuard peers, mutate MikroTik devices, or store plaintext secrets.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import row_to_dict
from .setup_wizard_common import SetupWizardValidationError
from .wg_peer_manager import SERVER_IP_DEFAULT, SERVER_IP_ENV, SUBNET_DEFAULT, SUBNET_ENV


SETUP_VPN_POOL_ENV = "HOBERADIUS_SETUP_WIZARD_VPN_POOL"
SETUP_SERVER_IP_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP"
SERVER_ENDPOINT_ENV = "HOBERADIUS_WG_SERVER_ENDPOINT"
DEFAULT_ENDPOINT_HOST = "187.77.70.18"
DEFAULT_ENDPOINT_PORT = 51820

ACTIVE_STATUSES = {"reserved", "generated", "applied", "verified"}


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _safe_slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    return (safe or fallback)[:80]


def _pool_from_env() -> ipaddress.IPv4Network:
    raw = (
        os.environ.get(SETUP_VPN_POOL_ENV)
        or os.environ.get(SUBNET_ENV)
        or SUBNET_DEFAULT
    )
    try:
        pool = ipaddress.ip_network(str(raw).strip(), strict=False)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{SETUP_VPN_POOL_ENV} must be valid IPv4 CIDR") from exc
    if pool.version != 4:
        raise SetupWizardValidationError(f"{SETUP_VPN_POOL_ENV} must be IPv4 CIDR")
    return pool


def _server_ip_from_env(pool: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    raw = (
        os.environ.get(SETUP_SERVER_IP_ENV)
        or os.environ.get(SERVER_IP_ENV)
        or SERVER_IP_DEFAULT
    )
    try:
        server_ip = ipaddress.IPv4Address(str(raw).strip())
    except ValueError as exc:
        raise SetupWizardValidationError(f"{SETUP_SERVER_IP_ENV} must be valid IPv4") from exc
    if server_ip not in pool:
        raise SetupWizardValidationError("server VPN IP must be inside setup wizard VPN pool")
    return server_ip


def _endpoint_defaults() -> tuple[str, int]:
    raw = str(os.environ.get(SERVER_ENDPOINT_ENV) or "").strip()
    if raw:
        host, _, port = raw.partition(":")
        if host:
            try:
                endpoint_port = int(port or DEFAULT_ENDPOINT_PORT)
            except ValueError:
                endpoint_port = DEFAULT_ENDPOINT_PORT
            return host, endpoint_port
    return DEFAULT_ENDPOINT_HOST, DEFAULT_ENDPOINT_PORT


def _masked_ref(prefix: str, index: int) -> str:
    return f"{prefix}-{index:04d}"


@dataclass(frozen=True)
class RouterProvisioningReservation:
    id: int
    tenant_id: int
    wizard_run_id: int | None
    router_label: str
    router_identity: str
    status: str
    vpn_pool_cidr: str
    router_vpn_ip: str
    server_vpn_ip: str
    wireguard_interface_name: str
    wireguard_peer_name: str
    wireguard_public_key: str
    wireguard_private_key_ref: str
    radius_secret_ref: str
    api_username: str
    api_password_ref: str
    allocation_index: int
    created_at: str
    updated_at: str
    retired_at: str
    lifecycle_state: str = "reserved"
    failure_reason: str = ""
    lifecycle_updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "RouterProvisioningReservation":
        data = row_to_dict(row)
        return cls(
            id=int(data["id"]),
            tenant_id=int(data["tenant_id"]),
            wizard_run_id=int(data["wizard_run_id"]) if data.get("wizard_run_id") is not None else None,
            router_label=str(data.get("router_label") or ""),
            router_identity=str(data.get("router_identity") or ""),
            status=str(data.get("status") or "reserved"),
            vpn_pool_cidr=str(data.get("vpn_pool_cidr") or ""),
            router_vpn_ip=str(data.get("router_vpn_ip") or ""),
            server_vpn_ip=str(data.get("server_vpn_ip") or ""),
            wireguard_interface_name=str(data.get("wireguard_interface_name") or "hr-wg"),
            wireguard_peer_name=str(data.get("wireguard_peer_name") or ""),
            wireguard_public_key=str(data.get("wireguard_public_key") or ""),
            wireguard_private_key_ref=str(data.get("wireguard_private_key_ref") or ""),
            radius_secret_ref=str(data.get("radius_secret_ref") or ""),
            api_username=str(data.get("api_username") or ""),
            api_password_ref=str(data.get("api_password_ref") or ""),
            allocation_index=int(data.get("allocation_index") or 0),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            retired_at=str(data.get("retired_at") or ""),
            lifecycle_state=str(data.get("lifecycle_state") or data.get("status") or "reserved"),
            failure_reason=str(data.get("failure_reason") or ""),
            lifecycle_updated_at=str(data.get("lifecycle_updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "wizard_run_id": self.wizard_run_id,
            "router_label": self.router_label,
            "router_identity": self.router_identity,
            "status": self.status,
            "vpn_pool_cidr": self.vpn_pool_cidr,
            "router_vpn_ip": self.router_vpn_ip,
            "server_vpn_ip": self.server_vpn_ip,
            "wireguard_interface_name": self.wireguard_interface_name,
            "wireguard_peer_name": self.wireguard_peer_name,
            "wireguard_public_key": self.wireguard_public_key,
            "wireguard_private_key_ref": self.wireguard_private_key_ref,
            "radius_secret_ref": self.radius_secret_ref,
            "api_username": self.api_username,
            "api_password_ref": self.api_password_ref,
            "allocation_index": self.allocation_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retired_at": self.retired_at,
            "lifecycle_state": self.lifecycle_state,
            "failure_reason": self.failure_reason,
            "lifecycle_updated_at": self.lifecycle_updated_at,
            "masked_sensitive_values": {
                "wireguard_private_key": "***",
                "radius_secret": "***",
                "api_password": "***",
            },
        }


class VpnIpAllocator:
    def next_available(
        self,
        *,
        conn,
        tenant_id: int,
        pool: ipaddress.IPv4Network,
        server_ip: ipaddress.IPv4Address,
    ) -> tuple[ipaddress.IPv4Address, int]:
        rows = conn.execute(
            """
            SELECT ip_address FROM router_ip_allocations
            WHERE tenant_id=? AND pool_name=? AND status IN ('reserved', 'active')
            """,
            (int(tenant_id), str(pool)),
        ).fetchall()
        reserved = {ipaddress.IPv4Address(str(row["ip_address"])) for row in rows}
        reserved.add(server_ip)
        for offset, host in enumerate(pool.hosts(), start=1):
            if host == server_ip or host in reserved:
                continue
            allocation_index = int(host) - int(pool.network_address) - 1
            return host, allocation_index
        raise SetupWizardValidationError("setup wizard VPN pool is exhausted")


class RouterCredentialPlanner:
    def plan(self, *, registry_id: int | None, allocation_index: int) -> dict[str, str]:
        suffix = f"{int(allocation_index):04d}"
        return {
            "wireguard_peer_name": f"hr-peer-{suffix}",
            "wireguard_interface_name": "hr-wg",
            "wireguard_private_key_ref": _masked_ref("wg-private-key-ref", allocation_index),
            "radius_secret_ref": _masked_ref("radius-secret-ref", allocation_index),
            "api_username": f"hr-api-{suffix}",
            "api_password_ref": _masked_ref("api-password-ref", allocation_index),
        }


class RouterProvisioningService:
    def __init__(
        self,
        *,
        allocator: VpnIpAllocator | None = None,
        credential_planner: RouterCredentialPlanner | None = None,
    ) -> None:
        self._allocator = allocator or VpnIpAllocator()
        self._credential_planner = credential_planner or RouterCredentialPlanner()

    def reserve_for_run(
        self,
        *,
        tenant_id: int,
        wizard_run_id: int,
        router_label: str = "",
        router_identity: str = "",
        force_new: bool = False,
    ) -> dict[str, Any]:
        pool = _pool_from_env()
        server_ip = _server_ip_from_env(pool)
        label = _safe_slug(router_label, f"router-{wizard_run_id}")
        identity = _safe_slug(router_identity, label)
        now = _now()
        with transaction() as conn:
            if not force_new:
                existing = conn.execute(
                    """
                    SELECT * FROM router_provisioning_registry
                    WHERE tenant_id=? AND wizard_run_id=? AND status IN ('reserved', 'generated', 'applied', 'verified')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(tenant_id), int(wizard_run_id)),
                ).fetchone()
                if existing:
                    return RouterProvisioningReservation.from_row(existing).to_dict()
            else:
                existing = conn.execute(
                    """
                    SELECT id FROM router_provisioning_registry
                    WHERE tenant_id=? AND wizard_run_id=? AND status IN ('reserved', 'generated', 'applied', 'verified')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(tenant_id), int(wizard_run_id)),
                ).fetchone()
                if existing:
                    raise SetupWizardValidationError(
                        "release the existing router provisioning reservation before generating a new one"
                    )

            router_ip, allocation_index = self._allocator.next_available(
                conn=conn,
                tenant_id=int(tenant_id),
                pool=pool,
                server_ip=server_ip,
            )
            creds = self._credential_planner.plan(
                registry_id=None,
                allocation_index=allocation_index,
            )
            cur = conn.execute(
                """
                INSERT INTO router_provisioning_registry (
                  tenant_id, wizard_run_id, router_label, router_identity, status,
                  vpn_pool_cidr, router_vpn_ip, server_vpn_ip,
                  wireguard_interface_name, wireguard_peer_name, wireguard_public_key,
                  wireguard_private_key_ref, radius_secret_ref,
                  api_username, api_password_ref, allocation_index,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    int(wizard_run_id),
                    label,
                    identity,
                    str(pool),
                    str(router_ip),
                    str(server_ip),
                    creds["wireguard_interface_name"],
                    creds["wireguard_peer_name"],
                    "pending-router-public-key",
                    creds["wireguard_private_key_ref"],
                    creds["radius_secret_ref"],
                    creds["api_username"],
                    creds["api_password_ref"],
                    allocation_index,
                    now,
                    now,
                ),
            )
            registry_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO router_ip_allocations (
                  registry_id, tenant_id, pool_name, ip_address, allocation_type,
                  status, created_at
                ) VALUES (?, ?, ?, ?, 'router_vpn', 'reserved', ?)
                """,
                (registry_id, int(tenant_id), str(pool), str(router_ip), now),
            )
            conn.execute(
                """
                UPDATE setup_wizard_runs
                SET generated_vpn_ip=?, generated_router_vpn_ip=?,
                    generated_radius_secret_ref=?, generated_api_username=?,
                    updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (
                    str(server_ip),
                    str(router_ip),
                    creds["radius_secret_ref"],
                    creds["api_username"],
                    now,
                    int(tenant_id),
                    int(wizard_run_id),
                ),
            )
            row = conn.execute(
                "SELECT * FROM router_provisioning_registry WHERE id=?",
                (registry_id,),
            ).fetchone()
            return RouterProvisioningReservation.from_row(row).to_dict()

    def latest_for_run(self, *, tenant_id: int, wizard_run_id: int) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM router_provisioning_registry
            WHERE tenant_id=? AND wizard_run_id=? AND status IN ('reserved', 'generated', 'applied', 'verified')
            ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id), int(wizard_run_id)),
        ).fetchone()
        return RouterProvisioningReservation.from_row(row).to_dict() if row else None

    def mark_generated(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE router_provisioning_registry
                SET status='generated', updated_at=?
                WHERE tenant_id=? AND id=? AND status='reserved'
                """,
                (now, int(tenant_id), int(registry_id)),
            )
            row = conn.execute(
                "SELECT * FROM router_provisioning_registry WHERE tenant_id=? AND id=?",
                (int(tenant_id), int(registry_id)),
            ).fetchone()
        if not row:
            raise SetupWizardValidationError("router provisioning reservation not found")
        return RouterProvisioningReservation.from_row(row).to_dict()

    def release_reservation(self, *, tenant_id: int, registry_id: int, reason: str = "") -> dict[str, Any]:
        now = _now()
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM router_provisioning_registry WHERE tenant_id=? AND id=?",
                (int(tenant_id), int(registry_id)),
            ).fetchone()
            if not row:
                raise SetupWizardValidationError("router provisioning reservation not found")
            status = str(row["status"] or "")
            if status not in {"reserved", "generated", "failed"}:
                raise SetupWizardValidationError("only reserved/generated/failed provisioning can be released")
            conn.execute(
                """
                UPDATE router_provisioning_registry
                SET status='retired', lifecycle_state='retired',
                    retired_at=?, updated_at=?, lifecycle_updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (now, now, now, int(tenant_id), int(registry_id)),
            )
            conn.execute(
                """
                UPDATE router_ip_allocations
                SET status='released', released_at=?
                WHERE tenant_id=? AND registry_id=? AND status IN ('reserved', 'active')
                """,
                (now, int(tenant_id), int(registry_id)),
            )
            released = conn.execute(
                "SELECT * FROM router_provisioning_registry WHERE tenant_id=? AND id=?",
                (int(tenant_id), int(registry_id)),
            ).fetchone()
        return RouterProvisioningReservation.from_row(released).to_dict()

    def endpoint_defaults(self) -> dict[str, Any]:
        host, port = _endpoint_defaults()
        return {"vps_public_endpoint": host, "endpoint_port": port}
