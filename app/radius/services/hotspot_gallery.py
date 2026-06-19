# -*- coding: utf-8 -*-
"""hotspot_gallery — معرض القوالب الجاهزة حسب نوع المنشأة (P4).

كل «قالب معرض» تركيبة جاهزة: قالب أساسي من المكتبة (base_slug) +
ثيم + إضافات مفعّلة بإعداد منطقي + تعديلات متغيّرات (اللون/الترحيب) —
مصنّفة ووسومة بنوع المنشأة (شبكات/مطعم/كافيه/محل/فندق/صالون/عيادة/
مول/مدرسة...). المالك يختار قالبًا → يُحمَّل في المصمّم قابلًا للتحرير
بالكامل (يندمج فوق متغيّراته الحالية فيبقى اسمه/شعاره/دعمه).

الشكل مصدر حقيقة واحد: GALLERY. التطبيق يحوّل القالب إلى
(template_slug, variables-overrides, addons-config) ويحفظه عبر
hotspot_designs_repo.save_design — لا منطق نشر جديد.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# أنواع المنشآت (vertical) — المفتاح، التسمية، الأيقونة.
VERTICALS: dict[str, tuple[str, str]] = {
    "isp": ("شبكات ومزوّدو إنترنت", "wifi"),
    "restaurant": ("مطاعم", "utensils"),
    "cafe": ("كافيهات", "mug-hot"),
    "shop": ("محلات ومتاجر", "bag-shopping"),
    "hotel": ("فنادق ومنتجعات", "hotel"),
    "salon": ("صالونات وتجميل", "scissors"),
    "clinic": ("عيادات ومراكز طبية", "stethoscope"),
    "mall": ("مولات ومجمّعات", "store"),
    "school": ("مدارس وجامعات", "graduation-cap"),
}
VERTICAL_ORDER = tuple(VERTICALS.keys())


def _ad(key: str, **config) -> dict:
    """إدخال إضافة مفعّلة بإعداده."""
    return {"enabled": True, "config": dict(config)}


@dataclass(frozen=True)
class GalleryTemplate:
    key: str
    name_ar: str
    vertical: str
    desc_ar: str
    base_slug: str                       # قالب من LIBRARY
    icon: str = "wand-magic-sparkles"
    tags: tuple[str, ...] = ()
    variables: dict = field(default_factory=dict)   # تعديلات متغيّرات
    addons: dict = field(default_factory=dict)       # {key:{enabled,config}}


# ════════════════════════════════════════════════════════════════
# المعرض — تُضاف القوالب هنا فقط.
# ════════════════════════════════════════════════════════════════
GALLERY: list[GalleryTemplate] = [
    # ── شبكات / ISP ──
    GalleryTemplate(
        key="isp_pro", name_ar="مزوّد إنترنت احترافي", vertical="isp",
        desc_ar="واجهة احترافية مع إعلانات وترقية الباقات وروابط التواصل.",
        base_slug="gradient_pro", icon="wifi",
        tags=("احترافي", "باقات"),
        variables={"ACCENT_COLOR": "#2563EB",
                   "WELCOME_TEXT": "مرحبًا بك في شبكتنا — سجّل دخولك للمتابعة"},
        addons={
            "theme_branded": _ad("theme_branded"),
            "announcements": _ad("announcements", title="إعلانات الشبكة",
                                 body="سرعات جديدة متوفّرة الآن\nالدعم الفنّي ٢٤/٧"),
            "tier_upsell": _ad("tier_upsell", title="ارتقِ لباقة أسرع",
                               subtitle="سرعة أعلى وبلا حدود"),
            "returning_user": _ad("returning_user"),
        }),
    GalleryTemplate(
        key="isp_simple", name_ar="مزوّد إنترنت بسيط", vertical="isp",
        desc_ar="صفحة دخول نظيفة وسريعة مع تذكّر المستخدم وموافقة الشروط.",
        base_slug="card", icon="bolt",
        tags=("بسيط", "سريع"),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={
            "theme_minimal": _ad("theme_minimal"),
            "returning_user": _ad("returning_user"),
            "tos_consent": _ad("tos_consent", text="أوافق على سياسة الاستخدام"),
        }),

    # ── مطاعم ──
    GalleryTemplate(
        key="restaurant_qr", name_ar="مطعم — قائمة QR", vertical="restaurant",
        desc_ar="قائمة طعام بـQR + تواصل + تقييم جوجل + مواقيت الصلاة.",
        base_slug="card", icon="utensils",
        tags=("QR", "تقييم"),
        variables={"ACCENT_COLOR": "#d97706",
                   "WELCOME_TEXT": "أهلًا بك — تفضّل بتصفّح قائمتنا"},
        addons={
            "theme_gradient": _ad("theme_gradient"),
            "qr_menu": _ad("qr_menu", title="قائمة الطعام", url=""),
            "feedback_review": _ad("feedback_review"),
            "social_links": _ad("social_links"),
            "prayer_times": _ad("prayer_times"),
        }),
    GalleryTemplate(
        key="restaurant_ramadan", name_ar="مطعم — رمضان", vertical="restaurant",
        desc_ar="ثيم رمضاني مع مواقيت الصلاة والعروض وروابط التواصل.",
        base_slug="card", icon="moon",
        tags=("رمضان", "موسمي"),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={
            "theme_seasonal": _ad("theme_seasonal", season="ramadan"),
            "prayer_times": _ad("prayer_times"),
            "announcements": _ad("announcements", title="عروض رمضان",
                                 body="إفطار صائم بأسعار خاصة\nاحجز طاولتك مبكرًا"),
            "social_links": _ad("social_links"),
        }),

    # ── كافيهات ──
    GalleryTemplate(
        key="cafe_chill", name_ar="كافيه عصري", vertical="cafe",
        desc_ar="ثيم زجاجي مع راديو وبرنامج ولاء وروابط تواصل.",
        base_slug="card", icon="mug-hot",
        tags=("عصري", "ولاء"),
        variables={"ACCENT_COLOR": "#0d9488",
                   "WELCOME_TEXT": "استمتع بقهوتك مع إنترنت مجاني"},
        addons={
            "theme_glass": _ad("theme_glass"),
            "internet_radio": _ad("internet_radio", title="راديو الكافيه"),
            "loyalty": _ad("loyalty", message="اجمع نقاطك مع كل زيارة!"),
            "social_links": _ad("social_links"),
            "feedback_review": _ad("feedback_review"),
        }),

    # ── محلات ──
    GalleryTemplate(
        key="shop_promo", name_ar="متجر — عروض", vertical="shop",
        desc_ar="لافتة راعٍ وكوبون خصم وترقية — لزيادة المبيعات.",
        base_slug="card", icon="bag-shopping",
        tags=("عروض", "كوبون"),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={
            "theme_branded": _ad("theme_branded"),
            "sponsor_banner": _ad("sponsor_banner", label="عرض اليوم"),
            "coupons": _ad("coupons", desc="خصم ترحيبي", code="WELCOME10"),
            "tier_upsell": _ad("tier_upsell"),
        }),

    # ── فنادق ──
    GalleryTemplate(
        key="hotel_lux", name_ar="فندق فاخر", vertical="hotel",
        desc_ar="ثيم ليلي ملكي مع الطقس ومبدّل اللغة واستبيان الضيوف.",
        base_slug="royal_night", icon="hotel",
        tags=("فاخر", "متعدّد اللغات"),
        variables={"ACCENT_COLOR": "#7c3aed",
                   "WELCOME_TEXT": "أهلًا بك في فندقنا — نتمنى لك إقامة سعيدة"},
        addons={
            "theme_dark": _ad("theme_dark"),
            "weather": _ad("weather", city=""),
            "multilang": _ad("multilang"),
            "survey": _ad("survey", question="كيف تقيّم إقامتك؟"),
            "social_links": _ad("social_links"),
        }),

    # ── صالونات ──
    GalleryTemplate(
        key="salon_glam", name_ar="صالون أنيق", vertical="salon",
        desc_ar="معرض صور وروابط حجز وتقييم وبرنامج ولاء.",
        base_slug="card", icon="scissors",
        tags=("صور", "حجز"),
        variables={"ACCENT_COLOR": "#db2777"},
        addons={
            "theme_glass": _ad("theme_glass"),
            "image_carousel": _ad("image_carousel"),
            "feedback_review": _ad("feedback_review"),
            "loyalty": _ad("loyalty", message="عضوية الدلال: نقاط مع كل زيارة"),
            "social_links": _ad("social_links"),
        }),

    # ── عيادات ──
    GalleryTemplate(
        key="clinic_calm", name_ar="عيادة هادئة", vertical="clinic",
        desc_ar="مظهر بسيط مطمئن مع إعلانات ومواقيت الصلاة وموافقة.",
        base_slug="minimal", icon="stethoscope",
        tags=("بسيط", "طبي"),
        variables={"ACCENT_COLOR": "#0d9488",
                   "WELCOME_TEXT": "مرحبًا بك — نتمنى لك دوام الصحة"},
        addons={
            "theme_minimal": _ad("theme_minimal"),
            "announcements": _ad("announcements", title="تنبيهات العيادة",
                                 body="مواعيد العمل ٩ص–٩م\nاحجز موعدك مسبقًا"),
            "prayer_times": _ad("prayer_times"),
            "tos_consent": _ad("tos_consent", text="أوافق على سياسة الخصوصية"),
        }),

    # ── مولات ──
    GalleryTemplate(
        key="mall_buzz", name_ar="مول حيوي", vertical="mall",
        desc_ar="شريط أخبار ولافتة راعٍ وترقية وروابط تواصل.",
        base_slug="aurora_store", icon="store",
        tags=("إعلانات", "حيوي"),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={
            "theme_gradient": _ad("theme_gradient"),
            "news_ticker": _ad("news_ticker",
                               items="خصومات نهاية الأسبوع\nافتتاح متجر جديد"),
            "sponsor_banner": _ad("sponsor_banner", label="رعاة المول"),
            "tier_upsell": _ad("tier_upsell"),
            "social_links": _ad("social_links"),
        }),

    # ── مدارس / جامعات ──
    GalleryTemplate(
        key="school_edu", name_ar="مدرسة/جامعة", vertical="school",
        desc_ar="هوية تعليمية مع إعلانات وموافقة الشروط وجمع بيانات الطلبة.",
        base_slug="classic", icon="graduation-cap",
        tags=("تعليمي", "موافقة"),
        variables={"ACCENT_COLOR": "#2563EB",
                   "WELCOME_TEXT": "مرحبًا بك في شبكة الحرم — سجّل دخولك"},
        addons={
            "theme_branded": _ad("theme_branded"),
            "announcements": _ad("announcements", title="إعلانات",
                                 body="بدء التسجيل الأسبوع القادم\nمكتبة مفتوحة حتى ١٠م"),
            "tos_consent": _ad("tos_consent", text="أوافق على سياسة الاستخدام المقبول"),
            "data_collection": _ad("data_collection", ask_phone="no"),
        }),
]

GALLERY_BY_KEY = {t.key: t for t in GALLERY}


def by_vertical() -> dict[str, list[GalleryTemplate]]:
    """قوالب المعرض مجمّعة بنوع المنشأة بترتيب VERTICAL_ORDER."""
    out: dict[str, list[GalleryTemplate]] = {v: [] for v in VERTICAL_ORDER}
    for t in GALLERY:
        out.setdefault(t.vertical, []).append(t)
    return {v: out[v] for v in VERTICAL_ORDER if out.get(v)}


def get(key: str) -> GalleryTemplate | None:
    return GALLERY_BY_KEY.get(key)


def resolve(key: str, *, base_vars: dict | None = None):
    """يحوّل قالب معرض إلى (template_slug, variables, addons) جاهزة
    للحفظ/المعاينة. يندمج فوق متغيّرات المستخدم الحالية (base_vars)
    فيبقى الاسم/الشعار/الدعم، وتُطبَّق تعديلات القالب (اللون/الترحيب)."""
    t = GALLERY_BY_KEY.get(key)
    if not t:
        return None
    variables = dict(base_vars or {})
    variables.update(t.variables)
    return t.base_slug, variables, dict(t.addons)


__all__ = [
    "VERTICALS", "VERTICAL_ORDER", "GalleryTemplate", "GALLERY",
    "GALLERY_BY_KEY", "by_vertical", "get", "resolve",
]
