"""NAS Devices repo."""
from __future__ import annotations

from typing import Optional, Sequence

from ...core.types import NasDevice
from ..connection import db, transaction
from ..helpers import now_iso, parse_dt


def _row(r) -> NasDevice:
    return NasDevice(
        id=r["id"], tenant_id=r["tenant_id"], name=r["name"], address=r["address"],
        secret=r["secret"], vendor=r["vendor"], nas_type=r["nas_type"],
        shortname=r["shortname"], ports=r["ports"], snmp_community=r["snmp_community"],
        auth_port=r["auth_port"], acct_port=r["acct_port"], coa_port=r["coa_port"],
        api_port=r["api_port"], api_user=r["api_user"], api_password=r["api_password"],
        api_use_tls=bool(r["api_use_tls"]),
        location=r["location"], coordinates=r["coordinates"],
        monitoring_enabled=bool(r["monitoring_enabled"]),
        description=r["description"], enabled=bool(r["enabled"]),
        last_seen_at=parse_dt(r["last_seen_at"]),
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )


def list_nas(tenant_id: int, *, limit: int = 100, offset: int = 0) -> list[NasDevice]:
    cur = db().execute(
        "SELECT * FROM nas_devices WHERE tenant_id = ? ORDER BY id LIMIT ? OFFSET ?",
        (tenant_id, limit, offset)
    )
    return [_row(r) for r in cur.fetchall()]


def get_nas(tenant_id: int, nas_id: int) -> Optional[NasDevice]:
    cur = db().execute(
        "SELECT * FROM nas_devices WHERE tenant_id = ? AND id = ?",
        (tenant_id, nas_id)
    )
    row = cur.fetchone()
    return _row(row) if row else None


def upsert_nas(d: NasDevice) -> NasDevice:
    now = now_iso()
    with transaction() as conn:
        if d.id is None:
            cur = conn.execute("""
                INSERT INTO nas_devices(tenant_id, name, shortname, address, secret, vendor, nas_type,
                    ports, snmp_community, auth_port, acct_port, coa_port,
                    api_port, api_user, api_password, api_use_tls,
                    location, coordinates, monitoring_enabled, description, enabled,
                    created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (d.tenant_id, d.name, d.shortname, d.address, d.secret, d.vendor, d.nas_type,
                  d.ports, d.snmp_community, d.auth_port, d.acct_port, d.coa_port,
                  d.api_port, d.api_user, d.api_password, int(d.api_use_tls),
                  d.location, d.coordinates, int(d.monitoring_enabled),
                  d.description, int(d.enabled), now, now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE nas_devices SET
                    name=?, shortname=?, address=?, secret=?, vendor=?, nas_type=?,
                    ports=?, snmp_community=?, auth_port=?, acct_port=?, coa_port=?,
                    api_port=?, api_user=?, api_password=?, api_use_tls=?,
                    location=?, coordinates=?, monitoring_enabled=?, description=?, enabled=?, updated_at=?
                WHERE tenant_id = ? AND id = ?
            """, (d.name, d.shortname, d.address, d.secret, d.vendor, d.nas_type,
                  d.ports, d.snmp_community, d.auth_port, d.acct_port, d.coa_port,
                  d.api_port, d.api_user, d.api_password, int(d.api_use_tls),
                  d.location, d.coordinates, int(d.monitoring_enabled),
                  d.description, int(d.enabled), now, d.tenant_id, d.id))
            new_id = d.id
    return get_nas(d.tenant_id, new_id)


def delete_nas(tenant_id: int, nas_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM nas_devices WHERE tenant_id = ? AND id = ?", (tenant_id, nas_id))
