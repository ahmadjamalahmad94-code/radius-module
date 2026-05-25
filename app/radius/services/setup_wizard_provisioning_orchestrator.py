"""Provisioning orchestration for Setup Wizard router onboarding.

The orchestrator coordinates reservations, lifecycle, prepared WireGuard peer
records, and recovery. It deliberately does not apply router or VPS changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import row_to_dict
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_router_lifecycle import RouterLifecycleService
from .setup_wizard_router_provisioning import RouterProvisioningService


PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _mask_key(public_key: str) -> str:
    key = str(public_key or "").strip()
    if len(key) < 12:
        return "***"
    return f"{key[:6]}...{key[-6:]}"


@dataclass(frozen=True)
class PreparedWireGuardPeer:
    id: int
    tenant_id: int
    registry_id: int
    wizard_run_id: int | None
    peer_name: str
    router_vpn_ip: str
    server_vpn_ip: str
    router_public_key_masked: str
    server_public_key: str
    server_private_key_ref: str
    allowed_ips: str
    listen_port: int
    status: str
    error_message: str
    created_at: str
    updated_at: str
    retired_at: str

    @classmethod
    def from_row(cls, row: Any) -> "PreparedWireGuardPeer":
        data = row_to_dict(row)
        return cls(
            id=int(data["id"]),
            tenant_id=int(data["tenant_id"]),
            registry_id=int(data["registry_id"]),
            wizard_run_id=int(data["wizard_run_id"]) if data.get("wizard_run_id") is not None else None,
            peer_name=str(data.get("peer_name") or ""),
            router_vpn_ip=str(data.get("router_vpn_ip") or ""),
            server_vpn_ip=str(data.get("server_vpn_ip") or ""),
            router_public_key_masked=str(data.get("router_public_key_masked") or ""),
            server_public_key=str(data.get("server_public_key") or ""),
            server_private_key_ref=str(data.get("server_private_key_ref") or ""),
            allowed_ips=str(data.get("allowed_ips") or ""),
            listen_port=int(data.get("listen_port") or 51820),
            status=str(data.get("status") or "prepared"),
            error_message=str(data.get("error_message") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            retired_at=str(data.get("retired_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "registry_id": self.registry_id,
            "wizard_run_id": self.wizard_run_id,
            "peer_name": self.peer_name,
            "router_vpn_ip": self.router_vpn_ip,
            "server_vpn_ip": self.server_vpn_ip,
            "router_public_key_masked": self.router_public_key_masked,
            "server_public_key": self.server_public_key,
            "server_private_key_ref": self.server_private_key_ref,
            "allowed_ips": self.allowed_ips,
            "listen_port": self.listen_port,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retired_at": self.retired_at,
            "masked_sensitive_values": {
                "router_public_key": self.router_public_key_masked or "***",
                "server_private_key": "***",
            },
        }


class PreparedWireGuardPeerService:
    """Stores prepared server-side peer plans without applying them."""

    def latest_for_registry(self, *, tenant_id: int, registry_id: int) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM prepared_wireguard_peers
            WHERE tenant_id=? AND registry_id=?
              AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied')
            ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id), int(registry_id)),
        ).fetchone()
        return PreparedWireGuardPeer.from_row(row).to_dict() if row else None

    def latest_for_run(self, *, tenant_id: int, wizard_run_id: int) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM prepared_wireguard_peers
            WHERE tenant_id=? AND wizard_run_id=?
              AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied')
            ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id), int(wizard_run_id)),
        ).fetchone()
        return PreparedWireGuardPeer.from_row(row).to_dict() if row else None

    def prepare_from_reservation(
        self,
        *,
        tenant_id: int,
        reservation: dict[str, Any],
        listen_port: int = 51820,
    ) -> dict[str, Any]:
        existing = self.latest_for_registry(
            tenant_id=tenant_id,
            registry_id=int(reservation["id"]),
        )
        if existing:
            expected_allowed_ips = f'{reservation["router_vpn_ip"]}/32'
            if (
                str(existing.get("router_vpn_ip") or "") != str(reservation["router_vpn_ip"])
                or str(existing.get("allowed_ips") or "") != expected_allowed_ips
            ):
                now = _now()
                with transaction() as conn:
                    conn.execute(
                        """
                        UPDATE prepared_wireguard_peers
                        SET router_vpn_ip=?, server_vpn_ip=?, allowed_ips=?, updated_at=?
                        WHERE tenant_id=? AND id=?
                        """,
                        (
                            str(reservation["router_vpn_ip"]),
                            str(reservation["server_vpn_ip"]),
                            expected_allowed_ips,
                            now,
                            int(tenant_id),
                            int(existing["id"]),
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM prepared_wireguard_peers WHERE tenant_id=? AND id=?",
                        (int(tenant_id), int(existing["id"])),
                    ).fetchone()
                return PreparedWireGuardPeer.from_row(row).to_dict()
            return existing
        now = _now()
        allowed_ips = f'{reservation["router_vpn_ip"]}/32'
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO prepared_wireguard_peers (
                  tenant_id, registry_id, wizard_run_id, peer_name,
                  router_vpn_ip, server_vpn_ip, router_public_key,
                  router_public_key_masked, server_public_key,
                  server_private_key_ref, allowed_ips, listen_port,
                  status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', '', '', ?, ?, ?, 'waiting_router_key', ?, ?)
                """,
                (
                    int(tenant_id),
                    int(reservation["id"]),
                    reservation.get("wizard_run_id"),
                    str(reservation["wireguard_peer_name"]),
                    str(reservation["router_vpn_ip"]),
                    str(reservation["server_vpn_ip"]),
                    str(reservation.get("wireguard_private_key_ref") or "server-private-key-ref-pending"),
                    allowed_ips,
                    int(listen_port),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM prepared_wireguard_peers WHERE id=?",
                (int(cur.lastrowid),),
            ).fetchone()
        return PreparedWireGuardPeer.from_row(row).to_dict()

    def submit_router_public_key(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        public_key: str,
    ) -> dict[str, Any]:
        key = str(public_key or "").strip()
        if not PUBLIC_KEY_RE.match(key):
            raise SetupWizardValidationError("invalid WireGuard router public key format")
        now = _now()
        masked = _mask_key(key)
        with transaction() as conn:
            duplicate = conn.execute(
                """
                SELECT id FROM prepared_wireguard_peers
                WHERE tenant_id=? AND router_public_key=? AND registry_id<>?
                  AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied')
                LIMIT 1
                """,
                (int(tenant_id), key, int(registry_id)),
            ).fetchone()
            if duplicate:
                raise SetupWizardValidationError("Public key is already assigned to another router.")
            row = conn.execute(
                """
                SELECT * FROM prepared_wireguard_peers
                WHERE tenant_id=? AND registry_id=?
                  AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied')
                ORDER BY id DESC LIMIT 1
                """,
                (int(tenant_id), int(registry_id)),
            ).fetchone()
            if not row:
                raise SetupWizardValidationError("prepared WireGuard peer not found")
            conn.execute(
                """
                UPDATE prepared_wireguard_peers
                SET router_public_key=?, router_public_key_masked=?,
                    status='ready_to_apply', updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (key, masked, now, int(tenant_id), int(row["id"])),
            )
            updated = conn.execute(
                "SELECT * FROM prepared_wireguard_peers WHERE tenant_id=? AND id=?",
                (int(tenant_id), int(row["id"])),
            ).fetchone()
        return PreparedWireGuardPeer.from_row(updated).to_dict()

    def retire_for_registry(self, *, tenant_id: int, registry_id: int) -> None:
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE prepared_wireguard_peers
                SET status='retired', retired_at=?, updated_at=?
                WHERE tenant_id=? AND registry_id=?
                  AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied')
                """,
                (now, now, int(tenant_id), int(registry_id)),
            )


class RouterProvisioningOrchestrator:
    """Single coordination point for router onboarding state."""

    def __init__(
        self,
        *,
        registry: RouterProvisioningService | None = None,
        lifecycle: RouterLifecycleService | None = None,
        peer_service: PreparedWireGuardPeerService | None = None,
    ) -> None:
        self._registry = registry or RouterProvisioningService()
        self._lifecycle = lifecycle or RouterLifecycleService()
        self._peers = peer_service or PreparedWireGuardPeerService()

    def start_or_resume(
        self,
        *,
        tenant_id: int,
        wizard_run_id: int,
        router_label: str = "",
        router_identity: str = "",
    ) -> dict[str, Any]:
        reservation = self._registry.reserve_for_run(
            tenant_id=tenant_id,
            wizard_run_id=wizard_run_id,
            router_label=router_label,
            router_identity=router_identity,
        )
        return self.status(tenant_id=tenant_id, registry_id=int(reservation["id"]))

    def record_script_generated(
        self,
        *,
        tenant_id: int,
        reservation: dict[str, Any],
        listen_port: int = 51820,
    ) -> dict[str, Any]:
        registry_id = int(reservation["id"])
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state == "reserved":
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="script_generated",
                reason="vpn/radius script generated",
            )
        elif state not in {"script_generated", "waiting_router_key"}:
            raise SetupWizardValidationError(f"cannot generate router script from lifecycle state {state}")
        peer = self._peers.prepare_from_reservation(
            tenant_id=tenant_id,
            reservation=reservation,
            listen_port=listen_port,
        )
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state == "script_generated":
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="waiting_router_key",
                reason="prepared peer is waiting for router public key",
            )
        return self.status(tenant_id=tenant_id, registry_id=registry_id) | {
            "prepared_wireguard_peer": peer,
        }

    def submit_router_public_key(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        public_key: str,
        actor: str = "wizard",
    ) -> dict[str, Any]:
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state not in {"waiting_router_key", "router_key_received", "peer_pending", "peer_ready"}:
            raise SetupWizardValidationError("router public key can be submitted only while waiting for key")
        peer = self._peers.submit_router_public_key(
            tenant_id=tenant_id,
            registry_id=registry_id,
            public_key=public_key,
        )
        if state == "waiting_router_key":
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="router_key_received",
                actor=actor,
                reason="router public key received",
            )
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="peer_pending",
                actor=actor,
                reason="server peer plan is pending apply",
            )
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="peer_ready",
                actor=actor,
                reason="server peer plan ready to apply manually later",
            )
        return self.status(tenant_id=tenant_id, registry_id=registry_id) | {
            "prepared_wireguard_peer": peer,
        }

    def mark_vpn_verified(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        return self._lifecycle.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="vpn_verified",
            reason="vpn verification passed",
        )

    def mark_radius_verified(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state == "vpn_verified":
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="radius_pending",
                reason="radius verification started",
            )
        return self._lifecycle.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="radius_verified",
            reason="radius verification passed",
        )

    def mark_api_verified(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state == "radius_verified":
            self._lifecycle.transition(
                tenant_id=tenant_id,
                registry_id=registry_id,
                to_state="api_pending",
                reason="api verification started",
            )
        return self._lifecycle.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="api_verified",
            reason="api verification passed",
        )

    def complete(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        return self._lifecycle.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="fully_onboarded",
            reason="router fully onboarded",
        )

    def reissue_router_script(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        state = self._lifecycle.current_state(tenant_id=tenant_id, registry_id=registry_id)
        if state in {"failed", "retired", "fully_onboarded"}:
            raise SetupWizardValidationError("router script cannot be reissued from current lifecycle state")
        return self.status(tenant_id=tenant_id, registry_id=registry_id)

    def retire_router(self, *, tenant_id: int, registry_id: int, reason: str = "retired") -> dict[str, Any]:
        self._peers.retire_for_registry(tenant_id=tenant_id, registry_id=registry_id)
        registry = self._lifecycle.retire(
            tenant_id=tenant_id,
            registry_id=registry_id,
            reason=reason,
        )
        return {
            "registry": registry,
            "prepared_wireguard_peer": self._peers.latest_for_registry(
                tenant_id=tenant_id,
                registry_id=registry_id,
            ),
            "history": self._lifecycle.history(tenant_id=tenant_id, registry_id=registry_id),
        }

    def status(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        registry = self._lifecycle.get_registry(tenant_id=tenant_id, registry_id=registry_id)
        return {
            "registry": registry,
            "current_state": str(registry.get("lifecycle_state") or "reserved"),
            "prepared_wireguard_peer": self._peers.latest_for_registry(
                tenant_id=tenant_id,
                registry_id=registry_id,
            ),
            "history": self._lifecycle.history(tenant_id=tenant_id, registry_id=registry_id),
        }

    def status_for_run(self, *, tenant_id: int, wizard_run_id: int) -> dict[str, Any] | None:
        reservation = self._registry.latest_for_run(
            tenant_id=tenant_id,
            wizard_run_id=wizard_run_id,
        )
        if not reservation:
            return None
        return self.status(tenant_id=tenant_id, registry_id=int(reservation["id"]))
