"""شحن الشبكات — MT46.

لوحة المزوّد لإدارة اشتراكات عملائه: رصيد ماليّ، أيّام مدفوعة، أيّام
مجانيّة، وتمديد. كل عمليّة تُوثَّق في ``tenant_topup_ledger`` وتُحدّث
التاريخ الفعليّ على ``tenants`` (paid_until / trial_ends_at).

قرارات صريحة:
  • «أيّام مدفوعة» تُمدّد ``paid_until`` وتُحوّل الشبكة إلى ``paid``؛ إن
    مرّرتَ مبلغًا يُخصَم من الرصيد (ويُسمَح بالسالب — دَينٌ على العميل،
    لا نَحجب الخدمة تلقائيًّا).
  • «أيّام مجانيّة» تُمدّد paid_until بلا مبلغ ولا مسّ الرصيد.
  • التمديد دائمًا من **الأبعد** بين الآن ونهاية الاشتراك الحاليّة — كي
    لا يَبتلع تمديدٌ مبكّر ما تبقّى من مدّة سارية.

العزل: owner-only يفرضه المسار؛ كل استعلام هنا مُقيَّد بـtenant_id.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from ..db.connection import db, transaction
from ..db.helpers import now_iso

_MAX_DAYS = 3650   # عشر سنوات — سقفٌ للسلامة لا سياسة


class TopupError(Exception):
    pass


def _tenant_row(conn, tenant_id: int):
    r = conn.execute("SELECT credit_balance FROM tenants WHERE id=?",
                     (int(tenant_id),)).fetchone()
    if r is None:
        raise TopupError("الشبكة غير موجودة.")
    return r


def balance(tenant_id: int) -> float:
    r = db().execute("SELECT credit_balance FROM tenants WHERE id=?",
                     (int(tenant_id),)).fetchone()
    return float(r["credit_balance"]) if r else 0.0


def _extend_from_furthest(current: Optional[str], days: int) -> datetime:
    """يُمدّد من الأبعد بين الآن والنهاية الحاليّة + days."""
    now = datetime.utcnow()
    base = now
    if current:
        try:
            cur = datetime.fromisoformat(str(current).replace("Z", "")[:19])
            if cur > now:
                base = cur
        except Exception:  # noqa: BLE001
            pass
    return base + timedelta(days=days)


def _clamp_days(days: Any) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        raise TopupError("عدد الأيّام غير صحيح.")
    if d < 1 or d > _MAX_DAYS:
        raise TopupError(f"عدد الأيّام يجب أن يكون بين ١ و{_MAX_DAYS}.")
    return d


def _amount(value: Any) -> float:
    try:
        a = float(value or 0)
    except (TypeError, ValueError):
        raise TopupError("المبلغ غير صحيح.")
    return round(a, 2)


def add_credit(*, tenant_id: int, amount: float, note: str = "",
               actor: str = "") -> dict[str, Any]:
    """يُضيف (أو يَخصم، إن كان سالبًا) رصيدًا ماليًّا للشبكة."""
    amt = _amount(amount)
    if amt == 0:
        raise TopupError("المبلغ صفر — لا حركة.")
    with transaction() as conn:
        cur = float(_tenant_row(conn, tenant_id)["credit_balance"])
        new_bal = round(cur + amt, 2)
        conn.execute("UPDATE tenants SET credit_balance=? WHERE id=?",
                     (new_bal, int(tenant_id)))
        _log(conn, tenant_id, "credit", amount=amt, days=0,
             balance_after=new_bal, note=note, actor=actor)
    return {"balance": new_bal, "amount": amt}


def add_paid_days(*, tenant_id: int, days: Any, amount: Any = 0,
                  charge_balance: bool = False, note: str = "",
                  actor: str = "") -> dict[str, Any]:
    """يُمدّد الاشتراك المدفوع بـ``days`` ويُحوّل الشبكة إلى paid.

    ``amount`` مبلغ الشحنة (اختياريّ، للسجلّ). ``charge_balance`` يَخصمه
    من الرصيد (يُسمَح بالسالب = دَين)."""
    from .tenants import get_tenants_service
    d = _clamp_days(days)
    amt = _amount(amount)
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        raise TopupError("الشبكة غير موجودة.")
    new_end = _extend_from_furthest(
        t.paid_until.isoformat() if t.paid_until else None, d)
    svc.update(actor=actor, tenant_id=tenant_id,
               billing_mode="paid", paid_until=new_end)
    with transaction() as conn:
        bal = float(_tenant_row(conn, tenant_id)["credit_balance"])
        if charge_balance and amt:
            bal = round(bal - amt, 2)
            conn.execute("UPDATE tenants SET credit_balance=? WHERE id=?",
                         (bal, int(tenant_id)))
        _log(conn, tenant_id, "paid_days", amount=amt, days=d,
             balance_after=bal, note=note, actor=actor)
    return {"paid_until": new_end, "balance": bal, "days": d}


def add_free_days(*, tenant_id: int, days: Any, note: str = "",
                  actor: str = "") -> dict[str, Any]:
    """يُمدّد الاشتراك بـ``days`` **مجّانًا** (بلا مبلغ ولا مسّ الرصيد).

    يُمدّد paid_until إن كانت الشبكة مدفوعة، وإلّا trial_ends_at — فيَقع
    التمديد في الحقل الذي يُنفَّذ فعلًا لكل حالة."""
    from .tenants import get_tenants_service
    from ..core.tenant import TENANT_STATUS_TRIAL
    d = _clamp_days(days)
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        raise TopupError("الشبكة غير موجودة.")
    if (t.billing_mode or "free") == "paid":
        new_end = _extend_from_furthest(
            t.paid_until.isoformat() if t.paid_until else None, d)
        svc.update(actor=actor, tenant_id=tenant_id, paid_until=new_end)
        field = "paid_until"
    else:
        new_end = _extend_from_furthest(
            t.trial_ends_at.isoformat() if t.trial_ends_at else None, d)
        svc.update(actor=actor, tenant_id=tenant_id,
                   status=TENANT_STATUS_TRIAL, trial_ends_at=new_end)
        field = "trial_ends_at"
    with transaction() as conn:
        bal = float(_tenant_row(conn, tenant_id)["credit_balance"])
        _log(conn, tenant_id, "free_days", amount=0, days=d,
             balance_after=bal, note=note, actor=actor)
    return {"field": field, "until": new_end, "days": d}


def ledger(tenant_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT * FROM tenant_topup_ledger WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def recent(*, limit: int = 40) -> list[dict[str, Any]]:
    """آخر حركات الشحن عبر كل الشبكات — للعرض العامّ في اللوحة."""
    rows = db().execute(
        "SELECT * FROM tenant_topup_ledger ORDER BY id DESC LIMIT ?",
        (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def _log(conn, tenant_id, kind, *, amount, days, balance_after, note, actor):
    conn.execute(
        "INSERT INTO tenant_topup_ledger"
        " (tenant_id, kind, amount, days, balance_after, note, actor, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (int(tenant_id), kind, float(amount), int(days), float(balance_after),
         str(note or "")[:300], str(actor or "")[:120], now_iso()))
