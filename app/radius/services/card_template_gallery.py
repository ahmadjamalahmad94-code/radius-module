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

# سلسلة تذييل افتراضية مشتركة موجزة.
_F_KEEP = "احتفظ ببيانات الدخول حتى انتهاء الصلاحية"
_F_SCAN = "امسح رمز QR أو أدخل البيانات يدويًا للاتصال"
_F_ENJOY = "اتصل واستمتع بخدمة إنترنت مستقرة"


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
    out[key] = preset
    GALLERY_META[key] = (vertical, style)


def _build() -> dict[str, dict]:
    g: dict[str, dict] = {}
    # ── مقهى/كافيه ──
    _reg(g, "cafe_warm", "cafe", "colorful",
         _p("كافيه دافئ", "#7c3a1d", "#c8772f", "#fcd34d", "#fff7ed",
            "#ffedd5", "rounded", "wave", "مقهاك", "واي فاي الضيوف",
            "تفضّل بالاتصال واستمتع بقهوتك"))
    _reg(g, "cafe_mint", "cafe", "minimal",
         _p("كافيه منعش", "#ecfeff", "#cffafe", "#0d9488", "#0f172a",
            "#ecfeff", "clean", "clean", "مقهاك", "دخول الإنترنت", _F_SCAN))
    _reg(g, "cafe_mocha", "cafe", "luxe",
         _p("موكا داكن", "#1c1410", "#5b3a29", "#d6a06a", "#fdf6ec",
            "#efe2d3", "boxed", "grid", "مقهاك", "بطاقة واي فاي"))
    # ── مطعم/وجبات ──
    _reg(g, "resto_appetite", "restaurant", "colorful",
         _p("مطعم شهي", "#7f1d1d", "#ea580c", "#fde047", "#fff7ed",
            "#ffe4d6", "rounded", "signal", "مطعمك", "واي فاي مجاني",
            "بالهناء والعافية — اتصل بشبكتنا"))
    _reg(g, "resto_fastfood", "restaurant", "colorful",
         _p("وجبات سريعة", "#b91c1c", "#f59e0b", "#fff200", "#ffffff",
            "#fff7cc", "rounded", "wave", "مطعمك", "كود الواي فاي"))
    _reg(g, "resto_fine", "restaurant", "luxe",
         _p("مطعم راقٍ", "#0b0b0d", "#2b2b30", "#caa24a", "#fbf7ee",
            "#efe6cf", "boxed", "grid", "مطعمك", "دخول الضيوف"))
    # ── عيادة/طبي ──
    _reg(g, "clinic_calm", "clinic", "minimal",
         _p("عيادة هادئة", "#f0fdfa", "#ccfbf1", "#0ea5e9", "#0f172a",
            "#e0f2fe", "clean", "clean", "عيادتك", "إنترنت المرضى",
            "نتمنى لك دوام الصحة — استخدم البيانات للاتصال"))
    _reg(g, "clinic_trust", "clinic", "gradient",
         _p("طبي موثوق", "#0c4a6e", "#0ea5e9", "#7dd3fc", "#f0f9ff",
            "#e0f2fe", "clean", "grid", "عيادتك", "دخول الإنترنت", _F_SCAN))
    _reg(g, "clinic_care", "clinic", "minimal",
         _p("رعاية", "#ecfdf5", "#d1fae5", "#059669", "#064e3b",
            "#d1fae5", "clean", "clean", "مركزك الطبي", "واي فاي الزوّار"))
    # ── محل/تجزئة ──
    _reg(g, "shop_bold", "shop", "colorful",
         _p("متجر جريء", "#6d28d9", "#db2777", "#fde047", "#ffffff",
            "#fae8ff", "rounded", "signal", "متجرك", "واي فاي العملاء"))
    _reg(g, "shop_clean", "shop", "minimal",
         _p("متجر بسيط", "#ffffff", "#f1f5f9", "#0ea5e9", "#0f172a",
            "#eff6ff", "clean", "clean", "متجرك", "دخول الإنترنت"))
    _reg(g, "shop_sale", "shop", "colorful",
         _p("عروض", "#c2410c", "#f97316", "#fde047", "#fff7ed",
            "#ffedd5", "rounded", "wave", "متجرك", "كود الواي فاي",
            "تسوّق واتصل — عروضنا بانتظارك"))
    # ── شبكة/مزوّد إنترنت ──
    _reg(g, "isp_ultra", "isp", "tech",
         _p("ألياف فائقة", "#020617", "#1d4ed8", "#38bdf8", "#ffffff",
            "#dbeafe", "boxed", "grid", "شبكتك", "دخول الألياف",
            "بطاقة دخول بسرعة عالية"))
    _reg(g, "isp_speed", "isp", "neon",
         _p("سرعة قصوى", "#04110a", "#065f46", "#22c55e", "#ecfdf5",
            "#d1fae5", "boxed", "signal", "شبكتك", "رمز الدخول"))
    _reg(g, "isp_wave", "isp", "gradient",
         _p("موجة الشبكة", "#312e81", "#0d9488", "#67e8f9", "#f0fdfa",
            "#cffafe", "rounded", "wave", "شبكتك", "دخول واي فاي", _F_SCAN))
    # ── فندق/منتجع ──
    _reg(g, "hotel_lux", "hotel", "luxe",
         _p("فندق فخم", "#0b1220", "#1e293b", "#caa24a", "#fbf7ee",
            "#efe6cf", "boxed", "grid", "فندقك", "إنترنت النزلاء",
            "نتمنى لك إقامة سعيدة"))
    _reg(g, "hotel_resort", "hotel", "gradient",
         _p("منتجع", "#065f46", "#0d9488", "#5eead4", "#f0fdfa",
            "#ccfbf1", "rounded", "wave", "منتجعك", "واي فاي الضيوف"))
    _reg(g, "hotel_classic", "hotel", "luxe",
         _p("كلاسيكي", "#3b0a13", "#7f1d1d", "#e7c873", "#fff7ed",
            "#f5e6cf", "boxed", "grid", "فندقك", "بطاقة دخول"))
    # ── صالون/تجميل ──
    _reg(g, "salon_rose", "salon", "colorful",
         _p("صالون وردي", "#9d174d", "#db2777", "#fbcfe8", "#fff1f7",
            "#fce7f3", "rounded", "wave", "صالونك", "واي فاي الزبائن"))
    _reg(g, "salon_glam", "salon", "luxe",
         _p("جلام أسود ذهبي", "#0b0b0d", "#2b2b30", "#e7b6c8", "#fdf2f8",
            "#f3e8ee", "boxed", "grid", "صالونك", "دخول الإنترنت"))
    # ── جيم/رياضة ──
    _reg(g, "gym_power", "gym", "neon",
         _p("جيم قوّة", "#0a0a0a", "#7f1d1d", "#ef4444", "#fff5f5",
            "#fee2e2", "boxed", "signal", "ناديك", "واي فاي الأعضاء",
            "اشحن طاقتك واتصل بشبكتنا"))
    _reg(g, "gym_energy", "gym", "neon",
         _p("طاقة", "#0a0f05", "#1a2e05", "#a3e635", "#f7fee7",
            "#ecfccb", "rounded", "signal", "ناديك", "رمز الدخول"))
    # ── مدرسة/تعليم ──
    _reg(g, "school_bright", "school", "gradient",
         _p("مدرسة مشرقة", "#1d4ed8", "#0ea5e9", "#bae6fd", "#f0f9ff",
            "#dbeafe", "clean", "grid", "مدرستك", "إنترنت الحرم"))
    _reg(g, "school_kids", "school", "colorful",
         _p("أطفال مرح", "#7c3aed", "#f97316", "#fde047", "#ffffff",
            "#fae8ff", "rounded", "wave", "مدرستك", "واي فاي الطلاب"))
    # ── مناسبات ──
    _reg(g, "event_wedding", "events", "luxe",
         _p("أعراس", "#3f2d1a", "#a8853f", "#f3d98b", "#fffaf0",
            "#f5ead2", "boxed", "grid", "مناسبتك", "واي فاي الضيوف",
            "ألف مبروك — اتصل بشبكتنا"))
    _reg(g, "event_party", "events", "colorful",
         _p("حفلة", "#6d28d9", "#db2777", "#22d3ee", "#ffffff",
            "#f5e1ff", "rounded", "signal", "مناسبتك", "كود الواي فاي"))
    # ── مسجد/جمعية خيرية ──
    _reg(g, "mosque_serene", "mosque", "heritage",
         _p("مسجد", "#052e16", "#166534", "#d4af37", "#f0fdf4",
            "#dcfce7", "boxed", "grid", "مسجدك", "واي فاي المصلّين",
            "نسأل الله لكم القبول — استخدم البيانات للاتصال"))
    _reg(g, "charity_hope", "mosque", "minimal",
         _p("جمعية خيرية", "#064e3b", "#0d9488", "#5eead4", "#f0fdfa",
            "#ccfbf1", "clean", "clean", "جمعيتك", "إنترنت الزوّار"))
    # ── ألعاب/Gaming ──
    _reg(g, "gaming_neon", "gaming", "neon",
         _p("قيمنق نيون", "#0a0118", "#3b0764", "#a3e635", "#f5f3ff",
            "#ede9fe", "boxed", "grid", "صالتك", "واي فاي اللاعبين",
            "جاهز للّعب — اتصل بأعلى سرعة"))
    _reg(g, "gaming_arcade", "gaming", "colorful",
         _p("أركيد", "#1e1b4b", "#db2777", "#22d3ee", "#ffffff",
            "#e0e7ff", "rounded", "signal", "صالتك", "رمز الدخول"))
    # ── عام/Generic + أنماط إضافية ──
    _reg(g, "generic_clean", "generic", "minimal",
         _p("عام نظيف", "#ffffff", "#eef2f7", "#2563eb", "#0f172a",
            "#eff6ff", "clean", "clean", "شبكتك", "بطاقة دخول"))
    _reg(g, "heritage_arabesque", "generic", "heritage",
         _p("تراثي عربي", "#3b2410", "#8a5a2b", "#e7c873", "#fff7ed",
            "#f3e3c9", "boxed", "grid", "شبكتك", "بطاقة دخول",
            "زخرفة عربية أنيقة — احتفظ ببياناتك"))
    _reg(g, "elegant_business", "generic", "business",
         _p("بطاقة عمل أنيقة", "#0f172a", "#334155", "#94a3b8", "#f8fafc",
            "#e2e8f0", "clean", "clean", "شبكتك", "دخول الإنترنت"))
    _reg(g, "ticket_vibe", "generic", "ticket",
         _p("نمط التذكرة", "#b45309", "#f59e0b", "#1f2937", "#ffffff",
            "#fff7e0", "rounded", "wave", "شبكتك", "تذكرة دخول",
            "تذكرتك للاتصال — صالحة حتى انتهاء الرصيد"))
    return g


GALLERY_PRESETS: dict[str, dict] = _build()

__all__ = ["GALLERY_PRESETS", "GALLERY_META"]
