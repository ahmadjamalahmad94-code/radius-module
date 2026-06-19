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
    "gym": ("نوادٍ رياضية", "dumbbell"),
    "barber": ("حلاقة وعناية رجالية", "scissors"),
    "pharmacy": ("صيدليات", "prescription-bottle-medical"),
    "coworking": ("مساحات عمل مشتركة", "laptop"),
    "transport": ("مطارات ونقل", "plane"),
    "event": ("مناسبات وأعراس", "champagne-glasses"),
    "gas": ("محطات وقود", "gas-pump"),
    "mosque": ("مساجد ومراكز", "mosque"),
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

    # ════════════════════════════════════════════════════════════
    # توسعة المعرض — تشكيلات متنوّعة لكل نوع + أنواع جديدة
    # ════════════════════════════════════════════════════════════

    # ── شبكات / ISP (إضافات) ──
    GalleryTemplate(
        key="isp_gaming", name_ar="إنترنت للألعاب", vertical="isp",
        desc_ar="مظهر ليلي حماسي مع عجلة حظ وترقية وروابط تواصل.",
        base_slug="royal_night", icon="gamepad", tags=("ليلي", "ألعاب"),
        variables={"ACCENT_COLOR": "#7c3aed"},
        addons={"theme_dark": _ad("theme_dark"),
                "animated_svg": _ad("animated_svg", shape="wifi"),
                "spin_to_win": _ad("spin_to_win", prizes="سرعة مضاعفة ساعة\nخصم ترقية"),
                "tier_upsell": _ad("tier_upsell"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="isp_family", name_ar="إنترنت العائلة", vertical="isp",
        desc_ar="هوية هادئة مع إعلانات وموافقة استخدام وتذكّر المستخدم.",
        base_slug="card", icon="house", tags=("عائلي", "بسيط"),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={"theme_branded": _ad("theme_branded"),
                "announcements": _ad("announcements", title="إعلانات",
                                     body="رقابة أبوية متاحة\nالدعم ٢٤/٧"),
                "tos_consent": _ad("tos_consent"),
                "returning_user": _ad("returning_user")}),

    # ── مطاعم (إضافات) ──
    GalleryTemplate(
        key="restaurant_fine", name_ar="مطعم راقٍ", vertical="restaurant",
        desc_ar="مظهر ليلي فاخر مع الطقس وتقييم جوجل وروابط تواصل.",
        base_slug="royal_night", icon="wine-glass", tags=("راقٍ", "ليلي"),
        variables={"ACCENT_COLOR": "#b45309"},
        addons={"theme_dark": _ad("theme_dark"),
                "weather": _ad("weather"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="restaurant_fast", name_ar="وجبات سريعة", vertical="restaurant",
        desc_ar="ألوان نابضة مع كوبون ومؤقّت وصول وقائمة QR.",
        base_slug="swift_login", icon="burger", tags=("سريع", "كوبون"),
        variables={"ACCENT_COLOR": "#dc2626"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "coupons": _ad("coupons", desc="خصم الطلب الأول", code="FAST15"),
                "qr_menu": _ad("qr_menu", title="قائمة الطعام"),
                "countdown_access": _ad("countdown_access", seconds="5")}),

    # ── كافيهات (إضافات) ──
    GalleryTemplate(
        key="cafe_minimal", name_ar="كافيه بسيط", vertical="cafe",
        desc_ar="مظهر نظيف مع راديو وبرنامج ولاء.",
        base_slug="minimal", icon="mug-saucer", tags=("بسيط", "هادئ"),
        variables={"ACCENT_COLOR": "#92400e"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "internet_radio": _ad("internet_radio", title="موسيقى الكافيه"),
                "loyalty": _ad("loyalty")}),
    GalleryTemplate(
        key="cafe_morning", name_ar="كافيه الصباح", vertical="cafe",
        desc_ar="تدرّج دافئ مع ساعة سعيدة مجدولة وروابط تواصل.",
        base_slug="card", icon="sun", tags=("صباحي", "عروض"),
        variables={"ACCENT_COLOR": "#ea580c"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "scheduled_content": _ad("scheduled_content",
                                         message="ساعة سعيدة ٧–٩ص: قهوتك علينا",
                                         start_hour="7", end_hour="9"),
                "social_links": _ad("social_links"),
                "live_clock": _ad("live_clock")}),
    GalleryTemplate(
        key="cafe_artsy", name_ar="كافيه فنّي", vertical="cafe",
        desc_ar="زجاجي أنيق مع معرض صور ورسوم متحرّكة.",
        base_slug="fiber_glow", icon="palette", tags=("فنّي", "صور"),
        variables={"ACCENT_COLOR": "#0d9488"},
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel"),
                "animated_svg": _ad("animated_svg", shape="blob")}),

    # ── محلات (إضافات) ──
    GalleryTemplate(
        key="shop_lux", name_ar="متجر فاخر", vertical="shop",
        desc_ar="مظهر ليلي راقٍ مع معرض صور وكوبون وروابط تواصل.",
        base_slug="royal_night", icon="gem", tags=("فاخر", "صور"),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "coupons": _ad("coupons", code="VIP10"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="shop_sale", name_ar="متجر — تخفيضات", vertical="shop",
        desc_ar="ثيم موسمي مع لافتة راعٍ ومؤقّت عرض.",
        base_slug="aurora_store", icon="tags", tags=("تخفيضات", "موسمي"),
        variables={"ACCENT_COLOR": "#e11d48"},
        addons={"theme_seasonal": _ad("theme_seasonal", season="eid"),
                "sponsor_banner": _ad("sponsor_banner", label="عرض محدود"),
                "countdown_access": _ad("countdown_access", seconds="8",
                                        label="ينتهي العرض خلال")}),

    # ── فنادق (إضافات) ──
    GalleryTemplate(
        key="hotel_resort", name_ar="منتجع سياحي", vertical="hotel",
        desc_ar="تدرّج منعش مع الطقس ومعرض صور ومبدّل لغة.",
        base_slug="emerald", icon="umbrella-beach", tags=("منتجع", "متعدّد اللغات"),
        variables={"ACCENT_COLOR": "#0d9488"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "weather": _ad("weather"),
                "image_carousel": _ad("image_carousel"),
                "multilang": _ad("multilang")}),
    GalleryTemplate(
        key="hotel_business", name_ar="فندق أعمال", vertical="hotel",
        desc_ar="مظهر بسيط احترافي مع مبدّل لغة واستبيان وتواصل.",
        base_slug="minimal", icon="briefcase", tags=("أعمال", "بسيط"),
        variables={"ACCENT_COLOR": "#1e40af"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "multilang": _ad("multilang"),
                "survey": _ad("survey", question="كيف نخدمك أفضل؟"),
                "social_links": _ad("social_links")}),

    # ── صالونات (إضافات) ──
    GalleryTemplate(
        key="salon_spa", name_ar="سبا واسترخاء", vertical="salon",
        desc_ar="زجاجي ناعم مع معرض صور وولاء وساعة سعيدة.",
        base_slug="card", icon="spa", tags=("سبا", "ولاء"),
        variables={"ACCENT_COLOR": "#be185d"},
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel"),
                "loyalty": _ad("loyalty"),
                "scheduled_content": _ad("scheduled_content",
                                         message="عرض منتصف الأسبوع",
                                         start_hour="10", end_hour="14")}),
    GalleryTemplate(
        key="salon_modern", name_ar="صالون عصري", vertical="salon",
        desc_ar="تدرّج جريء مع تقييم جوجل وروابط حجز.",
        base_slug="fiber_glow", icon="wand-sparkles", tags=("عصري", "تقييم"),
        variables={"ACCENT_COLOR": "#db2777"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),

    # ── عيادات (إضافات) ──
    GalleryTemplate(
        key="clinic_modern", name_ar="مركز طبي حديث", vertical="clinic",
        desc_ar="هوية طبية مع مواقيت الصلاة وإعلانات وموافقة.",
        base_slug="card", icon="house-medical", tags=("حديث", "طبي"),
        variables={"ACCENT_COLOR": "#0891b2"},
        addons={"theme_branded": _ad("theme_branded"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title="تنبيهات",
                                     body="حملة فحص مجاني هذا الأسبوع"),
                "tos_consent": _ad("tos_consent")}),
    GalleryTemplate(
        key="clinic_dental", name_ar="عيادة أسنان", vertical="clinic",
        desc_ar="مظهر نظيف مع تقييم وإعلانات مواعيد.",
        base_slug="minimal", icon="tooth", tags=("أسنان", "بسيط"),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "feedback_review": _ad("feedback_review"),
                "announcements": _ad("announcements", title="مواعيد",
                                     body="احجز موعدك أونلاين")}),

    # ── مولات (إضافات) ──
    GalleryTemplate(
        key="mall_premium", name_ar="مول راقٍ", vertical="mall",
        desc_ar="ليلي فاخر مع معرض صور ولافتة رعاة وتواصل.",
        base_slug="royal_night", icon="bag-shopping", tags=("راقٍ", "رعاة"),
        variables={"ACCENT_COLOR": "#6d28d9"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "sponsor_banner": _ad("sponsor_banner", label="رعاة المول"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="mall_family", name_ar="مول عائلي", vertical="mall",
        desc_ar="تدرّج مرح مع عجلة حظ وكوبونات.",
        base_slug="aurora_store", icon="children", tags=("عائلي", "جوائز"),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "spin_to_win": _ad("spin_to_win", prizes="خصم ١٠٪\nهدية للأطفال"),
                "coupons": _ad("coupons", code="MALL5")}),

    # ── مدارس/جامعات (إضافات) ──
    GalleryTemplate(
        key="school_uni", name_ar="جامعة", vertical="school",
        desc_ar="هوية أكاديمية مع إعلانات ومبدّل لغة وجمع بيانات.",
        base_slug="card", icon="building-columns", tags=("جامعي", "متعدّد اللغات"),
        variables={"ACCENT_COLOR": "#1d4ed8"},
        addons={"theme_branded": _ad("theme_branded"),
                "announcements": _ad("announcements", title="إعلانات الحرم",
                                     body="بدء التسجيل\nمواعيد الامتحانات"),
                "multilang": _ad("multilang"),
                "data_collection": _ad("data_collection", ask_phone="no")}),
    GalleryTemplate(
        key="school_kids", name_ar="مدرسة أطفال", vertical="school",
        desc_ar="ألوان مبهجة مع رسوم متحرّكة وإعلانات.",
        base_slug="card", icon="child-reaching", tags=("أطفال", "مرح"),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "animated_svg": _ad("animated_svg", shape="blob"),
                "announcements": _ad("announcements", title="أخبار المدرسة",
                                     body="رحلة نهاية الأسبوع")}),

    # ── نوادٍ رياضية (جديد) ──
    GalleryTemplate(
        key="gym_power", name_ar="نادٍ رياضي قوي", vertical="gym",
        desc_ar="ليلي حماسي مع رسوم متحرّكة وتقييم وتواصل.",
        base_slug="royal_night", icon="dumbbell", tags=("قوّة", "ليلي"),
        variables={"ACCENT_COLOR": "#dc2626"},
        addons={"theme_dark": _ad("theme_dark"),
                "animated_svg": _ad("animated_svg", shape="wifi"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="gym_class", name_ar="نادٍ — حصص", vertical="gym",
        desc_ar="تدرّج نشيط مع جدول حصص مجدول وتواصل.",
        base_slug="swift_login", icon="person-running", tags=("حصص", "نشط"),
        variables={"ACCENT_COLOR": "#f97316"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "scheduled_content": _ad("scheduled_content",
                                         message="حصة اليوغا ٦م",
                                         start_hour="17", end_hour="19"),
                "social_links": _ad("social_links")}),

    # ── حلاقة (جديد) ──
    GalleryTemplate(
        key="barber_classic", name_ar="حلاقة كلاسيكية", vertical="barber",
        desc_ar="مظهر ليلي أنيق مع معرض صور وبرنامج ولاء.",
        base_slug="dark", icon="scissors", tags=("كلاسيك", "ولاء"),
        variables={"ACCENT_COLOR": "#a16207"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "loyalty": _ad("loyalty"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="barber_modern", name_ar="حلاقة عصرية", vertical="barber",
        desc_ar="زجاجي نظيف مع تقييم وحجز.",
        base_slug="card", icon="user-tie", tags=("عصري", "حجز"),
        variables={"ACCENT_COLOR": "#0f172a"},
        addons={"theme_glass": _ad("theme_glass"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),

    # ── صيدليات (جديد) ──
    GalleryTemplate(
        key="pharmacy_care", name_ar="صيدلية رعاية", vertical="pharmacy",
        desc_ar="هوية صحية هادئة مع مواقيت الصلاة وإعلانات وموافقة.",
        base_slug="minimal", icon="prescription-bottle-medical",
        tags=("رعاية", "بسيط"),
        variables={"ACCENT_COLOR": "#059669"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title="تنبيهات",
                                     body="توصيل مجاني للأدوية"),
                "tos_consent": _ad("tos_consent")}),
    GalleryTemplate(
        key="pharmacy_24", name_ar="صيدلية ٢٤ ساعة", vertical="pharmacy",
        desc_ar="هوية واضحة مع محتوى مجدول وإعلانات.",
        base_slug="card", icon="clock", tags=("٢٤ ساعة",),
        variables={"ACCENT_COLOR": "#0891b2"},
        addons={"theme_branded": _ad("theme_branded"),
                "scheduled_content": _ad("scheduled_content",
                                         message="مناوبة ليلية متاحة الآن",
                                         start_hour="22", end_hour="6"),
                "announcements": _ad("announcements", title="خدمات",
                                     body="استشارة صيدلانية مجانية")}),

    # ── مساحات عمل مشتركة (جديد) ──
    GalleryTemplate(
        key="cowork_pro", name_ar="مساحة عمل احترافية", vertical="coworking",
        desc_ar="مظهر بسيط مع تحليلات وقائمة QR وتواصل.",
        base_slug="minimal", icon="laptop", tags=("احترافي", "تحليلات"),
        variables={"ACCENT_COLOR": "#4f46e5"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "analytics": _ad("analytics", vertical="coworking"),
                "qr_menu": _ad("qr_menu", title="دليل المكان"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="cowork_creative", name_ar="مساحة إبداعية", vertical="coworking",
        desc_ar="تدرّج حيوي مع معرض صور ورسوم متحرّكة.",
        base_slug="fiber_glow", icon="lightbulb", tags=("إبداعي",),
        variables={"ACCENT_COLOR": "#7c3aed"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "image_carousel": _ad("image_carousel"),
                "animated_svg": _ad("animated_svg", shape="waves")}),

    # ── مطارات ونقل (جديد) ──
    GalleryTemplate(
        key="transport_air", name_ar="مطار", vertical="transport",
        desc_ar="مظهر ليلي مع الطقس ومبدّل لغة وتواصل — للمسافرين.",
        base_slug="royal_night", icon="plane", tags=("مطار", "متعدّد اللغات"),
        variables={"ACCENT_COLOR": "#1e40af"},
        addons={"theme_dark": _ad("theme_dark"),
                "weather": _ad("weather"),
                "multilang": _ad("multilang"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="transport_metro", name_ar="مترو/حافلات", vertical="transport",
        desc_ar="هوية واضحة مع شريط أخبار ومبدّل لغة.",
        base_slug="card", icon="train-subway", tags=("نقل عام",),
        variables={"ACCENT_COLOR": "#0d9488"},
        addons={"theme_branded": _ad("theme_branded"),
                "news_ticker": _ad("news_ticker",
                                   items="مواعيد محدّثة\nخطوط جديدة"),
                "multilang": _ad("multilang")}),

    # ── مناسبات وأعراس (جديد) ──
    GalleryTemplate(
        key="event_wedding", name_ar="عرس/مناسبة", vertical="event",
        desc_ar="زجاجي راقٍ مع معرض صور وعدّاد ومشاركة.",
        base_slug="card", icon="ring", tags=("عرس", "صور"),
        variables={"ACCENT_COLOR": "#be123c"},
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel"),
                "countdown_access": _ad("countdown_access", seconds="5",
                                        label="نبدأ خلال"),
                "referral": _ad("referral", message="شارك صور المناسبة")}),
    GalleryTemplate(
        key="event_party", name_ar="حفلة", vertical="event",
        desc_ar="ألوان احتفالية مع عجلة حظ ومشاركة.",
        base_slug="fiber_glow", icon="champagne-glasses", tags=("حفلة", "جوائز"),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "spin_to_win": _ad("spin_to_win", prizes="هدية\nصورة فورية"),
                "social_links": _ad("social_links")}),

    # ── محطات وقود (جديد) ──
    GalleryTemplate(
        key="gas_quick", name_ar="محطة — خدمة سريعة", vertical="gas",
        desc_ar="هوية واضحة مع كوبون وقود وقائمة QR للمتجر.",
        base_slug="card", icon="gas-pump", tags=("سريع", "كوبون"),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={"theme_branded": _ad("theme_branded"),
                "coupons": _ad("coupons", desc="خصم غسيل السيارة", code="WASH5"),
                "qr_menu": _ad("qr_menu", title="متجر المحطة")}),
    GalleryTemplate(
        key="gas_loyalty", name_ar="محطة — ولاء", vertical="gas",
        desc_ar="تدرّج مع برنامج ولاء ولافتة راعٍ.",
        base_slug="swift_login", icon="oil-can", tags=("ولاء",),
        variables={"ACCENT_COLOR": "#ca8a04"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "loyalty": _ad("loyalty", message="نقاط مع كل تعبئة"),
                "sponsor_banner": _ad("sponsor_banner", label="شركاؤنا")}),

    # ── مساجد ومراكز (جديد) ──
    GalleryTemplate(
        key="mosque_serene", name_ar="مسجد — مواقيت", vertical="mosque",
        desc_ar="هوية هادئة مع مواقيت الصلاة والتاريخ الهجري وإعلانات.",
        base_slug="emerald", icon="mosque", tags=("مواقيت", "هادئ"),
        variables={"ACCENT_COLOR": "#047857"},
        addons={"theme_branded": _ad("theme_branded"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title="إعلانات المسجد",
                                     body="درس بعد العشاء\nحلقة تحفيظ")}),
    GalleryTemplate(
        key="mosque_ramadan", name_ar="مسجد — رمضان", vertical="mosque",
        desc_ar="ثيم رمضاني مع مواقيت الصلاة وإعلانات الأنشطة.",
        base_slug="card", icon="star-and-crescent", tags=("رمضان", "موسمي"),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={"theme_seasonal": _ad("theme_seasonal", season="ramadan"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title="برنامج رمضان",
                                     body="تراويح ٩م\nإفطار صائم يوميًّا")}),
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
