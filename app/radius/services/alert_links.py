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


# ════════════════════════════════════════════════════════════════════════
# روابط تلجرام العميقة (مطلقة) للتنبيهات التي تتطلّب تدخّل المدير فقط
# ════════════════════════════════════════════════════════════════════════
# تنبيهات تحتاج إجراءً من المدير → تحمل رابطًا مباشرًا للصفحة المعالِجة.
# التنبيهات الإخبارية (إضافة مشترك، رفع سرعة، فصل شبكة…) لا رابط لها.
ACTION_ALERTS = frozenset({
    "payment_pending_review",   # دفعة/تحويل بانتظار المراجعة → صفحة المراجعة
    "service_request_new",      # طلب خدمة جديد → صفحة الطلبات
    "store_chat",               # رسالة دعم متجر → خيط المحادثة للردّ
    "store_chat_unanswered",    # تذكير رسالة متأخّرة → خيط المحادثة للردّ
    "portal_message",           # رسالة من بوابة المشترك → صفحة البوابة للردّ
    "store_deposit",            # طلب إيداع → تبويب الإيداعات للتأكيد
    "store_withdrawal",         # طلب سحب → تبويب السحوبات للتنفيذ
    "auto_block_triggered",     # حظر تلقائي → صفحة التحكّم بالدخول للمراجعة
    "mac_clone_detected",       # كشف استنساخ MAC → صفحة منع الاستنساخ للمراجعة
    "allow_mode_unknown_device", # رفض نمط السماح → صفحة التحكّم بالدخول
})


def _configured_base() -> str:
    """عنوان اللوحة العام المضبوط (اختياري) — يفيد خلف بروكسي عكسي حيث قد
    لا يكون host الطلب صحيحًا. فارغ → نعتمد host الطلب عبر _external."""
    try:
        from flask import g
        from ..core.tenant import DEFAULT_TENANT_ID
        from ..db.repos import tenants_repo
        tid = int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
        return str(tenants_repo.get_setting(tid, "system.public_base_url", "") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def _abs(endpoint: str, **params) -> str | None:
    """رابط مطلق لنقطة داخلية. يفضّل العنوان المضبوط ثمّ host الطلب
    (_external). يُعيد None إن تعذّر البناء (لا سياق طلب / endpoint غائب)."""
    if not _has(endpoint):
        return None
    from flask import url_for
    base = _configured_base()
    if base:
        try:
            return base + url_for(endpoint, **params)
        except Exception:  # noqa: BLE001
            pass
    try:
        return url_for(endpoint, _external=True, **params)
    except Exception:  # noqa: BLE001 — لا سياق طلب (مُطلِق غير ويب) → بلا رابط
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def action_link(key: str, context: dict | None = None) -> str | None:
    """رابط مطلق للصفحة التي يُعالَج فيها التنبيه — للتنبيهات التي تتطلّب
    تدخّلًا فقط. غيرها (إخباري) → None (بلا رابط). لا يرفع استثناءً أبدًا.

    `context` نفس قاموس سياق dispatch؛ نستخرج منه معرّف الكيان لبناء رابط
    مثبَّت على العنصر (مثل request_id / card_user_id / ticket_id)، وإلا
    نرتدّ لصفحة القسم."""
    if key not in ACTION_ALERTS:
        return None
    ctx = context or {}
    try:
        if key == "payment_pending_review":
            rid = _int(ctx.get("request_id"))
            if rid is not None:
                url = _abs("radius.payment_collection_request_detail", request_id=rid)
                if url:
                    return url
            return _abs("radius.payment_collection_review_queue_web")

        if key == "service_request_new":
            return _abs("radius.service_request_list")

        if key in ("store_chat", "store_chat_unanswered"):
            cu = _int(ctx.get("card_user_id"))
            if cu is not None:
                url = _abs("radius.store_support", chat=cu, _anchor="chat")
                if url:
                    return url
            return _abs("radius.store_support")

        if key == "portal_message":
            tk = _int(ctx.get("ticket_id"))
            if tk is not None:
                url = _abs("radius.tk_view", tid=tk)
                if url:
                    return url
            return _abs("radius.customer_portals_admin")

        if key == "store_deposit":
            rid = _int(ctx.get("request_id"))
            anchor = f"dep-{rid}" if rid is not None else "deposits"
            return _abs("radius.store_support", tab="deposits", _anchor=anchor)

        if key == "store_withdrawal":
            rid = _int(ctx.get("request_id"))
            anchor = f"wd-{rid}" if rid is not None else "withdrawals"
            return _abs("radius.store_support", tab="withdrawals", _anchor=anchor)

        if key == "auto_block_triggered":
            return _abs("radius.access_control_page")

        if key == "mac_clone_detected":
            return _abs("radius.anti_mac_clone_page")

        if key == "allow_mode_unknown_device":
            # نفس الصفحة (allow-mode قسم داخل access_control_page) — مرساة قسم
            # «نمط السماح» للاستقرار على المكان الصحيح بصرف النظر عن الأقسام
            # الأعلى منه.
            return _abs("radius.access_control_page", _anchor="allow-mode")
    except Exception:  # noqa: BLE001 — التعميق لا يكسر الإرسال أبدًا
        _LOG.debug("action_link failed for %s", key, exc_info=True)
    return None


__all__ = ["alert_target_url", "action_link", "ACTION_ALERTS"]
