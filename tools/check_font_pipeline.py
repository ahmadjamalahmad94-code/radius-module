# -*- coding: utf-8 -*-
"""فاحص خط البطاقات — شغّله هكذا من جذر المشروع:

    python tools/check_font_pipeline.py

يطبع: إصدار Pillow، هل دعم Raqm (تشكيل الحروف العربية) متوفر، وأي ملف
خط عربي سيختاره مُصدِّر الـPDF فعليًا — ثم يرسم عيّنة نص عربي إلى صورة
sample_font_check.png بجانب هذا الملف بنفس المسار الذي يسلكه تصدير
الـPDF تمامًا، لترى بعينك كيف سيظهر النص العربي في ملف الـPDF على هذا
الجهاز بالذات.

لماذا؟ المعاينة الحية في المتصفح ترسم بخط «المراعي» (Almarai) دائمًا،
لكن الـPDF يُرسم على الخادم/جهازك عبر Pillow: إن كان Pillow مبنيًا مع
Raqm (وهو الوضع الافتراضي في عجلات pip الرسمية) فسيستخدم المراعي نفسه
ويطابق المعاينة؛ وإن كان بدونه فسيسقط إلى خط نظام (Tahoma/Arial) ويبدو
النص مختلفًا. هذا الفاحص يكشف أي حالة عندك.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

SAMPLE_TEXT = "بطاقة إنترنت — HobeRadius تجربة الخط"
OUTPUT_PNG = os.path.join(_HERE, "sample_font_check.png")


def _load_card_renderer():
    """يحمّل موديول card_renderer مباشرة من ملفه.

    لا نستورد حزمة app كاملة (app/__init__.py يسحب Flask وإعدادات
    التطبيق) — الفاحص يحتاج منطق الخطوط فقط، فنحمّل الملف بمعزل عن
    بقية المشروع حتى يعمل على أي جهاز فيه Pillow فقط.
    """
    import importlib.util

    path = os.path.join(_ROOT, "app", "radius", "services", "card_renderer.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location("hobe_card_renderer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    print("=" * 60)
    print("فاحص خط بطاقات HobeRadius")
    print("=" * 60)

    # 1) Pillow نفسه
    try:
        import PIL
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except Exception as exc:
        print(f"[خطأ] Pillow غير مثبّت أو معطوب: {exc}")
        print("ثبّته بالأمر:  pip install pillow")
        return 1
    print(f"إصدار Pillow: {PIL.__version__}")

    # 2) دعم Raqm (تشكيل الحروف العربية مثل المتصفح)
    try:
        cr = _load_card_renderer()
    except Exception as exc:
        print(f"[خطأ] تعذّر تحميل موديول card_renderer: {exc}")
        print("شغّل الفاحص من جذر المشروع:  python tools/check_font_pipeline.py")
        return 1

    raqm = cr._pil_supports_raqm()
    if raqm:
        print("دعم Raqm: متوفر ✓ — سيُرسم نص الـPDF بخط المراعي مطابقًا للمعاينة الحية.")
    else:
        print("دعم Raqm: غير متوفر ✗ — سيسقط الـPDF إلى خط نظام (Tahoma/Arial)")
        print("          فيختلف شكل النص العربي عن المعاينة. الحل المعتاد:")
        print("          pip install --force-reinstall pillow")
        print("          (العجلات الرسمية تشحن Raqm منذ Pillow 8.2)")

    # 3) أي ملف خط سيُختار فعليًا؟ (نفس منطق التصدير حرفيًا)
    if raqm:
        chosen = cr._arabic_raster_font_path(weight=700)
        path_kind = "مسار Raqm (المراعي/القاهرة المشحونة)"
    else:
        chosen = cr._font_path_for_arabic(bold=True)
        path_kind = "مسار أشكال العرض القديمة (خط نظام)"
    print(f"المسار المُستخدم: {path_kind}")
    print(f"ملف الخط المختار: {chosen}")
    if not chosen or not os.path.isfile(chosen):
        print("[خطأ] لم يُعثر على أي ملف خط صالح — تأكد من وجود app/static/fonts/Almarai-*.ttf")
        return 1

    # 4) ارسم عيّنة بنفس دالة التصدير نفسها
    rendered = cr._build_arabic_text_image(
        SAMPLE_TEXT,
        size=48,
        color="#0f172a",
        weight=700,
        max_width=900,
        direction="rtl",
        opacity=1.0,
    )
    if not rendered:
        print("[خطأ] فشل رسم عيّنة النص — راجع الرسائل أعلاه.")
        return 1
    # الدالة تعيد الآن 4 عناصر (الرابع بيانات أصل التخطيط لمطابقة
    # المعاينة) — نلتقط أول ثلاثة فقط هنا.
    png_bytes, w, h = rendered[0], rendered[1], rendered[2]

    # ضعها فوق خلفية بيضاء حتى تُرى بوضوح في أي عارض صور.
    from io import BytesIO

    from PIL import Image

    text_img = Image.open(BytesIO(png_bytes)).convert("RGBA")
    canvas = Image.new("RGB", (w + 40, h + 40), (255, 255, 255))
    canvas.paste(text_img, (20, 20), text_img)
    canvas.save(OUTPUT_PNG, format="PNG")
    print(f"\nتم إنشاء صورة العيّنة: {OUTPUT_PNG}")
    print("افتحها الآن — هكذا بالضبط سيظهر النص العربي في ملف الـPDF المُصدَّر.")
    if raqm:
        print("(يجب أن يطابق خط المراعي في المعاينة الحية.)")
    else:
        print("(سيبدو بخط نظام مختلف عن المعاينة حتى تُصلح دعم Raqm.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
