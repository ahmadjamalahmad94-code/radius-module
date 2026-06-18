"""ربط منح المزوّد (provider grant) بصفحات/قسم الـadmin.

تحوّل قرار منح-الخدمة إلى:
  • اسم القسم الذي يجب إخفاؤه من الشريط الجانبي (إذا كانت الخدمة موقوفة)،
  • قرار رفض/سماح عند `before_request` (حارس المسار) بدون تجاوز من السوبر،
  • مساعد قالب يستعمله ``sub_item`` لإسقاط البند صامتًا.

النموذج: لكلّ endpoint (أو بادئة) → service_key نطاقي يُستفسَر عنه. القائمة
الافتراضية تغطّي القطاعات الرئيسية. غير المسجَّل = ليس مُغلَقًا من المزوّد
(يُسمح به دائمًا، RBAC الداخلي يتولّى الباقي).
"""
from __future__ import annotations

from typing import Optional

from ..services import provider_grant


# ─────────────────────────────────────────────────────────────────────
# خريطة endpoint → provider service key
# (الـendpoint هو ما بعد البادئة 'radius.' — نفس نمط _NAV_PERM.)
# الخدمات أدناه أكثرها يقابل قسمًا كاملاً (المشتركون، البطاقات، التقارير،
# الشبكة، المالية، …) — قابلة للتوسعة دون كسر شيء، لأن غير المسجَّل = سماح.
# ─────────────────────────────────────────────────────────────────────
_ENDPOINT_TO_SERVICE: dict[str, str] = {
    # ── المشتركون ──
    "users_list":                "subscribers",
    "users_new":                 "subscribers",
    "users_edit":                "subscribers",
    "users_update":              "subscribers",
    "users_create":              "subscribers",
    "subscribers_list":          "subscribers",
    "subscribers_overview":      "subscribers",
    "subscriber_groups_list":    "subscribers",
    "subscriber_360":            "subscribers",
    "online_list":               "subscribers",

    # ── البطاقات ──
    "cards_overview":            "cards",
    "cards_list":                "cards",
    "cards_batches":             "cards",
    "cards_generate":            "cards",
    "cards_checker":             "cards",
    "cards_recharge_list":       "cards",
    "card_marketplace":          "cards",
    "card_users_list":           "cards",

    # ── التقارير ──
    "reports_index":             "reports",
    "rep_login_states":          "reports",
    "rep_login_states_cards":    "reports",
    "rep_login_states_card_store": "reports",
    "rep_login_states_sub_portal": "reports",
    "rep_login_attempts":        "reports",
    "rep_admin_login_attempts":  "reports",
    "rep_traffic":               "reports",
    "rep_finance":               "reports",
    "rep_audit":                 "reports",
    "rep_active_sessions":       "reports",

    # ── المالية والمحاسبة ──
    "finance_hub":               "finance",
    "finance_center":            "finance",
    "finance_accounting_page":   "finance",
    "finance_billing_page":      "finance",
    "finance_collection_page":   "finance",
    "payment_collection_review_queue_web": "finance",
    "payments_lab":              "finance",

    # ── الشبكة والمايكروتيك ──
    "mt_operations":             "network",
    "mt_dashboard":              "network",
    "mt_alerts_index":           "network",
    "mt_topology":               "network",
    "mt_problems":               "network",
    "mt_router_overview":        "network",
    "mt_setup":                  "network",
    "devices_list":              "network",
    "pool_list":                 "network",
    "device_health_page":        "network",

    # ── الإعدادات والإدارة ──
    "settings_page":             "settings",
    "access_control_page":       "access_control",
    "anti_mac_clone_page":       "anti_mac_clone",
    "backups":                   "backups",

    # ── الاتصالات والمتجر ──
    "communications_index":      "communications",
    "store_support":             "store",
    "store_overview":            "store",
    "service_request_list":      "service_requests",

    # ── أدوات الـRADIUS الأساسية ──
    "tools_index":               "tools",
    "tool_test_auth":            "tools",
    "tool_radius_log":           "tools",

    # ── بوّابة المشترك (customer portal) ──
    "customer_portals_admin":    "customer_portal",
    # ملاحظة: البوّابة المواجهة للزبون نفسها تُحرَس عند نقاط الإدخال
    # المخصّصة، لأنّ «مخفية من البوابة» تخصّها وحدها (لا الإدارة).
}


def service_key_for_endpoint(endpoint: str) -> Optional[str]:
    """يُعيد مفتاح خدمة المزوّد المرتبط بنقطة admin (بلا بادئة 'radius.').
    None = ليس مُسجَّلًا (السماح هو الافتراضي)."""
    if not endpoint:
        return None
    name = endpoint.split(".", 1)[-1] if "." in endpoint else endpoint
    if name in _ENDPOINT_TO_SERVICE:
        return _ENDPOINT_TO_SERVICE[name]
    # بادئات شائعة (rep_*, finance_*, mt_*) — لو لم يُسجَّل صريحًا.
    for prefix, key in (
        ("rep_", "reports"),
        ("reports_", "reports"),
        ("finance_", "finance"),
        ("mt_", "network"),
        ("payment_collection_", "finance"),
        ("cards_", "cards"),
        ("users_", "subscribers"),
        ("subscribers_", "subscribers"),
        ("communications_", "communications"),
        ("store_", "store"),
    ):
        if name.startswith(prefix):
            return key
    return None


def is_endpoint_blocked_by_provider(tenant_id: int,
                                      endpoint: str) -> tuple[bool, str]:
    """يُرجع (مُغلَق؟، مفتاح-الخدمة-المسؤول). True = نمنع — حتى للسوبر.

    «مُغلَق» يعني «موقوفة» فقط (disabled/suspended/locked/hidden في feature).
    «مدفوعة-غير-مفعّلة» (locked_upgrade) ليست مُغلَقة بهذا المعنى — لها
    دالّتها المنفصلة is_endpoint_requires_upgrade التي يَستهلكها الحارس
    لإعادة التوجيه إلى صفحة «طلب تفعيل / ترقية» بدل blocked.
    """
    skey = service_key_for_endpoint(endpoint)
    if not skey:
        return False, ""
    return provider_grant.is_service_disabled(int(tenant_id), skey), skey


def is_endpoint_requires_upgrade(tenant_id: int,
                                   endpoint: str) -> tuple[bool, str]:
    """يُرجع (يحتاج ترقية؟، مفتاح-الخدمة). True → نُعيد التوجيه لصفحة
    «طلب تفعيل / ترقية» (لا hard-block ولا hide). للسوبر-أدمن أيضًا
    (قرار تجاري، فوق RBAC)."""
    skey = service_key_for_endpoint(endpoint)
    if not skey:
        return False, ""
    return provider_grant.requires_upgrade(int(tenant_id), skey), skey


# ─────────────────────────────────────────────────────────────────────
# مساعدات قوالب (لتعليق sub_item في الشريط الجانبي)
# ─────────────────────────────────────────────────────────────────────
def template_helpers() -> dict[str, object]:
    """تُحقَن في الـcontext processor — قابلة للنداء من الـsidebar:

        {% if not provider_endpoint_blocked(endpoint) %}
            {{ sub_item(...) }}
        {% endif %}

    أو ضمن sub_item نفسه (تعديل المايكرو) — نُفضّل الأخيرة لتعميمها كلّيًّا.
    """
    def _provider_endpoint_blocked(endpoint: str) -> bool:
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            tid = int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
        except Exception:  # noqa: BLE001
            tid = 1
        blocked, _ = is_endpoint_blocked_by_provider(tid, endpoint)
        return blocked

    def _provider_service_disabled(service_key: str) -> bool:
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            tid = int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
        except Exception:  # noqa: BLE001
            tid = 1
        return provider_grant.is_service_disabled(tid, service_key)

    def _provider_endpoint_requires_upgrade(endpoint: str) -> bool:
        """True عند locked_upgrade — السايدبار يَعرض الشارة، الماكرو يُبقي
        البند مرئيًّا. الـredirect إلى صفحة الترقية يحدث على مستوى الـperm
        guard، لا هنا."""
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            tid = int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
        except Exception:  # noqa: BLE001
            tid = 1
        needs, _ = is_endpoint_requires_upgrade(tid, endpoint)
        return needs

    return {
        "provider_endpoint_blocked": _provider_endpoint_blocked,
        "provider_service_disabled": _provider_service_disabled,
        "provider_endpoint_requires_upgrade": _provider_endpoint_requires_upgrade,
    }


__all__ = [
    "service_key_for_endpoint",
    "is_endpoint_blocked_by_provider",
    "is_endpoint_requires_upgrade",
    "template_helpers",
    "_ENDPOINT_TO_SERVICE",
]
