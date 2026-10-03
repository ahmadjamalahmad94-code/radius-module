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
from app.i18n_text import N_

from dataclasses import dataclass, field

# أنواع المنشآت (vertical) — المفتاح، التسمية، الأيقونة.
VERTICALS: dict[str, tuple[str, str]] = {
    "isp": ("شبكات ومزوّدو إنترنت", "wifi"),
    "restaurant": ("مطاعم", "utensils"),
    "cafe": (N_("كافيهات"), "mug-hot"),
    "shop": (N_("محلات ومتاجر"), "bag-shopping"),
    "hotel": ("فنادق ومنتجعات", "hotel"),
    "salon": ("صالونات وتجميل", "scissors"),
    "clinic": ("عيادات ومراكز طبية", "stethoscope"),
    "mall": ("مولات ومجمّعات", "store"),
    "school": (N_("مدارس وجامعات"), "graduation-cap"),
    "gym": ("نوادٍ رياضية", "dumbbell"),
    "barber": ("حلاقة وعناية رجالية", "scissors"),
    "pharmacy": (N_("صيدليات"), "prescription-bottle-medical"),
    "coworking": ("مساحات عمل مشتركة", "laptop"),
    "transport": ("مطارات ونقل", "plane"),
    "event": (N_("مناسبات وأعراس"), "champagne-glasses"),
    "gas": (N_("محطات وقود"), "gas-pump"),
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
        key="isp_pro", name_ar=N_("مزوّد إنترنت احترافي"), vertical="isp",
        desc_ar=N_("واجهة احترافية مع إعلانات وترقية الباقات وروابط التواصل."),
        base_slug="gradient_pro", icon="wifi",
        tags=(N_("احترافي"), N_("باقات")),
        variables={"ACCENT_COLOR": "#2563EB",
                   "WELCOME_TEXT": N_("مرحبًا بك في شبكتنا — سجّل دخولك للمتابعة")},
        addons={
            "theme_branded": _ad("theme_branded"),
            "announcements": _ad("announcements", title=N_("إعلانات الشبكة"),
                                 body=N_("سرعات جديدة متوفّرة الآن\nالدعم الفنّي ٢٤/٧")),
            "tier_upsell": _ad("tier_upsell", title=N_("ارتقِ لباقة أسرع"),
                               subtitle=N_("سرعة أعلى وبلا حدود")),
            "returning_user": _ad("returning_user"),
        }),
    GalleryTemplate(
        key="isp_simple", name_ar=N_("مزوّد إنترنت بسيط"), vertical="isp",
        desc_ar=N_("صفحة دخول نظيفة وسريعة مع تذكّر المستخدم وموافقة الشروط."),
        base_slug="card", icon="bolt",
        tags=(N_("بسيط"), N_("سريع")),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={
            "theme_minimal": _ad("theme_minimal"),
            "returning_user": _ad("returning_user"),
            "tos_consent": _ad("tos_consent", text=N_("أوافق على سياسة الاستخدام")),
        }),

    # ── مطاعم ──
    GalleryTemplate(
        key="restaurant_qr", name_ar=N_("مطعم — قائمة QR"), vertical="restaurant",
        desc_ar=N_("قائمة طعام بـQR + تواصل + تقييم جوجل + مواقيت الصلاة."),
        base_slug="card", icon="utensils",
        tags=("QR", N_("تقييم")),
        variables={"ACCENT_COLOR": "#d97706",
                   "WELCOME_TEXT": N_("أهلًا بك — تفضّل بتصفّح قائمتنا")},
        addons={
            "theme_gradient": _ad("theme_gradient"),
            "qr_menu": _ad("qr_menu", title=N_("قائمة الطعام"), url=""),
            "feedback_review": _ad("feedback_review"),
            "social_links": _ad("social_links"),
            "prayer_times": _ad("prayer_times"),
        }),
    GalleryTemplate(
        key="restaurant_ramadan", name_ar=N_("مطعم — رمضان"), vertical="restaurant",
        desc_ar=N_("ثيم رمضاني مع مواقيت الصلاة والعروض وروابط التواصل."),
        base_slug="card", icon="moon",
        tags=(N_("رمضان"), N_("موسمي")),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={
            "theme_seasonal": _ad("theme_seasonal", season="ramadan"),
            "prayer_times": _ad("prayer_times"),
            "announcements": _ad("announcements", title=N_("عروض رمضان"),
                                 body=N_("إفطار صائم بأسعار خاصة\nاحجز طاولتك مبكرًا")),
            "social_links": _ad("social_links"),
        }),

    # ── كافيهات ──
    GalleryTemplate(
        key="cafe_chill", name_ar=N_("كافيه عصري"), vertical="cafe",
        desc_ar=N_("ثيم زجاجي مع راديو وبرنامج ولاء وروابط تواصل."),
        base_slug="card", icon="mug-hot",
        tags=(N_("عصري"), N_("ولاء")),
        variables={"ACCENT_COLOR": "#0d9488",
                   "WELCOME_TEXT": N_("استمتع بقهوتك مع إنترنت مجاني")},
        addons={
            "theme_glass": _ad("theme_glass"),
            "internet_radio": _ad("internet_radio", title=N_("راديو الكافيه")),
            "loyalty": _ad("loyalty", message=N_("اجمع نقاطك مع كل زيارة!")),
            "social_links": _ad("social_links"),
            "feedback_review": _ad("feedback_review"),
        }),

    # ── محلات ──
    GalleryTemplate(
        key="shop_promo", name_ar=N_("متجر — عروض"), vertical="shop",
        desc_ar=N_("لافتة راعٍ وكوبون خصم وترقية — لزيادة المبيعات."),
        base_slug="card", icon="bag-shopping",
        tags=(N_("عروض"), N_("كوبون")),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={
            "theme_branded": _ad("theme_branded"),
            "sponsor_banner": _ad("sponsor_banner", label=N_("عرض اليوم")),
            "coupons": _ad("coupons", desc=N_("خصم ترحيبي"), code="WELCOME10"),
            "tier_upsell": _ad("tier_upsell"),
        }),

    # ── فنادق ──
    GalleryTemplate(
        key="hotel_lux", name_ar=N_("فندق فاخر"), vertical="hotel",
        desc_ar=N_("ثيم ليلي ملكي مع الطقس ومبدّل اللغة واستبيان الضيوف."),
        base_slug="royal_night", icon="hotel",
        tags=(N_("فاخر"), N_("متعدّد اللغات")),
        variables={"ACCENT_COLOR": "#7c3aed",
                   "WELCOME_TEXT": N_("أهلًا بك في فندقنا — نتمنى لك إقامة سعيدة")},
        addons={
            "theme_dark": _ad("theme_dark"),
            "weather": _ad("weather", city=""),
            "multilang": _ad("multilang"),
            "survey": _ad("survey", question=N_("كيف تقيّم إقامتك؟")),
            "social_links": _ad("social_links"),
        }),

    # ── صالونات ──
    GalleryTemplate(
        key="salon_glam", name_ar=N_("صالون أنيق"), vertical="salon",
        desc_ar=N_("معرض صور وروابط حجز وتقييم وبرنامج ولاء."),
        base_slug="card", icon="scissors",
        tags=(N_("صور"), N_("حجز")),
        variables={"ACCENT_COLOR": "#db2777"},
        addons={
            "theme_glass": _ad("theme_glass"),
            "image_carousel": _ad("image_carousel"),
            "feedback_review": _ad("feedback_review"),
            "loyalty": _ad("loyalty", message=N_("عضوية الدلال: نقاط مع كل زيارة")),
            "social_links": _ad("social_links"),
        }),

    # ── عيادات ──
    GalleryTemplate(
        key="clinic_calm", name_ar=N_("عيادة هادئة"), vertical="clinic",
        desc_ar=N_("مظهر بسيط مطمئن مع إعلانات ومواقيت الصلاة وموافقة."),
        base_slug="minimal", icon="stethoscope",
        tags=(N_("بسيط"), N_("طبي")),
        variables={"ACCENT_COLOR": "#0d9488",
                   "WELCOME_TEXT": N_("مرحبًا بك — نتمنى لك دوام الصحة")},
        addons={
            "theme_minimal": _ad("theme_minimal"),
            "announcements": _ad("announcements", title=N_("تنبيهات العيادة"),
                                 body=N_("مواعيد العمل ٩ص–٩م\nاحجز موعدك مسبقًا")),
            "prayer_times": _ad("prayer_times"),
            "tos_consent": _ad("tos_consent", text=N_("أوافق على سياسة الخصوصية")),
        }),

    # ── مولات ──
    GalleryTemplate(
        key="mall_buzz", name_ar=N_("مول حيوي"), vertical="mall",
        desc_ar=N_("شريط أخبار ولافتة راعٍ وترقية وروابط تواصل."),
        base_slug="aurora_store", icon="store",
        tags=(N_("إعلانات"), N_("حيوي")),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={
            "theme_gradient": _ad("theme_gradient"),
            "news_ticker": _ad("news_ticker",
                               items=N_("خصومات نهاية الأسبوع\nافتتاح متجر جديد")),
            "sponsor_banner": _ad("sponsor_banner", label=N_("رعاة المول")),
            "tier_upsell": _ad("tier_upsell"),
            "social_links": _ad("social_links"),
        }),

    # ── مدارس / جامعات ──
    GalleryTemplate(
        key="school_edu", name_ar=N_("مدرسة/جامعة"), vertical="school",
        desc_ar=N_("هوية تعليمية مع إعلانات وموافقة الشروط وجمع بيانات الطلبة."),
        base_slug="classic", icon="graduation-cap",
        tags=(N_("تعليمي"), N_("موافقة")),
        variables={"ACCENT_COLOR": "#2563EB",
                   "WELCOME_TEXT": N_("مرحبًا بك في شبكة الحرم — سجّل دخولك")},
        addons={
            "theme_branded": _ad("theme_branded"),
            "announcements": _ad("announcements", title=N_("إعلانات"),
                                 body=N_("بدء التسجيل الأسبوع القادم\nمكتبة مفتوحة حتى ١٠م")),
            "tos_consent": _ad("tos_consent", text=N_("أوافق على سياسة الاستخدام المقبول")),
            "data_collection": _ad("data_collection", ask_phone="no"),
        }),

    # ════════════════════════════════════════════════════════════
    # توسعة المعرض — تشكيلات متنوّعة لكل نوع + أنواع جديدة
    # ════════════════════════════════════════════════════════════

    # ── شبكات / ISP (إضافات) ──
    GalleryTemplate(
        key="isp_gaming", name_ar=N_("إنترنت للألعاب"), vertical="isp",
        desc_ar=N_("مظهر ليلي حماسي مع عجلة حظ وترقية وروابط تواصل."),
        base_slug="royal_night", icon="gamepad", tags=(N_("ليلي"), N_("ألعاب")),
        variables={"ACCENT_COLOR": "#7c3aed"},
        # ملاحظة WYSIWYG: «royal_night» جلدٌ فاخر مُصمَّم خلفيّتُه تُعرّف
        # المظهر؛ أُزيلت رسوم animated_svg المفروضة افتراضيًّا كي تُطابِق
        # بطاقةُ المعرض الناتجَ المُطبَّق (لا موجات/زخرفة مفاجئة). يُمكن
        # للعميل إضافتها يدويًّا بعد التطبيق.
        addons={"theme_dark": _ad("theme_dark"),
                "spin_to_win": _ad("spin_to_win", prizes=N_("سرعة مضاعفة ساعة\nخصم ترقية")),
                "tier_upsell": _ad("tier_upsell"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="isp_family", name_ar=N_("إنترنت العائلة"), vertical="isp",
        desc_ar=N_("هوية هادئة مع إعلانات وموافقة استخدام وتذكّر المستخدم."),
        base_slug="card", icon="house", tags=(N_("عائلي"), N_("بسيط")),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={"theme_branded": _ad("theme_branded"),
                "announcements": _ad("announcements", title=N_("إعلانات"),
                                     body=N_("رقابة أبوية متاحة\nالدعم ٢٤/٧")),
                "tos_consent": _ad("tos_consent"),
                "returning_user": _ad("returning_user")}),

    # ── مطاعم (إضافات) ──
    GalleryTemplate(
        key="restaurant_fine", name_ar=N_("مطعم راقٍ"), vertical="restaurant",
        desc_ar=N_("مظهر ليلي فاخر مع الطقس وتقييم جوجل وروابط تواصل."),
        base_slug="royal_night", icon="wine-glass", tags=(N_("راقٍ"), N_("ليلي")),
        variables={"ACCENT_COLOR": "#b45309"},
        addons={"theme_dark": _ad("theme_dark"),
                "weather": _ad("weather"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="restaurant_fast", name_ar=N_("وجبات سريعة"), vertical="restaurant",
        desc_ar=N_("ألوان نابضة مع كوبون ومؤقّت وصول وقائمة QR."),
        base_slug="swift_login", icon="burger", tags=(N_("سريع"), N_("كوبون")),
        variables={"ACCENT_COLOR": "#dc2626"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "coupons": _ad("coupons", desc=N_("خصم الطلب الأول"), code="FAST15"),
                "qr_menu": _ad("qr_menu", title=N_("قائمة الطعام")),
                "countdown_access": _ad("countdown_access", seconds="5")}),

    # ── كافيهات (إضافات) ──
    GalleryTemplate(
        key="cafe_minimal", name_ar=N_("كافيه بسيط"), vertical="cafe",
        desc_ar=N_("مظهر نظيف مع راديو وبرنامج ولاء."),
        base_slug="minimal", icon="mug-saucer", tags=(N_("بسيط"), N_("هادئ")),
        variables={"ACCENT_COLOR": "#92400e"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "internet_radio": _ad("internet_radio", title=N_("موسيقى الكافيه")),
                "loyalty": _ad("loyalty")}),
    GalleryTemplate(
        key="cafe_morning", name_ar=N_("كافيه الصباح"), vertical="cafe",
        desc_ar=N_("تدرّج دافئ مع ساعة سعيدة مجدولة وروابط تواصل."),
        base_slug="card", icon="sun", tags=(N_("صباحي"), N_("عروض")),
        variables={"ACCENT_COLOR": "#ea580c"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "scheduled_content": _ad("scheduled_content",
                                         message=N_("ساعة سعيدة ٧–٩ص: قهوتك علينا"),
                                         start_hour="7", end_hour="9"),
                "social_links": _ad("social_links"),
                "live_clock": _ad("live_clock")}),
    GalleryTemplate(
        key="cafe_artsy", name_ar=N_("كافيه فنّي"), vertical="cafe",
        desc_ar=N_("زجاجي أنيق مع معرض صور."),
        base_slug="fiber_glow", icon="palette", tags=(N_("فنّي"), N_("صور")),
        variables={"ACCENT_COLOR": "#0d9488"},
        # WYSIWYG: «fiber_glow» جلدٌ متوهّج خلفيّتُه تُعرّف المظهر؛ أُزيلت
        # رسوم animated_svg المفروضة كي تُطابِق البطاقةُ الناتجَ المُطبَّق.
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel")}),

    # ── محلات (إضافات) ──
    GalleryTemplate(
        key="shop_lux", name_ar=N_("متجر فاخر"), vertical="shop",
        desc_ar=N_("مظهر ليلي راقٍ مع معرض صور وكوبون وروابط تواصل."),
        base_slug="royal_night", icon="gem", tags=(N_("فاخر"), N_("صور")),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "coupons": _ad("coupons", code="VIP10"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="shop_sale", name_ar=N_("متجر — تخفيضات"), vertical="shop",
        desc_ar=N_("ثيم موسمي مع لافتة راعٍ ومؤقّت عرض."),
        base_slug="aurora_store", icon="tags", tags=(N_("تخفيضات"), N_("موسمي")),
        variables={"ACCENT_COLOR": "#e11d48"},
        addons={"theme_seasonal": _ad("theme_seasonal", season="eid"),
                "sponsor_banner": _ad("sponsor_banner", label=N_("عرض محدود")),
                "countdown_access": _ad("countdown_access", seconds="8",
                                        label=N_("ينتهي العرض خلال"))}),

    # ── فنادق (إضافات) ──
    GalleryTemplate(
        key="hotel_resort", name_ar=N_("منتجع سياحي"), vertical="hotel",
        desc_ar=N_("تدرّج منعش مع الطقس ومعرض صور ومبدّل لغة."),
        base_slug="emerald", icon="umbrella-beach", tags=(N_("منتجع"), N_("متعدّد اللغات")),
        variables={"ACCENT_COLOR": "#0d9488"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "weather": _ad("weather"),
                "image_carousel": _ad("image_carousel"),
                "multilang": _ad("multilang")}),
    GalleryTemplate(
        key="hotel_business", name_ar=N_("فندق أعمال"), vertical="hotel",
        desc_ar=N_("مظهر بسيط احترافي مع مبدّل لغة واستبيان وتواصل."),
        base_slug="minimal", icon="briefcase", tags=(N_("أعمال"), N_("بسيط")),
        variables={"ACCENT_COLOR": "#1e40af"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "multilang": _ad("multilang"),
                "survey": _ad("survey", question=N_("كيف نخدمك أفضل؟")),
                "social_links": _ad("social_links")}),

    # ── صالونات (إضافات) ──
    GalleryTemplate(
        key="salon_spa", name_ar=N_("سبا واسترخاء"), vertical="salon",
        desc_ar=N_("زجاجي ناعم مع معرض صور وولاء وساعة سعيدة."),
        base_slug="card", icon="spa", tags=(N_("سبا"), N_("ولاء")),
        variables={"ACCENT_COLOR": "#be185d"},
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel"),
                "loyalty": _ad("loyalty"),
                "scheduled_content": _ad("scheduled_content",
                                         message=N_("عرض منتصف الأسبوع"),
                                         start_hour="10", end_hour="14")}),
    GalleryTemplate(
        key="salon_modern", name_ar=N_("صالون عصري"), vertical="salon",
        desc_ar=N_("تدرّج جريء مع تقييم جوجل وروابط حجز."),
        base_slug="fiber_glow", icon="wand-sparkles", tags=(N_("عصري"), N_("تقييم")),
        variables={"ACCENT_COLOR": "#db2777"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),

    # ── عيادات (إضافات) ──
    GalleryTemplate(
        key="clinic_modern", name_ar=N_("مركز طبي حديث"), vertical="clinic",
        desc_ar=N_("هوية طبية مع مواقيت الصلاة وإعلانات وموافقة."),
        base_slug="card", icon="house-medical", tags=(N_("حديث"), N_("طبي")),
        variables={"ACCENT_COLOR": "#0891b2"},
        addons={"theme_branded": _ad("theme_branded"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title=N_("تنبيهات"),
                                     body=N_("حملة فحص مجاني هذا الأسبوع")),
                "tos_consent": _ad("tos_consent")}),
    GalleryTemplate(
        key="clinic_dental", name_ar=N_("عيادة أسنان"), vertical="clinic",
        desc_ar=N_("مظهر نظيف مع تقييم وإعلانات مواعيد."),
        base_slug="minimal", icon="tooth", tags=(N_("أسنان"), N_("بسيط")),
        variables={"ACCENT_COLOR": "#0ea5e9"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "feedback_review": _ad("feedback_review"),
                "announcements": _ad("announcements", title=N_("مواعيد"),
                                     body=N_("احجز موعدك أونلاين"))}),

    # ── مولات (إضافات) ──
    GalleryTemplate(
        key="mall_premium", name_ar=N_("مول راقٍ"), vertical="mall",
        desc_ar=N_("ليلي فاخر مع معرض صور ولافتة رعاة وتواصل."),
        base_slug="royal_night", icon="bag-shopping", tags=(N_("راقٍ"), N_("رعاة")),
        variables={"ACCENT_COLOR": "#6d28d9"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "sponsor_banner": _ad("sponsor_banner", label=N_("رعاة المول")),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="mall_family", name_ar=N_("مول عائلي"), vertical="mall",
        desc_ar=N_("تدرّج مرح مع عجلة حظ وكوبونات."),
        base_slug="aurora_store", icon="children", tags=(N_("عائلي"), N_("جوائز")),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "spin_to_win": _ad("spin_to_win", prizes=N_("خصم ١٠٪\nهدية للأطفال")),
                "coupons": _ad("coupons", code="MALL5")}),

    # ── مدارس/جامعات (إضافات) ──
    GalleryTemplate(
        key="school_uni", name_ar=N_("جامعة"), vertical="school",
        desc_ar=N_("هوية أكاديمية مع إعلانات ومبدّل لغة وجمع بيانات."),
        base_slug="card", icon="building-columns", tags=(N_("جامعي"), N_("متعدّد اللغات")),
        variables={"ACCENT_COLOR": "#1d4ed8"},
        addons={"theme_branded": _ad("theme_branded"),
                "announcements": _ad("announcements", title=N_("إعلانات الحرم"),
                                     body=N_("بدء التسجيل\nمواعيد الامتحانات")),
                "multilang": _ad("multilang"),
                "data_collection": _ad("data_collection", ask_phone="no")}),
    GalleryTemplate(
        key="school_kids", name_ar=N_("مدرسة أطفال"), vertical="school",
        desc_ar=N_("ألوان مبهجة مع رسوم متحرّكة وإعلانات."),
        base_slug="card", icon="child-reaching", tags=(N_("أطفال"), N_("مرح")),
        variables={"ACCENT_COLOR": "#16a34a"},
        # استثناء مقصود: قالب «أطفال» مبنيّ على الجلد العامّ «card» (لا جلد
        # فاخر بخلفيّة خاصّة)، والكتلة العضويّة المتحرّكة جزءٌ أصيل من هويّته
        # المرحة — فتُبقى. (راجع قاعدة WYSIWYG: نُزيل animated_svg فقط حين
        # يَتعارض مع جلدٍ فاخر يُعرّف خلفيّتَه بنفسه.)
        addons={"theme_gradient": _ad("theme_gradient"),
                "animated_svg": _ad("animated_svg", shape="blob"),
                "announcements": _ad("announcements", title=N_("أخبار المدرسة"),
                                     body=N_("رحلة نهاية الأسبوع"))}),

    # ── نوادٍ رياضية (جديد) ──
    GalleryTemplate(
        key="gym_power", name_ar=N_("نادٍ رياضي قوي"), vertical="gym",
        desc_ar=N_("ليلي حماسي مع تقييم وتواصل."),
        base_slug="royal_night", icon="dumbbell", tags=(N_("قوّة"), N_("ليلي")),
        variables={"ACCENT_COLOR": "#dc2626"},
        # WYSIWYG: «royal_night» جلدٌ فاخر؛ أُزيلت رسوم animated_svg المفروضة
        # كي تُطابِق بطاقةُ المعرض الناتجَ المُطبَّق (لا زخرفة مفاجئة).
        addons={"theme_dark": _ad("theme_dark"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="gym_class", name_ar=N_("نادٍ — حصص"), vertical="gym",
        desc_ar=N_("تدرّج نشيط مع جدول حصص مجدول وتواصل."),
        base_slug="swift_login", icon="person-running", tags=(N_("حصص"), N_("نشط")),
        variables={"ACCENT_COLOR": "#f97316"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "scheduled_content": _ad("scheduled_content",
                                         message=N_("حصة اليوغا ٦م"),
                                         start_hour="17", end_hour="19"),
                "social_links": _ad("social_links")}),

    # ── حلاقة (جديد) ──
    GalleryTemplate(
        key="barber_classic", name_ar=N_("حلاقة كلاسيكية"), vertical="barber",
        desc_ar=N_("مظهر ليلي أنيق مع معرض صور وبرنامج ولاء."),
        base_slug="dark", icon="scissors", tags=(N_("كلاسيك"), N_("ولاء")),
        variables={"ACCENT_COLOR": "#a16207"},
        addons={"theme_dark": _ad("theme_dark"),
                "image_carousel": _ad("image_carousel"),
                "loyalty": _ad("loyalty"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="barber_modern", name_ar=N_("حلاقة عصرية"), vertical="barber",
        desc_ar=N_("زجاجي نظيف مع تقييم وحجز."),
        base_slug="card", icon="user-tie", tags=(N_("عصري"), N_("حجز")),
        variables={"ACCENT_COLOR": "#0f172a"},
        addons={"theme_glass": _ad("theme_glass"),
                "feedback_review": _ad("feedback_review"),
                "social_links": _ad("social_links")}),

    # ── صيدليات (جديد) ──
    GalleryTemplate(
        key="pharmacy_care", name_ar=N_("صيدلية رعاية"), vertical="pharmacy",
        desc_ar=N_("هوية صحية هادئة مع مواقيت الصلاة وإعلانات وموافقة."),
        base_slug="minimal", icon="prescription-bottle-medical",
        tags=(N_("رعاية"), N_("بسيط")),
        variables={"ACCENT_COLOR": "#059669"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title=N_("تنبيهات"),
                                     body=N_("توصيل مجاني للأدوية")),
                "tos_consent": _ad("tos_consent")}),
    GalleryTemplate(
        key="pharmacy_24", name_ar=N_("صيدلية ٢٤ ساعة"), vertical="pharmacy",
        desc_ar=N_("هوية واضحة مع محتوى مجدول وإعلانات."),
        base_slug="card", icon="clock", tags=(N_("٢٤ ساعة"),),
        variables={"ACCENT_COLOR": "#0891b2"},
        addons={"theme_branded": _ad("theme_branded"),
                "scheduled_content": _ad("scheduled_content",
                                         message=N_("مناوبة ليلية متاحة الآن"),
                                         start_hour="22", end_hour="6"),
                "announcements": _ad("announcements", title=N_("خدمات"),
                                     body=N_("استشارة صيدلانية مجانية"))}),

    # ── مساحات عمل مشتركة (جديد) ──
    GalleryTemplate(
        key="cowork_pro", name_ar=N_("مساحة عمل احترافية"), vertical="coworking",
        desc_ar=N_("مظهر بسيط مع تحليلات وقائمة QR وتواصل."),
        base_slug="minimal", icon="laptop", tags=(N_("احترافي"), N_("تحليلات")),
        variables={"ACCENT_COLOR": "#4f46e5"},
        addons={"theme_minimal": _ad("theme_minimal"),
                "analytics": _ad("analytics", vertical="coworking"),
                "qr_menu": _ad("qr_menu", title=N_("دليل المكان")),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="cowork_creative", name_ar=N_("مساحة إبداعية"), vertical="coworking",
        desc_ar=N_("تدرّج حيوي مع معرض صور."),
        base_slug="fiber_glow", icon="lightbulb", tags=(N_("إبداعي"),),
        variables={"ACCENT_COLOR": "#7c3aed"},
        # WYSIWYG: كان هذا القالب يَفرض «موجات» animated_svg فوق جلد
        # «fiber_glow» المتوهّج — وهي الموجاتُ المفاجئة التي يَراها المالك
        # على الصفحة المُطبَّقة دون أن تَظهر في بطاقة المعرض. أُزيلت كي
        # تُطابِق البطاقةُ الناتجَ المُطبَّق؛ يُبقي الجلدُ مظهرَه الخاصّ.
        addons={"theme_gradient": _ad("theme_gradient"),
                "image_carousel": _ad("image_carousel")}),

    # ── مطارات ونقل (جديد) ──
    GalleryTemplate(
        key="transport_air", name_ar=N_("مطار"), vertical="transport",
        desc_ar=N_("مظهر ليلي مع الطقس ومبدّل لغة وتواصل — للمسافرين."),
        base_slug="royal_night", icon="plane", tags=(N_("مطار"), N_("متعدّد اللغات")),
        variables={"ACCENT_COLOR": "#1e40af"},
        addons={"theme_dark": _ad("theme_dark"),
                "weather": _ad("weather"),
                "multilang": _ad("multilang"),
                "social_links": _ad("social_links")}),
    GalleryTemplate(
        key="transport_metro", name_ar=N_("مترو/حافلات"), vertical="transport",
        desc_ar=N_("هوية واضحة مع شريط أخبار ومبدّل لغة."),
        base_slug="card", icon="train-subway", tags=(N_("نقل عام"),),
        variables={"ACCENT_COLOR": "#0d9488"},
        addons={"theme_branded": _ad("theme_branded"),
                "news_ticker": _ad("news_ticker",
                                   items=N_("مواعيد محدّثة\nخطوط جديدة")),
                "multilang": _ad("multilang")}),

    # ── مناسبات وأعراس (جديد) ──
    GalleryTemplate(
        key="event_wedding", name_ar=N_("عرس/مناسبة"), vertical="event",
        desc_ar=N_("زجاجي راقٍ مع معرض صور وعدّاد ومشاركة."),
        base_slug="card", icon="ring", tags=(N_("عرس"), N_("صور")),
        variables={"ACCENT_COLOR": "#be123c"},
        addons={"theme_glass": _ad("theme_glass"),
                "image_carousel": _ad("image_carousel"),
                "countdown_access": _ad("countdown_access", seconds="5",
                                        label=N_("نبدأ خلال")),
                "referral": _ad("referral", message=N_("شارك صور المناسبة"))}),
    GalleryTemplate(
        key="event_party", name_ar=N_("حفلة"), vertical="event",
        desc_ar=N_("ألوان احتفالية مع عجلة حظ ومشاركة."),
        base_slug="fiber_glow", icon="champagne-glasses", tags=(N_("حفلة"), N_("جوائز")),
        variables={"ACCENT_COLOR": "#9333ea"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "spin_to_win": _ad("spin_to_win", prizes=N_("هدية\nصورة فورية")),
                "social_links": _ad("social_links")}),

    # ── محطات وقود (جديد) ──
    GalleryTemplate(
        key="gas_quick", name_ar=N_("محطة — خدمة سريعة"), vertical="gas",
        desc_ar=N_("هوية واضحة مع كوبون وقود وقائمة QR للمتجر."),
        base_slug="card", icon="gas-pump", tags=(N_("سريع"), N_("كوبون")),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={"theme_branded": _ad("theme_branded"),
                "coupons": _ad("coupons", desc=N_("خصم غسيل السيارة"), code="WASH5"),
                "qr_menu": _ad("qr_menu", title=N_("متجر المحطة"))}),
    GalleryTemplate(
        key="gas_loyalty", name_ar=N_("محطة — ولاء"), vertical="gas",
        desc_ar=N_("تدرّج مع برنامج ولاء ولافتة راعٍ."),
        base_slug="swift_login", icon="oil-can", tags=(N_("ولاء"),),
        variables={"ACCENT_COLOR": "#ca8a04"},
        addons={"theme_gradient": _ad("theme_gradient"),
                "loyalty": _ad("loyalty", message=N_("نقاط مع كل تعبئة")),
                "sponsor_banner": _ad("sponsor_banner", label=N_("شركاؤنا"))}),

    # ── مساجد ومراكز (جديد) ──
    GalleryTemplate(
        key="mosque_serene", name_ar=N_("مسجد — مواقيت"), vertical="mosque",
        desc_ar=N_("هوية هادئة مع مواقيت الصلاة والتاريخ الهجري وإعلانات."),
        base_slug="emerald", icon="mosque", tags=(N_("مواقيت"), N_("هادئ")),
        variables={"ACCENT_COLOR": "#047857"},
        addons={"theme_branded": _ad("theme_branded"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title=N_("إعلانات المسجد"),
                                     body=N_("درس بعد العشاء\nحلقة تحفيظ"))}),
    GalleryTemplate(
        key="mosque_ramadan", name_ar=N_("مسجد — رمضان"), vertical="mosque",
        desc_ar=N_("ثيم رمضاني مع مواقيت الصلاة وإعلانات الأنشطة."),
        base_slug="card", icon="star-and-crescent", tags=(N_("رمضان"), N_("موسمي")),
        variables={"ACCENT_COLOR": "#16a34a"},
        addons={"theme_seasonal": _ad("theme_seasonal", season="ramadan"),
                "prayer_times": _ad("prayer_times"),
                "announcements": _ad("announcements", title=N_("برنامج رمضان"),
                                     body=N_("تراويح ٩م\nإفطار صائم يوميًّا"))}),
    # ════════════════════════════════════════════════════════════
    # قوالب الجلود الجديدة (feat/hotspot-gallery-expansion) عبر الأنواع
    # ════════════════════════════════════════════════════════════
    # ── Clean Card ──
    GalleryTemplate(
        key="clean_shop", name_ar=N_("متجر نظيف"), vertical="shop",
        desc_ar=N_("بطاقة فاتحة محايدة بسيطة — دعم 24/7 وتذييل المكان."),
        base_slug="clean_card", icon="bag-shopping", tags=(N_("بسيط"), N_("نظيف")),
        variables={"ACCENT_COLOR": "#2563EB", "BG_COLOR": "#F1F5F9"},
        addons={"support_card": _ad("support_card", phone="0590000000"),
                "venue_footer": _ad("venue_footer", address=N_("شارع الحمراء"),
                                    phone="0590000000", tagline=N_("نسعد بخدمتك"))}),
    GalleryTemplate(
        key="clean_clinic", name_ar=N_("عيادة نظيفة"), vertical="clinic",
        desc_ar=N_("بطاقة هادئة مطمئنة — شريحة اتصال ودعم وتذييل المكان."),
        base_slug="clean_card", icon="stethoscope", tags=(N_("طبي"), N_("بسيط")),
        variables={"ACCENT_COLOR": "#0d9488", "BG_COLOR": "#F0FDFA"},
        addons={"online_chip": _ad("online_chip"),
                "support_card": _ad("support_card", phone="0590000000")}),
    GalleryTemplate(
        key="clean_office", name_ar=N_("مكتب نظيف"), vertical="coworking",
        desc_ar=N_("افتراضي محايد أنيق — تذكّرني وإظهار كلمة المرور."),
        base_slug="clean_card", icon="briefcase", tags=(N_("مكتب"), N_("افتراضي")),
        variables={"ACCENT_COLOR": "#4f46e5", "BG_COLOR": "#F1F5F9"},
        addons={"remember_me": _ad("remember_me"), "password_eye": _ad("password_eye")}),

    # ── Photo Backdrop ──
    GalleryTemplate(
        key="photo_hotel", name_ar=N_("فندق بخلفية صورة"), vertical="hotel",
        desc_ar=N_("صورة ملء الشاشة وبطاقة زجاجية — تاريخ/وقت ودعم وشراء بطاقة."),
        base_slug="photo_backdrop", icon="hotel", tags=(N_("صورة"), N_("زجاجي")),
        variables={"ACCENT_COLOR": "#0ea5e9", "BG_COLOR": "#0B1020"},
        addons={"datetime_greeting": _ad("datetime_greeting"),
                "support_card": _ad("support_card", phone="0590000000"),
                "buy_card_cta": _ad("buy_card_cta", label=N_("اشترِ بطاقة"),
                                    url="https://pay.example.com/topup")}),
    GalleryTemplate(
        key="photo_restaurant", name_ar=N_("مطعم بخلفية صورة"), vertical="restaurant",
        desc_ar=N_("صورة طعام شهيّة وبطاقة زجاجية — تقييم وروابط تواصل."),
        base_slug="photo_backdrop", icon="utensils", tags=(N_("صورة"), N_("مطعم")),
        variables={"ACCENT_COLOR": "#f97316", "BG_COLOR": "#1c1917"},
        addons={"rating_badge": _ad("rating_badge", stars="5"),
                "venue_footer": _ad("venue_footer", phone="0590000000",
                                    tagline=N_("ألذّ الأطباق بانتظارك"))}),

    # ── Food Co-Brand ──
    GalleryTemplate(
        key="food_resto", name_ar=N_("مطعم تعاون"), vertical="restaurant",
        desc_ar=N_("كريمي/خوخي مرح مع شعارين وتقييم وتذييل."),
        base_slug="food_cobrand", icon="burger", tags=(N_("طعام"), N_("تعاون")),
        variables={"ACCENT_COLOR": "#f97316", "ACCENT2_COLOR": "#ea580c"},
        addons={"cobrand_dual_logo": _ad("cobrand_dual_logo"),
                "rating_badge": _ad("rating_badge", stars="5")}),
    GalleryTemplate(
        key="food_cafe", name_ar=N_("كافيه تعاون"), vertical="cafe",
        desc_ar=N_("دافئ ومرح — ساعة سعيدة مجدولة وتواصل."),
        base_slug="food_cobrand", icon="mug-hot", tags=(N_("كافيه"), N_("مرح")),
        variables={"ACCENT_COLOR": "#d97706", "ACCENT2_COLOR": "#b45309"},
        addons={"scheduled_content": _ad("scheduled_content",
                                         message=N_("ساعة سعيدة ٤–٦م"), start_hour="16",
                                         end_hour="18"),
                "datetime_greeting": _ad("datetime_greeting")}),

    # ── Crimson Luxe ──
    GalleryTemplate(
        key="crimson_fine", name_ar=N_("مطعم فاخر قرمزي"), vertical="restaurant",
        desc_ar=N_("أسود/قرمزي بشاشة منقسمة — تقييم وموثّق ودخول موظّفين وزخرفة."),
        base_slug="crimson_luxe", icon="wine-glass", tags=(N_("فاخر"), N_("منقسم")),
        variables={"ACCENT_COLOR": "#0b1020", "ACCENT2_COLOR": "#dc2626"},
        addons={"rating_badge": _ad("rating_badge", stars="5"),
                "staff_login_link": _ad("staff_login_link"),
                "datetime_greeting": _ad("datetime_greeting"),
                "ornamental_divider": _ad("ornamental_divider")}),
    GalleryTemplate(
        key="crimson_resort", name_ar=N_("منتجع قرمزي"), vertical="hotel",
        desc_ar=N_("فخامة داكنة بشاشة منقسمة — مبدّل لغة وشراء بطاقة."),
        base_slug="crimson_luxe", icon="umbrella-beach", tags=(N_("منتجع"), N_("فخم")),
        variables={"ACCENT_COLOR": "#0b1020", "ACCENT2_COLOR": "#b91c1c"},
        addons={"multilang": _ad("multilang"),
                "buy_card_cta": _ad("buy_card_cta", url="https://pay.example.com/x")}),

    # ── Gilded Hospitality ──
    GalleryTemplate(
        key="gilded_resto", name_ar=N_("مطعم مذهّب"), vertical="restaurant",
        desc_ar=N_("عاجي/ذهبي بنصفين وزخارف — تقييم وتذييل تواصل."),
        base_slug="gilded_hospitality", icon="crown", tags=(N_("ذهبي"), N_("راقٍ")),
        variables={"ACCENT_COLOR": "#b8860b", "ACCENT2_COLOR": "#d4af37"},
        addons={"ornamental_divider": _ad("ornamental_divider"),
                "rating_badge": _ad("rating_badge", stars="5"),
                "venue_footer": _ad("venue_footer", phone="0590000000")}),
    GalleryTemplate(
        key="gilded_boutique", name_ar=N_("بوتيك مذهّب"), vertical="shop",
        desc_ar=N_("عاجي/ذهبي أنيق — زخرفة وزر شراء."),
        base_slug="gilded_hospitality", icon="gem", tags=(N_("بوتيك"), N_("ذهبي")),
        variables={"ACCENT_COLOR": "#a16207", "ACCENT2_COLOR": "#d4af37"},
        addons={"ornamental_divider": _ad("ornamental_divider"),
                "buy_card_cta": _ad("buy_card_cta", url="https://pay.example.com/x")}),

    # ── Soft Sky ──
    GalleryTemplate(
        key="soft_clinic", name_ar=N_("عيادة سماوية"), vertical="clinic",
        desc_ar=N_("باستيلي ناعم وأزرار حبّة — شريحة اتصال ودعم."),
        base_slug="soft_sky", icon="stethoscope", tags=(N_("ناعم"), N_("طبي")),
        variables={"ACCENT_COLOR": "#0ea5e9", "BG_COLOR": "#E0F2FE"},
        addons={"online_chip": _ad("online_chip"),
                "support_card": _ad("support_card", phone="0590000000")}),
    GalleryTemplate(
        key="soft_cowork", name_ar=N_("مساحة عمل سماوية"), vertical="coworking",
        desc_ar=N_("هادئ ومنعش — قراءة الجهاز وتحليلات."),
        base_slug="soft_sky", icon="laptop", tags=(N_("ناعم"), N_("عمل")),
        variables={"ACCENT_COLOR": "#06b6d4", "BG_COLOR": "#ECFEFF"},
        addons={"device_readout": _ad("device_readout"),
                "analytics": _ad("analytics", vertical="coworking")}),

    # ── Carrier App ──
    GalleryTemplate(
        key="carrier_isp", name_ar=N_("مزوّد إنترنت — تطبيق"), vertical="isp",
        desc_ar=N_("بوابة متعدّدة التبويبات بشريط سفلي — باقات ونقاط بيع وحالة شبكة."),
        base_slug="carrier_app", icon="wifi", tags=(N_("تبويبات"), N_("مشغّل")),
        variables={"ACCENT_COLOR": "#16a34a", "BG_COLOR": "#F0FDF4"},
        addons={"tab_bar_nav": _ad("tab_bar_nav"),
                "dealers_directory": _ad("dealers_directory",
                                         dealers=N_("المركز|0590000000|الحمراء\nفرع الشمال|0591111111|الزهور"),
                                         payments=N_("مدى، أبل باي")),
                "package_ribbons": _ad("package_ribbons", title=N_("باقة 100 جيجا"),
                                       ribbon=N_("الأكثر طلبًا")),
                "network_status_strip": _ad("network_status_strip")}),
    GalleryTemplate(
        key="carrier_reseller", name_ar=N_("موزّع — تطبيق"), vertical="shop",
        desc_ar=N_("تطبيق مشغّل بلون سماوي — نقاط بيع وشراء بطاقة."),
        base_slug="carrier_app", icon="store", tags=(N_("موزّع"), N_("تبويبات")),
        variables={"ACCENT_COLOR": "#0ea5e9", "BG_COLOR": "#F0F9FF"},
        addons={"tab_bar_nav": _ad("tab_bar_nav"),
                "dealers_directory": _ad("dealers_directory",
                                         dealers=N_("نقطتي|0590000000|السوق")),
                "buy_card_cta": _ad("buy_card_cta", url="https://pay.example.com/x")}),

    # ── Tech Terminal ──
    GalleryTemplate(
        key="terminal_isp", name_ar=N_("شبكة — محطّة تقنية"), vertical="isp",
        desc_ar=N_("داكن تقني بشريط حالة شبكة — قراءة الجهاز وشريحة اتصال."),
        base_slug="tech_terminal", icon="network-wired", tags=(N_("تقني"), N_("داكن")),
        variables={"ACCENT_COLOR": "#22c55e", "BG_COLOR": "#070B14"},
        addons={"network_status_strip": _ad("network_status_strip"),
                "device_readout": _ad("device_readout"),
                "online_chip": _ad("online_chip")}),
    GalleryTemplate(
        key="terminal_cowork", name_ar=N_("مساحة تقنية"), vertical="coworking",
        desc_ar=N_("مظهر تقني داكن — تحليلات وقراءة جهاز."),
        base_slug="tech_terminal", icon="laptop-code", tags=(N_("تقني"), N_("عمل")),
        variables={"ACCENT_COLOR": "#10b981", "BG_COLOR": "#070B14"},
        addons={"analytics": _ad("analytics", vertical="coworking"),
                "device_readout": _ad("device_readout")}),

    # ── Frost Glass Blue ──
    GalleryTemplate(
        key="frost_retail", name_ar=N_("متجر زجاج ثلجي"), vertical="shop",
        desc_ar=N_("زجاجية جليدية زرقاء — تذكّرني وشراء بطاقة وإظهار كلمة المرور."),
        base_slug="frost_glass_blue", icon="snowflake", tags=(N_("زجاجي"), N_("أزرق")),
        variables={"ACCENT_COLOR": "#1d4ed8", "BG_COLOR": "#DBEAFE"},
        addons={"remember_me": _ad("remember_me"), "password_eye": _ad("password_eye"),
                "buy_card_cta": _ad("buy_card_cta", url="https://pay.example.com/x")}),
    GalleryTemplate(
        key="frost_clinic", name_ar=N_("عيادة زجاج ثلجي"), vertical="clinic",
        desc_ar=N_("جليدي مطمئن — شريحة اتصال ودعم."),
        base_slug="frost_glass_blue", icon="stethoscope", tags=(N_("زجاجي"), N_("طبي")),
        variables={"ACCENT_COLOR": "#2563eb", "BG_COLOR": "#DBEAFE"},
        addons={"online_chip": _ad("online_chip"),
                "support_card": _ad("support_card", phone="0590000000")}),

    # ── Telemetry Console ──
    GalleryTemplate(
        key="telemetry_network", name_ar=N_("لوحة قياس الشبكة"), vertical="isp",
        desc_ar=N_("جلد متصل بشريط حالة — إحصاءات MAC/IP وأشرطة سرعة وعدّاد وتحديث."),
        base_slug="telemetry_console", icon="gauge-high", tags=(N_("متصل"), N_("إحصاءات")),
        variables={"ACCENT_COLOR": "#0ea5e9", "BG_COLOR": "#0C4A6E"},
        addons={"mac_dashboard": _ad("mac_dashboard"),
                "throughput_bars": _ad("throughput_bars"),
                "countdown_tile": _ad("countdown_tile", minutes="120"),
                "network_status_strip": _ad("network_status_strip"),
                "refresh_session": _ad("refresh_session")}),
    GalleryTemplate(
        key="telemetry_cowork", name_ar=N_("مركز أعمال — لوحة قياس"), vertical="coworking",
        desc_ar=N_("لوحة متصل لمراكز الأعمال — سرعة وعدّاد وتحديث جلسة."),
        base_slug="telemetry_console", icon="building", tags=(N_("أعمال"), N_("متصل")),
        variables={"ACCENT_COLOR": "#0891b2", "BG_COLOR": "#0C4A6E"},
        addons={"throughput_bars": _ad("throughput_bars"),
                "countdown_tile": _ad("countdown_tile", minutes="240"),
                "refresh_session": _ad("refresh_session")}),
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
    فيبقى الاسم/الشعار/الدعم، وتُطبَّق تعديلات القالب (اللون/الترحيب).

    يونيو 2026: يَحقن MOTIF_ICON تلقائيًّا من vertical القَالب — كي
    تَحمل صَفحة الـhotspot «بَصمة قِطاعيّة» (كوب قهوة لكافيه، صَليب
    طبّي لعيادة، …) بنفس مَكتبة motifs المُستعملة على الكروت. لا
    تَتجاوز تَعديلات الـoperator اليَدوية — تُحفَظ كقيمة افتراضيّة
    فَوق الـbase فقط لو لم يُحدّدها."""
    t = GALLERY_BY_KEY.get(key)
    if not t:
        return None
    variables = dict(base_vars or {})
    variables.update(t.variables)
    # MOTIF_ICON: تعديل يَدوي > متغيّر قالب > افتراضيّ من vertical
    if "MOTIF_ICON" not in variables or not variables["MOTIF_ICON"]:
        from . import card_motifs
        variables["MOTIF_ICON"] = card_motifs.VERTICAL_TO_MOTIF.get(
            t.vertical, "wifi")
    return t.base_slug, variables, dict(t.addons)


__all__ = [
    "VERTICALS", "VERTICAL_ORDER", "GalleryTemplate", "GALLERY",
    "GALLERY_BY_KEY", "by_vertical", "get", "resolve",
]
