"""NAS Devices repo."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ...core.types import NasDevice
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _g(row: Any, key: str, default):
    """Safe getter — fallback for snapshots before migration 014."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


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
        # RM-H5 fields (safe defaults for pre-014 snapshots)
        last_check_at=parse_dt(_g(r, "last_check_at", None)),
        last_check_status=_g(r, "last_check_status", "") or "",
        require_message_authenticator=bool(_g(r, "require_message_authenticator", 0)),
        ssh_port=_g(r, "ssh_port", 22) or 22,
        tags=_g(r, "tags", "") or "",
        metadata=_g(r, "metadata", "{}") or "{}",
        deleted_at=parse_dt(_g(r, "deleted_at", None)),
        deleted_by=_g(r, "deleted_by", "") or "",
        delete_reason=_g(r, "delete_reason", "") or "",
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
        # feat/mikrotik-user-import — واجهة الجلب المفضّلة (migration 124).
        # قراءة آمنة (افتراضي auto لقواعد ما قبل 124).
        api_type=_g(r, "api_type", "auto") or "auto",
    )


def list_nas(tenant_id: int, *, limit: int = 100, offset: int = 0,
             include_deleted: bool = False) -> list[NasDevice]:
    sql = "SELECT * FROM nas_devices WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    cur = db().execute(
        sql,
        vals
    )
    return [_row(r) for r in cur.fetchall()]


def get_nas(tenant_id: int, nas_id: int,
            include_deleted: bool = False) -> Optional[NasDevice]:
    sql = "SELECT * FROM nas_devices WHERE tenant_id = ? AND id = ?"
    vals: list = [tenant_id, nas_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(
        sql,
        vals
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
                    require_message_authenticator, ssh_port, tags, metadata,
                    deleted_at, deleted_by, delete_reason,
                    created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (d.tenant_id, d.name, d.shortname, d.address, d.secret, d.vendor, d.nas_type,
                  d.ports, d.snmp_community, d.auth_port, d.acct_port, d.coa_port,
                  d.api_port, d.api_user, d.api_password, int(d.api_use_tls),
                  d.location, d.coordinates, int(d.monitoring_enabled),
                  d.description, int(d.enabled),
                  int(d.require_message_authenticator), d.ssh_port, d.tags, d.metadata or "{}",
                  dt_to_iso(d.deleted_at), d.deleted_by, d.delete_reason,
                  now, now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE nas_devices SET
                    name=?, shortname=?, address=?, secret=?, vendor=?, nas_type=?,
                    ports=?, snmp_community=?, auth_port=?, acct_port=?, coa_port=?,
                    api_port=?, api_user=?, api_password=?, api_use_tls=?,
                    location=?, coordinates=?, monitoring_enabled=?, description=?, enabled=?,
                    require_message_authenticator=?, ssh_port=?, tags=?, metadata=?,
                    deleted_at=?, deleted_by=?, delete_reason=?,
                    updated_at=?
                WHERE tenant_id = ? AND id = ?
            """, (d.name, d.shortname, d.address, d.secret, d.vendor, d.nas_type,
                  d.ports, d.snmp_community, d.auth_port, d.acct_port, d.coa_port,
                  d.api_port, d.api_user, d.api_password, int(d.api_use_tls),
                  d.location, d.coordinates, int(d.monitoring_enabled),
                  d.description, int(d.enabled),
                  int(d.require_message_authenticator), d.ssh_port, d.tags, d.metadata or "{}",
                  dt_to_iso(d.deleted_at), d.deleted_by, d.delete_reason,
                  now, d.tenant_id, d.id))
            new_id = d.id
    return get_nas(d.tenant_id, new_id)


def record_check(tenant_id: int, nas_id: int, *, status: str) -> None:
    """RM-H5: يحفظ نتيجة test-connection."""
    with transaction() as conn:
        conn.execute(
            "UPDATE nas_devices SET last_check_at=?, last_check_status=?, updated_at=? "
            "WHERE tenant_id=? AND id=?",
            (now_iso(), status, now_iso(), tenant_id, nas_id))


def set_api_type(tenant_id: int, nas_id: int, api_type: str) -> None:
    """feat/mikrotik-user-import — يضبط واجهة الجلب المفضّلة (auto|rest|api).
    دالة مخصّصة صغيرة (لا تمرّ عبر upsert الكبير)."""
    val = api_type if api_type in ("auto", "rest", "api") else "auto"
    with transaction() as conn:
        conn.execute(
            "UPDATE nas_devices SET api_type=?, updated_at=? WHERE tenant_id=? AND id=?",
            (val, now_iso(), int(tenant_id), int(nas_id)))


def delete_nas(tenant_id: int, nas_id: int) -> None:
    archive_nas(tenant_id, nas_id)


def archive_nas(tenant_id: int, nas_id: int, *, actor: str = "",
                reason: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE nas_devices
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?,
                enabled = 0, monitoring_enabled = 0, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300],
              now_iso(), tenant_id, nas_id))
        return cur.rowcount > 0


def restore_nas(tenant_id: int, nas_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE nas_devices
            SET deleted_at = NULL, deleted_by = '', delete_reason = '',
                enabled = 0, monitoring_enabled = 0, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NOT NULL
        """, (now_iso(), tenant_id, nas_id))
        return cur.rowcount > 0
