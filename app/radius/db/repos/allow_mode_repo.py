"""allow_mode_repo — مخزن «نمط السماح» (feat/access-control).

جدولان (migration 126):
  • allow_mode_policies — سياسة الواحدة لكل (مستأجر، نطاق، معرّف نطاق):
    mode (open|tofu|manual) + max_devices + active. UNIQUE.
  • allow_mode_devices  — أجهزة كل سياسة. username='' = مشترك بين كل
    مستخدمي النطاق. UNIQUE(policy_id, username, mac).

كل القراءات/الكتابات tenant-scoped (السياسة تحمل tenant_id، والأجهزة
ترتبط بسياسة). تطبيع MAC مركزي (UPPER + ':'). محصّن: أخطاء القراءة في
المسارات الساخنة (auth) تُلتقط في طبقة الخدمة، لا هنا.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


# ─────────────────────────────────────────────────────────────────────
# تطبيع MAC
# ─────────────────────────────────────────────────────────────────────
def normalize_mac(mac: str) -> str:
    return (mac or "").strip().upper().replace("-", ":")


VALID_MODES = ("open", "tofu", "manual")
VALID_SCOPES = ("plan", "card_batch")


# ════════════════════════════════════════════════════════════════════════
# Policies
# ════════════════════════════════════════════════════════════════════════
_POLICY_COLS = (
    "id", "tenant_id", "scope_type", "scope_id", "mode", "max_devices",
    "active", "note", "created_by", "created_at", "updated_at",
)


def _policy_row(r) -> dict[str, Any]:
    return {c: r[c] for c in _POLICY_COLS}


def get_policy(tenant_id: int, scope_type: str,
               scope_id: int) -> Optional[dict[str, Any]]:
    """يُرجع السياسة النشطة المطابقة، أو None. آمن للاستخدام في hot path."""
    if scope_type not in VALID_SCOPES or scope_id is None:
        return None
    row = db().execute(
        "SELECT * FROM allow_mode_policies "
        "WHERE tenant_id = ? AND scope_type = ? AND scope_id = ? "
        "  AND active = 1",
        (int(tenant_id), scope_type, int(scope_id)),
    ).fetchone()
    return _policy_row(row) if row else None


def get_policy_by_id(tenant_id: int, policy_id: int) -> Optional[dict[str, Any]]:
    row = db().execute(
        "SELECT * FROM allow_mode_policies WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return _policy_row(row) if row else None


def list_policies(tenant_id: int, *, scope_type: str = "",
                  active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM allow_mode_policies WHERE tenant_id = ?"
    vals: list[Any] = [int(tenant_id)]
    if scope_type:
        sql += " AND scope_type = ?"
        vals.append(scope_type)
    if active_only:
        sql += " AND active = 1"
    sql += " ORDER BY scope_type, scope_id"
    rows = db().execute(sql, vals).fetchall()
    return [_policy_row(r) for r in rows]


def upsert_policy(*, tenant_id: int, scope_type: str, scope_id: int,
                  mode: str, max_devices: int,
                  active: bool = True, note: str = "",
                  by: int = 0) -> dict[str, Any]:
    """يُنشئ أو يُحدّث سياسة. يرفع ValueError لمدخلات غير صالحة."""
    if scope_type not in VALID_SCOPES:
        raise ValueError(f"scope_type غير صالح: {scope_type}")
    if mode not in VALID_MODES:
        raise ValueError(f"mode غير صالح: {mode}")
    if scope_id is None or int(scope_id) <= 0:
        raise ValueError("scope_id مطلوب")
    now = now_iso()
    tid = int(tenant_id)
    sid = int(scope_id)
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM allow_mode_policies "
            "WHERE tenant_id = ? AND scope_type = ? AND scope_id = ?",
            (tid, scope_type, sid),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO allow_mode_policies
                   (tenant_id, scope_type, scope_id, mode, max_devices,
                    active, note, created_by, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (tid, scope_type, sid, mode, int(max_devices),
                 1 if active else 0, note or "",
                 int(by), now, now),
            )
        else:
            conn.execute(
                """UPDATE allow_mode_policies SET
                       mode = ?, max_devices = ?,
                       active = ?, note = ?, updated_at = ?
                   WHERE id = ?""",
                (mode, int(max_devices), 1 if active else 0,
                 note or "", now, int(existing["id"])),
            )
        row = conn.execute(
            "SELECT * FROM allow_mode_policies "
            "WHERE tenant_id = ? AND scope_type = ? AND scope_id = ?",
            (tid, scope_type, sid),
        ).fetchone()
    return _policy_row(row)


def delete_policy(tenant_id: int, policy_id: int) -> bool:
    """يحذف السياسة + كل أجهزتها (CASCADE يدويًّا — لا FK في SQLite عادةً)."""
    with transaction() as conn:
        conn.execute(
            "DELETE FROM allow_mode_devices "
            "WHERE policy_id IN (SELECT id FROM allow_mode_policies "
            "                    WHERE tenant_id = ? AND id = ?)",
            (int(tenant_id), int(policy_id)),
        )
        cur = conn.execute(
            "DELETE FROM allow_mode_policies "
            "WHERE tenant_id = ? AND id = ?",
            (int(tenant_id), int(policy_id)),
        )
        return (cur.rowcount or 0) > 0


def set_policy_active(tenant_id: int, policy_id: int, active: bool) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE allow_mode_policies SET active = ?, updated_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (1 if active else 0, now_iso(),
             int(tenant_id), int(policy_id)),
        )
        return (cur.rowcount or 0) > 0


# ════════════════════════════════════════════════════════════════════════
# Devices
# ════════════════════════════════════════════════════════════════════════
_DEVICE_COLS = (
    "id", "policy_id", "username", "mac", "source", "label",
    "last_seen_at", "use_count", "created_by", "created_at",
)


def _device_row(r) -> dict[str, Any]:
    return {c: r[c] for c in _DEVICE_COLS}


def list_devices(policy_id: int, *, username: str = "") -> list[dict[str, Any]]:
    """قائمة أجهزة السياسة. لو username محدّد فقط الشخصي + المشترك ('')."""
    if username:
        rows = db().execute(
            "SELECT * FROM allow_mode_devices "
            "WHERE policy_id = ? AND (username = ? OR username = '') "
            "ORDER BY username DESC, created_at DESC",
            (int(policy_id), username),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM allow_mode_devices "
            "WHERE policy_id = ? "
            "ORDER BY username, created_at DESC",
            (int(policy_id),),
        ).fetchall()
    return [_device_row(r) for r in rows]


def count_devices(policy_id: int, *, username: str = "",
                   source: str = "") -> int:
    sql = "SELECT COUNT(*) AS n FROM allow_mode_devices WHERE policy_id = ?"
    vals: list[Any] = [int(policy_id)]
    if username:
        sql += " AND username = ?"
        vals.append(username)
    if source:
        sql += " AND source = ?"
        vals.append(source)
    row = db().execute(sql, vals).fetchone()
    return int(row["n"]) if row else 0


def find_device_match(policy_id: int, *, username: str,
                       mac: str) -> Optional[dict[str, Any]]:
    """يبحث عن جهاز يطابق هذا (username, mac) — يفضّل المباراة الشخصية ثم
    المشتركة (username=''). يعيد None لو لا تطابق."""
    mac = normalize_mac(mac)
    if not mac:
        return None
    row = db().execute(
        "SELECT * FROM allow_mode_devices "
        "WHERE policy_id = ? AND mac = ? AND (username = ? OR username = '') "
        "ORDER BY CASE WHEN username = '' THEN 1 ELSE 0 END "
        "LIMIT 1",
        (int(policy_id), mac, username or ""),
    ).fetchone()
    return _device_row(row) if row else None


def add_device(*, policy_id: int, username: str, mac: str,
               source: str = "manual", label: str = "",
               by: int = 0) -> Optional[dict[str, Any]]:
    """يُضيف جهازًا. لو موجود (نفس policy/username/mac) يرجع الصفّ القائم
    دون استثناء. يطبّع MAC. لا يَفرض حدًّا — الفرض في طبقة الخدمة."""
    mac = normalize_mac(mac)
    if not mac:
        return None
    if source not in ("manual", "auto"):
        source = "manual"
    now = now_iso()
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM allow_mode_devices "
            "WHERE policy_id = ? AND username = ? AND mac = ?",
            (int(policy_id), username or "", mac),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO allow_mode_devices
                   (policy_id, username, mac, source, label,
                    last_seen_at, use_count, created_by, created_at)
                   VALUES (?,?,?,?,?,?,0,?,?)""",
                (int(policy_id), username or "", mac, source,
                 label or "", "", int(by), now),
            )
        row = conn.execute(
            "SELECT * FROM allow_mode_devices "
            "WHERE policy_id = ? AND username = ? AND mac = ?",
            (int(policy_id), username or "", mac),
        ).fetchone()
    return _device_row(row) if row else None


def touch_device(device_id: int) -> None:
    """يُحدّث آخر ظهور + use_count. لا يكسر مسار الـauth."""
    try:
        with transaction() as conn:
            conn.execute(
                "UPDATE allow_mode_devices "
                "SET last_seen_at = ?, use_count = use_count + 1 "
                "WHERE id = ?",
                (now_iso(), int(device_id)),
            )
    except Exception:  # noqa: BLE001
        pass


def delete_device(device_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM allow_mode_devices WHERE id = ?",
            (int(device_id),),
        )
        return (cur.rowcount or 0) > 0


__all__ = [
    "VALID_MODES", "VALID_SCOPES", "normalize_mac",
    # policies
    "get_policy", "get_policy_by_id", "list_policies",
    "upsert_policy", "delete_policy", "set_policy_active",
    # devices
    "list_devices", "count_devices", "find_device_match",
    "add_device", "touch_device", "delete_device",
]
