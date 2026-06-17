"""mac_clone_repo — مخزن «منع استنساخ MAC» (feat/anti-mac-clone).

جدولان (migration 125):
  • mac_clone_bindings — صفّ لكل (tenant, username, mac): البصمة المُلْزَمة
    + سياق نموذجي + إحصائيات verify/mismatch + status (active/superseded/
    suspended).
  • mac_clone_events — سجلّ حدث-لكل-قرار (bind/verify_ok/clone_detected/
    stepup_required/concurrent_kick) للتدقيق + الواجهة + التنبيهات.

كل القراءات/الكتابات tenant-scoped. تطبيع MAC مركزي (upper + ':' separator).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


# ─────────────────────────────────────────────────────────────────────
# تطبيع MAC — مطابق لـ access_control.normalize_mac (UPPER + ':')
# ─────────────────────────────────────────────────────────────────────
def normalize_mac(mac: str) -> str:
    return (mac or "").strip().upper().replace("-", ":")


# ════════════════════════════════════════════════════════════════════════
# Bindings
# ════════════════════════════════════════════════════════════════════════
_BINDING_COLS = (
    "id", "tenant_id", "username", "mac",
    "hostname", "dhcp_class_id", "os_family", "device_brand", "device_model",
    "ua_hash", "ua_sample", "vendor_oui",
    "nas_ip", "called_station", "nas_port", "nas_port_type",
    "status", "bind_confidence",
    "first_seen_at", "last_seen_at", "last_verified_at",
    "verify_count", "mismatch_count",
)


def _binding_row(r) -> dict[str, Any]:
    return {c: r[c] for c in _BINDING_COLS}


def get_binding(tenant_id: int, username: str, mac: str) -> Optional[dict[str, Any]]:
    mac = normalize_mac(mac)
    if not username or not mac:
        return None
    row = db().execute(
        "SELECT * FROM mac_clone_bindings "
        "WHERE tenant_id = ? AND username = ? AND mac = ?",
        (int(tenant_id), username, mac),
    ).fetchone()
    return _binding_row(row) if row else None


def list_bindings_for_user(tenant_id: int, username: str) -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT * FROM mac_clone_bindings "
        "WHERE tenant_id = ? AND username = ? "
        "ORDER BY last_seen_at DESC",
        (int(tenant_id), username),
    ).fetchall()
    return [_binding_row(r) for r in rows]


def list_bindings(tenant_id: int, *, status: str = "",
                  limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM mac_clone_bindings WHERE tenant_id = ?"
    vals: list[Any] = [int(tenant_id)]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    sql += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?"
    vals.extend([int(limit), int(offset)])
    rows = db().execute(sql, vals).fetchall()
    return [_binding_row(r) for r in rows]


def count_bindings(tenant_id: int, *, status: str = "") -> int:
    sql = "SELECT COUNT(*) AS n FROM mac_clone_bindings WHERE tenant_id = ?"
    vals: list[Any] = [int(tenant_id)]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    row = db().execute(sql, vals).fetchone()
    return int(row["n"]) if row else 0


def upsert_binding(*, tenant_id: int, username: str, mac: str,
                   hostname: str = "", dhcp_class_id: str = "",
                   os_family: str = "", device_brand: str = "",
                   device_model: str = "",
                   ua_hash: str = "", ua_sample: str = "",
                   vendor_oui: str = "",
                   nas_ip: str = "", called_station: str = "",
                   nas_port: str = "", nas_port_type: str = "",
                   bind_confidence: str = "medium") -> dict[str, Any]:
    """ينشئ صفّ binding جديد أو يُحدّث القائم (last_seen_at + verify_count + سياق).

    عند الإنشاء يُسجَّل first_seen_at = last_seen_at = الآن. عند التحديث يُحدَّث
    last_seen_at + last_verified_at + verify_count+=1، ولا يُكتب فوق إشارات
    الجهاز الموجودة بقيم فارغة (نحافظ على ما لدينا — قد لا تتوفّر إشارة الـDHCP
    في كل دورة). يعيد الـrow النهائي.
    """
    mac = normalize_mac(mac)
    if not username or not mac:
        raise ValueError("username + mac مطلوبان")
    now = now_iso()
    tid = int(tenant_id)
    with transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM mac_clone_bindings "
            "WHERE tenant_id = ? AND username = ? AND mac = ?",
            (tid, username, mac),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO mac_clone_bindings
                    (tenant_id, username, mac,
                     hostname, dhcp_class_id, os_family,
                     device_brand, device_model,
                     ua_hash, ua_sample, vendor_oui,
                     nas_ip, called_station, nas_port, nas_port_type,
                     status, bind_confidence,
                     first_seen_at, last_seen_at, last_verified_at,
                     verify_count, mismatch_count)
                VALUES (?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?,?, ?,?, ?,?,?, 0,0)
                """,
                (tid, username, mac,
                 hostname or "", dhcp_class_id or "", os_family or "",
                 device_brand or "", device_model or "",
                 ua_hash or "", ua_sample or "", vendor_oui or "",
                 nas_ip or "", called_station or "", nas_port or "",
                 nas_port_type or "",
                 "active", bind_confidence or "medium",
                 now, now, now),
            )
        else:
            def _keep(new, old):
                return new if (new or "").strip() else (old or "")

            conn.execute(
                """
                UPDATE mac_clone_bindings SET
                    hostname=?, dhcp_class_id=?, os_family=?,
                    device_brand=?, device_model=?,
                    ua_hash=?, ua_sample=?, vendor_oui=?,
                    nas_ip=?, called_station=?, nas_port=?, nas_port_type=?,
                    last_seen_at=?, last_verified_at=?,
                    verify_count = verify_count + 1
                WHERE tenant_id=? AND username=? AND mac=?
                """,
                (
                    _keep(hostname,      existing["hostname"]),
                    _keep(dhcp_class_id, existing["dhcp_class_id"]),
                    _keep(os_family,     existing["os_family"]),
                    _keep(device_brand,  existing["device_brand"]),
                    _keep(device_model,  existing["device_model"]),
                    _keep(ua_hash,       existing["ua_hash"]),
                    _keep(ua_sample,     existing["ua_sample"]),
                    _keep(vendor_oui,    existing["vendor_oui"]),
                    _keep(nas_ip,        existing["nas_ip"]),
                    _keep(called_station, existing["called_station"]),
                    _keep(nas_port,      existing["nas_port"]),
                    _keep(nas_port_type, existing["nas_port_type"]),
                    now, now,
                    tid, username, mac,
                ),
            )
        row = conn.execute(
            "SELECT * FROM mac_clone_bindings "
            "WHERE tenant_id = ? AND username = ? AND mac = ?",
            (tid, username, mac),
        ).fetchone()
    return _binding_row(row)


def bump_mismatch(tenant_id: int, username: str, mac: str) -> None:
    """يزيد عدّاد عدم التطابق على binding قائم. لا ينشئ صفًا."""
    mac = normalize_mac(mac)
    if not username or not mac:
        return
    with transaction() as conn:
        conn.execute(
            "UPDATE mac_clone_bindings "
            "SET mismatch_count = mismatch_count + 1, last_seen_at = ? "
            "WHERE tenant_id = ? AND username = ? AND mac = ?",
            (now_iso(), int(tenant_id), username, mac),
        )


def set_binding_status(tenant_id: int, binding_id: int, status: str) -> bool:
    if status not in ("active", "superseded", "suspended"):
        return False
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE mac_clone_bindings SET status = ? "
            "WHERE tenant_id = ? AND id = ?",
            (status, int(tenant_id), int(binding_id)),
        )
        return (cur.rowcount or 0) > 0


def delete_binding(tenant_id: int, binding_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM mac_clone_bindings "
            "WHERE tenant_id = ? AND id = ?",
            (int(tenant_id), int(binding_id)),
        )
        return (cur.rowcount or 0) > 0


# ════════════════════════════════════════════════════════════════════════
# Events
# ════════════════════════════════════════════════════════════════════════
_EVENT_COLS = (
    "id", "tenant_id", "username", "mac",
    "event_type", "decision", "confidence", "score",
    "signals", "nas_ip", "called_station", "nas_port",
    "reason", "created_at",
)


def _event_row(r) -> dict[str, Any]:
    out = {c: r[c] for c in _EVENT_COLS}
    raw = out.get("signals") or ""
    try:
        out["signals_obj"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        out["signals_obj"] = {}
    return out


def log_event(*, tenant_id: int, username: str, mac: str,
              event_type: str, decision: str = "",
              confidence: str = "", score: int = 0,
              signals: Optional[dict] = None,
              nas_ip: str = "", called_station: str = "",
              nas_port: str = "", reason: str = "") -> int:
    mac = normalize_mac(mac)
    payload = json.dumps(signals, ensure_ascii=False) if signals else ""
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO mac_clone_events
                (tenant_id, username, mac, event_type, decision, confidence,
                 score, signals, nas_ip, called_station, nas_port, reason,
                 created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (int(tenant_id), username or "", mac, event_type, decision or "",
             confidence or "", int(score or 0), payload,
             nas_ip or "", called_station or "", nas_port or "",
             reason or "", now_iso()),
        )
        return int(cur.lastrowid or 0)


def list_events(tenant_id: int, *, username: str = "", event_type: str = "",
                limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM mac_clone_events WHERE tenant_id = ?"
    vals: list[Any] = [int(tenant_id)]
    if username:
        sql += " AND username = ?"
        vals.append(username)
    if event_type:
        sql += " AND event_type = ?"
        vals.append(event_type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    vals.extend([int(limit), int(offset)])
    rows = db().execute(sql, vals).fetchall()
    return [_event_row(r) for r in rows]


def count_events_by_type(tenant_id: int, *, since: str = "") -> dict[str, int]:
    sql = ("SELECT event_type, COUNT(*) AS n FROM mac_clone_events "
           "WHERE tenant_id = ?")
    vals: list[Any] = [int(tenant_id)]
    if since:
        sql += " AND created_at >= ?"
        vals.append(since)
    sql += " GROUP BY event_type"
    rows = db().execute(sql, vals).fetchall()
    return {r["event_type"]: int(r["n"]) for r in rows}


__all__ = [
    "normalize_mac",
    "get_binding", "list_bindings", "list_bindings_for_user",
    "count_bindings", "upsert_binding", "bump_mismatch",
    "set_binding_status", "delete_binding",
    "log_event", "list_events", "count_events_by_type",
]
