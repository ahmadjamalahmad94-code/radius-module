"""خدمة الإشعارات الموحّدة (العمود الفقري) — طبقة فوق notifications_repo.

تجمع:
  • notify(): إنشاء إشعار مع إزالة تكرار آمنة.
  • recent_for_bell()/unread_count(): تغذية جرس شريط الأعلى.
  • surface_license_countdown(): إظهار «يتبقّى X يوم» لترخيص/اشتراك اللوحة
    عند عتبات 7/3/1 يومًا — من بيانات الترخيص المحلّية (آخر لقطة). يَتفادى
    تكرار ما ترسله لوحة التراخيص عبر الجسر (dedupe بالنوع/المصدر).

كل الدوال آمنة الفشل: لا تكسر أي صفحة أو دورة عامل.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from ..db.repos import notifications_repo

_LOG = logging.getLogger(__name__)

# هدف الرابط العميق لإشعارات الترخيص/الاشتراك (صفحة الحساب تعرض حالة الترخيص).
_ACCOUNT_LINK = "/admin/radius/account"

# عتبات العدّ التنازلي (تنازليًّا): نُطلق أصغر نطاق ينطبق فقط، فيظهر إشعار
# واحد لكل نطاق عبر الزمن (7 ثم 3 ثم 1) بلا فيضان.
_BANDS = (1, 3, 7)


def notify(tenant_id: int, *, type: str = "system", severity: str = "info",
           title: str = "", body: str = "", link: str = "",
           dedup_key: str = "", source: str = "local",
           source_ref: str = "") -> Optional[int]:
    """ينشئ إشعارًا (أو يتجاهله إن تكرّر مفتاحه). يُرجع id أو None."""
    try:
        return notifications_repo.create(
            tenant_id, type=type, severity=severity, title=title, body=body,
            link=link, dedup_key=dedup_key, source=source, source_ref=source_ref)
    except Exception:  # noqa: BLE001 — الإشعارات لا تكسر شيئًا أبدًا
        _LOG.exception("notify failed")
        return None


def recent_for_bell(tenant_id: int, limit: int = 6) -> list[dict]:
    try:
        return notifications_repo.recent(tenant_id, limit=limit)
    except Exception:  # noqa: BLE001
        return []


def unread_count(tenant_id: int) -> int:
    try:
        return notifications_repo.unread_count(tenant_id)
    except Exception:  # noqa: BLE001
        return 0


def _parse_date(value) -> Optional[_dt.date]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d"):
        try:
            if fmt is None:
                return _dt.datetime.fromisoformat(s).date()
            return _dt.datetime.strptime(s[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _band_for(days_left: int) -> Optional[int]:
    """أصغر نطاق عتبة ينطبق على المتبقّي (1<3<7)، أو None لو أبعد من 7."""
    for band in _BANDS:
        if days_left <= band:
            return band
    return None


def _countdown_copy(days_left: int, band: int, expiry: _dt.date) -> tuple:
    """يُرجع (severity, title, body) لإشعار العدّ التنازلي."""
    if days_left < 0:
        return ("critical", "انتهى ترخيص اللوحة",
                f"انتهى الترخيص بتاريخ {expiry.isoformat()} — جدّد لتفادي إيقاف اللوحة.")
    if days_left == 0:
        return ("critical", "ينتهي ترخيص اللوحة اليوم",
                f"ينتهي الترخيص اليوم ({expiry.isoformat()}) — جدّد الآن.")
    if days_left == 1:
        return ("critical", "يتبقّى يوم واحد على انتهاء ترخيص اللوحة",
                f"ينتهي الترخيص غدًا ({expiry.isoformat()}) — جدّد لتفادي الإيقاف.")
    sev = "warning" if band <= 3 else "info"
    return (sev, f"يتبقّى {days_left} أيام على انتهاء ترخيص اللوحة",
            f"ينتهي الترخيص بتاريخ {expiry.isoformat()} — يُنصح بالتجديد المبكّر.")


def surface_license_countdown(tenant_id: int = 1,
                              *, today: Optional[_dt.date] = None) -> dict:
    """يُظهر إشعار «يتبقّى X يوم» لترخيص اللوحة عند عتبات 7/3/1 من بيانات
    الترخيص المحلّية. يَتفادى التكرار: مفتاح يحمل التاريخ+النطاق (فلا يتكرّر
    نفس النطاق)، وتخطٍّ كامل لو كان هناك إشعار ترخيص غير مقروء قادم من
    لوحة التراخيص عبر الجسر (نَعتمد على ما ترسله هي بدل التكرار).

    يُرجع dict تشخيصيًّا: {fired, reason, days_left?, band?, id?}.
    """
    try:
        from .license_lifecycle import evaluate_cached
        decision = evaluate_cached(tenant_id)
        expiry = _parse_date(getattr(decision, "expires_at", None))
    except Exception:  # noqa: BLE001 — غياب الجسر/الترخيص ⇒ لا عدّ تنازلي محلّي
        return {"fired": False, "reason": "no_license_data"}

    if expiry is None:
        return {"fired": False, "reason": "no_expiry"}

    ref = today or _dt.date.today()
    days_left = (expiry - ref).days
    band = _band_for(days_left)
    if band is None:
        return {"fired": False, "reason": "outside_window", "days_left": days_left}

    # إزالة تكرار عبر-المصدر: لو لوحة التراخيص أرسلت إشعار ترخيص غير مقروء،
    # نَعتمد عليه ولا نُكرّر محلّيًّا.
    try:
        if notifications_repo.has_unread_of_type(tenant_id, "license", source="bridge"):
            return {"fired": False, "reason": "bridge_active", "days_left": days_left}
    except Exception:  # noqa: BLE001
        pass

    severity, title, body = _countdown_copy(days_left, band, expiry)
    dedup_key = f"license_expiry:{expiry.isoformat()}:{band}"
    nid = notify(tenant_id, type="license", severity=severity, title=title,
                 body=body, link=_ACCOUNT_LINK, dedup_key=dedup_key, source="local")
    return {"fired": nid is not None, "reason": "ok", "days_left": days_left,
            "band": band, "id": nid}
