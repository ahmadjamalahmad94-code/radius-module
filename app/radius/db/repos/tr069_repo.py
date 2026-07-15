"""طبقة الوصول لبيانات TR-069 — أجهزة/أوامر/لقطات/أحداث. tenant-scoped دائمًا."""
from __future__ import annotations

import json
from typing import Any

from ..connection import db, transaction
from ..helpers import now_iso, row_to_dict


# ─────────────── الأجهزة ───────────────
_ALLOWED_DEVICE_COLS = {
    "owner_admin_id", "subscriber_id", "radius_username", "nas_id",
    "acs_device_id", "oui", "product_class", "serial_number",
    "manufacturer", "model_name", "hardware_version", "software_version",
    "wan_mac", "lan_mac", "wan_ip", "lan_ip", "pppoe_username_detected",
    "data_model", "root_object", "enrollment_token_hash", "cwmp_username",
    "cwmp_password_enc", "conn_req_username", "conn_req_password_enc",
    "status", "provisioning_status", "is_online", "is_managed",
    "management_disabled_at", "last_inform_at", "last_boot_at", "last_sync_at",
    "last_error_at", "last_error_code", "last_error_message", "metadata_json",
}
# أعمدة INTEGER/TEXT nullable — يُسمح لها بـ None؛ الباقي NOT NULL DEFAULT ''.
_NULLABLE_DEVICE_COLS = {"owner_admin_id", "subscriber_id", "nas_id",
                         "management_disabled_at", "last_inform_at", "last_boot_at",
                         "last_sync_at", "last_error_at"}


def create_device(tenant_id: int, **fields: Any) -> int:
    """يُدرِج صفّ جهاز. الأعمدة غير المُمرَّرة تأخذ DEFAULT من المخطّط (لا NULL)."""
    if isinstance(fields.get("metadata_json"), (dict, list)):
        fields["metadata_json"] = json.dumps(fields["metadata_json"])
    if "is_managed" in fields:
        fields["is_managed"] = 1 if fields["is_managed"] in (1, True, "1") else 0
    if "is_online" in fields:
        fields["is_online"] = 1 if fields["is_online"] else 0
    # لا نُدرِج None لعمود NOT NULL — نتركه للـ DEFAULT.
    cols, vals = [], []
    for k, v in fields.items():
        if k not in _ALLOWED_DEVICE_COLS:
            continue
        if v is None and k not in _NULLABLE_DEVICE_COLS:
            continue
        cols.append(k)
        vals.append(v)
    placeholders = ", ".join(["?"] * (len(cols) + 2))
    with transaction() as c:
        cur = c.execute(
            f"INSERT INTO tr069_devices(tenant_id, {', '.join(cols + ['created_at'])}) "
            f"VALUES({placeholders})",
            (tenant_id, *vals, now_iso()),
        )
        return int(cur.lastrowid)


def get_device(tenant_id: int, device_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM tr069_devices WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
        (tenant_id, int(device_id)),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_by_acs_id(tenant_id: int, acs_device_id: str) -> dict | None:
    row = db().execute(
        "SELECT * FROM tr069_devices WHERE tenant_id=? AND acs_device_id=? "
        "AND deleted_at IS NULL",
        (tenant_id, str(acs_device_id)),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_devices(tenant_id: int, *, owner_admin_id: int | None = None,
                 status: str = "", q: str = "", limit: int = 300) -> list[dict]:
    where = ["tenant_id=?", "deleted_at IS NULL"]
    params: list = [tenant_id]
    if owner_admin_id is not None:
        where.append("(owner_admin_id=? OR owner_admin_id IS NULL)")
        params.append(int(owner_admin_id))
    if status:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(radius_username LIKE ? OR serial_number LIKE ? OR "
                     "model_name LIKE ? OR wan_ip LIKE ? OR wan_mac LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like, like]
    rows = db().execute(
        f"SELECT * FROM tr069_devices WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC LIMIT ?",
        (*params, int(limit)),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def update_device(tenant_id: int, device_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction() as c:
        c.execute(
            f"UPDATE tr069_devices SET {sets} WHERE tenant_id=? AND id=?",
            (*fields.values(), tenant_id, int(device_id)),
        )


def soft_delete_device(tenant_id: int, device_id: int) -> None:
    with transaction() as c:
        c.execute(
            "UPDATE tr069_devices SET deleted_at=?, updated_at=? WHERE tenant_id=? AND id=?",
            (now_iso(), now_iso(), tenant_id, int(device_id)),
        )


def count_devices(tenant_id: int) -> dict[str, int]:
    rows = db().execute(
        "SELECT status, COUNT(*) c FROM tr069_devices "
        "WHERE tenant_id=? AND deleted_at IS NULL GROUP BY status",
        (tenant_id,),
    ).fetchall()
    out = {r["status"]: int(r["c"]) for r in rows}
    online = db().execute(
        "SELECT COUNT(*) c FROM tr069_devices WHERE tenant_id=? AND is_online=1 "
        "AND deleted_at IS NULL", (tenant_id,)).fetchone()
    out["online"] = int(online["c"]) if online else 0
    out["total"] = sum(v for k, v in out.items() if k not in ("online",))
    return out


# ─────────────── الأوامر ───────────────
def create_action(tenant_id: int, *, device_id: int, action_type: str,
                  requested_by: str = "", owner_admin_id: int | None = None,
                  request_payload: dict | None = None, safe_summary: str = "",
                  idempotency_key: str = "", correlation_id: str = "",
                  expires_at: str = "", max_retries: int = 3) -> int:
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO tr069_device_actions(tenant_id, owner_admin_id, device_id, "
            "requested_by, action_type, request_payload, safe_summary, status, "
            "queued_at, expires_at, max_retries, correlation_id, idempotency_key, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, owner_admin_id, int(device_id), requested_by, action_type,
             json.dumps(request_payload or {}), safe_summary, "queued", now,
             expires_at or None, int(max_retries), correlation_id, idempotency_key, now),
        )
        return int(cur.lastrowid)


def get_action(tenant_id: int, action_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM tr069_device_actions WHERE tenant_id=? AND id=?",
        (tenant_id, int(action_id)),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_actions(tenant_id: int, device_id: int, *, limit: int = 50) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM tr069_device_actions WHERE tenant_id=? AND device_id=? "
        "ORDER BY id DESC LIMIT ?",
        (tenant_id, int(device_id), int(limit)),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def claim_pending_actions(limit: int = 20) -> list[dict]:
    """أوامر منتظرة عبر كل المستأجرين للعامل الخلفيّ (queued/waiting_for_inform)."""
    rows = db().execute(
        "SELECT * FROM tr069_device_actions "
        "WHERE status IN ('queued','waiting_for_inform') ORDER BY id LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def update_action(action_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction() as c:
        c.execute(
            f"UPDATE tr069_device_actions SET {sets} WHERE id=?",
            (*fields.values(), int(action_id)),
        )


# ─────────────── لقطة/أحداث ───────────────
def upsert_snapshot(tenant_id: int, device_id: int, **fields: Any) -> None:
    payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in fields.items()}
    payload.setdefault("wan_status", "")
    now = now_iso()
    cols = ["uptime_seconds", "wan_status", "wifi_json", "connected_hosts",
            "firmware_json", "dsl_json", "raw_json"]
    vals = {c: payload.get(c) for c in cols}
    with transaction() as c:
        c.execute(
            "INSERT INTO tr069_device_snapshots(device_id, tenant_id, "
            + ", ".join(cols) + ", captured_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            + ", ".join(f"{c2}=excluded.{c2}" for c2 in cols) + ", captured_at=excluded.captured_at",
            (int(device_id), tenant_id, *[vals[c] for c in cols], now),
        )


def get_snapshot(tenant_id: int, device_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM tr069_device_snapshots WHERE tenant_id=? AND device_id=?",
        (tenant_id, int(device_id)),
    ).fetchone()
    return row_to_dict(row) if row else None


def record_event(tenant_id: int, *, device_id: int | None, acs_device_id: str = "",
                 event_type: str = "", detail: dict | None = None) -> None:
    with transaction() as c:
        c.execute(
            "INSERT INTO tr069_events(tenant_id, device_id, acs_device_id, event_type, "
            "detail_json, created_at) VALUES(?,?,?,?,?,?)",
            (tenant_id, device_id, acs_device_id, event_type,
             json.dumps(detail or {}), now_iso()),
        )


def list_events(tenant_id: int, device_id: int, *, limit: int = 50) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM tr069_events WHERE tenant_id=? AND device_id=? "
        "ORDER BY id DESC LIMIT ?",
        (tenant_id, int(device_id), int(limit)),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def record_assignment(tenant_id: int, *, device_id: int, subscriber_id: int | None,
                      radius_username: str = "", assigned_by: str = "",
                      method: str = "") -> None:
    with transaction() as c:
        c.execute(
            "INSERT INTO tr069_device_assignments(tenant_id, device_id, subscriber_id, "
            "radius_username, assigned_by, method, created_at) VALUES(?,?,?,?,?,?,?)",
            (tenant_id, int(device_id), subscriber_id, radius_username,
             assigned_by, method, now_iso()),
        )
