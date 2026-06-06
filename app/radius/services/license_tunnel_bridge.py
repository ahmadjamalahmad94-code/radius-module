"""CHR tunnel consumer over the signed license bridge.

The license panel owns the CHR and is the source of truth for tunnels. This
service is a THIN CONSUMER:

  * request_tunnel  — ask the panel to provision a tunnel, surface the SSTP
    user/password ONCE for local injection, and store only metadata + a
    non-reversible secret fingerprint.
  * sync_tunnels    — capture the panel's tunnel list (incl. manual
    PPTP/L2TP/IPsec), apply lifecycle (revoked → delete, suspended → disable),
    then ACK the names stored so the panel stops re-sending passwords.

SECURITY (RADIUS is sold to customers): no raw CHR/tunnel secret is ever
persisted here. We never generate CHR credentials locally.
"""
from __future__ import annotations

from typing import Any

from app.radius.db.repos import bridge_tunnels_repo
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    AdminPanelClient,
)

# Keys we read out of a panel tunnel object (tolerant to minor naming).
_NAME_KEYS = ("name", "remote_name", "tunnel_name", "id")
_USER_KEYS = ("username", "user", "sstp_user", "ppp_user")
_PASS_KEYS = ("password", "sstp_password", "secret", "ppp_password")


def _first(obj: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        val = obj.get(key)
        if val not in (None, ""):
            return str(val)
    return default


class LicenseTunnelBridgeService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    # ── request ─────────────────────────────────────────────────────────────
    def request_tunnel(
        self,
        *,
        tenant_id: int = 1,
        tunnel_type: str = "sstp",
        router_id: int | str = "",
        label: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        result = self.admin_client.request_vpn_tunnel(
            tunnel_type=tunnel_type, router_id=router_id, label=label, notes=notes,
        )
        if not result.get("ok"):
            return {"ok": False, "status": result.get("status") or "unavailable",
                    "error": result.get("error") or {}}
        response = result.get("response") or {}
        tunnel = response.get("tunnel") if isinstance(response.get("tunnel"), dict) else response
        name = _first(tunnel, _NAME_KEYS)
        if not name:
            return {"ok": False, "status": "invalid_payload",
                    "error": {"code": "invalid_payload", "message": "tunnel name missing"}}
        username = _first(tunnel, _USER_KEYS)
        password = _first(tunnel, _PASS_KEYS)
        # Persist metadata + fingerprint ONLY. The raw password is returned once
        # below for local injection and never written to the DB.
        bridge_tunnels_repo.upsert_tunnel(
            tenant_id=tenant_id,
            remote_name=name,
            tunnel_type=_first(tunnel, ("type", "tunnel_type"), default=tunnel_type or "sstp"),
            status="active",
            source="requested",
            username=username,
            secret_ref=bridge_tunnels_repo.secret_fingerprint(password),
            remote_address=_first(tunnel, ("remote_address", "server", "address")),
            vpn_subnet=_first(tunnel, ("vpn_subnet", "subnet")),
            notes=str(label or notes or ""),
        )
        # ACK immediately — we stored it; panel can stop resending the password.
        acked = False
        ack = self.admin_client.ack_vpn_tunnels([name])
        if ack.get("ok"):
            bridge_tunnels_repo.mark_acked(tenant_id, [name])
            acked = True
        return {
            "ok": True,
            "status": "requested",
            "tunnel": {
                "remote_name": name,
                "tunnel_type": _first(tunnel, ("type", "tunnel_type"), default=tunnel_type or "sstp"),
                "username": username,
                "remote_address": _first(tunnel, ("remote_address", "server", "address")),
                "vpn_subnet": _first(tunnel, ("vpn_subnet", "subnet")),
                "acked": acked,
            },
            # One-time credentials for local injection — NOT persisted anywhere.
            "credentials": {"username": username, "password": password},
        }

    # ── periodic sync ────────────────────────────────────────────────────────
    def sync_tunnels(self, *, tenant_id: int = 1) -> dict[str, Any]:
        result = self.admin_client.fetch_vpn_tunnels()
        if not result.get("ok"):
            return {"ok": False, "status": result.get("status") or "unavailable",
                    "error": result.get("error") or {}}
        response = result.get("response") or {}
        tunnels = response.get("tunnels")
        if not isinstance(tunnels, list):
            return {"ok": False, "status": "invalid_payload",
                    "error": {"code": "invalid_payload", "message": "tunnels must be a list"}}

        active = suspended = revoked = 0
        need_ack: list[str] = []
        for entry in tunnels:
            if not isinstance(entry, dict):
                continue
            name = _first(entry, _NAME_KEYS)
            if not name:
                continue
            status = str(entry.get("status") or "active").strip().lower()
            if status == "revoked":
                bridge_tunnels_repo.delete_tunnel(tenant_id, name)
                revoked += 1
                continue
            password = _first(entry, _PASS_KEYS)
            row = bridge_tunnels_repo.upsert_tunnel(
                tenant_id=tenant_id,
                remote_name=name,
                tunnel_type=_first(entry, ("type", "tunnel_type"), default="sstp"),
                status="suspended" if status == "suspended" else "active",
                source="synced",
                username=_first(entry, _USER_KEYS),
                # Blank secret_ref leaves any existing fingerprint intact (the
                # panel only resends the password before we ack).
                secret_ref=bridge_tunnels_repo.secret_fingerprint(password) if password else "",
                remote_address=_first(entry, ("remote_address", "server", "address")),
                vpn_subnet=_first(entry, ("vpn_subnet", "subnet")),
            )
            if status == "suspended":
                suspended += 1
            else:
                active += 1
            if not row.get("acked"):
                need_ack.append(name)

        acked = 0
        if need_ack:
            ack = self.admin_client.ack_vpn_tunnels(need_ack)
            if ack.get("ok"):
                acked = bridge_tunnels_repo.mark_acked(tenant_id, need_ack)
        return {
            "ok": True,
            "status": "ok",
            "active_count": active,
            "suspended_count": suspended,
            "revoked_count": revoked,
            "acked_count": acked,
        }
