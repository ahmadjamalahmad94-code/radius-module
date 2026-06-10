"""Section flags — مصدر واحد لإخفاء/تعطيل أقسام لوحة الإدارة، يُحرس به
الشريط الجانبي والمسارات الخادمية معًا.

السياق
─────
حتى الآن كانت بنود التنقّل المخفية من الشريط الجانبي ـ مثل بطاقات
«شبكة العمليّات» التجريبية و«دفع بيانات التوزيع» و«الإعداد الهندسي»
و«إعداد الأسطول» ـ تُخفى بالتعليق في `_sidebar.html` فقط، بينما مساراتها
تبقى مسجَّلة وقابلة للوصول المباشر بالعنوان (`/network/devices`،
`/mt-push-setup`، `/setup-wizard/fleet`…) بلا 403. هذه ثغرة Broken
Access Control: الشريط الجانبي يُخفي والمتصفّح المباشر يدخل.

الحلّ
────
سجلّ مركزي بالأقسام يربط كل قسم بـ:
  • قائمة endpointات Flask (بدون بادئة `radius.`).
  • علم «إخفاء» (hidden): القسم لا يَظهر في الشريط الجانبي + كل مساراته
    تعيد 403 لمن ليس مدير رئيسي (super_admin).
  • علم «تعطيل/إيقاف» (disabled): مثل الإخفاء تمامًا في الحارس الخادمي،
    لكنه يَظهر في الشريط الجانبي للسوبر مع شارة «موقوف» حتى يَعرف المالك
    أن القسم ما زال موجودًا لكنه مُجمَّد. كلاهما يتجاوزهما السوبر دائمًا.

العلامتان تُخزَّنان في `tenant_settings` (مفاتيح
`section.<name>.hidden` / `section.<name>.disabled` = "1"/"0") فيتحكّم
المالك بهما من واجهة الإدارة بلا أمر طرفي. القراءة مخزَّنة لكل طلب في
`flask.g` فلا يتكرّر استعلام DB لكل بند تنقّل.

عقد الاستخدام
────────────
  • `section_of(endpoint)`         → اسم القسم المنطقي أو None.
  • `get_flags(name, tenant_id=…)` → {"hidden": bool, "disabled": bool}.
  • `is_section_blocked(endpoint)` → True إذا كان endpoint يخصّ قسمًا
    مُخفيًّا أو مُعطّلًا (يُستخدم في الحارس الخادمي ـ النتيجة 403).
  • `list_sections()`               → لقائمة التحكّم في واجهة الإدارة.
  • `set_flags(name, hidden, disabled, by=…)` → كتابة من واجهة الإدارة.

الـRBAC القديم يبقى متكاملًا مع هذا السجلّ: مفتاح `_NAV_PERM` يقرّر «من
يَرى ماذا» داخل الأقسام التي مساراتها مفتوحة؛ السجلّ هنا يقرّر «أيّ
قسم مفتوح/مغلق أساسًا» على مستوى المستأجر كاملًا.
"""
from __future__ import annotations

from typing import Iterable, Optional

from flask import g, session


# مصدر الحقيقة الواحد: قسم منطقي → endpointات تنتمي إليه.
# عند إخفاء/تعطيل القسم تُحجب كل هذه endpointات من المتصفّح المباشر.
# هذه الأقسام التي حُذفت من الشريط الجانبي ولكن مساراتها بقيت مفتوحة (وفق
# تدقيق المالك) + الأقسام الرئيسية الكبيرة الموجودة في الشريط الآن حتى
# يَتحكّم المالك بها من نفس الواجهة الموحَّدة عوضًا عن التعليق في القالب.
SECTION_REGISTRY: dict[str, dict] = {
    # ── الأقسام «المخفية بالتعليق» في الشريط الجانبي والتي مساراتها بقيت
    #    مفتوحة قبل هذا الإصلاح (تدقيق المالك، يونيو 2026) ──
    "network_ops_legacy": {
        "label": "عائلة شبكة العمليّات (التجريبية)",
        "description": "بطاقات «تابع أجهزة الشبكة»/«مسح الشبكة»/«تنبيهات Telegram»"
                       " — أُخفِيت مؤقتاً حتى اكتمال صقل التجربة. كانت مساراتها"
                       " قابلة للوصول المباشر قبل هذا الإصلاح.",
        "endpoints": (
            "network_devices_list",
            "network_ip_scan_page",
            "network_telegram_settings",
        ),
        # افتراضيًا مخفية (نفس نيّة المالك من قبل) — يستطيع إعادة إظهارها من
        # الإدارة دون لمس القالب.
        "default_hidden": True,
        "default_disabled": False,
    },
    "dhcp_push": {
        "label": "دفع بيانات التوزيع (DHCP push)",
        "description": "البطاقة الجانبية أُزيلت بعد اعتماد نفق WireGuard المركزي."
                       " المسار /mt-push-setup يبقى مسجَّلًا، فاستخدم هذه البوابة"
                       " لإغلاقه على غير السوبر.",
        "endpoints": ("mt_push_setup",),
        "default_hidden": True,
        "default_disabled": False,
    },
    "engineering_setup": {
        "label": "الإعداد الهندسي",
        "description": "معالج /setup-wizard المتقدّم — يبقى super-only في RBAC،"
                       " ويُغلق هنا للجميع إن أراد المالك سحبه نهائيًا.",
        "endpoints": ("setup_wizard_page",),
        "default_hidden": True,
        "default_disabled": False,
    },
    "fleet_setup": {
        "label": "إعداد أسطول الراوترات",
        "description": "صفحات /setup-wizard/fleet — كانت مفتوحة بلا أيّ حارس قبل"
                       " هذا الإصلاح.",
        "endpoints": (
            "setup_wizard_fleet_page",
            "setup_wizard_fleet_data",
            "setup_wizard_fleet_router",
            "setup_wizard_fleet_router_resume",
            "setup_wizard_fleet_router_retire",
        ),
        "default_hidden": True,
        "default_disabled": False,
    },
    # ── الأقسام الرئيسية في الشريط الجانبي ── (افتراضيًا ظاهرة وفعّالة،
    #    لكن المالك يستطيع تعطيل أيّها من واجهة الإدارة لاحقًا) ──
    "payments_lab": {
        "label": "مختبر الدفع الإلكتروني (تجريبي)",
        "description": "محاكاة كاملة لتدفّق الدفع قبل ربط البوّابات الفعلية."
                       " مخفيّ افتراضيًا في النشر الإنتاجي.",
        "endpoints": (
            "payments_lab",
            "pay_demo",
            "pay_demo_start",
            "pay_demo_otp",
            "pay_demo_resend",
        ),
        "default_hidden": False,
        "default_disabled": False,
    },
    "store_support": {
        "label": "دعم وطلبات المتجر",
        "description": "تأكيد إيداع/سحب وحركة الشات — حركة مال حقيقية."
                       " RBAC يحرسها بـstore.review، وهذا القسم يُتيح تجميدها"
                       " للمستأجر بأكمله عند الحاجة.",
        "endpoints": (
            "store_support",
            "store_support_deposit_confirm",
            "store_support_deposit_reject",
            "store_support_withdrawal_confirm",
            "store_support_withdrawal_reject",
            "store_support_payment_method_create",
            "store_support_payment_method_update",
            "store_support_chat_post",
        ),
        "default_hidden": False,
        "default_disabled": False,
    },
}


# عكس الفهرس: endpoint → اسم القسم. يُبنى عند الاستيراد ـ ثابت.
_EP_TO_SECTION: dict[str, str] = {
    ep: name
    for name, spec in SECTION_REGISTRY.items()
    for ep in spec["endpoints"]
}


# مفاتيح tenant_settings
def _key_hidden(name: str) -> str:   return f"section.{name}.hidden"
def _key_disabled(name: str) -> str: return f"section.{name}.disabled"


def _truthy(value: object) -> bool:
    """تطبيع للقيمة المخزَّنة في tenant_settings — "1"/"true"/"yes"/"on" → True."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "on")


def _resolve_tenant_id(override: Optional[int] = None) -> int:
    if override is not None:
        return int(override)
    try:
        from ..core.tenant import DEFAULT_TENANT_ID
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except Exception:  # noqa: BLE001
        return 1


def _get_setting_safe(tid: int, key: str, default: str = "") -> str:
    """قراءة محصّنة من DB — لا تكسر الصفحة أبدًا عند فشل DB."""
    try:
        from ..db.repos import tenants_repo
        return tenants_repo.get_setting(tid, key, default) or ""
    except Exception:  # noqa: BLE001 — fail-open
        return default


def _read_request_cache(tid: int) -> dict[str, dict[str, bool]]:
    """قراءة كل أعلام الأقسام مرّةً لكل طلب، مع كاش في flask.g."""
    cached = getattr(g, "_section_flags_cache", None)
    if cached and cached.get("_tid") == tid:
        return cached
    flags: dict[str, dict[str, bool]] = {"_tid": tid}
    for name, spec in SECTION_REGISTRY.items():
        h_raw = _get_setting_safe(tid, _key_hidden(name), "")
        d_raw = _get_setting_safe(tid, _key_disabled(name), "")
        hidden   = _truthy(h_raw) if h_raw != "" else bool(spec.get("default_hidden", False))
        disabled = _truthy(d_raw) if d_raw != "" else bool(spec.get("default_disabled", False))
        flags[name] = {"hidden": hidden, "disabled": disabled}
    try:
        g._section_flags_cache = flags
    except Exception:  # noqa: BLE001 — خارج سياق الطلب
        pass
    return flags


def section_of(endpoint: str) -> Optional[str]:
    """يُرجع اسم القسم الذي ينتمي إليه endpoint — أو None إن لم يكن مُسجَّلًا.

    يقبل الاسم بصيغة `radius.xxx` أو `xxx`. الـendpoint غير المسجَّل في
    هذا السجلّ لا تؤثّر عليه أعلام الأقسام (يخضع فقط للـRBAC المعتاد).
    """
    if not endpoint:
        return None
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return _EP_TO_SECTION.get(name)


def get_flags(name: str, *, tenant_id: Optional[int] = None) -> dict[str, bool]:
    """يُرجع علامتي القسم (hidden/disabled) — مدمجتين مع الافتراضي."""
    spec = SECTION_REGISTRY.get(name)
    if not spec:
        return {"hidden": False, "disabled": False}
    tid = _resolve_tenant_id(tenant_id)
    cache = _read_request_cache(tid)
    return dict(cache.get(name, {"hidden": False, "disabled": False}))


def is_section_blocked(endpoint: str, *, tenant_id: Optional[int] = None) -> bool:
    """هل endpoint يخصّ قسمًا مُخفيًّا أو مُعطّلًا؟ (السوبر يَتجاوز خارجيًا
    قبل استدعاء هذه الدالة، فلا حاجة للتحقّق منه هنا.)"""
    name = section_of(endpoint)
    if not name:
        return False
    f = get_flags(name, tenant_id=tenant_id)
    return bool(f.get("hidden") or f.get("disabled"))


def is_section_hidden(name: str, *, tenant_id: Optional[int] = None) -> bool:
    return bool(get_flags(name, tenant_id=tenant_id).get("hidden"))


def is_section_disabled(name: str, *, tenant_id: Optional[int] = None) -> bool:
    return bool(get_flags(name, tenant_id=tenant_id).get("disabled"))


def list_sections(*, tenant_id: Optional[int] = None) -> list[dict]:
    """قائمة الأقسام لواجهة الإدارة — مع الأعلام الحالية ووصف لكلٍّ منها."""
    tid = _resolve_tenant_id(tenant_id)
    out: list[dict] = []
    for name, spec in SECTION_REGISTRY.items():
        f = get_flags(name, tenant_id=tid)
        out.append({
            "name": name,
            "label": spec.get("label", name),
            "description": spec.get("description", ""),
            "endpoints": list(spec.get("endpoints", ())),
            "hidden": f["hidden"],
            "disabled": f["disabled"],
            "default_hidden": bool(spec.get("default_hidden", False)),
            "default_disabled": bool(spec.get("default_disabled", False)),
        })
    return out


def set_flags(name: str, *, hidden: bool, disabled: bool,
              tenant_id: Optional[int] = None, by: int = 0) -> dict[str, bool]:
    """يُحدِّث علامات القسم في tenant_settings ويُبطل كاش الطلب الحالي."""
    if name not in SECTION_REGISTRY:
        raise KeyError(f"unknown section: {name}")
    tid = _resolve_tenant_id(tenant_id)
    from ..db.repos import tenants_repo
    tenants_repo.set_setting(tid, _key_hidden(name),   "1" if hidden else "0",   by=by)
    tenants_repo.set_setting(tid, _key_disabled(name), "1" if disabled else "0", by=by)
    # أبطل الكاش حتى تَنعكس النتيجة في بقيّة الطلب الحالي.
    try:
        if hasattr(g, "_section_flags_cache"):
            delattr(g, "_section_flags_cache")
    except Exception:  # noqa: BLE001
        pass
    return {"hidden": hidden, "disabled": disabled}


def reset_to_defaults(name: str, *, tenant_id: Optional[int] = None, by: int = 0) -> dict[str, bool]:
    """يُعيد القسم إلى علاماته الافتراضية كما هي في السجلّ."""
    spec = SECTION_REGISTRY.get(name)
    if not spec:
        raise KeyError(f"unknown section: {name}")
    return set_flags(
        name,
        hidden=bool(spec.get("default_hidden", False)),
        disabled=bool(spec.get("default_disabled", False)),
        tenant_id=tenant_id, by=by,
    )


def known_section_names() -> tuple[str, ...]:
    return tuple(SECTION_REGISTRY.keys())


def endpoints_for(name: str) -> tuple[str, ...]:
    spec = SECTION_REGISTRY.get(name)
    if not spec:
        return ()
    return tuple(spec.get("endpoints", ()))


def all_managed_endpoints() -> Iterable[str]:
    return tuple(_EP_TO_SECTION.keys())


# ── مساعد جلسة: المدير الحالي هل هو super؟ ─────────────────────────
# الحارس الخادمي يَستدعيه قبل تطبيق أعلام القسم. يُكرَّر هنا منعًا لاستيراد
# دائري مع `session_helpers` (الذي يَستورد admins_repo و services).
def current_is_super() -> bool:
    try:
        return bool(session.get("is_super_admin"))
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "SECTION_REGISTRY",
    "section_of", "get_flags", "set_flags", "reset_to_defaults",
    "is_section_blocked", "is_section_hidden", "is_section_disabled",
    "list_sections", "known_section_names", "endpoints_for",
    "all_managed_endpoints", "current_is_super",
]
