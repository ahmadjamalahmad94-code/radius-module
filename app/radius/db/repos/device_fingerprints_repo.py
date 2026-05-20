"""Device fingerprints repo (migration 026).

Cache of (hostname, dhcp_class_id, parsed os/brand/model) per MAC,
populated by the background DHCP-lease sync from MikroTik. Reads are
hot path — used on every card-checker render and subscribers list.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


# ─────────────────────────────────────────────────────────────────────
# Row shape (dict, intentionally not a dataclass — pure pass-through
# data, no behavior, used for templating + JSON).
#
# {
#     "id": int,
#     "tenant_id": str,
#     "mac": str,                # AA:BB:CC:DD:EE:FF lower-case
#     "hostname": str,
#     "dhcp_class_id": str,
#     "os_family": str,          # android | ios | windows | macos | linux | other | ''
#     "os_version": str,
#     "device_brand": str,
#     "device_model": str,
#     "ip_address": str,
#     "nas_id": int | None,
#     "first_seen_at": str,
#     "last_seen_at": str,
# }
# ─────────────────────────────────────────────────────────────────────


def _normalize_mac(mac: str) -> str:
    """AA:BB:CC:DD:EE:FF lower-case — consistent across all callers."""
    if not mac:
        return ""
    return mac.strip().lower()


def _row(r) -> dict[str, Any]:
    return {
        "id":            r["id"],
        "tenant_id":     r["tenant_id"],
        "mac":           r["mac"],
        "hostname":      r["hostname"] or "",
        "dhcp_class_id": r["dhcp_class_id"] or "",
        "os_family":     r["os_family"] or "",
        "os_version":    r["os_version"] or "",
        "device_brand":  r["device_brand"] or "",
        "device_model":  r["device_model"] or "",
        "ip_address":    r["ip_address"] or "",
        "nas_id":        r["nas_id"],
        "first_seen_at": r["first_seen_at"],
        "last_seen_at":  r["last_seen_at"],
    }


def get_by_mac(tenant_id: Any, mac: str) -> Optional[dict[str, Any]]:
    mac = _normalize_mac(mac)
    if not mac:
        return None
    row = db().execute(
        "SELECT * FROM device_fingerprints WHERE tenant_id = ? AND mac = ?",
        (str(tenant_id), mac),
    ).fetchone()
    return _row(row) if row else None


def get_many_by_macs(tenant_id: Any, macs: list[str]) -> dict[str, dict[str, Any]]:
    """Batch lookup — returns {mac_lower: fingerprint}. Missing MACs absent."""
    norm = [m for m in (_normalize_mac(x) for x in macs or []) if m]
    if not norm:
        return {}
    # de-dup
    norm = list({m for m in norm})
    placeholders = ",".join("?" for _ in norm)
    rows = db().execute(
        f"SELECT * FROM device_fingerprints "
        f"WHERE tenant_id = ? AND mac IN ({placeholders})",
        [str(tenant_id), *norm],
    ).fetchall()
    return {r["mac"]: _row(r) for r in rows}


def list_for_tenant(tenant_id: Any, *, limit: int = 500,
                    offset: int = 0,
                    os_family: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM device_fingerprints WHERE tenant_id = ?"
    vals: list[Any] = [str(tenant_id)]
    if os_family:
        sql += " AND os_family = ?"
        vals.append(os_family)
    sql += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?"
    vals.extend([int(limit), int(offset)])
    rows = db().execute(sql, vals).fetchall()
    return [_row(r) for r in rows]


def upsert(
    *,
    tenant_id: Any,
    mac: str,
    hostname: str = "",
    dhcp_class_id: str = "",
    os_family: str = "",
    os_version: str = "",
    device_brand: str = "",
    device_model: str = "",
    ip_address: str = "",
    nas_id: Optional[int] = None,
) -> bool:
    """Insert-or-update by (tenant_id, mac).

    On update: only overwrites a stored value when the incoming value
    is non-empty. This protects against a stale lease wiping a good
    hostname when MT returns just '' for one cycle.

    Returns True if a row was written (insert or actual change), False
    if the row already exists and nothing changed.
    """
    mac = _normalize_mac(mac)
    if not mac:
        return False

    now = now_iso()
    tid = str(tenant_id)

    with transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM device_fingerprints WHERE tenant_id = ? AND mac = ?",
            (tid, mac),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO device_fingerprints
                    (tenant_id, mac, hostname, dhcp_class_id,
                     os_family, os_version, device_brand, device_model,
                     ip_address, nas_id,
                     first_seen_at, last_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (tid, mac,
                 hostname or "", dhcp_class_id or "",
                 os_family or "", os_version or "",
                 device_brand or "", device_model or "",
                 ip_address or "", nas_id,
                 now, now),
            )
            return True

        # Merge — keep existing value when incoming is empty.
        def _keep(new, old):
            return new if (new or "").strip() else (old or "")

        new_vals = (
            _keep(hostname,      existing["hostname"]),
            _keep(dhcp_class_id, existing["dhcp_class_id"]),
            _keep(os_family,     existing["os_family"]),
            _keep(os_version,    existing["os_version"]),
            _keep(device_brand,  existing["device_brand"]),
            _keep(device_model,  existing["device_model"]),
            _keep(ip_address,    existing["ip_address"]),
            nas_id if nas_id is not None else existing["nas_id"],
            now,
            tid, mac,
        )
        conn.execute(
            """
            UPDATE device_fingerprints SET
                hostname=?, dhcp_class_id=?,
                os_family=?, os_version=?,
                device_brand=?, device_model=?,
                ip_address=?, nas_id=?,
                last_seen_at=?
            WHERE tenant_id=? AND mac=?
            """,
            new_vals,
        )
        return True


def count_for_tenant(tenant_id: Any) -> int:
    row = db().execute(
        "SELECT COUNT(*) AS n FROM device_fingerprints WHERE tenant_id = ?",
        (str(tenant_id),),
    ).fetchone()
    return int(row["n"]) if row else 0
