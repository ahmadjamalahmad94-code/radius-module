"""plan_lifecycle — «تجديد تلقائي» و«استخدام مرة وحدة» على مستوى الباقة.

قرارات المالك (2026-10-06):

• «تجديد تلقائي» نمطٌ لكلّ باقة (``access_plans.auto_renew_mode``):
    off      بدون (الافتراض)
    debt     «مسموح بالدين» — يُجدَّد عند الانتهاء ويُسجَّل السعر دينًا على المشترك
    balance  «خصم من الرصيد المتاح» — يُجدَّد فقط إن غطّى رصيدُه السعر فيُخصم؛
             وإلّا لا تجديد (ينتهي عاديًّا) ويُنبَّه المدراء
    free     «مجاني»
  التجديد = تمديدٌ بفترة الباقة المعتادة **من لحظة الانتهاء** (سقف المالك: سنة في
  المرّة). يمرّ بنفس خدمة التمديد اليدويّ (``subscriber_actions.extend_subscriber``)
  فيكتب نفس الدفتر/التدقيق/التنبيه ويدفع أثره للجلسة الحيّة كالتمديد اليدويّ — لا
  مسار مالٍ موازٍ. المطالبة في ``plan_auto_renewals`` (مفتاح: مشترك + لحظة
  الانتهاء) تمنع تجديد الفترة نفسها مرّتين. تنبيه إدارة «تجديد تلقائي» بالنتيجة.

• «استخدام مرة وحدة» = **حساب مؤقّت** (مثلًا زبون يرفع ملفًّا كبيرًا لساعة): عند
  انتهاء وقته يُعطَّل الحساب تلقائيًّا (status=disabled، يُطرد إن كان متّصلًا عبر
  مسار التعطيل نفسه)، ولا يُجدَّد ولا يُمدَّد (``reject_single_use``). الحذف مسموح.

المُشغِّل: ``sweep()`` من عامل «نافذة الجدولة» كلّ دقيقة (نفس دورة كنسة الكوتة).
كلّ شيء محصّن: فشل مشتركٍ لا يوقف الباقين ولا يكسر العامل.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from ..core.errors import RadiusError, RadiusValidationError

_LOG = logging.getLogger(__name__)

SINGLE_USE_MSG = N_("هاد حساب مؤقت للاستخدام مرة وحدة")
ACTOR_AUTO_RENEW = "system:auto-renew"
ACTOR_SINGLE_USE = "system:single-use"

# يُجدَّد ما ينتهي خلال دقيقتين (العامل كلّ دقيقة) — فيُمدَّد من لحظة انتهائه
# قبل أن يُرفض. ما انتهى منذ أكثر من ربع ساعة (عامل متوقّف) لا يُجدَّد بأثرٍ رجعيّ.
RENEW_LEAD = timedelta(minutes=2)
RENEW_LOOKBACK = timedelta(minutes=15)

_CHARGE = {"debt": "debt", "balance": "paid", "free": "free"}
_FMT = "%Y-%m-%d %H:%M:%S"
_EXP = "replace(replace(s.expire_at, 'T', ' '), 'Z', '')"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _plan(tenant_id: int, plan_id) -> Optional[object]:
    if not plan_id:
        return None
    try:
        from ..db.repos import plans_repo
        return plans_repo.get_plan(int(tenant_id or 1), int(plan_id), include_deleted=True)
    except Exception:  # noqa: BLE001
        return None


def is_single_use(sub, plan=None) -> bool:
    """مشتركٌ (لا بطاقة) على باقة «استخدام مرة وحدة»."""
    if sub is None:
        return False
    if getattr(sub, "user_type", "") == "card" or getattr(sub, "card_batch_id", None):
        return False
    plan = plan if plan is not None else _plan(getattr(sub, "tenant_id", 1),
                                               getattr(sub, "plan_id", None))
    return bool(plan is not None and getattr(plan, "single_use_once", False))


def reject_single_use(sub, plan=None) -> None:
    """التجديد/التمديد/الدفع-للوقت لحسابٍ مؤقّت ⇒ 422 «هاد حساب مؤقت…».

    الحساب الذي لم يُفعَّل بعد (بلا انتهاء: «أنشئ ثم ادفع») يُسمح بتفعيله مرّة."""
    if is_single_use(sub, plan) and getattr(sub, "expire_at", None) is not None:
        raise RadiusValidationError(_tr(SINGLE_USE_MSG))


@contextmanager
def _tenant_ctx(tenant_id: int):
    """``g.tenant_id`` للمحوّل خارج الطلب (العامل). سياق تطبيقٍ قائم يُعاد له."""
    from flask import Flask, g, has_app_context
    if has_app_context():
        old = getattr(g, "tenant_id", None)
        g.tenant_id = int(tenant_id)
        try:
            yield
        finally:
            if old is None:
                g.pop("tenant_id", None)
            else:
                g.tenant_id = old
        return
    with Flask("plan-lifecycle").app_context():
        g.tenant_id = int(tenant_id)
        yield


# ─────────────── «استخدام مرة وحدة» — تعطيل عند الانتهاء ───────────────

def disable_expired_single_use(tenant_id: int, now: Optional[datetime] = None) -> list[str]:
    from ..db.connection import db
    from .users import get_users_service
    now_s = (now or _utcnow()).strftime(_FMT)
    rows = db().execute(
        "SELECT s.username FROM subscribers s "
        "  JOIN access_plans p ON p.id = s.plan_id AND p.tenant_id = s.tenant_id "
        " WHERE s.tenant_id = ? AND COALESCE(p.single_use_once, 0) = 1 "
        "   AND COALESCE(s.user_type, 'subscriber') != 'card' AND s.card_batch_id IS NULL "
        "   AND s.deleted_at IS NULL AND COALESCE(s.status, '') != 'disabled' "
        f"  AND s.expire_at IS NOT NULL AND s.expire_at != '' AND {_EXP} <= ?",
        (int(tenant_id), now_s)).fetchall()
    done = []
    svc = get_users_service()
    for r in rows:
        username = str(r["username"])
        try:
            # نفس «تعطيل» اليدويّ: الحالة + تدقيق + طرد الجلسة الحيّة (PoD).
            svc.disable(actor=ACTOR_SINGLE_USE, username=username)
            done.append(username)
        except Exception:  # noqa: BLE001
            _LOG.exception("single-use disable failed for %s", username)
    return done


# ─────────────── «تجديد تلقائي» ───────────────

def _claim(tenant_id: int, sub_id: int, period_end: str, username: str,
           plan_id, mode: str) -> bool:
    from ..db.connection import db
    cur = db().execute(
        "INSERT OR IGNORE INTO plan_auto_renewals (tenant_id, subscriber_id, period_end, "
        "username, plan_id, mode, result, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (int(tenant_id), int(sub_id), period_end, username, plan_id, mode,
         _utcnow().strftime(_FMT)))
    return bool(cur.rowcount)


def _finish(tenant_id: int, sub_id: int, period_end: str, **fields) -> None:
    from ..db.connection import db
    fields["updated_at"] = _utcnow().strftime(_FMT)
    sets = ", ".join(f"{k} = ?" for k in fields)
    db().execute(
        f"UPDATE plan_auto_renewals SET {sets} WHERE tenant_id = ? AND subscriber_id = ? "
        "AND period_end = ?", (*fields.values(), int(tenant_id), int(sub_id), period_end))


_RESULT_AR = {
    "renewed": N_("تمّ التجديد"),
    "insufficient": N_("لم يُجدَّد — الرصيد لا يكفي"),
    "stale": N_("لم يُجدَّد — انتهى منذ مدّة"),
    "failed": N_("فشل التجديد"),
}


def _notify(tenant_id: int, *, username: str, mode: str, result: str, amount: float,
            currency: str, new_expiry: str, message: str, key: str) -> None:
    try:
        from .admin_alerts import dispatch
        from .plans import AUTO_RENEW_MODES
        from .users import _fmt_money_ar
        dispatch(int(tenant_id), "auto_renew", {
            "username": username,
            "mode": _tr(AUTO_RENEW_MODES.get(mode, mode)),
            "result": _tr(_RESULT_AR.get(result, result)),
            "amount": _fmt_money_ar(amount, currency) if amount else "—",
            "new_expiry": new_expiry or "—",
            "details": message or "—",
        }, dedup_key=key)
    except Exception:  # noqa: BLE001
        _LOG.warning("auto-renew notify failed for %s", username, exc_info=True)


def renew_due(tenant_id: int, now: Optional[datetime] = None) -> list[dict]:
    """يجدّد كلّ مشتركٍ تنتهي فترته الآن على باقةٍ تجديدها التلقائيّ مفعَّل."""
    from ..core import limits
    from ..core.system_config import default_currency, to_local
    from ..db.connection import db
    from .accounting import AccountingService
    from .subscriber_actions import ActionCaller, extend_subscriber
    from .users import get_users_service, plan_period_minutes

    now = now or _utcnow()
    rows = db().execute(
        "SELECT s.id, s.username, s.expire_at, p.id AS plan_id, p.auto_renew_mode AS mode "
        "  FROM subscribers s JOIN access_plans p "
        "    ON p.id = s.plan_id AND p.tenant_id = s.tenant_id "
        " WHERE s.tenant_id = ? AND p.auto_renew_mode IN ('debt', 'balance', 'free') "
        "   AND COALESCE(p.single_use_once, 0) = 0 "
        "   AND COALESCE(s.user_type, 'subscriber') != 'card' AND s.card_batch_id IS NULL "
        "   AND s.deleted_at IS NULL AND COALESCE(s.status, 'enabled') = 'enabled' "
        f"  AND s.expire_at IS NOT NULL AND s.expire_at != '' AND {_EXP} > ? AND {_EXP} <= ?",
        (int(tenant_id), (now - RENEW_LOOKBACK).strftime(_FMT),
         (now + RENEW_LEAD).strftime(_FMT))).fetchall()
    out = []
    caller = ActionCaller(tenant_id=int(tenant_id), admin_id=None, is_super=True,
                          actor=ACTOR_AUTO_RENEW)
    for r in rows:
        sid, username, mode = int(r["id"]), str(r["username"]), str(r["mode"])
        period_end = str(r["expire_at"]).replace("T", " ").replace("Z", "")[:19]
        if not _claim(tenant_id, sid, period_end, username, r["plan_id"], mode):
            continue                          # هذه الفترة جُدِّدت/حوولت سلفًا
        result, amount, currency, new_exp_s, msg = "failed", 0.0, "", "", ""
        try:
            svc = get_users_service()
            sub = svc.get(username)
            plan = _plan(tenant_id, r["plan_id"])
            period = int(plan_period_minutes(plan) or 0)
            minutes = min(period, int(limits.max_extend_minutes()))
            price = float(AccountingService(int(tenant_id)).price_basis(sub)["price"] or 0)
            if period and minutes < period:
                price = price * minutes / period
            amount = round(price, 2) if mode != "free" else 0.0
            currency = (getattr(plan, "currency", "") or "").strip().upper() or default_currency()
            charge = _CHARGE[mode] if amount > 0 else "free"
            old_exp = sub.expire_at
            target = old_exp + timedelta(minutes=minutes)
            if minutes <= 0 or target <= now:
                result, msg = "stale", ""
            else:
                kw = dict(charge_mode=charge, amount=amount, currency=currency,
                          notes=_tr("تجديد تلقائي — %(m)s", m=_tr(_mode_label(mode))))
                if old_exp > now:
                    # المرساة max(نهايته، الآن) = نهايته ⇒ من لحظة الانتهاء بالضبط.
                    saved = extend_subscriber(caller, username, minutes=minutes, **kw)
                else:
                    saved = extend_subscriber(caller, username, expire_at=target, **kw)
                result = "renewed"
                new_exp_s = to_local(saved.expire_at, tenant_id=int(tenant_id))
        except RadiusValidationError as e:
            result = "insufficient" if mode == "balance" else "failed"
            msg = getattr(e, "message", "") or str(e)
        except RadiusError as e:
            msg = getattr(e, "message", "") or str(e)
        except Exception as e:  # noqa: BLE001
            _LOG.exception("auto-renew failed for %s", username)
            msg = str(e)[:300]
        try:
            _finish(tenant_id, sid, period_end, result=result, amount=amount,
                    currency=currency, new_expire_at=new_exp_s, message=msg[:500])
        except Exception:  # noqa: BLE001
            _LOG.warning("auto-renew claim update failed for %s", username, exc_info=True)
        _notify(tenant_id, username=username, mode=mode, result=result, amount=amount,
                currency=currency, new_expiry=new_exp_s, message=msg,
                key=f"auto_renew:{sid}:{period_end}")
        out.append({"username": username, "result": result, "amount": amount,
                    "currency": currency, "new_expire_at": new_exp_s, "message": msg})
    return out


def _mode_label(mode: str) -> str:
    from .plans import AUTO_RENEW_MODES
    return AUTO_RENEW_MODES.get(mode, mode)


def _active_tenants() -> list[int]:
    try:
        from .schedule_window import _active_tenant_ids
        return _active_tenant_ids()
    except Exception:  # noqa: BLE001
        return [1]


def sweep(tenant_id: Optional[int] = None, now: Optional[datetime] = None) -> dict:
    """دورة واحدة: تجديد المستحقّين ثمّ تعطيل الحسابات المؤقّتة المنتهية."""
    stats = {"renewed": 0, "renew_skipped": 0, "single_use_disabled": 0}
    tenants = [int(tenant_id)] if tenant_id is not None else _active_tenants()
    for tid in tenants:
        try:
            with _tenant_ctx(tid):
                for item in renew_due(tid, now):
                    if item["result"] == "renewed":
                        stats["renewed"] += 1
                    else:
                        stats["renew_skipped"] += 1
                stats["single_use_disabled"] += len(disable_expired_single_use(tid, now))
        except Exception:  # noqa: BLE001
            _LOG.exception("plan_lifecycle sweep failed tenant=%s", tid)
    return stats


__all__ = ["SINGLE_USE_MSG", "is_single_use", "reject_single_use", "renew_due",
           "disable_expired_single_use", "sweep"]
