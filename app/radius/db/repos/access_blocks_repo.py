"""access_blocks — repo لـ«التحكم بالدخول» بطبقتيه (migration 123).

جدولان (التخزين مشترك للطبقتين، التمييز بعمود ``layer``):
  * access_blocks — سجلّات «تعليق الوصول» (نطاقي) و«الحظر» (IP/MAC)، 3 أنماط مدّة.
  * login_failure_tracker — عدّاد محاولات الفشل للحظر التلقائي (fail2ban).

كله tenant-scoped. لا منطق إنفاذ هنا (ذلك في services/access_control.py)؛
هذا المستوى I/O خام فقط.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso, row_to_dict

# الأنواع المسموحة (تُحقَّق في طبقة الخدمة أيضًا).
BLOCK_TYPES = (
    "subscriber", "group", "plan", "card_batch",
    "all_subscribers", "all_hotspot", "all_cards", "all_pppoe",
    "ip", "mac",
)
DURATION_MODES = ("permanent", "daily_window", "until")


# ════════════════════════════════════════════════════════════════════════
# access_blocks
# ════════════════════════════════════════════════════════════════════════


def create_block(
    *, tenant_id: int, block_type: str, target: str = "", reason: str = "",
    duration_mode: str = "permanent", window_start: str = "", window_end: str = "",
    expires_at: str = "", source: str = "manual", created_by: int = 0,
    layer: str = "suspension",
) -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO access_blocks
                (tenant_id, layer, block_type, target, reason, duration_mode,
                 window_start, window_end, expires_at, source, active,
                 created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (int(tenant_id), str(layer or "suspension"), str(block_type),
             str(target or ""), str(reason or ""), str(duration_mode),
             str(window_start or ""), str(window_end or ""),
             str(expires_at or ""), str(source or "manual"), int(created_by or 0),
             now, now),
        )
        return int(cur.lastrowid)


def list_blocks(tenant_id: int, *, active_only: bool = False,
                layer: Optional[str] = None, limit: int = 1000) -> list[dict]:
    sql = "SELECT * FROM access_blocks WHERE tenant_id = ?"
    vals: list = [int(tenant_id)]
    if active_only:
        sql += " AND active = 1"
    if layer:
        sql += " AND layer = ?"
        vals.append(str(layer))
    sql += " ORDER BY active DESC, id DESC LIMIT ?"
    vals.append(int(limit))
    return [row_to_dict(r) for r in db().execute(sql, vals).fetchall()]


def get_block(tenant_id: int, block_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM access_blocks WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(block_id)),
    ).fetchone()
    return row_to_dict(row) if row else None


def clear_block(tenant_id: int, block_id: int, *, by: int = 0) -> bool:
    """رفع الحظر (active=0 + ختم الإلغاء). يُعيد True لو غيّر صفًّا فعّالًا."""
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE access_blocks SET active = 0, cleared_at = ?, cleared_by = ?, "
            "updated_at = ? WHERE tenant_id = ? AND id = ? AND active = 1",
            (now_iso(), int(by or 0), now_iso(), int(tenant_id), int(block_id)),
        )
        return cur.rowcount > 0


def deactivate_expired(tenant_id: int, *, now: Optional[str] = None) -> int:
    """ينهي تلقائيًا حظور النمط ``until`` التي تجاوزت expires_at.

    يُعيد عدد الصفوف المُعطَّلة. يُستدعى كنسحٍ كسول من مسار الإنفاذ
    وكذلك يمكن جدولته. المقارنة نصّية على ISO-8601 (مرتّبة معجميًّا)."""
    ts = now or now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE access_blocks SET active = 0, cleared_at = ?, updated_at = ? "
            "WHERE tenant_id = ? AND active = 1 AND duration_mode = 'until' "
            "AND expires_at != '' AND expires_at <= ?",
            (ts, ts, int(tenant_id), ts),
        )
        return cur.rowcount


# ════════════════════════════════════════════════════════════════════════
# login_failure_tracker (fail2ban)
# ════════════════════════════════════════════════════════════════════════


def record_failure(*, tenant_id: int, ip: str = "", mac: str = "",
                   username: str = "") -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO login_failure_tracker (tenant_id, ip, mac, username, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(tenant_id), str(ip or ""), str(mac or ""),
             str(username or ""), now_iso()),
        )


def count_recent_failures(tenant_id: int, *, ip: Optional[str] = None,
                          mac: Optional[str] = None, since: str) -> int:
    """عدد المحاولات الفاشلة منذ ``since`` (ISO) لمطابقة IP أو MAC.

    يُمرَّر واحد من ip/mac على الأقل. المقارنة على القيمة غير الفارغة فقط."""
    sql = "SELECT COUNT(*) AS c FROM login_failure_tracker WHERE tenant_id = ? AND created_at >= ?"
    vals: list = [int(tenant_id), str(since)]
    if ip:
        sql += " AND ip = ?"
        vals.append(str(ip))
    if mac:
        sql += " AND mac = ?"
        vals.append(str(mac))
    row = db().execute(sql, vals).fetchone()
    return int(row["c"]) if row else 0


def purge_old_failures(tenant_id: int, *, before: str) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM login_failure_tracker WHERE tenant_id = ? AND created_at < ?",
            (int(tenant_id), str(before)),
        )
        return cur.rowcount


def has_active_block(tenant_id: int, *, block_type: str, target: str) -> bool:
    """هل يوجد حظر فعّال بنفس النوع/الهدف؟ (لمنع تكرار الحظر التلقائي)."""
    row = db().execute(
        "SELECT 1 FROM access_blocks WHERE tenant_id = ? AND active = 1 "
        "AND block_type = ? AND target = ? LIMIT 1",
        (int(tenant_id), str(block_type), str(target or "")),
    ).fetchone()
    return row is not None


__all__ = [
    "BLOCK_TYPES", "DURATION_MODES",
    "create_block", "list_blocks", "get_block", "clear_block",
    "deactivate_expired", "record_failure", "count_recent_failures",
    "purge_old_failures", "has_active_block",
]
