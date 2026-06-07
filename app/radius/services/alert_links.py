"""alert_links — تعميق روابط التنبيهات (deep-link) حسب نوع التنبيه.

مصدر الحقيقة الوحيد لوجهة كل تنبيه في الجرس وصفحة «كل التنبيهات».
يأخذ صفّ تنبيه (dict من alerts_repo، يحمل rule / router_id / dedup_key /
evidence / id) ويُرجِع رابط المورد/الإجراء المناسب عبر url_for بدل صفحة
تفاصيل التنبيه العامّة.

معرّف المرجع (ref id) لكل نوع:
  • تنبيهات الراوتر (auto.*): المرجع = عمود router_id.
  • تنبيهات المتجر (store.*): المرجع مضمَّن في dedup_key بصيغة «rule:refid»
    (لا يوجد عمود مخصّص له)، فنستخرج الرقم بعد آخر «:».

الخريطة (type → URL):
  auto.*  (router_id)        → radius.mt_dashboard(nas_id=router_id)   لوحة ذلك الراوتر
  store.chat:<cu_id>         → radius.store_support?chat=<cu_id>#chat   خيط محادثة الزبون
  store.deposit:<req_id>     → radius.store_support?tab=deposits#dep-<req_id>
  store.withdrawal:<req_id>  → radius.store_support?tab=withdrawals#wd-<req_id>
  store.registration:<cu_id> → radius.card_user_360(card_user_id=cu_id) صفحة المشترك
  غير معروف / بلا مرجع       → evidence.link إن وُجد، وإلا صفحة التنبيهات (fallback)

آمن دائمًا: أي خطأ (endpoint غير مسجّل، مرجع مفقود…) يسقط إلى الاحتياط
ولا يكسر أي صفحة.
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)


def _has(endpoint: str) -> bool:
    try:
        from flask import current_app
        return endpoint in current_app.view_functions
    except Exception:  # noqa: BLE001
        return False


def _ref_from_dedup(dedup_key) -> int | None:
    """المرجع لتنبيهات المتجر مخزَّن في ذيل dedup_key: «store.chat:42» → 42."""
    s = str(dedup_key or "")
    if ":" not in s:
        return None
    try:
        return int(s.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


def alert_target_url(alert) -> str:
    """رابط الوجهة العميق لتنبيه واحد. لا يرفع استثناءً أبدًا.

    `alert`: dict (أو ما يشبهه) يحمل rule / router_id / dedup_key /
    evidence / id كما يُرجِعها alerts_repo.
    """
    from flask import url_for
    try:
        rule = str((alert.get("rule") or "")).strip()
        router_id = alert.get("router_id")
        evidence = alert.get("evidence") or {}
        store_ref = _ref_from_dedup(alert.get("dedup_key"))

        # 1) تنبيهات الراوتر الآلية (offline / disabled / backup / snapshot /
        #    audit / alert / loop …): كلّها تحمل router_id → لوحة ذلك الراوتر.
        if rule.startswith("auto.") and router_id and _has("radius.mt_dashboard"):
            return url_for("radius.mt_dashboard", nas_id=int(router_id))

        # 2) رسالة شات جديدة من زبون → خيط محاددثته في «دعم وطلبات المتجر».
        if rule == "store.chat" and store_ref and _has("radius.store_support"):
            return url_for("radius.store_support", chat=store_ref, _anchor="chat")

        # 3) طلب شحن (إيداع) جديد → تبويب طلبات الشحن، مثبّتًا على الطلب.
        if rule == "store.deposit" and _has("radius.store_support"):
            anchor = f"dep-{store_ref}" if store_ref else "deposits"
            return url_for("radius.store_support", tab="deposits", _anchor=anchor)

        # 4) طلب سحب جديد → تبويب طلبات السحب، مثبّتًا على الطلب.
        if rule == "store.withdrawal" and _has("radius.store_support"):
            anchor = f"wd-{store_ref}" if store_ref else "withdrawals"
            return url_for("radius.store_support", tab="withdrawals", _anchor=anchor)

        # 5) تسجيل مشترك بطاقات جديد → صفحة ذلك المشترك مباشرة.
        if rule == "store.registration" and store_ref and _has("radius.card_user_360"):
            return url_for("radius.card_user_360", card_user_id=store_ref)

        # 6) رابط مخزَّن في الأدلّة (store_alerts يضع link) كاحتياط ليّن.
        link = evidence.get("link") if isinstance(evidence, dict) else None
        if link:
            return str(link)
    except Exception:  # noqa: BLE001 — التعميق لا يكسر أي صفحة أبدًا
        _LOG.debug("alert_target_url failed", exc_info=True)

    # احتياط أخير: نوع/مرجع غير معروف → صفحة التنبيهات العامّة.
    try:
        if _has("radius.mt_alerts_index"):
            return url_for("radius.mt_alerts_index")
    except Exception:  # noqa: BLE001
        pass
    return "#"


__all__ = ["alert_target_url"]
