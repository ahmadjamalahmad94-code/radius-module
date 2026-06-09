"""Local store for CHR tunnels delivered through the signed license bridge.

SECURITY (RADIUS is sold to customers): this repo NEVER stores a raw CHR/tunnel
secret. Only metadata + a non-reversible ``secret_ref`` fingerprint are kept.
The SSTP/PPP password from a tunnel request is surfaced once for local
injection and is never written here.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso

VALID_STATUSES = ("active", "suspended", "revoked")


def secret_fingerprint(secret: str) -> str:
    """Return a non-reversible reference for a tunnel secret (never the secret).

    Lets us notice when the panel rotates a password between syncs without
    persisting the password itself. Empty input yields an empty ref.
    """
    raw = str(secret or "")
    if not raw:
        return ""
    return "ref:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _row(r) -> dict[str, Any]:
    return dict(r)


def list_tunnels(tenant_id: int, *, include_revoked: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM bridge_tunnels WHERE tenant_id = ?"
    params: list[Any] = [int(tenant_id)]
    if not include_revoked:
        sql += " AND status != 'revoked'"
    sql += " ORDER BY id"
    return [_row(r) for r in db().execute(sql, params).fetchall()]


def get_by_remote_name(tenant_id: int, remote_name: str) -> Optional[dict[str, Any]]:
    row = db().execute(
        "SELECT * FROM bridge_tunnels WHERE tenant_id = ? AND remote_name = ?",
        (int(tenant_id), str(remote_name)),
    ).fetchone()
    return _row(row) if row else None


def upsert_tunnel(
    *,
    tenant_id: int = 1,
    remote_name: str,
    tunnel_type: str = "sstp",
    status: str = "active",
    source: str = "synced",
    username: str = "",
    secret_ref: str = "",
    remote_address: str = "",
    vpn_subnet: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Insert or update a tunnel record by (tenant_id, remote_name).

    ``secret_ref`` MUST already be a fingerprint (see ``secret_fingerprint``),
    never a raw secret. A blank secret_ref on update leaves the stored one
    untouched (the panel stops resending the password after ack).
    """
    remote_name = str(remote_name or "").strip()
    if not remote_name:
        raise ValueError("remote_name is required")
    status = str(status or "active").strip().lower()
    if status not in VALID_STATUSES:
        status = "active"
    enabled = 0 if status in {"suspended", "revoked"} else 1
    now = now_iso()
    existing = get_by_remote_name(tenant_id, remote_name)
    with transaction() as conn:
        if existing:
            # Keep the existing secret_ref when no new one is delivered.
            new_ref = secret_ref or existing.get("secret_ref") or ""
            conn.execute(
                """
                UPDATE bridge_tunnels
                SET tunnel_type = ?, status = ?, source = ?, username = ?,
                    secret_ref = ?, remote_address = ?, vpn_subnet = ?,
                    enabled = ?, notes = ?, updated_at = ?, last_synced_at = ?
                WHERE tenant_id = ? AND remote_name = ?
                """,
                (
                    str(tunnel_type or "sstp"), status, str(source or "synced"),
                    str(username or ""), new_ref, str(remote_address or ""),
                    str(vpn_subnet or ""), enabled, str(notes or ""), now, now,
                    int(tenant_id), remote_name,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO bridge_tunnels (
                    tenant_id, remote_name, tunnel_type, status, source,
                    username, secret_ref, remote_address, vpn_subnet,
                    acked, enabled, notes, created_at, updated_at, last_synced_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(tenant_id), remote_name, str(tunnel_type or "sstp"), status,
                    str(source or "synced"), str(username or ""), str(secret_ref or ""),
                    str(remote_address or ""), str(vpn_subnet or ""),
                    0, enabled, str(notes or ""), now, now, now,
                ),
            )
    return get_by_remote_name(tenant_id, remote_name) or {}


def mark_acked(tenant_id: int, remote_names: list[str]) -> int:
    """Flag tunnels as acknowledged to the panel (stops password resends)."""
    names = [str(n).strip() for n in (remote_names or []) if str(n).strip()]
    if not names:
        return 0
    placeholders = ",".join("?" for _ in names)
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE bridge_tunnels SET acked = 1, updated_at = ? "
            f"WHERE tenant_id = ? AND remote_name IN ({placeholders})",
            [now_iso(), int(tenant_id), *names],
        )
        return int(cur.rowcount or 0)


def disable_tunnel(tenant_id: int, remote_name: str) -> bool:
    """Local effect of a 'suspended' tunnel — keep the record, turn it off."""
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE bridge_tunnels SET status = 'suspended', enabled = 0, updated_at = ? "
            "WHERE tenant_id = ? AND remote_name = ?",
            (now_iso(), int(tenant_id), str(remote_name)),
        )
        return int(cur.rowcount or 0) > 0


def delete_tunnel(tenant_id: int, remote_name: str) -> bool:
    """Local effect of a 'revoked' tunnel — hard-remove the local record."""
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM bridge_tunnels WHERE tenant_id = ? AND remote_name = ?",
            (int(tenant_id), str(remote_name)),
        )
        return int(cur.rowcount or 0) > 0
