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
import threading
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
           source_ref: str = "", push: Optional[bool] = None,
           event_key: str = "") -> Optional[int]:
    """ينشئ إشعارًا (أو يتجاهله إن تكرّر مفتاحه). يُرجع id أو None.

    عند إنشاء إشعار **محلّيّ جديد** (نقطة الاختناق الوحيدة للجرس) يُحوَّل طلب
    الدفع إلى لوحة التراخيص عبر الجسر، فهي سلطة FCM المركزيّة وتُرسل للتطبيق
    الجوّال العالميّ — fire-and-forget: لا يَحجب ولا يَكسر كتابة الجرس مهما
    حدث. التحويل يُطلَق **مرّة واحدة** للصفّ الجديد فقط؛ إعادة الاستدعاء بنفس
    dedup_key (إصابة تكرار) لا تُعيد التحويل فلا تَتكرّر الإشعارات على الجوّال.

    ``push`` يَضبط تحويل الدفع للصفّ الجديد:
      • None  — السلوك الافتراضي: يُحوَّل (يَخدم المُتّصِلين المباشرين كالعدّ
        التنازلي للترخيص حيث الدفع جزء أصيل من الإشعار).
      • True  — يُحوَّل صراحةً.
      • False — لا يُحوَّل (الجرس يُكتب فقط) — يَستخدمه محرّك «إشعارات الإدارة»
        ليَجعل قناة «دفع الجوال» اختيارًا لكلّ حدث (toggle) لا تحويلًا دائمًا.

    إشعارات الجسر (source='bridge') لا تُحوَّل: مصدرها لوحة التراخيص أصلًا،
    فهي تُرسل FCM لها مباشرةً (تفادي تكرار/دورة)."""
    try:
        nid, is_new = notifications_repo.create_returning(
            tenant_id, type=type, severity=severity, title=title, body=body,
            link=link, dedup_key=dedup_key, source=source, source_ref=source_ref,
            event_key=event_key)
    except Exception:  # noqa: BLE001 — الإشعارات لا تكسر شيئًا أبدًا
        _LOG.exception("notify failed")
        return None
    do_push = push if push is not None else True
    if nid is not None and is_new and source != "bridge" and do_push:
        # التحويل لا يَكسر الإرجاع أبدًا — مُغلَّف هنا أيضًا (دفاع طبقيّ) فوق
        # حُرّاس _fire_push الداخليّة.
        try:
            _fire_push(tenant_id, title=title, body=body, link=link, ntype=type)
        except Exception:  # noqa: BLE001
            _LOG.debug("fire push raised (ignored)", exc_info=True)
    return nid


# ─── تحويل الدفع إلى لوحة التراخيص (fire-and-forget) ────────────────────────
#
# الدفع مركزيّ: التطبيق الجوّال العالميّ (com.hoberadius.app) مدعوم بمشروع
# Firebase واحد تملكه لوحة التراخيص. لذا لا تَحمل وحدة الراديوس مفتاح Firebase
# ولا تُنادي FCM — بل تُحوّل طلب الدفع للوحة عبر الجسر الموقَّع، واللوحة تُرسل
# FCM لأجهزة هذا العميل. التحويل لا يَحجب ولا يَكسر كتابة الجرس أبدًا.


def _fire_push(tenant_id: int, *, title: str, body: str, link: str,
               ntype: str) -> None:
    """يُحوّل طلب الدفع للوحة في خيط خلفيّ (لا يَحجب المُتّصِل). أيّ فشل في
    الإطلاق يُبتلَع — الجرس مكتوب أصلًا."""
    try:
        threading.Thread(
            target=_forward_push,
            kwargs={"title": title, "body": body, "link": link,
                    "ntype": ntype, "mode": "async"},
            name="fcm-forward", daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 — الدفع لا يَكسر شيئًا أبدًا
        _LOG.debug("fcm forward fire failed", exc_info=True)


def _forward_push(*, title: str, body: str, link: str = "",
                  ntype: str = "system", mode: str = "async") -> dict:
    """يُحوّل طلب دفع واحد للوحة التراخيص (سلطة FCM المركزيّة) عبر الجسر.

    متزامن وآمن الفشل بالكامل — يُستدعى من خيط خلفيّ (_fire_push) أو مباشرةً
    (إشعار تجريبيّ بـ mode='sync'). يُرجع ردّ اللوحة كما هو."""
    try:
        from .admin_panel_client import AdminPanelClient
        return AdminPanelClient().forward_push(
            title=title, body=body, link=link, ntype=ntype, mode=mode)
    except Exception:  # noqa: BLE001 — التحويل لا يَكسر شيئًا أبدًا
        _LOG.debug("fcm forward failed", exc_info=True)
        return {"ok": False, "status": "forward_error"}


def send_test_push(tenant_id: int, *, title: str = "", body: str = "") -> dict:
    """يُحوّل دفعة اختبار **متزامنة** للوحة (mode='sync') وتُرسلها اللوحة لكل
    أجهزة العميل المُسجَّلة مركزيًّا — يَخدم زرّ «أرسل إشعار تجريبي».

    لا يَكتب في الجرس (يَختبر مسار التحويل→الدفع وحده). يُرجع dict موحّدًا
    {ok, reason, sent, failed}؛ reason يُميّز الحالات: no_tokens (لا أجهزة) ·
    fcm_disabled (لا اعتماد على لوحة التراخيص) · https_required/disabled/
    config_missing (الجسر غير مُهيّأ) · sent (نجح)."""
    resp = _forward_push(
        title=title or "إشعار تجريبي من لوحة هوبراديوس",
        body=body or "إن وصلك هذا الإشعار فدفع الجوال يعمل بنجاح ✅",
        link="/admin/radius/notifications", ntype="system", mode="sync")
    # ردّ اللوحة المغلَّف: {ok, status, response:{ok,status,sent,failed,devices}}.
    inner = resp.get("response") if isinstance(resp, dict) else None
    if isinstance(inner, dict):
        return {
            "ok": bool(inner.get("ok")),
            "reason": str(inner.get("status") or ""),
            "sent": int(inner.get("sent") or 0),
            "failed": int(inner.get("failed") or 0),
            "devices": int(inner.get("devices") or 0),
        }
    # لم يصل الجسر للوحة (تعطيل/تهيئة ناقصة/HTTPS) — أعِد السبب الخارجيّ.
    return {"ok": False, "reason": str((resp or {}).get("status") or "unavailable"),
            "sent": 0, "failed": 0, "devices": 0}


def push_status(tenant_id: int) -> dict:
    """حالة دفع الجوال للعرض في اللوحة (آمنة الفشل دائمًا). الدفع مُدار
    مركزيًّا من لوحة التراخيص؛ هذه الوحدة تُحوّل فقط:
      • central          : دائمًا True (للتوضيح في الواجهة).
      • bridge_configured: هل الجسر مُهيّأ (مُفعَّل + رابط HTTPS + مفتاح ترخيص)؟"""
    base = {"central": True, "bridge_configured": False}
    try:
        from .admin_panel_client import AdminPanelClient
        cfg = AdminPanelClient().config
        base["bridge_configured"] = bool(
            cfg.enabled and not cfg.missing_fields()
            and str(cfg.base_url or "").lower().startswith("https://"))
    except Exception:  # noqa: BLE001 — الحالة لا تكسر صفحة أبدًا
        return base
    return base


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


def license_days_badge(expires_at, *, today: Optional[_dt.date] = None) -> dict:
    """شارة أيّام الترخيص المتبقّية لشريط الأعلى — دالّة نقيّة للقراءة فقط
    (لا آثار جانبيّة، لا تُنشئ إشعارًا). تُرجع
    ``{days_left, color, pulse, expiry}`` أو ``{days_left: None}`` حين لا
    تاريخ انتهاء.

    الألوان (طلب المالك): ≥20 يوم أخضر · 10–19 أصفر · 3–9 أحمر ·
    <3 أحمر نابض (يشمل «اليوم» والمنتهي)."""
    expiry = _parse_date(expires_at)
    if expiry is None:
        return {"days_left": None}
    ref = today or _dt.date.today()
    days_left = (expiry - ref).days
    if days_left >= 20:
        color, pulse = "green", False
    elif days_left >= 10:
        color, pulse = "amber", False
    elif days_left >= 3:
        color, pulse = "red", False
    else:
        color, pulse = "red", True
    return {"days_left": days_left, "color": color, "pulse": pulse,
            "expiry": expiry.isoformat()}


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
