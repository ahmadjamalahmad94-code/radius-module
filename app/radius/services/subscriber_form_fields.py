"""إدارةُ البيانات: أيُّ حقولِ نموذجِ المشترك تَظهر لصاحب هذه الشبكة.

ليست كلُّ شبكةٍ تحتاج كلَّ الحقول. مقهًى يبيع ساعاتٍ لا يسأل عن الرقم
الوطنيّ ولا خادم DNS، ومزوّدٌ منزليٌّ لا يفتح قسم «مايكروتيك متقدّم» مرّةً
في السنة. فالنموذجُ الواحدُ لكلّ الشبكات يعني أنّ أكثر مَن يستعمله يمرّ
يوميًّا على حقولٍ لا تعنيه.

🔑 **الإخفاءُ عرضٌ لا حذف.** الحقلُ المُطفأ يبقى في الصفحة وفي الـPOST،
يحمل قيمتَه المحفوظة كما هي، ويُخفى بالـCSS وحدَها. ولهذا سببٌ ثقيل:
`_form_dto` يبني الـDTO من الـPOST كاملًا، فحقلٌ غائبٌ عن الـPOST يصل
فارغًا ويُكتب فارغًا فوق ما في القاعدة. أي أنّ إخفاءً «نظيفًا» بحذف
الحقل من الصفحة كان سيمحو مدينةَ المشترك وبريدَه وملاحظاتِه عند أوّل
حفظ. الإخفاءُ بالعرض يجعل البياناتِ تمرّ كما هي ذهابًا وإيابًا.

🔒 **حقولٌ لا تُخفى** (`CORE`): ما لا يقوم الحسابُ بدونه أو ما طلب المالكُ
إبقاءَه ظاهرًا دائمًا. تُستثنى من السجلّ فلا مفتاحَ لها أصلًا — أوضحُ من
مفتاحٍ يرفض أن يُطفأ.

الافتراضُ **ظاهر**: شبكةٌ لم يلمس صاحبُها الصفحةَ ترى نموذجَها كما كان.
"""
from __future__ import annotations

import logging
from typing import Iterable

_LOG = logging.getLogger(__name__)

SETTING_PREFIX = "subscriber_form.field."

# ── الحقول الإلزاميّة: تظهر دائمًا ولا مفتاحَ لها ────────────────────
#    (اسم الدخول · الباقة · السرعة المخصّصة · السرعة المؤقّتة ·
#     الاسم الأوّل · الاسم الثاني · الجوال)
CORE: frozenset[str] = frozenset({
    "username", "plan_id",
    "custom_speed", "download_speed_kbps", "upload_speed_kbps",
    "temporary_speed", "temporary_speed_duration_minutes",
    "temporary_download_speed_kbps", "temporary_upload_speed_kbps",
    "name_first", "name_second", "mobile",
})


def _f(key: str, label: str, sel: str = "", warn: str = "") -> dict:
    """حقلٌ قابلٌ للإخفاء.

    ``sel`` محدِّدُ CSS للغلاف الذي يُخفى؛ افتراضُه غلافُ الحقل الذي يحوي
    مُدخَلًا بهذا الاسم. و``warn`` تحذيرٌ يُعرض في صفحة الإدارة حين يكون
    لإخفاءِ الحقل أثرٌ لا يخطر على البال.
    """
    return {
        "key": key,
        "label": label,
        "sel": sel or ('.uf-field:has([name="%s"])' % key),
        "warn": warn,
    }


# ── السجلّ: أقسامٌ بترتيب ظهورها في النموذج نفسِه ────────────────────
GROUPS: tuple[dict, ...] = (
    {
        "key": "account",
        "label": "حساب الإنترنت",
        "icon": "fa-wifi",
        "fields": (
            _f("login_without_password", "مفتاح «قسم كلمة المرور»",
               sel=".uf-field:has(#uf-lwp)"),
            _f("password", "كلمة المرور",
               warn="إخفاؤها يعني إنشاءَ مشتركين بلا كلمة مرور. لا تُخفِها "
                    "إلّا إن كانت شبكتُك تعمل بالدخول بالاسم وحدَه."),
            _f("status", "الحالة"),
            _f("auto_renewal", "التجديد التلقائيّ"),
            _f("service_type", "نوع الخدمة (هوت سبوت / برودباند)",
               sel='.uf-field:has([name="service_type"])'),
            _f("manager_id", "المدير المسؤول"),
            _f("group", "مجموعة المشترك"),
            _f("expiry", "تاريخ وساعة انتهاء الاشتراك",
               sel='.uf-field:has([name="expire_year"])',
               warn="عند الإنشاء، تاريخٌ فارغٌ يجعل الحساب منتهيًا فورَ "
                    "إضافته. لا تُخفِ هذا الحقل إلّا إن كنتَ تضبط الانتهاءَ "
                    "من مكانٍ آخر."),
            _f("custom_price", "سعر مخصّص للمشترك"),
        ),
    },
    {
        "key": "personal",
        "label": "المعلومات الشخصية",
        "icon": "fa-id-card",
        "fields": (
            _f("name_third", "الاسم الثالث",
               sel='.uf-field:has([data-name-part="third"])'),
            _f("name_fourth", "الاسم الرابع",
               sel='.uf-field:has([data-name-part="fourth"])'),
            _f("email", "البريد الإلكترونيّ"),
            _f("national_id", "الرقم الوطنيّ"),
            _f("payment_method", "طريقة الدفع المفضّلة"),
            _f("city", "المدينة"),
            _f("district", "المنطقة / الحيّ"),
            _f("remark", "الملاحظات"),
        ),
    },
    {
        "key": "speed",
        "label": "السرعة",
        "icon": "fa-gauge-high",
        "fields": (
            _f("speed_rules", "قواعد السرعة المجدوَلة",
               sel="#uf-speed .uf-sub-head--sched, #uf-speed .sr2-panel"),
        ),
    },
    {
        "key": "quota",
        "label": "الحصة والوقت",
        "icon": "fa-database",
        "fields": (
            _f("combined_quota_mb", "كوتا إجماليّة (مدمجة)"),
            _f("download_quota_mb", "كوتا التنزيل"),
            _f("upload_quota_mb", "كوتا الرفع"),
            _f("total_connection_time_min", "إجماليّ وقت الاتصال"),
            _f("quota_limit_enabled", "تطبيق حدّ الكوتا"),
            _f("connection_time_limit_enabled", "تطبيق حدّ وقت الاتصال"),
            _f("daily_connection_time_min", "وقت الاتصال اليوميّ"),
            _f("connection_schedule", "الأيّام والأوقات المسموحة",
               sel='.uf-field:has([name="connection_schedule"])'),
        ),
    },
    {
        "key": "network",
        "label": "الشبكة وقيود الاتصال",
        "icon": "fa-network-wired",
        "fields": (
            _f("mac_lock", "قفل عناوين MAC", sel=".uf-mac-manager"),
            _f("static_ip", "عنوان IP ثابت"),
            _f("nas_ip_address", "عنوان IP لجهاز الشبكة"),
            _f("service_name", "اسم الخدمة"),
            _f("device_count", "عدد الأجهزة المسموحة"),
            _f("equal_share_download", "تقسيم سرعة التنزيل على الأجهزة"),
            _f("equal_share_upload", "تقسيم سرعة الرفع على الأجهزة"),
            _f("device_limit_mode", "السلوك عند بلوغ حدّ الأجهزة"),
            _f("primary_dns_ppp", "خادم DNS الأساسيّ (PPP)"),
            _f("secondary_dns_ppp", "خادم DNS الثانويّ (PPP)"),
            _f("nas_port_id", "منفذ جهاز الشبكة"),
        ),
    },
    {
        "key": "pppoe",
        "label": "البرودباند (PPPoE)",
        "icon": "fa-ethernet",
        "fields": (
            _f("pppoe_username", "اسم مستخدم البرودباند"),
            _f("pppoe_password", "كلمة مرور البرودباند"),
            _f("pppoe_ip", "عنوان IP للبرودباند"),
        ),
    },
    {
        "key": "advanced",
        "label": "إعدادات شبكة متقدّمة جدًّا",
        "icon": "fa-sliders",
        "fields": (
            _f("mikrotik_filter_chain", "سلسلة الفلترة (MikroTik)"),
            _f("mikrotik_address_list", "قائمة العناوين (MikroTik)"),
            _f("mikrotik_framed_route", "المسار الموجَّه (MikroTik)"),
            _f("mikrotik_user_group", "مجموعة المستخدم (MikroTik)"),
            _f("mikrotik_winbox_group", "مجموعة WinBox"),
            _f("mikrotik_queue_priority", "أولويّة الطابور"),
            _f("framed_pool", "مجمّع العناوين (Framed-Pool)"),
            _f("acct_interim_interval_sec", "فترة تقارير المحاسبة"),
            _f("ppp_attributes_extra", "خصائص PPP إضافيّة"),
        ),
    },
)

ALL_FIELDS: tuple[dict, ...] = tuple(
    f for g in GROUPS for f in g["fields"])
_BY_KEY: dict[str, dict] = {f["key"]: f for f in ALL_FIELDS}


def field_keys() -> tuple[str, ...]:
    return tuple(f["key"] for f in ALL_FIELDS)


def _truthy(raw) -> bool:
    return str(raw if raw is not None else "1").strip().lower() not in (
        "0", "false", "no", "off", "")


def visibility(tenant_id: int) -> dict[str, bool]:
    """خريطةُ «ظاهر؟» لكلّ حقلٍ قابلٍ للإخفاء — الافتراضُ ظاهر.

    محصَّنة: أيُّ خطأٍ في القراءة يردّ الجميعَ ظاهرًا. إخفاءُ حقلٍ قرارُ
    صاحبِ الشبكة، وعطبٌ عابرٌ في قراءة الإعدادات يجب ألّا يُخفي حقلًا لم
    يطلب أحدٌ إخفاءَه.
    """
    out = {f["key"]: True for f in ALL_FIELDS}
    try:
        from ..db.repos import tenants_repo
        for key in out:
            out[key] = _truthy(
                tenants_repo.get_setting(int(tenant_id),
                                         SETTING_PREFIX + key, "1"))
    except Exception:  # noqa: BLE001
        _LOG.warning("subscriber_form_fields: تعذّرت قراءةُ إعدادات الظهور "
                     "(tenant=%r) — تُعرض كلُّ الحقول", tenant_id,
                     exc_info=True)
        return {f["key"]: True for f in ALL_FIELDS}
    return out


def hidden_keys(tenant_id: int) -> tuple[str, ...]:
    vis = visibility(tenant_id)
    return tuple(k for k, on in vis.items() if not on)


def hidden_css(tenant_id: int) -> str:
    """قواعدُ CSS تُخفي ما أُطفئ — تُحقَن في نموذج المشترك.

    الإخفاءُ خادميٌّ لا بجافاسكربت: حقلٌ يظهر لحظةً ثمّ يختفي وميضٌ قبيح،
    وأسوأُ منه أن يُكتب فيه شيءٌ قبل أن يختفي.
    """
    sels = [_BY_KEY[k]["sel"] for k in hidden_keys(tenant_id) if k in _BY_KEY]
    if not sels:
        return ""
    return ",\n".join(sels) + " { display: none !important; }"


def set_visibility(tenant_id: int, visible: Iterable[str], *,
                   by: int = 0) -> int:
    """يحفظ الظهورَ لكلّ الحقول دفعةً واحدة؛ يردّ عددَ ما تغيّر فعلًا.

    ``visible`` مجموعةُ المفاتيح المُشعَلة (ما يصل من النموذج)؛ وكلُّ ما
    عداها من السجلّ يُطفأ. فالغيابُ إطفاءٌ صريح — وهذا ما يفعله مربّعُ
    اختيارٍ غيرُ مؤشَّرٍ في HTML.
    """
    from ..db.repos import tenants_repo
    on = {str(k) for k in visible}
    changed = 0
    for f in ALL_FIELDS:
        key = SETTING_PREFIX + f["key"]
        val = "1" if f["key"] in on else "0"
        if tenants_repo.get_setting(int(tenant_id), key, "1") != val:
            tenants_repo.set_setting(int(tenant_id), key, val, by=by)
            changed += 1
    return changed
