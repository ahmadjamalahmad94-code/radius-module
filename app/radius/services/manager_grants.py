"""Granular per-manager grants — owner-configured, server-enforced.

هذا المصدر الموحّد لنظام صلاحيات المدير الدقيق الذي يَضبطه المالك من صفحة
«الصلاحيات والحدود» لكل مدير (``/business-operators/manager/<id>``). ثلاثة
مستويات، كلها مُخزَّنة على صفّ السياسة الموجود أصلًا
(``manager_distributor_policies``) — نَبني على المخزن القائم ولا نَفرع نظامًا
موازيًا:

  1. **وصول القسم (3 حالات)**: ``open`` (مفتوح) / ``locked`` (مقفول — ظاهر
     للعرض فقط) / ``hidden`` (مخفي). القسم غير المُهيّأ = ``open`` (غير
     انحداريّ: RBAC الدور يَبقى الحاكم حتى يَقفل/يُخفي المالك القسم صراحةً).
  2. **بوّابة الفعل**: create / edit / delete داخل قسم مفتوح (المستوى 2).
  3. **التحكّم الحقليّ**: أيّ الحقول بالضبط يَملك المدير تغييرها (المستوى 3).

المالك الرئيسي/السوبر يَتجاوز المستويات الثلاثة دائمًا (نفس عقد
``session_helpers._resolve_is_super`` و[[owner-only-bypass]]).

**سجلّ الأقسام قابل للتوسعة**: إضافة قسم = إدخال في ``MANAGER_SECTION_REGISTRY``
(القسم → endpointات + تصنيف endpointات العرض). أيّ endpoint غير مُدرَج لا
تُؤثّر عليه أعلام الأقسام (يَخضع لـRBAC العاديّ فقط).

كل القراءات مخزَّنة لكل طلب في ``flask.g`` — لا استعلام DB إضافيّ لكل بند.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from flask import g


# ─── قيم حالة القسم الثلاث ────────────────────────────────────────────────
OPEN = "open"
LOCKED = "locked"
HIDDEN = "hidden"
SECTION_STATES = (OPEN, LOCKED, HIDDEN)
# غير المُهيّأ = مفتوح (غير انحداريّ): المالك يَقفل/يُخفي صراحةً.
DEFAULT_SECTION_STATE = OPEN


# ─── سجلّ الأقسام: قسم منطقيّ → endpointات تنتمي إليه ────────────────────
# ``endpoints`` = كل endpointات القسم (عرضًا وكتابةً). عند «إخفاء» القسم
# تُحجب كلها (403 لأيّ method)؛ عند «قفله» تُحجب الكتابة فقط (403 لغير GET).
# القوائم غير حصريّة تمامًا لكنها تُغطّي بنود الشريط الجانبي والمسارات
# الحسّاسة لكل قسم — أضِف endpointات جديدة هنا عند الحاجة.
MANAGER_SECTION_REGISTRY: dict[str, dict[str, Any]] = {
    "subscribers": {
        "label": "المشتركون",
        "icon": "users",
        "endpoints": (
            # عرض
            "subscribers_overview", "subscribers_list", "users_list", "users_new",
            "users_edit", "users_profile", "users_360", "subscriber_360",
            "subscriber_groups_list", "online_list", "online_live_status",
            "connected_stats", "connected_stats_json",
            "rep_login_states_subscribers", "rep_subscriber_consumption",
            # كتابة/إجراء
            "users_create", "users_update", "users_delete", "users_bulk_delete",
            "users_toggle", "users_toggle_bulk", "users_extend", "users_extend_bulk",
            "users_change_plan", "users_quota_topup", "users_quota_topup_bulk",
            "users_quota_reset_daily", "users_quota_reset_daily_bulk",
            "users_balance_add", "users_balance_add_bulk",
            "users_send_sms", "users_send_sms_bulk", "users_send_credentials",
            "users_payment_create", "users_payment_create_bulk",
            "users_loan_create", "users_loan_create_bulk", "users_loan_settle",
            "online_temp_speed", "online_temp_speed_cancel", "users_temp_speed_cancel",
            "online_disconnect", "online_reconcile", "online_lock_mac", "online_lock_ip",
            "subscriber_groups_create", "subscriber_groups_update",
            "subscriber_groups_delete",
        ),
    },
    "cards": {
        "label": "البطاقات",
        "icon": "id-card",
        "endpoints": (
            # عرض
            "cards_overview", "cards_checker", "cards_checker_v2", "cards_batches",
            "cards_generate", "cards_offers", "cards_print_list", "print_templates",
            "cards_list", "card_marketplace", "card_users_list", "cards_recharge_list",
            "rep_login_states_cards", "cards_batches_export_csv",
            "cards_batches_export_pdf", "cards_batches_export_xlsx",
            "card_users_add",
            # كتابة/إجراء
            "cards_batch_edit", "cards_batches_bulk", "cards_batch_cards_actions",
            "cards_generate_progress_start", "cards_revoke", "cards_offer_use",
            "cards_recharge_new", "cards_recharge_batch_delete",
            "cards_print_new", "cards_print_batch_delete",
            "cards_import", "cards_import_analyze",
        ),
    },
    "plans": {
        "label": "الباقات والسرعات",
        "icon": "tags",
        "endpoints": (
            "plans_overview", "plans_list", "plans_new", "bw_list", "bw_new",
            "bandwidth_schedules",
            "plans_create", "plans_update", "plans_delete",
        ),
    },
    "distributors": {
        "label": "الموزّعون",
        "icon": "people-carry-box",
        "endpoints": (
            "distributors_list",
            "distributors_create", "distributors_update",
            "distributors_assign_batch", "distributors_settle",
        ),
    },
    "network": {
        "label": "الشبكة والراوترات",
        "icon": "network-wired",
        "endpoints": (
            "devices_list", "devices_new", "mt_operations", "mt_operations_live",
            "services_catalog", "pool_list", "diagnostics", "device_health_page",
            "device_health_api_checks", "device_health_api_list",
            "device_health_api_router_interfaces", "ipchange_page", "sync_list",
            "devices_create", "devices_update", "devices_toggle",
            "devices_bulk_toggle", "devices_delete",
            "network_devices_create", "network_devices_update", "network_devices_delete",
        ),
    },
    "reports": {
        "label": "التقارير",
        "icon": "chart-line",
        "endpoints": (
            "reports_home", "reports_financial", "reports_cards",
            "reports_distributors", "reports_archive", "reports_archive_create",
            "rep_sessions", "rep_failed_logins", "rep_login_status",
            "rep_login_states", "rep_mac_history", "rep_profile_changes",
            "rep_api_messages", "rep_coa_failures", "rep_manager_events",
            "rep_manager_login_status", "rep_user_events", "rep_speed_failures",
            "rep_used_cards", "rep_balance_movements", "rep_cash_transactions",
        ),
    },
    "finance": {
        "label": "المال والمحاسبة",
        "icon": "file-invoice-dollar",
        "endpoints": (
            "finance_center_hub", "accounting_hub", "billing_hub",
            "recharge_panel", "company_inventory", "finance_ledger",
            "finance_reports", "finance_reports_snapshot",
            "finance_reports_export_csv", "finance_reports_export_xlsx",
            "finance_reports_export_pdf",
            "business_finance_wallets_create", "business_finance_wallet_credit",
            "business_finance_wallet_debit", "inv_create",
        ),
    },
}


# عكس الفهرس: endpoint → اسم القسم (ثابت، يُبنى عند الاستيراد). endpoint في
# أكثر من قسم يُحسم لصالح أوّل قسم يُدرِجه (ترتيب السجلّ).
_EP_TO_SECTION: dict[str, str] = {}
for _sec, _spec in MANAGER_SECTION_REGISTRY.items():
    for _ep in _spec["endpoints"]:
        _EP_TO_SECTION.setdefault(_ep, _sec)


def section_names() -> tuple[str, ...]:
    return tuple(MANAGER_SECTION_REGISTRY.keys())


def section_of_endpoint(endpoint: str) -> Optional[str]:
    """اسم القسم الذي ينتمي إليه endpoint (يَقبل ``radius.xxx`` أو ``xxx``)."""
    if not endpoint:
        return None
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return _EP_TO_SECTION.get(name)


def is_mutating_method(method: str) -> bool:
    """هل الطلب كتابة؟ (locked يَسمح بالعرض ويَحجب الكتابة). GET/HEAD/OPTIONS
    = عرض؛ أيّ شيء آخر (POST/PUT/PATCH/DELETE) = كتابة."""
    return (method or "GET").upper() not in ("GET", "HEAD", "OPTIONS")


# ─── قراءة/تخزين grants صفّ المدير ────────────────────────────────────────
def _load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _grants_row(admin_id: Optional[int], tenant_id: int) -> dict[str, Any]:
    """يُرجع {section_access, action_grants, field_grants} للمدير — من
    ``manager_distributor_policies`` (entity_type='manager'). مخزَّن لكل طلب.

    لا يُنشئ صفًّا (قراءة فقط، الافتراض الآمن = فارغ). محصّن: أيّ خطأ DB
    يُرجع فارغًا (fail-open للأقسام: غياب سياسة = مفتوح، غير انحداريّ)."""
    empty = {"section_access": {}, "action_grants": {}, "field_grants": {}}
    if not admin_id:
        return empty
    key = (int(tenant_id or 1), int(admin_id))
    cache = getattr(g, "_mg_grants_cache", None)
    if isinstance(cache, dict) and cache.get("_key") == key:
        return cache["val"]
    val = dict(empty)
    try:
        from ..db.connection import db
        row = db().execute(
            """
            SELECT section_access_json, action_grants_json, field_grants_json
            FROM manager_distributor_policies
            WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
            """,
            (key[0], key[1]),
        ).fetchone()
        if row:
            val = {
                "section_access": _load(row["section_access_json"]),
                "action_grants": _load(row["action_grants_json"]),
                "field_grants": _load(row["field_grants_json"]),
            }
    except Exception:  # noqa: BLE001 — fail-open: لا نَكسر أيّ طلب على خطأ DB
        val = dict(empty)
    try:
        g._mg_grants_cache = {"_key": key, "val": val}
    except Exception:  # noqa: BLE001 — خارج سياق الطلب (اختبارات/CLI)
        pass
    return val


def _invalidate_cache() -> None:
    try:
        if hasattr(g, "_mg_grants_cache"):
            delattr(g, "_mg_grants_cache")
    except Exception:  # noqa: BLE001
        pass


# ─── المستوى 1: حالة القسم ────────────────────────────────────────────────
def section_state(admin_id: Optional[int], section: str, *, tenant_id: int = 1) -> str:
    """حالة قسمٍ للمدير: open/locked/hidden. غير المُهيّأ = DEFAULT_SECTION_STATE."""
    if section not in MANAGER_SECTION_REGISTRY:
        return OPEN
    access = _grants_row(admin_id, tenant_id).get("section_access") or {}
    val = str(access.get(section) or "").strip().lower()
    return val if val in SECTION_STATES else DEFAULT_SECTION_STATE


def endpoint_state(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> str:
    """حالة القسم الذي يَخصّه endpoint (open إن لم يَنتمِ لأيّ قسم مُدار)."""
    sec = section_of_endpoint(endpoint)
    if not sec:
        return OPEN
    return section_state(admin_id, sec, tenant_id=tenant_id)


def is_endpoint_hidden_for(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    return endpoint_state(admin_id, endpoint, tenant_id=tenant_id) == HIDDEN


def is_endpoint_locked_for(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    return endpoint_state(admin_id, endpoint, tenant_id=tenant_id) == LOCKED


def get_section_access(admin_id: Optional[int], *, tenant_id: int = 1) -> dict[str, str]:
    """الخريطة الكاملة (كل قسم → حالته الحاليّة) لواجهة الإعداد."""
    access = _grants_row(admin_id, tenant_id).get("section_access") or {}
    out: dict[str, str] = {}
    for name in MANAGER_SECTION_REGISTRY:
        val = str(access.get(name) or "").strip().lower()
        out[name] = val if val in SECTION_STATES else DEFAULT_SECTION_STATE
    return out


def section_catalog(admin_id: Optional[int], *, tenant_id: int = 1) -> list[dict[str, Any]]:
    """قائمة الأقسام + حالتها الحاليّة — لعرض مصفوفة الإعداد في القالب."""
    states = get_section_access(admin_id, tenant_id=tenant_id)
    return [
        {
            "name": name,
            "label": spec.get("label", name),
            "icon": spec.get("icon", "folder"),
            "state": states.get(name, DEFAULT_SECTION_STATE),
        }
        for name, spec in MANAGER_SECTION_REGISTRY.items()
    ]


# ─── الكتابة (من صفحة الصلاحيات) ─────────────────────────────────────────
def _ensure_policy_row(admin_id: int, tenant_id: int) -> None:
    """يَضمن وجود صفّ سياسة للمدير قبل UPDATE عمود grants — دون المساس بأيّ
    عمود آخر. نَستخدم ``get_policy(create=True)`` الذي يُنشئ الصفّ بالافتراضات
    **فقط إن كان غائبًا** (لا يُعيد كتابة الصلاحيات/الحدود القائمة — بخلاف
    ``set_policy`` الذي كان يَمسح permissions_json عند غياب المعامل)."""
    from .manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).get_policy(
        entity_type="manager", entity_id=int(admin_id), create=True)


def set_section_access(
    admin_id: int, mapping: dict[str, str], *, tenant_id: int = 1, by: int = 0
) -> dict[str, str]:
    """يَحفظ خريطة حالة الأقسام للمدير (يُطبّع القيم؛ يَتجاهل المفاتيح المجهولة).

    يُنشئ صفّ السياسة إن لم يكن موجودًا (عبر ``set_policy`` الذي يَبذر
    الافتراضات) ثم يُحدِّث عمود ``section_access_json`` وحده — دون المساس
    بالصلاحيات/الحدود الأخرى على الصفّ."""
    clean: dict[str, str] = {}
    for name, val in (mapping or {}).items():
        if name not in MANAGER_SECTION_REGISTRY:
            continue
        v = str(val or "").strip().lower()
        if v in SECTION_STATES and v != DEFAULT_SECTION_STATE:
            # نُخزّن فقط ما يَنحرف عن الافتراضي (open) — يُبقي الصفّ نظيفًا
            # وقابلية القراءة العكسيّة سليمة (الغياب = open).
            clean[name] = v
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "section_access_json", clean)
    _invalidate_cache()
    return clean


def _write_column(admin_id: int, tenant_id: int, column: str, value: dict[str, Any]) -> None:
    """كتابة عمود grants واحد على صفّ سياسة المدير (JSON)."""
    if column not in ("section_access_json", "action_grants_json", "field_grants_json"):
        raise ValueError(f"unknown grants column: {column}")
    from ..db.connection import db
    from ..db.helpers import now_iso
    db().execute(
        f"""
        UPDATE manager_distributor_policies
        SET {column}=?, updated_at=?
        WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
        """,
        (json.dumps(value or {}, ensure_ascii=False, sort_keys=True), now_iso(),
         int(tenant_id or 1), int(admin_id)),
    )


# ─── المستوى 2 و3: بوّابات الفعل والحقل (تخزين + قراءة؛ الإنفاذ في المراحل
#      التالية عبر مستهلكين في users_update / offer / batch update) ────────
def action_grants(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> Optional[dict[str, bool]]:
    """بوّابات create/edit/delete لكيان — أو None إن لم تُهيّأ (تحكّم مطفأ)."""
    grants = _grants_row(admin_id, tenant_id).get("action_grants") or {}
    ent = grants.get(entity)
    if not isinstance(ent, dict):
        return None
    return {k: bool(v) for k, v in ent.items()}


def field_grants(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> Optional[set[str]]:
    """الحقول المسموح للمدير تعديلها في كيان.

    - ``None`` = التحكّم الحقليّ **مطفأ** لهذا الكيان (كل الحقول قابلة للتعديل،
      سلوك اليوم — غير انحداريّ).
    - مجموعة (قد تكون فارغة) = التحكّم **مُفعَّل**: فقط هذه الحقول قابلة
      للتعديل؛ ما عداها يُسقَط/يُرفَض خادميًّا."""
    grants = _grants_row(admin_id, tenant_id).get("field_grants") or {}
    if entity not in grants:
        return None
    val = grants.get(entity)
    if not isinstance(val, list):
        return None
    return {str(x) for x in val}


def set_field_grants(
    admin_id: int, entity: str, fields: Optional[Iterable[str]], *, tenant_id: int = 1
) -> None:
    """يَضبط الحقول المسموحة لكيان. ``fields=None`` يُطفئ التحكّم (يَحذف المفتاح)."""
    current = dict(_grants_row(admin_id, tenant_id).get("field_grants") or {})
    if fields is None:
        current.pop(entity, None)
    else:
        current[entity] = sorted({str(f) for f in fields})
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "field_grants_json", current)
    _invalidate_cache()


def set_action_grants(
    admin_id: int, entity: str, actions: Optional[dict[str, bool]], *, tenant_id: int = 1
) -> None:
    """يَضبط بوّابات create/edit/delete لكيان. ``actions=None`` يُطفئ التحكّم."""
    current = dict(_grants_row(admin_id, tenant_id).get("action_grants") or {})
    if actions is None:
        current.pop(entity, None)
    else:
        current[entity] = {k: bool(v) for k, v in actions.items()}
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "action_grants_json", current)
    _invalidate_cache()


__all__ = [
    "OPEN", "LOCKED", "HIDDEN", "SECTION_STATES", "DEFAULT_SECTION_STATE",
    "MANAGER_SECTION_REGISTRY", "section_names", "section_of_endpoint",
    "is_mutating_method", "section_state", "endpoint_state",
    "is_endpoint_hidden_for", "is_endpoint_locked_for",
    "get_section_access", "section_catalog", "set_section_access",
    "action_grants", "field_grants", "set_field_grants", "set_action_grants",
]
