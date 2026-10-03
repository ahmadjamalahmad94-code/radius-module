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
from app.i18n_text import N_

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
        "label": N_("حساب الإنترنت"),
        "icon": "fa-wifi",
        "fields": (
            _f("login_without_password", N_("مفتاح «قسم كلمة المرور»"),
               sel=".uf-field:has(#uf-lwp)"),
            _f("password", N_("كلمة المرور"),
               warn=N_("إخفاؤها يعني إنشاءَ مشتركين بلا كلمة مرور. لا تُخفِها "
                    "إلّا إن كانت شبكتُك تعمل بالدخول بالاسم وحدَه.")),
            _f("status", N_("الحالة")),
            _f("auto_renewal", N_("التجديد التلقائيّ")),
            _f("service_type", N_("نوع الخدمة (هوت سبوت / برودباند)"),
               sel='.uf-field:has([name="service_type"])'),
            _f("manager_id", N_("المدير المسؤول")),
            _f("group", N_("مجموعة المشترك")),
            _f("expiry", N_("تاريخ وساعة انتهاء الاشتراك"),
               sel='.uf-field:has([name="expire_year"])',
               warn=N_("عند الإنشاء، تاريخٌ فارغٌ يجعل الحساب منتهيًا فورَ "
                    "إضافته. لا تُخفِ هذا الحقل إلّا إن كنتَ تضبط الانتهاءَ "
                    "من مكانٍ آخر.")),
            _f("custom_price", N_("سعر مخصّص للمشترك")),
        ),
    },
    {
        "key": "personal",
        "label": N_("المعلومات الشخصية"),
        "icon": "fa-id-card",
        "fields": (
            _f("name_third", N_("الاسم الثالث"),
               sel='.uf-field:has([data-name-part="third"])'),
            _f("name_fourth", N_("الاسم الرابع"),
               sel='.uf-field:has([data-name-part="fourth"])'),
            _f("email", N_("البريد الإلكترونيّ")),
            _f("national_id", N_("الرقم الوطنيّ")),
            _f("payment_method", N_("طريقة الدفع المفضّلة")),
            _f("city", N_("المدينة")),
            _f("district", N_("المنطقة / الحيّ")),
            _f("remark", N_("الملاحظات")),
        ),
    },
    {
        "key": "speed",
        "label": N_("السرعة"),
        "icon": "fa-gauge-high",
        "fields": (
            _f("speed_rules", N_("قواعد السرعة المجدوَلة"),
               sel="#uf-speed .uf-sub-head--sched, #uf-speed .sr2-panel"),
        ),
    },
    {
        "key": "quota",
        # لا حقلَ إلزاميًّا في هذا القسم، فإن أُطفئت حقولُه كلُّها
        # طُوي بعنوانه ورابطِه — عنوانُ قسمٍ بلا محتوًى ضجيج.
        "sel": '#uf-quota, .uf-side a[href="#uf-quota"]',
        "label": N_("الحصة والوقت"),
        "icon": "fa-database",
        "fields": (
            _f("combined_quota_mb", N_("كوتا إجماليّة (مدمجة)")),
            _f("download_quota_mb", N_("كوتا التنزيل")),
            _f("upload_quota_mb", N_("كوتا الرفع")),
            _f("total_connection_time_min", N_("إجماليّ وقت الاتصال")),
            _f("quota_limit_enabled", N_("تطبيق حدّ الكوتا")),
            _f("connection_time_limit_enabled", N_("تطبيق حدّ وقت الاتصال")),
            _f("daily_connection_time_min", N_("وقت الاتصال اليوميّ")),
            _f("connection_schedule", N_("الأيّام والأوقات المسموحة"),
               sel='.uf-field:has([name="connection_schedule"])'),
        ),
    },
    {
        "key": "network",
        # لا حقلَ إلزاميًّا في هذا القسم، فإن أُطفئت حقولُه كلُّها
        # طُوي بعنوانه ورابطِه — عنوانُ قسمٍ بلا محتوًى ضجيج.
        "sel": '#uf-network, .uf-side a[href="#uf-network"]',
        "label": N_("الشبكة وقيود الاتصال"),
        "icon": "fa-network-wired",
        "fields": (
            _f("mac_lock", N_("قفل عناوين MAC"), sel=".uf-mac-manager"),
            _f("static_ip", N_("عنوان IP ثابت")),
            _f("nas_ip_address", N_("عنوان IP لجهاز الشبكة")),
            _f("service_name", N_("اسم الخدمة")),
            _f("device_count", N_("عدد الأجهزة المسموحة")),
            _f("equal_share_download", N_("تقسيم سرعة التنزيل على الأجهزة")),
            _f("equal_share_upload", N_("تقسيم سرعة الرفع على الأجهزة")),
            _f("device_limit_mode", N_("السلوك عند بلوغ حدّ الأجهزة")),
            _f("primary_dns_ppp", N_("خادم DNS الأساسيّ (PPP)")),
            _f("secondary_dns_ppp", N_("خادم DNS الثانويّ (PPP)")),
            _f("nas_port_id", N_("منفذ جهاز الشبكة")),
        ),
    },
    {
        "key": "pppoe",
        # لا حقلَ إلزاميًّا في هذا القسم، فإن أُطفئت حقولُه كلُّها
        # طُوي بعنوانه ورابطِه — عنوانُ قسمٍ بلا محتوًى ضجيج.
        "sel": '#uf-pppoe, .uf-side a[href="#uf-pppoe"]',
        "label": N_("البرودباند (PPPoE)"),
        "icon": "fa-ethernet",
        "fields": (
            _f("pppoe_username", N_("اسم مستخدم البرودباند")),
            _f("pppoe_password", N_("كلمة مرور البرودباند")),
            _f("pppoe_ip", N_("عنوان IP للبرودباند")),
        ),
    },
    {
        "key": "advanced",
        # لا حقلَ إلزاميًّا في هذا القسم، فإن أُطفئت حقولُه كلُّها
        # طُوي بعنوانه ورابطِه — عنوانُ قسمٍ بلا محتوًى ضجيج.
        "sel": '#uf-advanced, .uf-side a[href="#uf-advanced"]',
        "label": N_("إعدادات شبكة متقدّمة جدًّا"),
        "icon": "fa-sliders",
        "fields": (
            _f("mikrotik_filter_chain", N_("سلسلة الفلترة (MikroTik)")),
            _f("mikrotik_address_list", N_("قائمة العناوين (MikroTik)")),
            _f("mikrotik_framed_route", N_("المسار الموجَّه (MikroTik)")),
            _f("mikrotik_user_group", N_("مجموعة المستخدم (MikroTik)")),
            _f("mikrotik_winbox_group", N_("مجموعة WinBox")),
            _f("mikrotik_queue_priority", N_("أولويّة الطابور")),
            _f("framed_pool", N_("مجمّع العناوين (Framed-Pool)")),
            _f("acct_interim_interval_sec", N_("فترة تقارير المحاسبة")),
            _f("ppp_attributes_extra", N_("خصائص PPP إضافيّة")),
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
    off = set(hidden_keys(tenant_id))
    sels = [_BY_KEY[k]["sel"] for k in off if k in _BY_KEY]
    # 🔑 خواءُ القسم يُحسَب هنا لا في المتصفّح: الأقسامُ المطويّة
    #    تُخفي جسمَها بـdisplay:none، فلا سبيلَ لجافاسكربت أن تميّز
    #    «مخفيٌّ بأمر المالك» من «مطويٌّ الآن» — والسجلُّ يعرف يقينًا.
    for g in GROUPS:
        if not g.get("sel"):
            continue
        if all(f["key"] in off for f in g["fields"]):
            sels.append(g["sel"])
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
