"""«نمط السماح» (Allow-mode) — مكمّل عكسي لـ«قائمة الحظر» (feat/access-control).

ثلاثة أنماط اختيارية لكل عرض/باقة (plan) أو حزمة بطاقات (card_batch):

  1) open    — بلا ربط أجهزة. حدّ الجلسات المتزامنة من plan كما هو
               (مناسب لبطاقات المحلات/الكاشير المتنقّلة عمدًا).
  2) tofu    — Trust-On-First-Use: أوّل MAC ناجح يُربَط تلقائيًّا بالحساب.
               max_devices سقف الأجهزة. ما بعدها = رفض (مضادّ مشاركة).
  3) manual  — قائمة سماح يدوية. الافتراضي default-deny.

الافتراض: بدون سياسة = لا نمط سماح = السلوك الطبيعي (السياسة OFF).
أولوية الحلّ: card_batch قبل plan (الأخصّ يفوز).

الإنفاذ في policy_engine بعد فحص MAC العشوائي وقبل حدّ الجلسات المتزامنة.
محصّن: أيّ خطأ يُسقط الفحص (سماح) كي لا نكسر الـauth أبدًا.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..db.repos import allow_mode_repo

_LOG = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
# Verdict
# ════════════════════════════════════════════════════════════════════════
@dataclass
class Verdict:
    """نتيجة فحص allow-mode لطلب auth واحد."""
    action: str = "allow"                # allow | deny
    reason: str = ""                     # internal code
    message: str = ""                    # Reply-Message عربي
    mode: str = ""                       # open | tofu | manual
    policy_id: Optional[int] = None
    matched_device_id: Optional[int] = None  # عند المطابقة
    auto_bound_device_id: Optional[int] = None  # عند TOFU bind جديد
    info: dict = field(default_factory=dict)


MSG_UNKNOWN_DEVICE = (
    "هذا الجهاز غير مُسجَّل في قائمة الأجهزة المسموح بها — راجع الإدارة")
MSG_AT_CAPACITY = (
    "تم الوصول للحدّ الأقصى للأجهزة المربوطة بهذا الحساب — تواصل مع الإدارة")


# ════════════════════════════════════════════════════════════════════════
# اختيار السياسة (precedence: card_batch ثم plan)
# ════════════════════════════════════════════════════════════════════════
def resolve_policy(tenant_id: int, *,
                    card_batch_id: Optional[int],
                    plan_id: Optional[int]) -> Optional[dict]:
    """يُرجع السياسة الفعّالة لهذا المستخدم، أو None لو لا سياسة. الأخصّ يفوز
    (card_batch قبل plan)."""
    try:
        if card_batch_id:
            p = allow_mode_repo.get_policy(int(tenant_id), "card_batch",
                                            int(card_batch_id))
            if p:
                return p
        if plan_id:
            p = allow_mode_repo.get_policy(int(tenant_id), "plan",
                                            int(plan_id))
            if p:
                return p
    except Exception:  # noqa: BLE001
        _LOG.warning("allow_mode: resolve_policy failed", exc_info=True)
    return None


# ════════════════════════════════════════════════════════════════════════
# الفحص الرئيسي
# ════════════════════════════════════════════════════════════════════════
def evaluate(tenant_id: int, *,
             username: str,
             plan_id: Optional[int],
             card_batch_id: Optional[int],
             mac: str,
             by: int = 0) -> Optional[Verdict]:
    """يُقيِّم نمط السماح لطلب auth. يعيد:
       • None لو لا سياسة (السلوك الطبيعي يكمل دون تدخّل).
       • Verdict(action='allow' | 'deny', reason=...) عند وجود سياسة.

    لا يكسر الـauth أبدًا — أي استثناء يُلتقط ويُسقط الفحص (سماح آمن).

    side-effect على TOFU: عند اتساع الحدّ ووجود MAC جديد، نُضيفه تلقائيًّا
    كجهاز شخصي للحساب (source='auto') كي تُحفَظ الـbinding مباشرة من
    داخل الفحص — يحافظ على «أوّل دخول = ربط ثابت».
    """
    try:
        policy = resolve_policy(int(tenant_id),
                                 card_batch_id=card_batch_id,
                                 plan_id=plan_id)
        if not policy:
            return None
        mode = str(policy.get("mode") or "open").lower()
        policy_id = int(policy.get("id") or 0)
        max_devices = int(policy.get("max_devices") or 0)

        # نمط open: لا ربط، نُمرّر للسلوك المعتاد (حدّ الجلسات من plan).
        if mode == "open":
            return Verdict(action="allow", reason="open_mode",
                           mode="open", policy_id=policy_id,
                           info={"max_devices": max_devices})

        norm_mac = allow_mode_repo.normalize_mac(mac or "")
        if not norm_mac:
            # بلا MAC لا نستطيع التحقّق — نُسقط الفحص بأمان (لا نكسر الـauth).
            return Verdict(action="allow", reason="no_mac",
                           mode=mode, policy_id=policy_id)

        # هل المطابقة موجودة؟ (شخصي أو مشترك)
        device = allow_mode_repo.find_device_match(
            policy_id, username=username or "", mac=norm_mac)
        if device:
            try:
                allow_mode_repo.touch_device(int(device["id"]))
            except Exception:  # noqa: BLE001
                pass
            return Verdict(action="allow", reason="device_allowed",
                           mode=mode, policy_id=policy_id,
                           matched_device_id=int(device["id"]),
                           info={"source": device.get("source") or "",
                                  "shared": (device.get("username") or "") == ""})

        # لا مطابقة → السلوك بحسب النمط:
        if mode == "tofu":
            # احسب أجهزة هذا الحساب فقط (السقف لكل حساب لا للسياسة).
            count = allow_mode_repo.count_devices(
                policy_id, username=username or "")
            if max_devices > 0 and count >= max_devices:
                return Verdict(action="deny", reason="allow_mode_at_capacity",
                               message=MSG_AT_CAPACITY,
                               mode=mode, policy_id=policy_id,
                               info={"current": count, "max": max_devices})
            # ضمن الحدّ → ربط تلقائي + سماح
            new_dev = allow_mode_repo.add_device(
                policy_id=policy_id, username=username or "",
                mac=norm_mac, source="auto",
                label=f"تلقائي · {username or '—'}", by=int(by))
            if new_dev:
                allow_mode_repo.touch_device(int(new_dev["id"]))
                return Verdict(action="allow", reason="tofu_bind",
                               mode=mode, policy_id=policy_id,
                               auto_bound_device_id=int(new_dev["id"]),
                               info={"current": count + 1, "max": max_devices})
            # فشل الإضافة الذريّ (مثلًا UNIQUE race) → نُعامله كرفض آمن
            return Verdict(action="deny", reason="allow_mode_bind_failed",
                           message=MSG_UNKNOWN_DEVICE,
                           mode=mode, policy_id=policy_id)

        # manual: default-deny
        return Verdict(action="deny", reason="allow_mode_unknown_device",
                       message=MSG_UNKNOWN_DEVICE,
                       mode=mode, policy_id=policy_id,
                       info={"max_devices": max_devices})
    except Exception:  # noqa: BLE001 — never break auth
        _LOG.warning("allow_mode.evaluate failed for %r", username,
                     exc_info=True)
        return None


# ════════════════════════════════════════════════════════════════════════
# تنبيه + CoA على الرفض (تأثير جانبي بعد القرار)
# ════════════════════════════════════════════════════════════════════════
def apply_decision(tenant_id: int, *, username: str, mac: str,
                    plan_id: Optional[int],
                    card_batch_id: Optional[int],
                    verdict: Verdict) -> None:
    """يطلق تنبيه إدارة على الرفض (لا تأثير على المسارات السلمية). يجب
    استدعاؤه فقط للرفض. لا يكسر الـauth."""
    if not verdict or verdict.action != "deny":
        return
    norm_mac = allow_mode_repo.normalize_mac(mac or "")
    try:
        from .admin_alerts import dispatch
        ctx = {
            "username": username,
            "mac": norm_mac or "—",
            "mode": verdict.mode or "—",
            "reason": _ar_reason(verdict.reason),
            "scope":  "حزمة بطاقات" if card_batch_id else "عرض/باقة",
            "scope_id": str(card_batch_id or plan_id or "—"),
        }
        dispatch(int(tenant_id), "allow_mode_unknown_device", ctx,
                 dedup_key=f"allow_mode:{username}:{norm_mac}")
    except Exception:  # noqa: BLE001
        _LOG.warning("allow_mode: admin alert failed", exc_info=True)


def _ar_reason(code: str) -> str:
    return {
        "allow_mode_unknown_device": "جهاز غير مسجّل (manual)",
        "allow_mode_at_capacity":    "تم الوصول للحدّ الأقصى للأجهزة (tofu)",
        "allow_mode_bind_failed":    "فشل ربط الجهاز تلقائيًّا",
    }.get(code, code or "")


# ════════════════════════════════════════════════════════════════════════
# الواجهة لـpolicy_engine
# ════════════════════════════════════════════════════════════════════════
def check_after_password(tenant_id: int, *, username: str,
                          plan_id: Optional[int],
                          card_batch_id: Optional[int],
                          calling_station_id: str) -> Optional[Verdict]:
    """واجهة الاستدعاء من policy_engine. يُستدعى بعد تحقّق كلمة المرور
    وفحص MAC العشوائي، وقبل حدّ الجلسات المتزامنة. None = لا سياسة (سماح)."""
    v = evaluate(int(tenant_id), username=username or "",
                 plan_id=plan_id, card_batch_id=card_batch_id,
                 mac=calling_station_id or "")
    if v is None:
        return None
    if v.action == "deny":
        apply_decision(int(tenant_id), username=username or "",
                       mac=calling_station_id or "",
                       plan_id=plan_id, card_batch_id=card_batch_id,
                       verdict=v)
    return v


__all__ = [
    "Verdict", "MSG_UNKNOWN_DEVICE", "MSG_AT_CAPACITY",
    "resolve_policy", "evaluate", "apply_decision",
    "check_after_password",
]
