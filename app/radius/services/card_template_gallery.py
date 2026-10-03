# -*- coding: utf-8 -*-
"""card_template_gallery — مكتبة قوالب كروت جاهزة موسّعة («القوالب الجاهزة»).

قوالب أصلية مدفوعة بالرموز فوق نفس محرّك تصيير الكروت القائم
(build_card_render_model): كل قالب مجرّد حزمة ألوان/نمط QR/زخرفة + نصوص
افتراضية (الاسم/العنوان/التذييل)، والمحرّك الواحد ينتجها جميعًا. لا
تخطيطات جديدة ولا نسخ بكسلي — تنوّع عبر الرموز فقط فتبقى كل الحقول
الوظيفية للكرت (شعار/SSID/مستخدم/كلمة مرور/سعر/صلاحية/QR/تذييل) سليمة
وقابلة للتحرير من الواجهة.

تُدمج في operations._PRINT_PRESETS عند الاستيراد فتظهر تلقائيًّا في معرض
«القوالب الجاهزة» وتمرّ بنفس مسارات المعاينة الحيّة وتصدير PDF.

مفاتيح كل قالب (نفس عقد _PRINT_PRESETS + pattern_style):
  label, gradient_start, gradient_end, accent_color, text_color,
  surface_color, qr_style (boxed|rounded|clean), pattern_style
  (signal|wave|grid|clean), brand_name, card_title, footer_text.

التصنيفات (vertical) والأنماط (style) مذكورة في GALLERY_META لأغراض
العرض/الاختبار فقط (المحرّك لا يحتاجها).
"""
from __future__ import annotations
from app.i18n_text import N_

# سلسلة تذييل افتراضية مشتركة موجزة.
_F_KEEP = N_("احتفظ ببيانات الدخول حتى انتهاء الصلاحية")
_F_SCAN = N_("امسح رمز QR أو أدخل البيانات يدويًا للاتصال")
_F_ENJOY = N_("اتصل واستمتع بخدمة إنترنت مستقرة")


def _p(label, gs, ge, accent, text, surface, qr, pattern,
       brand, title, footer=_F_KEEP):
    return {
        "label": label,
        "gradient_start": gs, "gradient_end": ge,
        "accent_color": accent, "text_color": text, "surface_color": surface,
        "qr_style": qr, "pattern_style": pattern,
        "brand_name": brand, "card_title": title, "footer_text": footer,
    }


# key → (vertical, style) — للعرض المجمَّع والاختبار.
GALLERY_META: dict[str, tuple[str, str]] = {}


def _reg(out, key, vertical, style, preset):
    # كل preset يَحمل motif يُحدّد رمز القطاع (يُرسم بجانب الـbrand +
    # كَـwatermark خَلف المحتوى). الـmotif يأتي من خريطة VERTICAL_TO_MOTIF
    # تلقائيًا — كافٍ لكل القَوالب الـ44 بلا تَكرار. يونيو 2026.
    from .card_motifs import VERTICAL_TO_MOTIF
    preset = dict(preset)
    preset.setdefault("icon", VERTICAL_TO_MOTIF.get(vertical, "wifi"))
    out[key] = preset
    GALLERY_META[key] = (vertical, style)


def _build() -> dict[str, dict]:
    g: dict[str, dict] = {}
    # ── مقهى/كافيه ──
    _reg(g, "cafe_warm", "cafe", "colorful",
         _p(N_("كافيه دافئ"), "#7c3a1d", "#c8772f", "#fcd34d", "#fff7ed",
            "#ffedd5", "rounded", "wave", N_("مقهاك"), N_("واي فاي الضيوف"),
            N_("تفضّل بالاتصال واستمتع بقهوتك")))
    _reg(g, "cafe_mint", "cafe", "minimal",
         _p(N_("كافيه منعش"), "#ecfeff", "#cffafe", "#0d9488", "#0f172a",
            "#ecfeff", "clean", "clean", N_("مقهاك"), N_("دخول الإنترنت"), _F_SCAN))
    _reg(g, "cafe_mocha", "cafe", "luxe",
         _p(N_("موكا داكن"), "#1c1410", "#5b3a29", "#d6a06a", "#fdf6ec",
            "#efe2d3", "boxed", "grid", N_("مقهاك"), N_("بطاقة واي فاي")))
    # ── مطعم/وجبات ──
    _reg(g, "resto_appetite", "restaurant", "colorful",
         _p(N_("مطعم شهي"), "#7f1d1d", "#ea580c", "#fde047", "#fff7ed",
            "#ffe4d6", "rounded", "signal", N_("مطعمك"), N_("واي فاي مجاني"),
            N_("بالهناء والعافية — اتصل بشبكتنا")))
    _reg(g, "resto_fastfood", "restaurant", "colorful",
         _p(N_("وجبات سريعة"), "#b91c1c", "#f59e0b", "#fff200", "#ffffff",
            "#fff7cc", "rounded", "wave", N_("مطعمك"), N_("كود الواي فاي")))
    _reg(g, "resto_fine", "restaurant", "luxe",
         _p(N_("مطعم راقٍ"), "#0b0b0d", "#2b2b30", "#caa24a", "#fbf7ee",
            "#efe6cf", "boxed", "grid", N_("مطعمك"), N_("دخول الضيوف")))
    # ── عيادة/طبي ──
    _reg(g, "clinic_calm", "clinic", "minimal",
         _p(N_("عيادة هادئة"), "#f0fdfa", "#ccfbf1", "#0ea5e9", "#0f172a",
            "#e0f2fe", "clean", "clean", N_("عيادتك"), N_("إنترنت المرضى"),
            N_("نتمنى لك دوام الصحة — استخدم البيانات للاتصال")))
    _reg(g, "clinic_trust", "clinic", "gradient",
         _p(N_("طبي موثوق"), "#0c4a6e", "#0ea5e9", "#7dd3fc", "#f0f9ff",
            "#e0f2fe", "clean", "grid", N_("عيادتك"), N_("دخول الإنترنت"), _F_SCAN))
    _reg(g, "clinic_care", "clinic", "minimal",
         _p(N_("رعاية"), "#ecfdf5", "#d1fae5", "#059669", "#064e3b",
            "#d1fae5", "clean", "clean", N_("مركزك الطبي"), N_("واي فاي الزوّار")))
    # ── محل/تجزئة ──
    _reg(g, "shop_bold", "shop", "colorful",
         _p(N_("متجر جريء"), "#6d28d9", "#db2777", "#fde047", "#ffffff",
            "#fae8ff", "rounded", "signal", N_("متجرك"), N_("واي فاي العملاء")))
    _reg(g, "shop_clean", "shop", "minimal",
         _p(N_("متجر بسيط"), "#ffffff", "#f1f5f9", "#0ea5e9", "#0f172a",
            "#eff6ff", "clean", "clean", N_("متجرك"), N_("دخول الإنترنت")))
    _reg(g, "shop_sale", "shop", "colorful",
         _p(N_("عروض"), "#c2410c", "#f97316", "#fde047", "#fff7ed",
            "#ffedd5", "rounded", "wave", N_("متجرك"), N_("كود الواي فاي"),
            N_("تسوّق واتصل — عروضنا بانتظارك")))
    # ── شبكة/مزوّد إنترنت ──
    _reg(g, "isp_ultra", "isp", "tech",
         _p(N_("ألياف فائقة"), "#020617", "#1d4ed8", "#38bdf8", "#ffffff",
            "#dbeafe", "boxed", "grid", N_("شبكتك"), N_("دخول الألياف"),
            N_("بطاقة دخول بسرعة عالية")))
    _reg(g, "isp_speed", "isp", "neon",
         _p(N_("سرعة قصوى"), "#04110a", "#065f46", "#22c55e", "#ecfdf5",
            "#d1fae5", "boxed", "signal", N_("شبكتك"), N_("رمز الدخول")))
    _reg(g, "isp_wave", "isp", "gradient",
         _p(N_("موجة الشبكة"), "#312e81", "#0d9488", "#67e8f9", "#f0fdfa",
            "#cffafe", "rounded", "wave", N_("شبكتك"), N_("دخول واي فاي"), _F_SCAN))
    # ── فندق/منتجع ──
    _reg(g, "hotel_lux", "hotel", "luxe",
         _p(N_("فندق فخم"), "#0b1220", "#1e293b", "#caa24a", "#fbf7ee",
            "#efe6cf", "boxed", "grid", N_("فندقك"), N_("إنترنت النزلاء"),
            N_("نتمنى لك إقامة سعيدة")))
    _reg(g, "hotel_resort", "hotel", "gradient",
         _p(N_("منتجع"), "#065f46", "#0d9488", "#5eead4", "#f0fdfa",
            "#ccfbf1", "rounded", "wave", N_("منتجعك"), N_("واي فاي الضيوف")))
    _reg(g, "hotel_classic", "hotel", "luxe",
         _p(N_("كلاسيكي"), "#3b0a13", "#7f1d1d", "#e7c873", "#fff7ed",
            "#f5e6cf", "boxed", "grid", N_("فندقك"), N_("بطاقة دخول")))
    # ── صالون/تجميل ──
    _reg(g, "salon_rose", "salon", "colorful",
         _p(N_("صالون وردي"), "#9d174d", "#db2777", "#fbcfe8", "#fff1f7",
            "#fce7f3", "rounded", "wave", N_("صالونك"), N_("واي فاي الزبائن")))
    _reg(g, "salon_glam", "salon", "luxe",
         _p(N_("جلام أسود ذهبي"), "#0b0b0d", "#2b2b30", "#e7b6c8", "#fdf2f8",
            "#f3e8ee", "boxed", "grid", N_("صالونك"), N_("دخول الإنترنت")))
    # ── جيم/رياضة ──
    _reg(g, "gym_power", "gym", "neon",
         _p(N_("جيم قوّة"), "#0a0a0a", "#7f1d1d", "#ef4444", "#fff5f5",
            "#fee2e2", "boxed", "signal", N_("ناديك"), N_("واي فاي الأعضاء"),
            N_("اشحن طاقتك واتصل بشبكتنا")))
    _reg(g, "gym_energy", "gym", "neon",
         _p(N_("طاقة"), "#0a0f05", "#1a2e05", "#a3e635", "#f7fee7",
            "#ecfccb", "rounded", "signal", N_("ناديك"), N_("رمز الدخول")))
    # ── مدرسة/تعليم ──
    _reg(g, "school_bright", "school", "gradient",
         _p(N_("مدرسة مشرقة"), "#1d4ed8", "#0ea5e9", "#bae6fd", "#f0f9ff",
            "#dbeafe", "clean", "grid", N_("مدرستك"), N_("إنترنت الحرم")))
    _reg(g, "school_kids", "school", "colorful",
         _p(N_("أطفال مرح"), "#7c3aed", "#f97316", "#fde047", "#ffffff",
            "#fae8ff", "rounded", "wave", N_("مدرستك"), N_("واي فاي الطلاب")))
    # ── مناسبات ──
    _reg(g, "event_wedding", "events", "luxe",
         _p(N_("أعراس"), "#3f2d1a", "#a8853f", "#f3d98b", "#fffaf0",
            "#f5ead2", "boxed", "grid", N_("مناسبتك"), N_("واي فاي الضيوف"),
            N_("ألف مبروك — اتصل بشبكتنا")))
    _reg(g, "event_party", "events", "colorful",
         _p(N_("حفلة"), "#6d28d9", "#db2777", "#22d3ee", "#ffffff",
            "#f5e1ff", "rounded", "signal", N_("مناسبتك"), N_("كود الواي فاي")))
    # ── مسجد/جمعية خيرية ──
    _reg(g, "mosque_serene", "mosque", "heritage",
         _p(N_("مسجد"), "#052e16", "#166534", "#d4af37", "#f0fdf4",
            "#dcfce7", "boxed", "grid", N_("مسجدك"), N_("واي فاي المصلّين"),
            N_("نسأل الله لكم القبول — استخدم البيانات للاتصال")))
    _reg(g, "charity_hope", "mosque", "minimal",
         _p(N_("جمعية خيرية"), "#064e3b", "#0d9488", "#5eead4", "#f0fdfa",
            "#ccfbf1", "clean", "clean", N_("جمعيتك"), N_("إنترنت الزوّار")))
    # ── ألعاب/Gaming ──
    _reg(g, "gaming_neon", "gaming", "neon",
         _p(N_("قيمنق نيون"), "#0a0118", "#3b0764", "#a3e635", "#f5f3ff",
            "#ede9fe", "boxed", "grid", N_("صالتك"), N_("واي فاي اللاعبين"),
            N_("جاهز للّعب — اتصل بأعلى سرعة")))
    _reg(g, "gaming_arcade", "gaming", "colorful",
         _p(N_("أركيد"), "#1e1b4b", "#db2777", "#22d3ee", "#ffffff",
            "#e0e7ff", "rounded", "signal", N_("صالتك"), N_("رمز الدخول")))
    # ── عام/Generic + أنماط إضافية ──
    _reg(g, "generic_clean", "generic", "minimal",
         _p(N_("عام نظيف"), "#ffffff", "#eef2f7", "#2563eb", "#0f172a",
            "#eff6ff", "clean", "clean", N_("شبكتك"), N_("بطاقة دخول")))
    _reg(g, "heritage_arabesque", "generic", "heritage",
         _p(N_("تراثي عربي"), "#3b2410", "#8a5a2b", "#e7c873", "#fff7ed",
            "#f3e3c9", "boxed", "grid", N_("شبكتك"), N_("بطاقة دخول"),
            N_("زخرفة عربية أنيقة — احتفظ ببياناتك")))
    _reg(g, "elegant_business", "generic", "business",
         _p(N_("بطاقة عمل أنيقة"), "#0f172a", "#334155", "#94a3b8", "#f8fafc",
            "#e2e8f0", "clean", "clean", N_("شبكتك"), N_("دخول الإنترنت")))
    _reg(g, "ticket_vibe", "generic", "ticket",
         _p(N_("نمط التذكرة"), "#b45309", "#f59e0b", "#1f2937", "#ffffff",
            "#fff7e0", "rounded", "wave", N_("شبكتك"), N_("تذكرة دخول"),
            N_("تذكرتك للاتصال — صالحة حتى انتهاء الرصيد")))
    return g


GALLERY_PRESETS: dict[str, dict] = _build()

__all__ = ["GALLERY_PRESETS", "GALLERY_META"]
