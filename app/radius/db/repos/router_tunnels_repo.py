"""Per-router VPN tunnel profile (SSTP management + L2TP/IPsec traffic).

Reads/writes the tunnel columns added to ``nas_devices`` by migration 092.
Kept as a small dedicated repo so the central nas_repo stays untouched.

SECURITY: only masked ``*_secret_ref`` pointers are persisted here — never
plaintext tunnel secrets. The generated secret is shown once at render time
(same pattern as the WireGuard private key) and is never written to this row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..connection import db, transaction

# Columns owned by migration 092. Writes are whitelisted against this set so a
# caller can never UPDATE an arbitrary column through this repo.
TUNNEL_COLUMNS = (
    "management_tunnel_type",
    "management_tunnel_status",
    "management_tunnel_interface_name",
    "management_remote_address",
    "management_vpn_subnet",
    "management_secret_ref",
    "sstp_verify_certificate",
    "traffic_tunnel_type",
    "traffic_tunnel_status",
    "traffic_tunnel_interface_name",
    "traffic_remote_address",
    "traffic_vpn_subnet",
    "traffic_mode",
    "traffic_routing_mark",
    "traffic_source_pool",
    "traffic_enabled",
    "traffic_ipsec_secret_ref",
)

# Anything matching these names must NEVER be passed in as a value — secrets go
# through the *_secret_ref masked pointers, not raw columns.
_FORBIDDEN_KEYS = ("password", "ipsec_secret", "secret_plain")


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def get_tunnel_profile(tenant_id: int, nas_id: int) -> dict | None:
    """Return the tunnel profile for a router, or None if it doesn't exist."""
    cols = ", ".join(TUNNEL_COLUMNS) + ", tunnel_updated_at"
    row = db().execute(
        f"SELECT {cols} FROM nas_devices WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(nas_id)),
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in (*TUNNEL_COLUMNS, "tunnel_updated_at")}


def update_tunnel_profile(tenant_id: int, nas_id: int, **fields: Any) -> bool:
    """Update whitelisted tunnel columns for a router.

    Unknown columns and any secret-looking keys are rejected (the latter
    loudly, to prevent accidental plaintext-secret storage). Returns True if a
    row was updated.
    """
    for key in fields:
        if key in _FORBIDDEN_KEYS:
            raise ValueError(
                f"refusing to store plaintext secret via tunnel profile: {key!r}"
            )
        if key not in TUNNEL_COLUMNS:
            raise ValueError(f"unknown tunnel column: {key!r}")
    if not fields:
        return False

    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [fields[key] for key in fields]
    values.append(_now_epoch())
    values.extend([int(tenant_id), int(nas_id)])
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE nas_devices SET {assignments}, tunnel_updated_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            values,
        )
        return cur.rowcount > 0


__all__ = ["TUNNEL_COLUMNS", "get_tunnel_profile", "update_tunnel_profile"]
