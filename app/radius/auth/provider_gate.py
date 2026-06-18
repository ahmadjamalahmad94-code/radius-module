"""ربط منح المزوّد (provider grant) بصفحات/قسم الـadmin.

تحوّل قرار منح-الخدمة إلى:
  • اسم القسم الذي يجب إخفاؤه من الشريط الجانبي (إذا كانت الخدمة موقوفة)،
  • قرار رفض/سماح عند `before_request` (حارس المسار) بدون تجاوز من السوبر،
  • مساعد قالب يستعمله ``sub_item`` لإسقاط البند صامتًا.

نموذج 2026-06-18: لكلّ endpoint (أو بادئة) → قائمة مرتّبة من *مرشّحات*
مفاتيح خدمة (أكثر تخصّصًا → أعمّ). الحارس يَحجب إذا كانت أيّ مرشّحة موقوفة
في عقد المزوّد. كنّا نَستعمل مفتاحًا واحدًا، وانكشف أنّ بعض العقود تَستعمل
مفاتيح مفصّلة (مثل ``finance_center``) بينما الخريطة كانت تُسطّحها إلى
مظلّة (``finance``) — فلا تَنطبق ``is_service_disabled`` ولا يَحدث الحجب
(صفحات تَفتح للسوبر-أدمن رغم العقد). الحلّ هنا: نُعيد قائمة المرشّحات
ونَحجب على أوّل ما يَنطبق.

غير المسجَّل صراحةً وبلا بادئة مَعروفة = ليس مُغلَقًا من المزوّد (يُسمح به
دائمًا، RBAC الداخلي يَتولّى الباقي).
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..services import provider_grant


# ─────────────────────────────────────────────────────────────────────
# (A) خريطة endpoint → مرشّحات صريحة
#     القيمة: tuple/list بمرشّحات مرتّبة (مرشّح-التخصّص أوّلًا، ثم المظلّة).
#     الحارس يَحجب إذا كانت **أيّ** مرشّحة موقوفة. هذا يَجعل المنح يَنطبق
#     سواء استعمل العقد ``finance_center`` (المفصّل) أو ``finance`` (المظلّة).
# ─────────────────────────────────────────────────────────────────────
_ENDPOINT_TO_SERVICE: dict[str, tuple[str, ...]] = {
    # ── المشتركون ──
    "users_list":                ("subscribers",),
    "users_new":                 ("subscribers",),
    "users_edit":                ("subscribers",),
    "users_update":              ("subscribers",),
    "users_create":              ("subscribers",),
    "subscribers_list":          ("subscribers",),
    "subscribers_overview":      ("subscribers",),
    "subscriber_360":            ("subscribers",),
    "online_list":               ("online", "subscribers"),

    # ── البطاقات ──
    "cards_overview":            ("cards",),
    "cards_list":                ("cards",),
    "cards_batches":             ("cards",),
    "cards_generate":            ("cards",),
    "cards_checker":             ("card_checker", "cards"),
    "cards_recharge_list":       ("cards_recharge", "cards"),
    "card_marketplace":          ("card_marketplace", "cards"),
    "card_users_list":           ("card_users", "cards"),

    # ── التقارير ──
    "reports_index":             ("reports",),
    "rep_login_states":          ("reports",),
    "rep_login_states_cards":    ("reports",),
    "rep_login_states_card_store": ("reports",),
    "rep_login_states_sub_portal": ("reports",),
    "rep_login_attempts":        ("reports",),
    "rep_admin_login_attempts":  ("reports",),
    "rep_traffic":               ("reports",),
    "rep_finance":               ("reports",),
    "rep_audit":                 ("audit", "reports"),
    "rep_active_sessions":       ("reports",),

    # ── المالية والمحاسبة ──
    # الحارس يَحجب لو العقد يَستعمل المفتاح المفصّل (finance_center) أو المظلّة
    # (finance). كذلك billing/accounting/payments قد تَكون مفتاحًا مستقلًّا
    # في العقد، فنُجرّب الأخصّ ثم المظلّة.
    "finance_hub":               ("finance",),
    "finance_center":            ("finance_center", "finance"),
    "finance_accounting_page":   ("accounting", "finance"),
    "finance_billing_page":      ("billing", "finance"),
    "finance_collection_page":   ("payment_collection", "finance"),
    "payment_collection_review_queue_web": ("payment_collection", "finance"),
    "payments_lab":              ("payments", "finance"),
    "accounting_hub":            ("accounting", "finance"),
    "billing_hub":               ("billing", "finance"),

    # ── الشبكة والمايكروتيك ──
    "mt_operations":             ("network",),
    "mt_dashboard":              ("network",),
    "mt_alerts_index":           ("router_alerts", "network"),
    "mt_topology":               ("mt_topology", "network"),
    "mt_problems":               ("network",),
    "mt_router_overview":        ("network",),
    "mt_setup":                  ("network",),
    "devices_list":              ("devices", "network"),
    "pool_list":                 ("pools", "network"),
    "device_health_page":        ("device_health", "network"),
    "netpolicy_index":           ("network_policy", "network"),

    # ── الإعدادات والإدارة ──
    "settings_page":             ("settings",),
    "system_settings_page":      ("settings", "system"),
    "access_control_page":       ("access_control",),
    "anti_mac_clone_page":       ("anti_mac_clone",),
    "backups":                   ("backups",),

    # ── الاتصالات والمتجر ──
    # الحارس يَحجب لو العقد يَستعمل المفتاح المفصّل (communications) أو أي
    # مظلّة بديلة (messaging/notifications/comms).
    "communications_index":      ("communications", "messaging", "notifications"),
    "store_support":             ("store_support", "store"),
    "store_overview":            ("store",),
    "service_request_list":      ("service_requests",),

    # ── أدوات الـRADIUS الأساسية ──
    "tools_index":               ("tools",),
    "tool_test_auth":            ("tools",),
    "tool_radius_log":           ("tools",),

    # ── بوّابة المشترك (customer portal) ──
    "customer_portals_admin":    ("customer_portals", "customer_portal"),
    # ملاحظة: البوّابة المواجهة للزبون نفسها تُحرَس عند نقاط الإدخال
    # المخصّصة، لأنّ «مخفية من البوابة» تخصّها وحدها (لا الإدارة).
}


# ─────────────────────────────────────────────────────────────────────
# (B) قائمة بادئات → مرشّحات (احتياطيّ شامل لكلّ نقاط /admin/radius/*).
#     الأطول أوّلًا حتى لا تَلتقط بادئة قصيرة (مثل ``users_``) ما تَستحقّه
#     بادئة أطول (مثل ``users_groups_``). تُطبَّق بترتيب القائمة، أوّل
#     مطابقة تَفوز.
# ─────────────────────────────────────────────────────────────────────
_PREFIX_TO_SERVICE: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ── أطول بادئات أولًا ──
    ("subscriber_groups_",     ("subscriber_groups", "subscribers")),
    ("subscriber_renewal_",    ("subscribers",)),
    ("subscriber_portal_",     ("subscriber_portal", "customer_portal")),
    ("subscriber_360",         ("subscribers",)),
    ("card_marketplace",       ("card_marketplace", "cards")),
    ("card_users_",            ("card_users", "cards")),
    ("card_user_",             ("card_users", "cards")),
    ("hotspot_cards_",         ("hotspot_cards", "cards")),
    ("hotspot_templates_",     ("hotspot_designs", "network")),
    ("hotspot_designs_",       ("hotspot_designs",)),
    ("recharge_",              ("cards_recharge", "cards")),
    ("network_telegram_",      ("network_telegram", "telegram", "communications")),
    ("admin_alerts_",          ("admin_alerts", "alerts", "communications")),
    ("admin_pricing_",         ("admin_pricing", "finance")),
    ("finance_center",         ("finance_center", "finance")),
    ("finance_accounting",     ("accounting", "finance")),
    ("finance_billing",        ("billing", "finance")),
    ("finance_collection",     ("payment_collection", "finance")),
    ("finance_hub",            ("finance",)),
    ("admin_bridge",           ("admin_bridge", "license_admin")),
    ("admins_",                ("admins",)),
    ("anti_mac_clone_",        ("anti_mac_clone", "security", "access_control")),
    ("access_control_",        ("access_control",)),
    ("audit_log",              ("audit_logs", "audit", "reports")),
    ("audit_",                 ("audit",)),
    ("auth_",                  ("internal_auth",)),
    ("setup_wizard",           ("setup_wizard",)),
    ("system_settings",        ("settings", "system")),
    ("system_",                ("system",)),
    ("sections_admin_",        ("admins", "settings")),
    ("service_request_",       ("service_requests",)),
    ("smart_alerts_",          ("alerts", "communications")),
    ("smart_alerts",           ("alerts", "communications")),
    ("router_events_",         ("router_alerts", "network")),
    ("router_metrics_",        ("router_metrics", "network")),
    ("remote_device_",         ("network",)),
    ("portal_card_",           ("customer_portal", "card_users")),
    ("portal_subscriber_",     ("subscriber_portal", "customer_portal")),
    ("print_templates",        ("print_templates",)),
    ("payment_collection_",    ("payment_collection", "finance")),
    ("payment_",               ("payments", "finance")),
    ("payments_",              ("payments", "finance")),
    ("ledger_",                ("ledger", "finance")),
    ("invoices_",              ("invoices", "finance")),
    ("invoice_",               ("invoices", "finance")),
    ("loans_",                 ("loans", "finance")),
    ("loan_",                  ("loans", "finance")),
    ("billing_",               ("billing", "finance")),
    ("accounting_",            ("accounting", "finance")),
    ("business_finance_",      ("finance", "business_os")),
    ("business_",              ("business_os",)),
    ("backups_",               ("backups",)),
    ("recycle_bin",            ("recycle_bin",)),
    ("bandwidth_schedules",    ("bandwidth_schedules", "bandwidth_control")),
    ("bandwidth_",             ("bandwidth_control",)),
    ("rep_",                   ("reports",)),
    ("reports_",               ("reports",)),
    ("report_",                ("reports",)),
    ("mt_",                    ("network",)),
    ("router_",                ("routers", "network")),
    ("routers_",               ("routers", "network")),
    ("network_policy",         ("network_policy", "network")),
    ("netpolicy_",             ("network_policy", "network")),
    ("pool_",                  ("pools", "network")),
    ("nas_",                   ("nas", "network")),
    ("devices_",               ("devices", "network")),
    ("device_",                ("devices", "network")),
    ("device_health_",         ("device_health", "network")),
    ("site_exit_",             ("site_exit", "network")),
    ("tunnels_",               ("network",)),
    ("tunnel_",                ("network",)),
    ("loop_",                  ("network",)),
    ("wg_",                    ("network",)),
    ("vpn_",                   ("network",)),
    ("communications_",        ("communications",)),
    ("telegram_",              ("telegram", "communications")),
    ("whatsapp_",              ("whatsapp", "communications")),
    ("alerts_",                ("alerts", "communications")),
    ("store_",                 ("store",)),
    ("marketplace_",           ("marketplace", "cards")),
    ("vch_",                   ("vouchers", "cards")),
    ("svc_",                   ("profiles", "plans")),
    ("plans_",                 ("plans", "profiles")),
    ("plan_",                  ("plans", "profiles")),
    ("profiles_",              ("profiles", "plans")),
    ("profile_",               ("profiles", "plans")),
    ("temp_speed",             ("temp_speed",)),
    ("ts_",                    ("temp_speed",)),
    ("coa_",                   ("live_session_control", "online")),
    ("online_",                ("online", "subscribers")),
    ("sessions_",              ("sessions", "online")),
    ("session_",               ("sessions", "online")),
    ("tok_",                   ("tokens", "settings")),
    ("tokens_",                ("tokens", "settings")),
    ("tk_",                    ("tickets", "service_requests")),
    ("tools_",                 ("tools",)),
    ("tool_",                  ("tools",)),
    ("wh_",                    ("webhooks", "settings")),
    ("webhooks_",              ("webhooks", "settings")),
    ("whatsapp",               ("whatsapp", "communications")),
    ("sgrp_",                  ("subscriber_groups", "subscribers")),
    ("roles_",                 ("admins", "settings")),
    ("tenants_",               ("tenants",)),
    ("settings_",              ("settings",)),
    ("setting_",               ("settings",)),
    ("share_groups_",          ("share_groups",)),
    ("license_",               ("lifecycle", "license_admin")),
    ("events_",                ("events", "reports")),
    ("event_",                 ("events", "reports")),
    ("sync_",                  ("system",)),
    ("reconcile_",             ("system",)),
    ("account",                ("admins",)),
    # ── الإكمال الكامل ──
    ("npc_walled_garden",      ("network_policy", "network")),
    ("npc_web_block",          ("network_policy", "network")),
    ("npc_router_",            ("network_policy", "network")),
    ("npc_",                   ("network_policy", "network")),
    ("network_device_bypass",  ("devices", "network")),
    ("network_devices",        ("devices", "network")),
    ("network_ip_",            ("network",)),
    ("network_",               ("network",)),
    ("collection_hub",         ("payment_collection", "finance")),
    ("company_inventory",      ("business_os",)),
    ("company_expense",        ("business_os", "finance")),
    ("company_",               ("business_os",)),
    ("operations_speed_",      ("temp_speed", "network")),
    ("operations_",            ("network",)),
    ("operations",             ("network",)),
    ("monitoring_",            ("monitoring",)),
    ("distributors_",          ("distributors",)),
    ("diagnostics",            ("mt_diagnostics", "network")),
    ("hotspot_errors_",        ("network", "hotspot_designs")),
    ("hotspot_",               ("network",)),
    ("bw_",                    ("bandwidth_control", "bandwidth_schedules")),
    ("inv_",                   ("invoices", "finance")),
    ("jobs_",                  ("tools", "system")),
    ("lifecycle_",             ("lifecycle", "system")),
    ("pay_demo",               ("payments", "finance")),
    ("dashboard",              ("dashboard",)),
    # ── أعمّ بادئات قطاع كامل (بعد الأخصّ) ──
    ("users_",                 ("subscribers",)),
    ("subscribers_",           ("subscribers",)),
    ("cards_",                 ("cards",)),
    ("finance_",               ("finance",)),
    ("communications",         ("communications",)),
)


def service_keys_for_endpoint(endpoint: str) -> list[str]:
    """يُعيد قائمة مرشّحات (الأخصّ أوّلًا) لمفاتيح خدمة المزوّد لهذه النقطة.
    قائمة فارغة = ليس مُسجَّلًا (سَماح افتراضيّ)."""
    if not endpoint:
        return []
    name = endpoint.split(".", 1)[-1] if "." in endpoint else endpoint
    out: list[str] = []
    explicit = _ENDPOINT_TO_SERVICE.get(name)
    if explicit:
        out.extend(explicit)
    for prefix, keys in _PREFIX_TO_SERVICE:
        if name.startswith(prefix):
            out.extend(keys)
            break
    # حذف التكرار مع الحفاظ على الترتيب
    seen: set[str] = set()
    deduped: list[str] = []
    for k in out:
        if k and k not in seen:
            seen.add(k)
            deduped.append(k)
    return deduped


def service_key_for_endpoint(endpoint: str) -> Optional[str]:
    """يُعيد المرشّحة الأولى — للتوافق مع كود قديم/قوالب. الحارس يَستعمل
    الـlist بأكمله. None = ليس مُسجَّلًا."""
    keys = service_keys_for_endpoint(endpoint)
    return keys[0] if keys else None


def is_endpoint_blocked_by_provider(tenant_id: int,
                                      endpoint: str) -> tuple[bool, str]:
    """يُرجع (مُغلَق؟، مفتاح-الخدمة-المسؤول). True = نمنع — حتى للسوبر.

    «مُغلَق» يعني «موقوفة» فقط (disabled/suspended/locked/hidden في feature).
    نَفحص جميع مرشّحات الخدمة لهذه النقطة، أوّل مرشّحة موقوفة تَنطبق تَحجب —
    هذا يَستوعب عقدًا يَستعمل المفتاح المفصّل أو المظلّة.
    «مدفوعة-غير-مفعّلة» (locked_upgrade) ليست مُغلَقة بهذا المعنى — لها
    دالّتها المنفصلة is_endpoint_requires_upgrade.
    """
    for skey in service_keys_for_endpoint(endpoint):
        if provider_grant.is_service_disabled(int(tenant_id), skey):
            return True, skey
    return False, ""


def is_endpoint_requires_upgrade(tenant_id: int,
                                   endpoint: str) -> tuple[bool, str]:
    """يُرجع (يحتاج ترقية؟، مفتاح-الخدمة). True → نُعيد التوجيه لصفحة
    «طلب تفعيل / ترقية» (لا hard-block ولا hide). للسوبر-أدمن أيضًا
    (قرار تجاري، فوق RBAC)."""
    for skey in service_keys_for_endpoint(endpoint):
        if provider_grant.requires_upgrade(int(tenant_id), skey):
            return True, skey
    return False, ""


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

    def _provider_multi_tenant_granted() -> bool:
        """True فقط عندما يَكون مُنحة «الجهات» (multi_tenant) ساريًا.

        قاعدة الاستعمال: مَركبة فوق ـmulti_tenant خدمة افتراضيًّا غير
        موجودة في معظم العقود؛ تَظهر فقط على العقود التي تُمنَح حقّ
        تشغيل عدّة جهات. لذا:

          • العقد لا يَحوي ـmulti_tenant → نَعتبره «غير ممنوح» (False)
          • العقد يَحويه و disabled → False (الفئة العاديّة للسايدبار)
          • العقد يَحويه و requires_upgrade → False (مدفوع غير مُفعَّل)
          • العقد يَحويه و enabled = active → True

        فائدته الأساسيّة: مفتاح بوابة لـ«تبديل الجهة» في الـtopbar (لا
        يَكفي ـprovider_service_disabled لأنّه يَفترض السماح الافتراضيّ).
        """
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            tid = int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
        except Exception:  # noqa: BLE001
            tid = 1
        try:
            g_obj = provider_grant.lookup(tid, "multi_tenant")
            if not g_obj.present:
                return False  # عقد لا يَذكره أصلًا = لم يُمنَح
            if g_obj.disabled:
                return False
            if g_obj.requires_upgrade:
                return False
            return True
        except Exception:  # noqa: BLE001 — fail-closed على هذه الخدمة عمدًا
            return False

    def _provider_multi_tenant_entity_limit() -> int:
        """عدد الجهات المسموح به في العقد (limits.multi_tenant.entity_count).
        قراءة من أيّ من الأسماء البديلة. None/0 = بلا حدّ → نُعيد قيمة
        كبيرة (10000) كي لا تَقطع القائمة عرضًا."""
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            tid = int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
        except Exception:  # noqa: BLE001
            tid = 1
        for path in ("multi_tenant.entity_count",
                      "multi_tenant.max",
                      "entities.max",
                      "tenants.max"):
            try:
                v = provider_grant.get_limit(tid, path)
            except Exception:  # noqa: BLE001
                v = None
            if v is not None and int(v) > 0:
                return int(v)
        return 10000

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

    # تسميات عربية للخدمات والحالات — تُستعمل في صفحة «حالة منح المزوّد»
    # والـtooltips في السايدبار. صدر مركزي واحد كي لا تَنبثق ترجمات
    # متفرّقة في القوالب.
    from ..services.provider_service_labels import (
        service_label_ar, service_status_ar, feature_state_ar,
    )

    return {
        "provider_endpoint_blocked": _provider_endpoint_blocked,
        "provider_service_disabled": _provider_service_disabled,
        "provider_endpoint_requires_upgrade": _provider_endpoint_requires_upgrade,
        "provider_multi_tenant_granted": _provider_multi_tenant_granted,
        "provider_multi_tenant_entity_limit": _provider_multi_tenant_entity_limit,
        "provider_service_label_ar": service_label_ar,
        "provider_service_status_ar": service_status_ar,
        "provider_feature_state_ar": feature_state_ar,
    }


__all__ = [
    "service_key_for_endpoint",
    "service_keys_for_endpoint",
    "is_endpoint_blocked_by_provider",
    "is_endpoint_requires_upgrade",
    "template_helpers",
    "_ENDPOINT_TO_SERVICE",
    "_PREFIX_TO_SERVICE",
]
