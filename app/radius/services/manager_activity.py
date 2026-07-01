"""عدّادات نشاط المدير اليوميّة (A2) — تدعم سقف الإنفاق اليوميّ/الشهريّ
ومعدّلات الأفعال اليوميّة. تُخزَّن كعدّاد لكل (مدير، يوم، فعل). النوافذ
اليوميّة/الشهريّة تُشتقّ من ``day`` (YYYY-MM-DD) فتُصفَّر تلقائيًّا بتغيّر اليوم.

الحدود من limits_json عبر manager_grants:
  • spend_cap_daily / spend_cap_monthly (money؛ 0 = بلا حدّ)
  • rate_daily = {action_key: N}  (0/غياب = بلا حدّ)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db.connection import db
from ..db.helpers import now_iso


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _month_prefix() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _tid(tenant_id: int) -> int:
    return int(tenant_id or 1)


def record(admin_id: int, action_key: str, *, amount_minor: int = 0, tenant_id: int = 1) -> None:
    """يُسجّل حدثًا: يَزيد العدّاد +1 والمبلغ للمدير في يوم اليوم/الفعل."""
    if not admin_id:
        return
    tid = _tid(tenant_id)
    day = _today()
    row = db().execute(
        "SELECT id FROM manager_activity_counters "
        "WHERE tenant_id=? AND admin_id=? AND day=? AND action_key=?",
        (tid, int(admin_id), day, action_key or ""),
    ).fetchone()
    if row:
        db().execute(
            "UPDATE manager_activity_counters SET count=count+1, amount_minor=amount_minor+?, "
            "updated_at=? WHERE id=?",
            (int(amount_minor or 0), now_iso(), int(row["id"])),
        )
    else:
        db().execute(
            "INSERT INTO manager_activity_counters"
            "(tenant_id, admin_id, day, action_key, count, amount_minor, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (tid, int(admin_id), day, action_key or "", 1, int(amount_minor or 0), now_iso()),
        )


def action_count_today(admin_id: int, action_key: str, *, tenant_id: int = 1) -> int:
    try:
        row = db().execute(
            "SELECT count FROM manager_activity_counters "
            "WHERE tenant_id=? AND admin_id=? AND day=? AND action_key=?",
            (_tid(tenant_id), int(admin_id), _today(), action_key or ""),
        ).fetchone()
        return int(row["count"]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def spend_today(admin_id: int, *, tenant_id: int = 1) -> int:
    try:
        row = db().execute(
            "SELECT COALESCE(SUM(amount_minor),0) AS s FROM manager_activity_counters "
            "WHERE tenant_id=? AND admin_id=? AND day=?",
            (_tid(tenant_id), int(admin_id), _today()),
        ).fetchone()
        return int(row["s"] if row else 0)
    except Exception:  # noqa: BLE001
        return 0


def spend_month(admin_id: int, *, tenant_id: int = 1) -> int:
    try:
        row = db().execute(
            "SELECT COALESCE(SUM(amount_minor),0) AS s FROM manager_activity_counters "
            "WHERE tenant_id=? AND admin_id=? AND substr(day,1,7)=?",
            (_tid(tenant_id), int(admin_id), _month_prefix()),
        ).fetchone()
        return int(row["s"] if row else 0)
    except Exception:  # noqa: BLE001
        return 0


def rate_blocked(admin_id: Optional[int], action_key: str, *, tenant_id: int = 1) -> bool:
    """هل بلغ المدير معدّل هذا الفعل اليوميّ؟ (0/غياب = بلا حدّ)."""
    if not admin_id:
        return False
    from .manager_grants import _grants_row
    rd = (_grants_row(admin_id, tenant_id).get("limits") or {}).get("rate_daily") or {}
    try:
        limit = int(rd.get(action_key) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return False
    return action_count_today(int(admin_id), action_key, tenant_id=tenant_id) >= limit


def gate_and_record(admin_id: Optional[int], action_key: str, *, tenant_id: int = 1) -> bool:
    """للحارس: هل الفعل محجوبٌ بمعدّله اليوميّ؟ إن كان له حدٌّ مضبوط: يَرفض
    عند بلوغه (True)، وإلّا يُسجّل المحاولة ويَسمح (False). بلا حدّ = لا تسجيل."""
    if not admin_id:
        return False
    from .manager_grants import _grants_row
    rd = (_grants_row(admin_id, tenant_id).get("limits") or {}).get("rate_daily") or {}
    try:
        limit = int(rd.get(action_key) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return False
    if action_count_today(int(admin_id), action_key, tenant_id=tenant_id) >= limit:
        return True
    record(int(admin_id), action_key, tenant_id=tenant_id)
    return False


def spend_block_reason(admin_id: Optional[int], add_minor: int, *, tenant_id: int = 1) -> Optional[str]:
    """يُرجع سبب المنع (عربيّ) إن كان إنفاق ``add_minor`` يَتجاوز السقف اليوميّ
    أو الشهريّ — أو None. (0 = بلا حدّ.) المبالغ minor."""
    if not admin_id or add_minor <= 0:
        return None
    from .manager_grants import _grants_row
    from .business_os_finance import money_to_minor
    lims = _grants_row(admin_id, tenant_id).get("limits") or {}

    def _cap_minor(key):
        try:
            return money_to_minor(lims.get(key) or 0)
        except Exception:  # noqa: BLE001
            return 0

    daily_cap = _cap_minor("spend_cap_daily")
    monthly_cap = _cap_minor("spend_cap_monthly")
    if daily_cap > 0 and spend_today(int(admin_id), tenant_id=tenant_id) + add_minor > daily_cap:
        return "يتجاوز سقف الإنفاق اليوميّ المسموح لك."
    if monthly_cap > 0 and spend_month(int(admin_id), tenant_id=tenant_id) + add_minor > monthly_cap:
        return "يتجاوز سقف الإنفاق الشهريّ المسموح لك."
    return None


__all__ = [
    "record", "action_count_today", "spend_today", "spend_month",
    "rate_blocked", "spend_block_reason", "gate_and_record",
]
