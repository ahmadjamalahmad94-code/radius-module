"""Unified card renderer — one render model, two output adapters.

Arabic support note
===================
Card text (brand, title, footer, hotspot, …) can be any mix of Arabic
and Latin. The live SVG preview renders Arabic with the Almarai family
first (Cairo as fallback — both loaded by the admin layout / shipped
under app/static/fonts/), so the PDF export rasterizes Arabic runs with
the SAME shipped Almarai TTFs (falling back to Cairo) whenever Pillow
has Raqm shaping (the default in official wheels) — preview and export
use identical letterforms. Without Raqm the export
falls back to arabic-reshaper presentation forms drawn with a system
font that maps those legacy codepoints (Tahoma/Arial/Noto Naskh/
DejaVu → bundled Almarai), so Arabic never degrades to tofu squares.

ReportLab's built-in Helvetica covers Latin only, so we also ship the
Almarai TTF under app/static/fonts/ and register it on first import
for the rare vector-text fallback. The PDF adapter inspects each run:

  - All-Latin    → Helvetica / Helvetica-Bold (unchanged)
  - Contains AR → Almarai / Almarai-Bold, after the run is reshaped
                   with `arabic-reshaper` (joins isolated letters into
                   their initial / medial / final / isolated glyph
                   forms) and re-ordered with `python-bidi` (so the
                   text flows right-to-left visually even when the PDF
                   only knows about LTR glyph runs).

The SVG adapter keeps the root geometry LTR so positions remain stable,
then sets direction per text element. Arabic headings/footers can render
RTL while credentials remain LTR so card numbers and passwords never get
reordered.


Why this module exists
======================
Before this module the live preview built a card with one set of
HTML/CSS rules while the PDF exporter drew an independent layout using
ReportLab. The two layouts had drifted: the preview showed brand/title
at the top with USER/PASS/QR pills, the PDF rendered a teal half on
the right side, dropped USER/PASS into the top-left corner because
`username_x` / `password_x` defaulted to 0, and brand/title sat at
hardcoded ReportLab Y coordinates that did not match the percentages
used in the HTML preview.

The fix collapses both paths to a single normalized render model.
Whatever the user sees in the live preview is exactly what comes out
of the PDF — up to uniform scaling.

The render model
================
The model is canvas-normalized in absolute pixel units:

  - Landscape cards live on a 1000x600 canvas.
  - Portrait  cards live on a 600x1000 canvas.

All element coordinates and sizes are in canvas units. The SVG adapter
maps them via `viewBox`; the PDF adapter maps them via `beginForm` +
`pdf.scale()`. Callers that want to scale the card down (or up) do it
on the outside — the model itself is always at canvas size.

The model shape is intentionally a plain dict so it is trivially
JSON-serialisable, easy to unit test, and easy to inspect when
diagnosing a preview/PDF mismatch.

Compat note
===========
Existing print templates persist:
  - layout_json (presets, colors, brand, title, footer …)
  - top-level username_x / username_y / password_x / password_y / qr_x / qr_y
    in millimetres relative to a card_width_mm x card_height_mm box.

`_resolve_positions` normalizes those legacy mm coordinates into the
canvas-fraction system used by the renderer. When the legacy positions
are still at their (0, 0) factory default we fall back to a sensible
layout that matches the live preview rather than piling everything
into the top-left corner like the old PDF path did.
"""
from __future__ import annotations

import base64
from io import BytesIO
import math
import os
import re
import uuid
from typing import Any, Iterable
from urllib.parse import urlencode

# Public canvas dimensions. Pinned constants so any drift between the
# SVG adapter and the PDF adapter is impossible.
CANVAS_LANDSCAPE = (1000, 600)
CANVAS_PORTRAIT = (600, 1000)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")

# ─── Arabic font + shaping ─────────────────────────────────────────
# Almarai is shipped under app/static/fonts/. Registration is lazy so
# importing this module never fails — if ReportLab or the TTF is
# missing for any reason the PDF adapter quietly falls back to
# Helvetica and the text-strip behaviour, exactly like before.

_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static", "fonts",
)
_ALMARAI_REGULAR_PATH = os.path.join(_FONTS_DIR, "Almarai-Regular.ttf")
_ALMARAI_BOLD_PATH = os.path.join(_FONTS_DIR, "Almarai-Bold.ttf")
_ALMARAI_EXTRABOLD_PATH = os.path.join(_FONTS_DIR, "Almarai-ExtraBold.ttf")

# Cairo is the family the live SVG preview actually renders with (the
# admin layout loads it from Google Fonts and the SVG font stack lists
# it first). We ship the same family so the PDF export matches the
# preview glyph-for-glyph instead of falling back to Tahoma / Noto
# Naskh, whose letterforms look nothing like the preview.
_CAIRO_REGULAR_PATH = os.path.join(_FONTS_DIR, "Cairo-Regular.ttf")
_CAIRO_BOLD_PATH = os.path.join(_FONTS_DIR, "Cairo-Bold.ttf")
_CAIRO_BLACK_PATH = os.path.join(_FONTS_DIR, "Cairo-Black.ttf")

PDF_FONT_LATIN = "Helvetica"
PDF_FONT_LATIN_BOLD = "Helvetica-Bold"
# Arabic family — يونيو 2026، تَنقيح المالك: Cairo هو الـbundled الأساسي
# (Google Fonts، رُخصة SIL OFL). يُسجَّل تَحت اسم "Almarai" في ReportLab
# للحَفاظ على ثَبات PDF_FONT_ARABIC API (يَستعمله كل الكود السابق)؛
# Cairo ينطبق فعليًّا. الـfallback إلى Almarai-TTFs لو Cairo مَفقود (لا
# يُتَوَقَّع لكنّه insurance). اسم العائلة في الـSVG/CSS يَبقى Cairo
# لـcss-stack consistency.
PDF_FONT_ARABIC = "Almarai"
PDF_FONT_ARABIC_BOLD = "Almarai-Bold"
PDF_FONT_ARABIC_EXTRABOLD = "Almarai-ExtraBold"

# Arabic block ranges that should trigger the Almarai path.
#   U+0600–U+06FF  Arabic
#   U+0750–U+077F  Arabic Supplement
#   U+08A0–U+08FF  Arabic Extended-A
#   U+FB50–U+FDFF  Arabic Presentation Forms-A
#   U+FE70–U+FEFF  Arabic Presentation Forms-B
_ARABIC_RE = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)

_arabic_fonts_ready: bool | None = None
# هل سُجّل وجه ExtraBold مع ReportLab؟ يُحسم مع أول استدعاء للتسجيل.
_arabic_extrabold_ready: bool = False
_arabic_text_image_cache: dict[tuple[Any, ...], tuple[bytes, int, int, dict]] = {}
_uploaded_background_reader_cache: dict[str, Any] = {}


def _ensure_arabic_fonts() -> bool:
    """Register the Arabic font family with ReportLab. Cached after the
    first call.

    يونيو 2026 — تَنقيح المالك: نُفَضّل **Cairo** (Google Fonts، SIL OFL)
    لِيُطابق font-family في الـCSS/SVG. الـfallback إلى Almarai-TTFs لو
    Cairo مَفقود (الـTTFs مَوجودة في الـrepo، fallback مَضمون).

    الأسماء المُسَجَّلة في ReportLab تَبقى PDF_FONT_ARABIC/...
    (للتَوافق مَع كل الكود السابق)، لكن الـTTF المُرتبط بها يُفضِّل
    Cairo. عناوين الـbrand 900/950 تُحَلّ إلى Cairo-Black."""
    global _arabic_fonts_ready, _arabic_extrabold_ready
    if _arabic_fonts_ready is not None:
        return _arabic_fonts_ready
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # تَرتيب التَفضيل: Cairo (مَنشود) → Almarai (fallback).
        # كل وجه يُسجَّل تَحت اسم PDF_FONT_ARABIC_* الثابت كي لا يَنكَسر
        # أيّ كود يَتوقّع الـAPI القَديم.
        regular_path = (_CAIRO_REGULAR_PATH
                         if os.path.isfile(_CAIRO_REGULAR_PATH)
                         else _ALMARAI_REGULAR_PATH)
        bold_path = (_CAIRO_BOLD_PATH
                      if os.path.isfile(_CAIRO_BOLD_PATH)
                      else _ALMARAI_BOLD_PATH)
        # Black/ExtraBold للعناوين 800+. Cairo-Black ≈ Almarai-ExtraBold.
        black_path = (_CAIRO_BLACK_PATH
                       if os.path.isfile(_CAIRO_BLACK_PATH)
                       else _ALMARAI_EXTRABOLD_PATH)

        if not (os.path.isfile(regular_path) and os.path.isfile(bold_path)):
            _arabic_fonts_ready = False
            return False
        pdfmetrics.registerFont(TTFont(PDF_FONT_ARABIC, regular_path))
        pdfmetrics.registerFont(TTFont(PDF_FONT_ARABIC_BOLD, bold_path))
        if os.path.isfile(black_path):
            try:
                pdfmetrics.registerFont(
                    TTFont(PDF_FONT_ARABIC_EXTRABOLD, black_path)
                )
                _arabic_extrabold_ready = True
            except Exception:  # pragma: no cover — corrupt file
                _arabic_extrabold_ready = False
        _arabic_fonts_ready = True
    except Exception:  # pragma: no cover — defensive
        _arabic_fonts_ready = False
    return _arabic_fonts_ready


def _has_arabic(text: str) -> bool:
    return bool(text) and bool(_ARABIC_RE.search(text))


def _text_direction(text: str, configured: str | None = None) -> str:
    """Return `rtl` or `ltr` for SVG/PDF text alignment.

    Template authors can force all card copy RTL/LTR, but the default is
    `auto`: Arabic strings align RTL while English, numbers, usernames,
    passwords, and QR payloads stay LTR.
    """
    direction = str(configured or "auto").strip().lower()
    if direction in {"rtl", "ltr"}:
        return direction
    return "rtl" if _has_arabic(text) else "ltr"


def _render_direction(
    configured: str | None,
    *,
    card_copy: str,
    credential_label_language: str,
) -> str:
    """Pick one of the four render engines: rtl/ltr x orientation.

    Orientation is handled by canvas size; this helper chooses the
    language half. RTL is not just text direction: the card composition
    is mirrored so Arabic text sits on the right and QR/barcode moves to
    the left, avoiding overlap.
    """
    direction = str(configured or "auto").strip().lower()
    if direction in {"rtl", "ltr"}:
        return direction
    if str(credential_label_language or "").lower() == "arabic":
        return "rtl"
    return "rtl" if _has_arabic(card_copy) else "ltr"


def _shape_arabic(text: str) -> str:
    """Apply arabic-reshaper + bidi so ReportLab can lay out RTL text.

    arabic-reshaper turns isolated Unicode letters into their proper
    initial/medial/final/isolated presentation forms. python-bidi then
    applies the Unicode Bidirectional Algorithm so the resulting
    glyphs end up in visual (RTL) order — which is what ReportLab
    will draw left-to-right but the human reads right-to-left.

    Falls back to the original text if either library fails, so a
    missing dependency at runtime never blows up the PDF export.

    Base direction is forced to RTL (`base_dir="R"`): card headings live on
    Arabic (RTL) engines, and a wrapped line may begin with a neutral or
    Latin token (e.g. "- واي فاي" or a digit). Without a fixed base, python
    -bidi auto-detects per line from the first strong char, which can flip
    such a line's order. Pinning RTL keeps every wrapped line in correct
    visual order — bidi is applied PER LINE here because the caller passes
    one already line-broken (logical) line at a time.
    """
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text), base_dir="R")
    except Exception:  # pragma: no cover — defensive
        return text


def _pick_pdf_font(text: str, *, weight: int = 400) -> str:
    """Choose the right font for a text run — weight-aware.

    Arabic strings need Almarai (shipped TTF). Pure Latin strings stay
    on Helvetica so existing receipts look identical to before. If
    Almarai isn't available for any reason we fall back to Helvetica
    — the Arabic glyphs won't render, but the PDF still opens.

    خريطة الأوزان (نفس ما يحلّه المتصفح في المعاينة الحية):
      وزن ≥ 800 → Almarai-ExtraBold (إن سُجّل) — عناوين البطاقة 900/950.
      وزن ≥ 600 → Almarai-Bold.
      غير ذلك   → Almarai العادي.
    Helvetica لا تملك وجهًا أثقل من Bold فيُحلّ ≥600 كله إلى
    Helvetica-Bold — نفس تدهور المتصفح عند غياب وجه أثقل.
    """
    bold = weight >= 600
    if _has_arabic(text) and _ensure_arabic_fonts():
        if weight >= 800 and _arabic_extrabold_ready:
            return PDF_FONT_ARABIC_EXTRABOLD
        return PDF_FONT_ARABIC_BOLD if bold else PDF_FONT_ARABIC
    return PDF_FONT_LATIN_BOLD if bold else PDF_FONT_LATIN


def _rgba_from_pdf_color(value: str, *, opacity: float = 1.0) -> tuple[int, int, int, int]:
    color = _pdf_color(value)
    alpha = max(0.0, min(1.0, opacity))
    return (
        int(max(0, min(255, round(color.red * 255)))),
        int(max(0, min(255, round(color.green * 255)))),
        int(max(0, min(255, round(color.blue * 255)))),
        int(round(alpha * 255)),
    )


_pil_raqm_available: bool | None = None


def _pil_supports_raqm() -> bool:
    """True when Pillow was built with libraqm (HarfBuzz text shaping).

    With Raqm, Pillow shapes Arabic from the ORIGINAL logical text using
    the font's own OpenType tables — exactly like a browser does — so we
    can draw with the shipped Cairo family and get glyphs identical to
    the live SVG preview. Official Pillow wheels bundle Raqm since 8.2,
    so this is the common case on both Windows and Linux installs.
    """
    global _pil_raqm_available
    if _pil_raqm_available is not None:
        return _pil_raqm_available
    try:
        from PIL import features

        _pil_raqm_available = bool(features.check("raqm"))
    except Exception:  # pragma: no cover — defensive
        _pil_raqm_available = False
    if not _pil_raqm_available:
        # تحذير يُسجَّل مرة واحدة فقط (الفحص نفسه مخزّن): بدون Raqm
        # يسقط تصدير الـPDF إلى مسار أشكال العرض القديمة المرسومة بخط
        # نظام (Tahoma/Arial/Noto) — فيختلف شكل النص العربي في الـPDF
        # عن خط المراعي الظاهر في المعاينة الحية. الحل: تثبيت عجلة
        # Pillow الرسمية (pip install --force-reinstall pillow) التي
        # تشحن Raqm منذ الإصدار 8.2. شغّل tools/check_font_pipeline.py
        # لرؤية ما سيبدو عليه نص الـPDF فعليًا على هذا الجهاز.
        try:
            import logging

            logging.getLogger(__name__).warning(
                "Pillow بدون دعم Raqm: نص الـPDF العربي سيُرسم بخط نظام "
                "(Tahoma/Arial) بدل خط المراعي الظاهر في المعاينة. "
                "ثبّت عجلة Pillow الرسمية لاستعادة التطابق، وشغّل "
                "python tools/check_font_pipeline.py للتشخيص."
            )
        except Exception:  # pragma: no cover — defensive
            pass
    return _pil_raqm_available


def _almarai_font_path(*, weight: int) -> str | None:
    """يعيد ملف خط المراعي (Almarai) المطابق للوزن المطلوب، أو None.

    المراعي هو خط البطاقات الأساسي الآن: المعاينة الحية تطلبه أولًا في
    سلسلة font-family، لذا يجب أن يرسم تصدير الـPDF بنفس الملفات حتى
    تتطابق الحروف حرفًا بحرف. الأوزان 400/700/800 موجودة كملفات TTF
    مشحونة؛ الأوزان 900/950 (عناوين البطاقة) تُحلّ إلى ExtraBold تمامًا
    كما يحلّها المتصفح عند غياب وزن أثقل. الخريطة الموحّدة في كل
    المسارات (نقطي + متجهي): ≥800 → ExtraBold، ≥600 → Bold، وإلا Regular.
    """
    if weight >= 800:
        for path in (_ALMARAI_EXTRABOLD_PATH, _ALMARAI_BOLD_PATH, _ALMARAI_REGULAR_PATH):
            if os.path.isfile(path):
                return path
    if weight >= 600:
        for path in (_ALMARAI_BOLD_PATH, _ALMARAI_EXTRABOLD_PATH, _ALMARAI_REGULAR_PATH):
            if os.path.isfile(path):
                return path
    for path in (_ALMARAI_REGULAR_PATH, _ALMARAI_BOLD_PATH, _ALMARAI_EXTRABOLD_PATH):
        if os.path.isfile(path):
            return path
    return None


def _cairo_font_path(*, weight: int) -> str | None:
    """Return the shipped Cairo TTF matching a CSS-ish weight, or None.

    The SVG preview asks for weights 900/950 on headings and labels, so
    the export maps >=900 to Cairo Black, >=700 to Cairo Bold and
    everything else to Cairo Regular — same resolution the browser does
    against the Google-Fonts Cairo faces.
    """
    if weight >= 900 and os.path.isfile(_CAIRO_BLACK_PATH):
        return _CAIRO_BLACK_PATH
    if weight >= 600:
        for path in (_CAIRO_BOLD_PATH, _CAIRO_BLACK_PATH):
            if os.path.isfile(path):
                return path
    for path in (_CAIRO_REGULAR_PATH, _CAIRO_BOLD_PATH, _CAIRO_BLACK_PATH):
        if os.path.isfile(path):
            return path
    return None


def _arabic_raster_font_path(*, weight: int) -> str | None:
    """مُختار خط مسار التصيير النقطي للعربية: القاهرة أولًا ثم المراعي.

    سلسلة font-family في معاينة SVG تبدأ بـ'Cairo' (بعد تعميم القاهرة)،
    فيُطابق التصدير/التصيير النقطي النفس الترتيب: القاهرة المشحونة أولًا،
    ثم المراعي كبديل — فلا يَختلف خطّ الـPDF/المعاينة عن خطّ المعاينة
    الحيّة (SVG). لا مراجع مكسورة (كلا الخطّين مشحون).
    """
    return _cairo_font_path(weight=weight) or _almarai_font_path(weight=weight)


# ─── تغطية المحارف (glyph coverage) ────────────────────────────────
# خط المراعي (وكذلك القاهرة) لا يحتوي رموز عملات مثل الشيكل ₪ (U+20AA)
# والليرة ₺ (U+20BA) — فكانت تظهر مربعات (tofu) في تصدير البطاقات منذ
# التحويل إلى المراعي. المتصفح في معاينة SVG يحل المشكلة تلقائيًا عبر
# سلسلة font-family (يسقط للرمز الناقص إلى Tahoma/Arial)، لكن مسار
# Pillow النقطي يرسم بخط واحد فقط. الحل هنا: قبل رسم أي نص نقطيًا
# نفحص أن كل محارفه موجودة في cmap الخط المختار؛ وإن نقص محرف نرسم
# النص كله بخط بديل يغطيه (نفس ما يفعله المتصفح بصريًا تقريبًا)،
# وإن لم يغطه أي خط متاح نستبدل رموز العملات المعروفة بنص مكافئ.

_font_codepoints_cache: dict[str, frozenset[int] | None] = {}

# محارف لا تحتاج غلافًا في cmap (مسافات/تحكم/فواصل عامة).
_COVERAGE_IGNORABLE = frozenset(
    {0x09, 0x0A, 0x0D, 0x20, 0xA0, 0x200C, 0x200D, 0x200E, 0x200F,
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}
)

# بدائل نصية أخيرة لرموز لا يغطيها أي خط متاح على الجهاز —
# تُستخدم فقط عند فشل كل الخطوط (الخيار ب): مربع tofu أسوأ من نص واضح.
_SYMBOL_TEXT_FALLBACKS = {
    "₪": "ILS",   # ₪ شيكل
    "₺": "TL",    # ₺ ليرة تركية
    "€": "EUR",   # € يورو
    "﷼": "ر.س",   # ﷼ ريال
    "✦": "*",     # ✦ نجمة زخرفية
}


def _font_codepoints(font_path: str) -> frozenset[int] | None:
    """يعيد مجموعة الكودبوينتات التي يغطيها ملف الخط (مخزّنة).

    يعتمد fontTools (موجودة ضمن تبعيات Pillow/reportlab عادة)؛ عند
    غيابها أو فشل قراءة الملف نعيد None = «لا نعرف» فلا نغيّر الخط —
    نفس السلوك القديم تمامًا (أمان للأنظمة الناقصة).
    """
    cached = _font_codepoints_cache.get(font_path)
    if cached is not None or font_path in _font_codepoints_cache:
        return cached
    result: frozenset[int] | None = None
    try:
        from fontTools.ttLib import TTFont as _FTFont

        ft = _FTFont(font_path, lazy=True, fontNumber=0)
        try:
            result = frozenset(ft.getBestCmap().keys())
        finally:
            ft.close()
    except Exception:  # pragma: no cover — fontTools غائبة أو ملف تالف
        result = None
    _font_codepoints_cache[font_path] = result
    return result


def _font_covers_text(font_path: str, text: str) -> bool:
    """True إن كان الخط يغطي كل محارف النص (أو تعذّر الفحص أصلًا)."""
    coverage = _font_codepoints(font_path)
    if coverage is None:
        return True  # لا نستطيع الفحص — نُبقي الخط كما هو
    return all(
        (cp in coverage)
        for cp in (ord(ch) for ch in text)
        if cp not in _COVERAGE_IGNORABLE
    )


def _missing_codepoints(font_path: str, text: str) -> set[int]:
    coverage = _font_codepoints(font_path)
    if coverage is None:
        return set()
    return {
        cp
        for cp in (ord(ch) for ch in text)
        if cp not in _COVERAGE_IGNORABLE and cp not in coverage
    }


def _symbol_fallback_font_candidates(*, weight: int) -> list[str]:
    """خطوط بديلة مرتّبة لرسم سطر فيه رمز ناقص من المراعي.

    الترتيب يطابق روح سلسلة font-family في معاينة SVG: القاهرة أولًا
    (شكلها أقرب للمراعي)، ثم خطوط النظام التي تجمع العربية مع رموز
    العملات (Tahoma/Arial/Segoe على ويندوز، Noto/DejaVu على لينكس).
    """
    bold = weight >= 600
    candidates = [
        _cairo_font_path(weight=weight),
        r"C:\Windows\Fonts\tahomabd.ttf" if bold else r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return [path for path in candidates if path and os.path.isfile(path)]


def _resolve_raster_font_for_text(
    text: str, primary_path: str, *, weight: int
) -> tuple[str, str]:
    """يختار (الخط، النص) الفعليين لرسم سطر نقطي بلا مربعات tofu.

    1) إن غطّى الخط الأساسي (المراعي عادة) كل المحارف → لا تغيير.
    2) وإلا نجرّب الخطوط البديلة بالترتيب ونرسم السطر كاملًا بأول خط
       يغطي كل محارفه — سطر كامل بخط واحد أبسط وأكثر اتساقًا بصريًا
       من خلط الخطوط داخل السطر الواحد.
    3) إن لم يغطِّ أي خط كل المحارف نستبدل رموز العملات/الزخارف
       المعروفة بنص مكافئ ونعيد المحاولة — وإلا نُبقي الأصل (أسوأ
       الحالات = السلوك القديم حرفيًا).
    """
    if _font_covers_text(primary_path, text):
        return primary_path, text
    for candidate in _symbol_fallback_font_candidates(weight=weight):
        if _font_covers_text(candidate, text):
            return candidate, text
    substituted = "".join(
        _SYMBOL_TEXT_FALLBACKS.get(ch, ch) for ch in text
    )
    if substituted != text:
        if _font_covers_text(primary_path, substituted):
            return primary_path, substituted
        for candidate in _symbol_fallback_font_candidates(weight=weight):
            if _font_covers_text(candidate, substituted):
                return candidate, substituted
        return primary_path, substituted
    return primary_path, text


# محارف فوق U+00FF تغطيها Helvetica المدمجة فعلًا (ترميز WinAnsi):
# نقطة التعداد • وعلامات الاقتباس والشرطات و… واليورو €. هذه تبقى على
# المسار المتجهي القديم حرفيًا حتى لا يتغير شكل كلمات المرور المقنّعة
# (••••••) ولا محاذاتها.
_WINANSI_EXTRA = frozenset(
    {0x20AC, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E,
     0x2013, 0x2014, 0x2020, 0x2021, 0x2022, 0x2026, 0x2030,
     0x2039, 0x203A, 0x02C6, 0x02DC, 0x0152, 0x0153, 0x0160,
     0x0161, 0x0178, 0x017D, 0x017E, 0x0192, 0x2122}
)


# محارف خارج تغطية Helvetica المدمجة في ReportLab (WinAnsi):
# رموز عملات مثل ₪/₺ والعربية والزخارف يجب أن تمر عبر مسار الصورة
# النقطية وإلا ظهرت مربعات سوداء في PDF.
def _needs_raster_text(text: str) -> bool:
    if not text:
        return False
    if _has_arabic(text):
        return True
    return any(
        ord(ch) > 0xFF and ord(ch) not in _WINANSI_EXTRA for ch in text
    )


def _font_path_for_arabic(*, bold: bool) -> str:
    """Pick a fallback font that can draw Arabic presentation-form glyphs.

    Used only when Pillow has NO Raqm support: the raster path then
    receives text after arabic-reshaper converts it to Unicode
    presentation forms (U+FBxx/U+FExx). Cairo/Almarai/Tajawal — like
    most modern Google fonts — do not map those legacy codepoints, so
    without Raqm we must fall back to system fonts that do (Tahoma,
    Arial, Noto Naskh, DejaVu). Prefer those, then bundled Almarai so
    exports still work on minimal installs.
    """
    candidates = [
        # Windows dev/customer machines.
        r"C:\Windows\Fonts\tahomabd.ttf" if bold else r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arabtype.ttf",
        r"C:\Windows\Fonts\trado.ttf",
        # Common Linux VPS font packages.
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return _ALMARAI_BOLD_PATH if bold and os.path.isfile(_ALMARAI_BOLD_PATH) else _ALMARAI_REGULAR_PATH


def _arabic_run_bbox(font, text: str, *, use_raqm: bool, direction: str):
    """Measure a text run, letting Raqm shape it when available."""
    if use_raqm:
        try:
            return font.getbbox(
                text,
                direction="rtl" if direction == "rtl" else "ltr",
                language="ar",
            )
        except Exception:  # pragma: no cover — Raqm probing safety
            pass
    return font.getbbox(text)


def _fit_arabic_raw_text(
    raw_text: str,
    *,
    font,
    max_width: int,
    use_raqm: bool = False,
    direction: str = "rtl",
) -> str:
    """Return raw Arabic text that fits after shaping.

    We trim before shaping because the PDF raster path draws the shaped
    visual string into a fixed canvas. This mirrors `_shrink_to_fit`
    for vector text but avoids cutting glyphs mid-image. On the Raqm
    path the font itself shapes the logical text, so no reshaping pass
    is needed for measurement.
    """
    if max_width <= 0:
        return raw_text
    text = raw_text
    ellipsis = "…"
    while text:
        probe = text if use_raqm else _shape_arabic(text)
        bbox = _arabic_run_bbox(font, probe, use_raqm=use_raqm, direction=direction)
        if (bbox[2] - bbox[0]) <= max_width:
            return text
        text = text[:-1]
    probe = ellipsis if use_raqm else _shape_arabic(ellipsis)
    bbox = _arabic_run_bbox(font, probe, use_raqm=use_raqm, direction=direction)
    return ellipsis if (bbox[2] - bbox[0]) <= max_width else ""


def _build_arabic_text_image(
    raw_text: str,
    *,
    size: float,
    color: str,
    weight: int = 700,
    max_width: float = 0,
    direction: str = "rtl",
    opacity: float = 1.0,
) -> tuple[bytes, int, int, dict] | None:
    """Rasterize an Arabic text run to a transparent PNG.

    ReportLab can embed the Almarai font, but PDF viewers still vary in
    Arabic shaping/bidi behavior for mixed RTL text. Rendering the
    shaped run into a tiny transparent image makes the exported card
    behave like the live preview screenshot: letters stay connected,
    glyph order is stable, and the whole text block scales uniformly
    with the card form.

    Font selection (preview/export convergence):
      - Pillow built with Raqm (the default in official wheels) →
        draw the ORIGINAL logical text with the shipped Cairo family,
        letting Raqm/HarfBuzz shape it through Cairo's OpenType tables.
        That is exactly what the browser does for the live SVG preview,
        so the exported glyphs match the preview.
      - No Raqm → legacy path: arabic-reshaper presentation forms drawn
        with a system font that maps those legacy codepoints (Tahoma /
        Arial / Noto Naskh / DejaVu), falling back to bundled Almarai.
    """
    # يُستدعى أيضًا لنصوص لاتينية تحمل رموزًا خارج تغطية Helvetica
    # (مثل سعر "₪ 1.50" بلا حرف عربي) — لذا الشرط هو «نص يحتاج
    # تصييرًا نقطيًا» وليس «نص عربي» فقط.
    if not raw_text or not _needs_raster_text(raw_text):
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # pragma: no cover - optional dependency safety
        return None
    use_raqm = False
    font_path = None
    if _pil_supports_raqm():
        # القاهرة أولًا (نفس ترتيب سلسلة الخطوط في معاينة SVG) ثم المراعي.
        font_path = _arabic_raster_font_path(weight=int(weight))
        use_raqm = font_path is not None
    if font_path is None:
        # مسار ما-قبل-Raqm: نرسم صور العرض المُعاد تشكيلها (presentation
        # forms) فيلزم خطّ يحويها. القاهرة (مثل أغلب خطوط OpenType الحديثة)
        # لا تَشحن كتلة Presentation-Forms-B في cmap — تُعتمد على تشكيل
        # HarfBuzz/Raqm — فيُسقط بعض الحروف لو رُسمت بها هنا. لذا نَستخدم
        # خطًّا يملك تلك الأشكال (نظام/المراعي). المعاينة الحيّة (SVG) تُعالج
        # هذا بـper-glyph fallback في المتصفّح (Cairo→Almarai).
        font_path = _font_path_for_arabic(bold=weight >= 600)
    if not font_path or not os.path.isfile(font_path):
        return None
    # فحص تغطية المحارف: المراعي لا يحوي ₪/₺ وغيرها فتُرسم مربعات.
    # عند نقص أي محرف نرسم السطر كاملًا بخط بديل يغطيه (القاهرة ثم
    # خطوط النظام)، وكحل أخير نستبدل الرمز بنص مكافئ — لا tofu أبدًا.
    font_path, raw_text = _resolve_raster_font_for_text(
        raw_text, font_path, weight=int(weight)
    )
    if not raw_text:
        return None
    font_size = max(1, int(round(size)))
    box_width = int(math.ceil(max_width)) if max_width and max_width > 0 else 0
    direction = "rtl" if direction == "rtl" else "ltr"
    cache_key = (
        raw_text,
        font_size,
        color,
        int(weight),
        box_width,
        direction,
        round(max(0.0, min(1.0, opacity)), 3),
        os.path.basename(font_path),
        use_raqm,
    )
    cached = _arabic_text_image_cache.get(cache_key)
    if cached:
        return cached
    try:
        if use_raqm:
            font = ImageFont.truetype(font_path, font_size)
        else:
            # The legacy path feeds PRE-shaped presentation forms; the
            # basic layout engine must draw them verbatim. (If Raqm is
            # compiled in but we chose the legacy path, Raqm's default
            # layout would re-apply bidi and mirror the string.)
            try:
                layout = ImageFont.Layout.BASIC
            except AttributeError:  # Pillow < 9.1
                layout = ImageFont.LAYOUT_BASIC
            font = ImageFont.truetype(font_path, font_size, layout_engine=layout)
    except Exception:  # pragma: no cover - corrupt font safety
        return None

    available_width = max(1, box_width - max(2, int(font_size * 0.16))) if box_width else 0
    fitted_raw = (
        _fit_arabic_raw_text(
            raw_text,
            font=font,
            max_width=available_width,
            use_raqm=use_raqm,
            direction=direction,
        )
        if available_width
        else raw_text
    )
    if use_raqm:
        # Raqm shapes + bidi-reorders the logical string itself.
        shaped = fitted_raw
    else:
        shaped = _shape_arabic(fitted_raw)
    if not shaped:
        return None

    bbox = _arabic_run_bbox(font, shaped, use_raqm=use_raqm, direction=direction)
    text_w = max(1, int(math.ceil(bbox[2] - bbox[0])))
    text_h = max(1, int(math.ceil(bbox[3] - bbox[1])))
    # حشوة صغيرة فقط لحماية حواف الحروف من القص عند التنعيم (AA) —
    # تُعاد قيمها للمستدعي كي يلغيها عند وضع الصورة على الصفحة، فيقع
    # أصل التخطيط (قمة الصاعد) في نفس نقطة معاينة SVG حرفيًا. سابقًا
    # كانت الحشوة + التوسيط العمودي داخل صندوق 1.35×الحجم تزيح النص
    # العربي المُصدَّر ~10–14px عن موضعه في المعاينة (خلل المطابقة).
    pad_x = max(2, int(math.ceil(font_size * 0.12)))
    pad_y = max(2, int(math.ceil(font_size * 0.22)))
    width = max(box_width, text_w + pad_x * 2)
    height = max(int(math.ceil(font_size * 1.35)), text_h + pad_y * 2)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_x = width - pad_x - text_w if direction == "rtl" else pad_x
    draw_y = (height - text_h) / 2 - bbox[1]
    fill = _rgba_from_pdf_color(color, opacity=opacity)
    if use_raqm:
        try:
            draw.text((draw_x, draw_y), shaped, font=font, fill=fill,
                      direction=direction, language="ar")
        except Exception:  # pragma: no cover — Raqm runtime safety
            draw.text((draw_x, draw_y), _shape_arabic(shaped), font=font, fill=fill)
    else:
        draw.text((draw_x, draw_y), shaped, font=font, fill=fill)
    buf = BytesIO()
    image.save(buf, format="PNG")
    try:
        ascent_px = float(font.getmetrics()[0])
    except Exception:  # pragma: no cover — defensive
        ascent_px = font_size * 0.78
    # origin_x/origin_y: موضع أصل تخطيط PIL (يسار النص، قمة الصاعد)
    # داخل الصورة — بهما يضع محوّل PDF النص في نفس إحداثيات SVG تمامًا.
    result = (
        buf.getvalue(), width, height,
        {
            "origin_x": float(draw_x),
            "origin_y": float(draw_y),
            "text_w": float(text_w),
            "ascent": ascent_px,
            "font_size": float(font_size),
        },
    )
    # Keep the cache bounded; export jobs may process hundreds of cards.
    if len(_arabic_text_image_cache) > 512:
        _arabic_text_image_cache.clear()
    _arabic_text_image_cache[cache_key] = result
    return result


def _pdf_draw_arabic_text_image(
    pdf,
    raw_text: str,
    *,
    x: float,
    y: float,
    size: float,
    color: str,
    weight: int,
    max_width: float,
    direction: str,
    opacity: float,
    ch: float,
    anchor: str = "top",
    halign: str = "auto",
) -> bool:
    """يرسم سطر النص النقطي بحيث يطابق موضعه معاينة SVG حرفيًا.

    anchor="top"   : y = قمة الصاعد (نفس dominant-baseline="hanging"
                     في SVG) — للعناوين/الميتا/الفوتر.
    anchor="middle": y = منتصف النص بصريًا (نفس dominant-baseline=
                     "middle") — لتسميات/قيم شرائط اليوزر والباس.

    أفقيًا: rtl ← الحافة اليمنى للنص عند x+max_width (نفس
    text-anchor="end")، وltr ← يسار النص عند x (نفس "start").
    التعويضات origin_x/origin_y الراجعة من باني الصورة تلغي الحشوة
    الداخلية، فلا انزياح ~10px كما في السابق.
    """
    rendered = _build_arabic_text_image(
        raw_text,
        size=size,
        color=color,
        weight=weight,
        max_width=max_width,
        direction=direction,
        opacity=opacity,
    )
    if not rendered:
        return False
    from reportlab.lib.utils import ImageReader

    png_bytes, width, height, meta = rendered
    origin_x = float(meta.get("origin_x") or 0.0)
    origin_y = float(meta.get("origin_y") or 0.0)
    text_w = float(meta.get("text_w") or width)
    ascent = float(meta.get("ascent") or size * 0.78)

    # ── أفقيًا ──
    if halign == "center" and max_width and max_width > 0:
        # وسط الحبر عند x + max_width/2 (مطابق لمرساة "middle").
        image_left = (x + (max_width - text_w) / 2.0) - origin_x
    elif direction == "rtl" and max_width and max_width > 0:
        # الحافة اليمنى للحبر عند x+max_width (مطابق لمرساة "end").
        image_left = (x + max_width) - (origin_x + text_w)
    else:
        # يسار النص عند x (مطابق لمرساة "start").
        image_left = x - origin_x

    # ── عموديًا ──
    if anchor == "middle":
        # منتصف SVG ≈ خط القاعدة − 0.26×الحجم (نصف ارتفاع x تقريبًا
        # كما تفسره المتصفحات وresvg). خط القاعدة داخل الصورة عند
        # origin_y + ascent.
        baseline_target = y + size * 0.26
        image_top = baseline_target - (origin_y + ascent)
    else:
        # "top" = dominant-baseline="hanging" في SVG: المتصفح (وresvg)
        # يضع «خط التعليق» عند y، وخط التعليق يقع عند 0.8×الصاعد فوق
        # خط القاعدة (قاعدة CSS عند غياب جدول BASE في الخط — حال
        # المراعي/القاهرة). إذًا خط القاعدة المستهدف = y + 0.8×الصاعد،
        # وداخل صورة PIL يقع خط القاعدة عند origin_y + الصاعد — أي أن
        # قمة الصورة = y − origin_y − 0.2×الصاعد. (التقويم القديم وضع
        # قمة الصاعد عند y فنزل النص ~0.2×الصاعد ≈ 9–14px عن المعاينة.)
        baseline_target = y + 0.8 * ascent
        image_top = baseline_target - (origin_y + ascent)

    pdf_y = ch - image_top - height
    pdf.drawImage(
        ImageReader(BytesIO(png_bytes)),
        image_left,
        pdf_y,
        width=width,
        height=height,
        mask="auto",
    )
    return True

# Default element placements as fractions of the canvas. They mirror the
# percentage positions used by the live preview's `.pr-card-preview`
# CSS so a freshly-created template (which has not been dragged) looks
# identical in preview and PDF.
# Bottom safe area for print: the footer/tagline glyph box — including
# descenders — must stay ABOVE this fraction of the card height so it never
# sits flush to the bottom edge nor gets clipped by the cutter's bleed.
# `_TEXT_FULL_DESCENT` is the glyph box height (cap-top → descender-bottom)
# as a multiple of the font size; Cairo has tall descenders, so we keep a
# conservative factor. These drive the footer clamp in build_card_render_model.
_CARD_SAFE_BOTTOM = 0.94
_TEXT_FULL_DESCENT = 1.2

_DEFAULT_POSITIONS: dict[str, dict[str, float]] = {
    "accent":   {"x": 0.05, "y": 0.07, "width": 0.90, "height": 0.018},
    "brand":    {"x": 0.06, "y": 0.20, "size": 0.075},
    "title":    {"x": 0.06, "y": 0.33, "size": 0.105},
    "user":     {"x": 0.06, "y": 0.50, "width": 0.46, "height": 0.13},
    "pass":     {"x": 0.06, "y": 0.66, "width": 0.46, "height": 0.13},
    "qr":       {"x": 0.66, "y": 0.36, "size": 0.27},
    # meta + footer lifted off the bottom edge: footer used to sit at y=0.95
    # (glyph bottom ≈ 0.99·H → flush/clipped). Now footer leaves ≥6% bottom
    # clearance and meta is raised to keep a clean gap above it.
    "meta":     {"x": 0.06, "y": 0.80, "size": 0.05},
    "footer":   {"x": 0.06, "y": 0.875, "size": 0.045},
}

_ENGINE_PROFILES: dict[str, dict[str, str]] = {
    "en_horizontal": {
        "orientation": "horizontal",
        "direction": "ltr",
        "credential_label_language": "english",
    },
    "en_vertical": {
        "orientation": "vertical",
        "direction": "ltr",
        "credential_label_language": "english",
    },
    "ar_horizontal": {
        "orientation": "horizontal",
        "direction": "rtl",
        "credential_label_language": "arabic",
    },
    "ar_vertical": {
        "orientation": "vertical",
        "direction": "rtl",
        "credential_label_language": "arabic",
    },
}

# Default show-flags. Mirror the same defaults the operations.py
# layout normaliser uses so a template that never set these explicitly
# still renders the expected elements.
_DEFAULT_SHOW = {
    "brand":    True,
    "title":    True,
    "username": True,
    "password": True,
    "qr":       True,
    "price":    False,
    "hotspot":  True,
    "validity": True,
    "serial":   True,
}


def normalize_render_engine(value: Any = None, layout: dict | None = None) -> str:
    """Return one of the four explicit card SVG engines.

    New templates store `render_engine` directly. Older templates are
    derived from their existing `card_orientation`, `text_direction`,
    and `credential_label_language` fields so compatibility is kept.
    """
    raw = str(value or "").strip().lower()
    if raw in _ENGINE_PROFILES:
        return raw
    layout = layout or {}
    orientation = str(layout.get("card_orientation") or "horizontal").strip().lower()
    orientation = "vertical" if orientation == "vertical" else "horizontal"
    direction = str(layout.get("text_direction") or "").strip().lower()
    label_language = str(layout.get("credential_label_language") or "").strip().lower()
    language = "ar" if direction == "rtl" or label_language == "arabic" else "en"
    return f"{language}_{orientation}"


# ───────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────

def build_card_render_model(
    template: dict,
    card: dict | object | None = None,
    *,
    overrides: dict | None = None,
) -> dict:
    """Build a normalized render model for one card.

    Parameters
    ----------
    template : dict
        A row from `card_print_templates` (or its dict equivalent). The
        renderer reads `layout_json`, `orientation`, `cards_per_row`,
        `cards_per_column`, plus legacy `username_x/y`, `password_x/y`,
        `qr_x/y` if present.
    card : dict or object or None
        Per-card data. Accepts a dict with `username`, `password`, `id`
        keys OR a `Card` dataclass instance. None produces a generic
        SAMPLE card suitable for "PDF عينة" or designer mock-ups.
    overrides : dict, optional
        Text overrides from the export-room override fields:
        brand_name, card_title, footer_text, hotspot_address,
        price_text, validity_text. These win over the template defaults
        but never replace per-card values like username or password.

    Returns
    -------
    dict
        The render model. See module docstring for shape.
    """
    layout = _hydrate_layout(template)
    overrides = overrides or {}

    engine = normalize_render_engine(layout.get("render_engine"), layout)
    profile = _ENGINE_PROFILES[engine]
    orient = profile["orientation"]
    render_direction = profile["direction"]
    credential_label_language = profile["credential_label_language"]
    canvas_w, canvas_h = CANVAS_PORTRAIT if orient == "vertical" else CANVAS_LANDSCAPE

    show = _resolve_show_flags(layout)

    # ── Text + meta ──
    brand_text   = _override(overrides, "brand_name",   layout, "HobeRadius")
    title_text   = _override(overrides, "card_title",   layout, "بطاقة إنترنت")
    footer_text  = _override(overrides, "footer_text",  layout, "")
    hotspot_text = _override(overrides, "hotspot_address", layout, "")
    price_text   = _override(overrides, "price_text",   layout, "")
    validity_txt = _override(overrides, "validity_text", layout, "")

    positions = _resolve_positions(template, layout, (canvas_w, canvas_h), render_direction=render_direction)

    text_color    = _safe_hex(layout.get("text_color"), "#ffffff")
    accent_color  = _safe_hex(layout.get("accent_color"), "#f59e0b")
    surface_color = _safe_hex(layout.get("surface_color"), "#e8f7fb")
    credential_ink = _safe_hex(layout.get("credential_text_color"), "#0f172a")
    credential_label_color = _safe_hex(layout.get("credential_label_color"), "#64748b")
    username_surface = _safe_hex(layout.get("username_surface_color"), surface_color)
    password_surface = _safe_hex(layout.get("password_surface_color"), surface_color)
    # Data-strip / pill transparency. The designer can dial the strips
    # back so the gradient shows through; default to the historical 0.95
    # so existing templates are unchanged.
    surface_opacity = max(0.0, min(1.0, _float(layout.get("surface_opacity"), 0.95)))
    credentials_surface_default = _boolish(layout.get("credential_background_enabled"), True)
    username_surface_enabled = _boolish(layout.get("username_surface_enabled"), credentials_surface_default)
    password_surface_enabled = _boolish(layout.get("password_surface_enabled"), credentials_surface_default)
    username_font_size = _optional_positive_float(layout.get("username_font_size"))
    password_font_size = _optional_positive_float(layout.get("password_font_size"))
    label_font_size = _optional_positive_float(layout.get("credential_label_font_size"))
    # مقاسات الخط بالنقاط الطباعية (طلب المالك: «مثل الوورد — 12، 14»):
    # عند font_size_unit='pt' القيم المخزّنة نقاط حقيقية — تُحوَّل لوحدات
    # الكانفس بمعامل (عرض الكانفس ÷ عرض البطاقة بالنقاط) فيطبع الخط
    # بحجمه الفعلي عند طباعة البطاقة بمقاسها المليمتري. القوالب القديمة
    # (بلا العلم) تبقى بوحدات الكانفس حرفيًا — لا يتغير رندرها.
    if str(layout.get("font_size_unit") or "").strip().lower() == "pt":
        _card_w_mm, _ = card_mm_box(layout, (canvas_w, canvas_h))
        _pt_factor = canvas_w / max(_card_w_mm * 72.0 / 25.4, 1.0)
        if username_font_size:
            username_font_size = username_font_size * _pt_factor
        if password_font_size:
            password_font_size = password_font_size * _pt_factor
        if label_font_size:
            label_font_size = label_font_size * _pt_factor
    qr_color = _safe_hex(layout.get("qr_color"), "#0f172a")
    qr_background_color = _safe_hex(layout.get("qr_background_color"), "#ffffff")
    # نمط رمز QR من المصمم: boxed = مربعات حادة فوق لوحة بيضاء (السلوك
    # التاريخي)، rounded = «ناعم» وحدات دائرية/منحنية، clean = «بسيط»
    # بدون لوحة/إطار خلف الرمز (الوحدات فقط فوق الخلفية).
    qr_style = _normalize_qr_style(layout.get("qr_style"))

    username, password, card_id = _extract_card_fields(card)
    uploaded_design = _is_uploaded_design(layout)

    elements: list[dict] = []

    # ── خَلفيّة نَمطيّة قِطاعيّة (تَنقيح يونيو 2026، طَلب المالك) ──
    # نَمط SVG قابل للتَكرار من ~6 motifs خَطّيّة دَقيقة لكل قِطاع (cafe
    # = كوب ذَهاب + فُنجان + حُبوب + مِلعقة + سُكّر + ورقة + إبريق…)،
    # يُغطّي الكَنفاس كاملاً بشَفافيّة هَامِسة. مَنظومة motifs واحدة
    # تَتَكَرّر تلقائيًّا عبر patternUnits="userSpaceOnUse" — تَعريف
    # واحد، خَلفيّة كاملة، حَجم تَخزينيّ ضَئيل.
    # default opacity 0.06 (نَمط مُتعَدّد العَناصر يَتحَمّل opacity أعلى
    # من single-shape watermark السابق دون إيذاء القَراءة).
    if not uploaded_design and _boolish(layout.get("watermark_enabled"), True):
        from . import card_motif_patterns, card_motifs as _cm
        # نَستنتج vertical من icon الـpreset (cafe_mocha icon="coffee"
        # → vertical "cafe") عبر inverse map.
        icon_key = str(layout.get("icon") or "wifi").strip().lower()
        vertical_hint: str | None = None
        if icon_key in card_motif_patterns.VERTICAL_SETS:
            vertical_hint = icon_key  # icon == vertical (مثل "cafe")
        else:
            for vk, motif_key in _cm.VERTICAL_TO_MOTIF.items():
                if motif_key == icon_key:
                    vertical_hint = vk
                    break
        vertical_hint = vertical_hint or "generic"
        wm_opacity = max(0.0, min(0.40,
            _float(layout.get("watermark_opacity"), 0.30)))
        if wm_opacity > 0:
            elements.append({
                "kind": "pattern_bg",
                "id": "pattern_bg",
                "vertical": vertical_hint,
                "color": text_color,
                "opacity": wm_opacity,
                "canvas_w": canvas_w,
                "canvas_h": canvas_h,
            })

    # Accent bar — first so it sits beneath the text but above the bg.
    acc = positions["accent"]
    if not uploaded_design:
        elements.append({
            "kind": "rect",
            "id": "accent",
            "x": acc["x"] * canvas_w,
            "y": acc["y"] * canvas_h,
            "width":  acc["width"]  * canvas_w,
            "height": acc["height"] * canvas_h,
            "fill": accent_color,
            "rx": (acc["height"] * canvas_h) / 2,
        })

    # عَرض heading (brand/title) — قَدْره نِسبيّ لعَرض الكَنفاس. يونيو 2026:
    # ضَيَّقناه عندما يَكون الـQR ظاهرًا كي لا يَتداخل النَصّ بصريًّا مع الـQR
    # (انحدار حَيّ بَلَّغ به المالك: «دخول الإنترنت» يُغطّي الـQR). الـQR
    # يَأخذ ~30% من العَرض على الجانب المُقابل؛ نَترك فَجوة 4% فيَبقى للنَصّ
    # ~60% (LTR) أو يَمتدّ من اليَمين حتّى حُدود الـQR (RTL).
    if show["qr"]:
        heading_width = 0.60 if orient == "vertical" else 0.55
    else:
        heading_width = 0.78 if orient == "vertical" else 0.86
    # Meta/footer span (also their max_width below).
    meta_footer_width = 0.80 if orient == "vertical" else 0.88

    # Heading/text horizontal placement by orientation & direction:
    #   • VERTICAL (both AR & EN): CENTRED — box at the card centre, text
    #     anchored "middle" (owner request, June 2026).
    #   • HORIZONTAL RTL: right-aligned to the right margin (Arabic reads from
    #     the right).  • HORIZONTAL LTR: left-aligned (default).
    is_vertical = orient == "vertical"
    text_margin = 0.07 if is_vertical else 0.06

    def _rtl_text_x(width_frac):
        if render_direction != "rtl":
            return None
        return max(0.0, 1.0 - text_margin - width_frac) * canvas_w

    def _heading_x_align(width_frac, default_x_frac):
        """Return (x_px, align) for a text box of width_frac."""
        if is_vertical:
            return ((1.0 - width_frac) / 2.0) * canvas_w, "center"
        if render_direction == "rtl":
            return max(0.0, 1.0 - text_margin - width_frac) * canvas_w, "end"
        return default_x_frac * canvas_w, "start"

    if not uploaded_design and show["brand"] and brand_text:
        brand_pos = positions["brand"]
        brand_base = brand_pos["size"] * canvas_h
        # Brand stays one line — shrink-to-fit only (allow_wrap=False).
        brand_size, _bl = _fit_heading(
            brand_text, brand_base, canvas_w * heading_width,
            weight=900, direction=render_direction,
            min_size_px=brand_base * 0.55, allow_wrap=False)
        brand_x_px, brand_align = _heading_x_align(heading_width, brand_pos["x"])
        brand_left_px = brand_x_px
        elements.append(_text_element(
            id="brand", text=brand_text, pos=brand_pos,
            canvas=(canvas_w, canvas_h), color=text_color, weight=900,
            max_width_frac=heading_width, direction=render_direction,
            size_px=brand_size, x_px=brand_x_px, align=brand_align,
        ))
        # رَمز قِطاعيّ صَغير بِجانب الـbrand — اختياريّ، *مَوقوف افتراضيًّا*
        # (تَنقيح المالك يونيو 2026: «دفش ومبالغ فيه»). يُفعَّل من
        # المُصمِّم بـbrand_icon_enabled=true لمن يُريد الإضافة.
        if _boolish(layout.get("brand_icon_enabled"), False):
            brand_pos = positions["brand"]
            brand_size_px = brand_pos["size"] * canvas_h
            icon_motif = str(layout.get("icon") or "wifi").strip() or "wifi"
            icon_size = brand_size_px * 1.30
            if render_direction == "rtl":
                # النَصّ يَنتهي (يَمينًا) عند brand_left + max_width. الرَمز
                # يَجلس يَمين النَصّ (بداية القراءة RTL) بمَسافة آمنة.
                icon_cx = brand_left_px \
                           + (heading_width * canvas_w) + icon_size * 0.20
                icon_cx = min(icon_cx, canvas_w - icon_size * 0.55)
            else:
                icon_cx = (brand_pos["x"] * canvas_w) - icon_size * 0.20
                icon_cx = max(icon_cx, icon_size * 0.55)
            icon_cy = (brand_pos["y"] * canvas_h) + brand_size_px * 0.45
            elements.append({
                "kind": "icon",
                "id": "brand_icon",
                "motif": icon_motif,
                "cx": icon_cx, "cy": icon_cy, "size": icon_size,
                "color": text_color,
                "opacity": 0.95,
            })

    if not uploaded_design and show["title"] and title_text:
        title_pos = positions["title"]
        title_base = title_pos["size"] * canvas_h
        title_y_px = title_pos["y"] * canvas_h
        # Vertical room before the next stacked element (credential pill) so a
        # 2-line title never collides with what's below it.
        below_y_px = positions.get("user", {}).get("y", 0.5) * canvas_h
        avail_v = max(0.0, below_y_px - title_y_px - canvas_h * 0.02)
        two_cap = (avail_v / (2 * _HEADING_LINE_STEP)) if avail_v else None
        title_size, title_lines = _fit_heading(
            title_text, title_base, canvas_w * heading_width,
            weight=950, direction=render_direction,
            min_size_px=title_base * 0.62, allow_wrap=True,
            two_line_size_cap=two_cap)
        step = title_size * _HEADING_LINE_STEP
        title_x_px, title_align = _heading_x_align(heading_width, title_pos["x"])
        for i, line in enumerate(title_lines):
            elements.append(_text_element(
                id="title" if i == 0 else f"title{i + 1}", text=line,
                pos=title_pos, canvas=(canvas_w, canvas_h),
                color=text_color, weight=950, max_width_frac=heading_width,
                direction=render_direction, size_px=title_size,
                y_px=title_y_px + i * step, x_px=title_x_px, align=title_align,
            ))

    if show["username"] and username:
        elements.append(_pill_element(
            id="user", label=_credential_label("user", credential_label_language), value=username,
            pos=positions["user"], canvas=(canvas_w, canvas_h),
            surface_color=username_surface,
            surface_enabled=username_surface_enabled,
            surface_opacity=surface_opacity,
            ink=credential_ink,
            label_color=credential_label_color,
            value_font_size=username_font_size,
            label_font_size=label_font_size,
            label_direction="rtl" if credential_label_language == "arabic" else "ltr",
            show_label=not uploaded_design,
            align="center" if is_vertical else None,
        ))

    if show["password"] and password:
        elements.append(_pill_element(
            id="pass", label=_credential_label("pass", credential_label_language), value=password,
            pos=positions["pass"], canvas=(canvas_w, canvas_h),
            surface_color=password_surface,
            surface_enabled=password_surface_enabled,
            surface_opacity=surface_opacity,
            ink=credential_ink,
            label_color=credential_label_color,
            value_font_size=password_font_size,
            label_font_size=label_font_size,
            is_password=True,
            label_direction="rtl" if credential_label_language == "arabic" else "ltr",
            show_label=not uploaded_design,
            align="center" if is_vertical else None,
        ))

    if show["qr"]:
        qr = positions["qr"]
        # رمز QR يجب أن يحترم حقول «الاستبدال» القادمة من غرفة الطباعة
        # تمامًا كما تحترمها النصوص الظاهرة: المستخدم يكتب عنوان البوابة
        # (hotspot_address) أو رابط الدخول التلقائي (hotspot_login_url)
        # في غرفة التصدير، فيجب أن يدخل العنوان في رابط QR نفسه — وإلا
        # عملت المعاينة في غرفة التصميم وفشل التصدير (الخلل المُبلَّغ).
        qr_layout = dict(layout)
        for qr_key in ("hotspot_login_url", "hotspot_address"):
            qr_override = str(overrides.get(qr_key) or "").strip()
            if qr_override:
                qr_layout[qr_key] = qr_override
        payload = _qr_login_payload(qr_layout, username, password, card_id)
        elements.append({
            "kind": "qr",
            "id": "qr",
            "payload": payload,
            "x": qr["x"] * canvas_w,
            "y": qr["y"] * canvas_h,
            "size": qr["size"] * canvas_w,
            "bg": qr_background_color,
            "fg": qr_color,
            # يمرَّر النمط لمحوّلي SVG وPDF معًا فيتطابق الشكل حرفيًا.
            "style": qr_style,
        })

    # Meta line: hotspot · price · validity · #serial
    meta_parts: list[str] = []
    if show["hotspot"]  and hotspot_text: meta_parts.append(hotspot_text)
    if show["price"]    and price_text:   meta_parts.append(price_text)
    if show["validity"] and validity_txt: meta_parts.append(validity_txt)
    if show["serial"]   and card_id:      meta_parts.append("#" + str(card_id))
    if not uploaded_design and meta_parts:
        meta_pos = positions["meta"]
        meta_x, meta_align = _heading_x_align(meta_footer_width, meta_pos["x"])
        meta_el = {
            "kind": "text",
            "id": "meta",
            "text": "  ·  ".join(meta_parts),
            "x": meta_x,
            "y": meta_pos["y"] * canvas_h,
            "size": meta_pos["size"] * canvas_h,
            "color": text_color,
            "weight": 800,
            "max_width": canvas_w * meta_footer_width,
            "direction": render_direction,
        }
        if meta_align == "center":
            meta_el["align"] = "center"
        elements.append(meta_el)

    if not uploaded_design and footer_text:
        footer_pos = positions["footer"]
        footer_size = footer_pos["size"] * canvas_h
        footer_y = footer_pos["y"] * canvas_h
        # Safety clamp: keep the tagline's full glyph box (descenders included)
        # above the bottom safe area so it never sits flush to the edge or gets
        # clipped — regardless of the configured/default fraction.
        max_footer_y = canvas_h * _CARD_SAFE_BOTTOM - footer_size * _TEXT_FULL_DESCENT
        if footer_y > max_footer_y:
            footer_y = max_footer_y
        footer_x, footer_align = _heading_x_align(meta_footer_width, footer_pos["x"])
        footer_el = {
            "kind": "text",
            "id": "footer",
            "text": footer_text,
            "x": footer_x,
            "y": footer_y,
            "size": footer_size,
            "color": text_color,
            "opacity": 0.82,
            "weight": 800,
            "max_width": canvas_w * meta_footer_width,
            "direction": render_direction,
        }
        if footer_align == "center":
            footer_el["align"] = "center"
        elements.append(footer_el)

    # Optional logo image. Entirely additive: when no logo data URL is
    # present in the layout nothing is appended, so existing templates are
    # byte-for-byte unchanged. Position/size come from the layout in the
    # same mm-based convention as the draggable pills (0,0 → engine default
    # top-left corner of the card with a sensible default size).
    logo_el = _logo_element(layout, (canvas_w, canvas_h))
    if logo_el is not None:
        elements.append(logo_el)

    return {
        "canvas": {"width": canvas_w, "height": canvas_h},
        "orientation": orient,
        "render_engine": engine,
        "render_direction": render_direction,
        "background": _background(layout),
        "elements": elements,
        "card_id": str(card_id) if card_id else "",
        "username": username,
        # password is kept in the model so the PDF adapter can render
        # it; the SVG adapter always masks it.
        "password": password,
    }


# ───────────────────────────────────────────────────────────────────
# SVG adapter
# ───────────────────────────────────────────────────────────────────

# ذاكرة على مستوى الموديول لقاعدة base64 لخطوط المراعي المضمَّنة في
# SVG المصغّرات: تُقرأ ملفات TTF وتُرمَّز مرة واحدة فقط مهما تكرر الطلب.
_embedded_font_css_cache: str | None = None


def _embedded_almarai_font_css() -> str:
    """يبني كتلة <style> بقواعد @font-face لخط المراعي مضمّنة data: URI.

    لماذا التضمين؟ المصغّرات تُعرض داخل <img src=".../thumbnail.svg">،
    وSVG داخل <img> معزول تمامًا: لا يرى CSS الصفحة ولا يستطيع تحميل
    خطوط من روابط خارجية — الطريقة الوحيدة ليظهر المراعي هي تضمين ملف
    الخط نفسه base64 داخل ملف الـSVG. النتيجة تُحسب مرة واحدة وتُحفظ
    على مستوى الموديول (الترميز ~500KB) فلا قراءة/ترميز لكل طلب.
    """
    global _embedded_font_css_cache
    if _embedded_font_css_cache is not None:
        return _embedded_font_css_cache
    faces: list[str] = []
    # يونيو 2026: نُضمِّن Cairo أوّلًا (المَطلوب من المالك) ثم Almarai
    # كـfallback. كِلاهما يَنطبق على @font-face باسمَيْن مُختلفَين كي
    # يَستطيع الـSVG font-family stack اختيار «Cairo» أوّلًا.
    # الوزن 800/Black مهم: عناوين البطاقة تطلب 900/950 والمتصفح يحلّها
    # إلى أثقل وجه مسجّل — بدونه تُركَّب «عريض صناعي» قبيح.
    for family, path, weight in (
        # Cairo first
        ("Cairo", _CAIRO_REGULAR_PATH, 400),
        ("Cairo", _CAIRO_BOLD_PATH, 700),
        ("Cairo", _CAIRO_BLACK_PATH, 800),
        # Almarai fallback (للقَوالب القَديمة التي تَطلب Almarai باسمها)
        ("Almarai", _ALMARAI_REGULAR_PATH, 400),
        ("Almarai", _ALMARAI_BOLD_PATH, 700),
        ("Almarai", _ALMARAI_EXTRABOLD_PATH, 800),
    ):
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            faces.append(
                f"@font-face{{font-family:'{family}';font-style:normal;"
                f"font-weight:{weight};"
                f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}"
            )
        except Exception:  # pragma: no cover — defensive: never break SVG
            continue
    css = ""
    if faces:
        # CDATA يحمي صلاحية XML حتى لو احتوى CSS محارف خاصة مستقبلًا.
        css = "<style type=\"text/css\"><![CDATA[" + "".join(faces) + "]]></style>"
    _embedded_font_css_cache = css
    return css


def render_card_svg(model: dict, *, mask_password: bool = True,
                    embed_fonts: bool = False) -> str:
    """Render the model as an inline SVG string.

    The SVG uses `viewBox="0 0 W H"` and `preserveAspectRatio="xMidYMid meet"`,
    so dropping it into ANY container scales the card uniformly without
    distortion. `mask_password=True` (default) replaces the password
    value with bullets — the live preview never reveals the real
    password.

    `embed_fonts=True` يضمّن وجوه خط المراعي كـdata: URI داخل الـSVG —
    مطلوب فقط عندما يُعرض الملف داخل <img> (المصغّرات/منتقي القوالب)
    حيث لا يصل CSS الصفحة ولا خطوطها. المعاينة الحية المضمّنة في الصفحة
    تبقى بدون تضمين (False) لتظل خفيفة وتستخدم خطوط الصفحة نفسها.
    """
    w = int(model["canvas"]["width"]); h = int(model["canvas"]["height"])
    bg = model.get("background") or {}
    uid = _svg_id("card", uuid.uuid4().hex[:10])
    bg_id = f"{uid}-bg"
    pattern_id = f"{uid}-pattern"
    clip_id = f"{uid}-clip"

    parts: list[str] = []
    # `direction="ltr"` is critical: the admin UI ships with
    # <html dir="rtl"> and every nested <text> inherits that direction
    # by default. In RTL, `text-anchor="start"` means the right edge of
    # the text box — so an LTR string like "HobeRadius" rendered at
    # x=60 walks off the LEFT side of the card and only the last few
    # characters stay visible inside the viewBox. Forcing ltr on the
    # SVG root (and on each <text> below) keeps card text laid out
    # left-to-right regardless of the document direction.
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" class="card-svg" '
        f'direction="ltr" '
        f'style="display:block;direction:ltr;overflow:visible;max-width:100%;max-height:100%">'
    )
    parts.append('<defs>')
    if embed_fonts:
        # تضمين المراعي لعرض الـSVG داخل <img> (لا وصول لخطوط الصفحة).
        parts.append(_embedded_almarai_font_css())
    parts.extend(_svg_defs(bg, w, h, bg_id=bg_id, pattern_id=pattern_id))
    parts.append(f'<clipPath id="{clip_id}"><rect x="0" y="0" width="{w}" height="{h}" rx="{int(w*0.025)}" ry="{int(w*0.025)}"/></clipPath>')
    parts.append('</defs>')

    parts.append(f'<g clip-path="url(#{clip_id})">')
    parts.extend(_svg_background(bg, w, h, bg_id=bg_id, pattern_id=pattern_id))

    for el in model["elements"]:
        kind = el.get("kind")
        if kind == "rect":
            parts.append(_svg_rect(el))
        elif kind == "text":
            parts.append(_svg_text(el, uid=uid))
        elif kind == "pill":
            parts.append(_svg_pill(el, mask_password=mask_password, uid=uid))
        elif kind == "qr":
            parts.append(_svg_qr_placeholder(el))
        elif kind == "image":
            parts.append(_svg_image(el))
        elif kind == "icon" or kind == "watermark":
            parts.append(_svg_motif(el))
        elif kind == "pattern_bg":
            parts.append(_svg_pattern_bg(el))

    parts.append('</g>')
    parts.append('</svg>')
    return "".join(parts)


# ───────────────────────────────────────────────────────────────────
# PDF adapter
# ───────────────────────────────────────────────────────────────────

def render_card_pdf(pdf, model: dict, *, form_name: str,
                     expose_password: bool = True,
                     include_background: bool = True,
                     include_ids: set[str] | None = None,
                     exclude_ids: set[str] | None = None) -> None:
    """Draw the model into a ReportLab named form at canvas coordinates.

    The adapter writes the card into `beginForm(form_name, 0, 0, W, H)`
    with W/H equal to the model's canvas. The caller is responsible
    for `pdf.translate(...)` + `pdf.scale(...)` + `pdf.doForm(form_name)`
    when placing the finished card on a sheet, and for choosing whether
    to render multiple cards as multiple forms.

    `expose_password=True` (default for PDF) renders the real password.
    Pass False to keep the password masked — useful for designer PDF
    samples that should not leak credentials.
    """
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])

    pdf.beginForm(form_name, 0, 0, cw, ch)
    try:
        _embed_arabic_font_marker(pdf, ch)
        if include_background:
            _pdf_background(pdf, model.get("background") or {}, cw, ch)
        for el in model["elements"]:
            el_id = str(el.get("id") or "")
            if include_ids is not None and el_id not in include_ids:
                continue
            if exclude_ids is not None and el_id in exclude_ids:
                continue
            kind = el.get("kind")
            if kind == "rect":
                _pdf_rect(pdf, el, ch)
            elif kind == "text":
                _pdf_text(pdf, el, ch)
            elif kind == "pill":
                _pdf_pill(pdf, el, ch, expose_password=expose_password)
            elif kind == "qr":
                _pdf_qr(pdf, el, ch)
            elif kind == "image":
                _pdf_image(pdf, el, ch)
            elif kind == "icon" or kind == "watermark":
                _pdf_motif(pdf, el, ch)
            elif kind == "pattern_bg":
                _pdf_pattern_bg(pdf, el, ch)
    finally:
        pdf.endForm()


def _embed_arabic_font_marker(pdf, ch: float) -> None:
    """Embed Almarai even when Arabic is rasterized for perfect shaping.

    تُختم الأوجه الثلاثة (Regular/Bold/ExtraBold) بحرف غير مرئي خارج
    الصفحة، فتظهر العائلة كاملة في قائمة خطوط الـPDF المضمّنة — توثيق
    أن العناوين العريضة رُسمت بأوزانها الصحيحة حتى عندما تكون النصوص
    العربية نفسها صورًا نقطية (subset بحرف واحد ≈ بضعة كيلوبايتات فقط).
    """
    if not _ensure_arabic_fonts():
        return
    faces = [PDF_FONT_ARABIC, PDF_FONT_ARABIC_BOLD]
    if _arabic_extrabold_ready:
        faces.append(PDF_FONT_ARABIC_EXTRABOLD)
    try:
        pdf.saveState()
        pdf.setFillColorRGB(1, 1, 1)
        for face in faces:
            pdf.setFont(face, 1)
            pdf.drawString(-1000, ch + 1000, "ا")
        pdf.restoreState()
    except Exception:
        try:
            pdf.restoreState()
        except Exception:
            pass


def place_card_form_uniform(pdf, model: dict, *, form_name: str,
                              slot_x: float, slot_y: float,
                              slot_width: float, slot_height: float,
                              stretch: bool = False) -> None:
    """Place an already-built form into a sheet slot.

    الافتراضي UNIFORM: the card is centered inside the slot and scaled by
    `min(slot_w/cw, slot_h/ch)` so its internal proportions (text size, QR
    shape, pill widths, accent bar position) are preserved exactly — the
    PDF equivalent of `preserveAspectRatio="xMidYMid meet"` in SVG.

    ``stretch=True`` (وضع «تمدد يملأ الخانة» في مركز التصدير): مقياس
    مستقل لكل محور فيملأ الكرت خانته طولًا وعرضًا تمامًا حسب إعدادات
    الإخراج — قد يغيّر النسب، وهذا مقصود (طلب المالك). رمز QR يبقى
    قابلًا للمسح ضمن تمدد معقول.

    Increasing cards_per_row or cards_per_column only changes the
    slot size — it never changes what is inside the form.
    """
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])
    if stretch:
        sx = slot_width / max(cw, 1.0)
        sy = slot_height / max(ch, 1.0)
        dx, dy = slot_x, slot_y
    else:
        sx = sy = min(slot_width / max(cw, 1.0), slot_height / max(ch, 1.0))
        dx = slot_x + (slot_width - cw * sx) / 2.0
        dy = slot_y + (slot_height - ch * sy) / 2.0
    pdf.saveState()
    try:
        pdf.translate(dx, dy)
        pdf.scale(sx, sy)
        pdf.doForm(form_name)
    finally:
        pdf.restoreState()


def _card_slot_fit(model: dict, *, slot_x: float, slot_y: float,
                   slot_width: float, slot_height: float) -> dict[str, float]:
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])
    fit = min(slot_width / max(cw, 1.0), slot_height / max(ch, 1.0))
    draw_w = cw * fit
    draw_h = ch * fit
    return {
        "scale": fit,
        "x": slot_x + (slot_width - draw_w) / 2.0,
        "y": slot_y + (slot_height - draw_h) / 2.0,
        "width": draw_w,
        "height": draw_h,
    }


def _cover_crop_image_bytes(image_bytes: bytes, *, aspect: float) -> bytes:
    """يقصّ الصورة مركزيًا إلى نسبة أبعاد البطاقة (مكافئ slice في SVG).

    معاينة SVG ترسم الخلفية بـpreserveAspectRatio="xMidYMid slice"
    (تغطية مع قص مركزي بلا تشويه)، بينما كان مسار PDF يمدّد الصورة
    (preserveAspectRatio=False) — فإذا اختلفت نسبة الصورة عن نسبة
    البطاقة انضغط التصميم المرفوع كله وتحركت كل العناصر المرسومة فوقه
    عن مواضعها في المعاينة (الخلل المُبلَّغ: «التوزيع يختلف»). القص هنا
    يجعل التصدير يطابق المعاينة هندسيًا حرفيًا. أي فشل → الأصل كما هو.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            w, h = img.size
            if w <= 0 or h <= 0 or aspect <= 0:
                return image_bytes
            current = w / h
            if abs(current - aspect) < 1e-3:
                return image_bytes
            if current > aspect:
                # أعرض من البطاقة → قص من الجانبين.
                new_w = int(round(h * aspect))
                left = (w - new_w) // 2
                box = (left, 0, left + new_w, h)
            else:
                # أطول من البطاقة → قص من الأعلى والأسفل.
                new_h = int(round(w / aspect))
                top = (h - new_h) // 2
                box = (0, top, w, top + new_h)
            cropped = img.crop(box)
            out = BytesIO()
            if cropped.mode in {"RGBA", "LA", "P"}:
                cropped.convert("RGBA").save(out, format="PNG")
            else:
                cropped.convert("RGB").save(out, format="JPEG", quality=90)
            return out.getvalue()
    except Exception:  # pragma: no cover — defensive: never break export
        return image_bytes


def _faded_rgba_png(image_bytes: bytes, opacity: float) -> bytes | None:
    """PNG بقناة ألفا مضروبة في opacity — لمزج الصورة فوق تدرّج PDF.

    ReportLab لا يدعم alpha لـdrawImage مباشرة، لكن شفافية PNG (SMask)
    تنجو من إعادة استخدام الـForm XObject (نفس ما يعتمد عليه نمط
    الزخرفة) — فنُحضّر الشفافية داخل الصورة نفسها. None = تخطَّ الرسم.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            rgba = img.convert("RGBA")
            alpha = max(0.0, min(1.0, float(opacity)))
            if alpha < 1.0:
                rgba.putalpha(rgba.getchannel("A").point(
                    lambda a: int(a * alpha)))
            out = BytesIO()
            rgba.save(out, format="PNG")
            return out.getvalue()
    except Exception:  # pragma: no cover — defensive: never break export
        return None


def _uploaded_background_image_reader(bg: dict, *, aspect: float | None = None):
    image_url = str(bg.get("image_data_url") or "")
    source = str(bg.get("source") or "preset")
    if source != "image" or not image_url.startswith("data:image/") or ";base64," not in image_url:
        return None
    cache_key = f"{round(aspect, 4) if aspect else 0}|{image_url}"
    cached = _uploaded_background_reader_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        from reportlab.lib.utils import ImageReader

        mime_part, encoded = image_url.split(";base64,", 1)
        image_bytes = base64.b64decode(encoded)
        if mime_part == "data:image/webp":
            image_bytes = _convert_bitmap_for_reportlab(image_bytes)
        if mime_part not in {"data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp"}:
            return None
        if aspect:
            # نفس سلوك المعاينة (xMidYMid slice): قص مركزي بلا تشويه.
            image_bytes = _cover_crop_image_bytes(image_bytes, aspect=aspect)
        image = ImageReader(BytesIO(image_bytes))
        if len(_uploaded_background_reader_cache) > 8:
            _uploaded_background_reader_cache.clear()
        _uploaded_background_reader_cache[cache_key] = image
        return image
    except Exception:
        return None


def model_uses_uploaded_background(model: dict) -> bool:
    """True when this card should use the dedicated uploaded-image export path."""
    return _uploaded_background_image_reader(model.get("background") or {}) is not None


def _contain_rect(img_w: float, img_h: float, x: float, y: float,
                  w: float, h: float) -> tuple[float, float, float, float]:
    """مستطيل «احتواء» (contain): الصورة كاملة داخل الإطار، ممركزة بلا قصّ.

    نفس هندسة preserveAspectRatio="xMidYMid meet" في معاينة SVG.
    أبعاد صورة غير صالحة → الإطار كما هو (سقوط آمن = تمديد)."""
    if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
        return (x, y, w, h)
    scale = min(w / img_w, h / img_h)
    iw, ih = img_w * scale, img_h * scale
    return (x + (w - iw) / 2.0, y + (h - ih) / 2.0, iw, ih)


def draw_uploaded_background_uniform(pdf, model: dict, *, slot_x: float, slot_y: float,
                                     slot_width: float, slot_height: float,
                                     stretch: bool = False) -> bool:
    """Draw an uploaded card image directly on the PDF page.

    Uploaded images deliberately bypass the shared card form/XObject.
    Some PDF viewers and ReportLab form reuse paths can drop bitmap
    resources nested inside reusable forms; placing the uploaded bitmap
    on the page first and then drawing the text/QR forms above it keeps
    customer-uploaded artwork visible for 500, 1000, and larger batch
    exports.
    """
    from reportlab.lib import colors

    bg = model.get("background") or {}
    cw = float(model["canvas"]["width"]) or 1.0
    ch = float(model["canvas"]["height"]) or 1.0
    # ملاءمة الصورة (image_fit): cover = قصّ مركزي لنسبة البطاقة (نفس
    # slice في SVG)، contain = كاملة بلا قصّ، stretch = تمديد. القصّ
    # يُطبّق فقط في cover.
    fit_mode = str(bg.get("image_fit") or "cover")
    # قصّ cover يتبع نسبة الكانفس **دائمًا** — حتى في وضع «تمدد يملأ
    # الخانة». القصّ على نسبة الخانة النهائية (نسخة سابقة) كان يحرّك
    # محتوى الصورة (حبات اليوزر/الباس المرسومة فيها) بينما نصوص البطاقة
    # تتمدد من الكانفس كما هي — فتنزاح الأرقام عن أماكنها المصممة
    # (شكوى «مكان التصميم يتغير عن الإخراج»). الآن: نفس قصّ المعاينة ثم
    # يتمدد كل شيء (صورة ونصوص) بنفس التحويل فتبقى المطابقة تامة.
    image = _uploaded_background_image_reader(
        bg, aspect=(cw / ch) if fit_mode == "cover" else None)
    if image is None:
        return False
    if stretch:
        dx, dy, dw, dh = slot_x, slot_y, slot_width, slot_height
        if fit_mode == "contain":
            # contain يجب أن يُحسب داخل «الكانفس» (نفس هندسة المعاينة)
            # ثم يُحوَّل صندوقه بمقياس الخانة المستقل لكل محور — حسابه
            # مباشرة داخل الخانة الممدودة (نسختها السابقة) كان يحفظ نسبة
            # الصورة الأصلية فتظهر أشرطة فارغة والأرقام تنزاح عن رسوم
            # الصورة (شكوى «ما في تمدد أفقي + منزاحات»).
            try:
                img_w, img_h = image.getSize()
            except Exception:  # noqa: BLE001
                img_w = img_h = 0
            rx, ry, rw, rh = _contain_rect(img_w, img_h, 0.0, 0.0, cw, ch)
            sx = slot_width / max(cw, 1.0)
            sy = slot_height / max(ch, 1.0)
            dx = slot_x + rx * sx
            # كانفس y من الأعلى بينما PDF من الأسفل — نقلب المحور.
            dy = slot_y + (ch - ry - rh) * sy
            dw, dh = rw * sx, rh * sy
    else:
        fit = _card_slot_fit(
            model,
            slot_x=slot_x,
            slot_y=slot_y,
            slot_width=slot_width,
            slot_height=slot_height,
        )
        dx, dy, dw, dh = fit["x"], fit["y"], fit["width"], fit["height"]
        if fit_mode == "contain":
            # الخانة الموحّدة تحفظ نسبة الكانفس — الاحتواء داخلها مكافئ
            # للاحتواء داخل الكانفس نفسه.
            try:
                img_w, img_h = image.getSize()
            except Exception:  # noqa: BLE001
                img_w = img_h = 0
            dx, dy, dw, dh = _contain_rect(img_w, img_h, dx, dy, dw, dh)
    opacity = max(0.0, min(1.0, float(bg.get("image_opacity") or 1.0)))
    pdf.saveState()
    try:
        pdf.drawImage(
            image,
            dx,
            dy,
            width=dw,
            height=dh,
            preserveAspectRatio=False,
            mask="auto",
        )
        if opacity < 1:
            pdf.setFillColor(colors.Color(1, 1, 1, alpha=max(0, 1 - opacity)))
            pdf.rect(dx, dy, dw, dh, stroke=0, fill=1)
    finally:
        pdf.restoreState()
    return True


def _split_hex_alpha(value: str, *, default_alpha: float = 1.0) -> tuple[str, float]:
    """Split a hex colour into an opaque 6-digit hex + an alpha 0..1.

    Accepts ``#RGB``, ``#RGBA``, ``#RRGGBB`` and ``#RRGGBBAA``. The colour
    pickers in the designer can hand back any of these shapes, and an
    8-digit value carries the chosen transparency in its last byte. We
    pull that alpha out here so both adapters can honour it instead of
    losing it (SVG via ``fill-opacity``, PDF via ``colors.Color(alpha=…)``).
    """
    raw = str(value or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    alpha = default_alpha
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    elif len(raw) == 4:
        raw = "".join(ch * 2 for ch in raw)
        alpha = int(raw[6:8], 16) / 255.0
        raw = raw[:6]
    elif len(raw) == 8:
        try:
            alpha = int(raw[6:8], 16) / 255.0
        except ValueError:
            alpha = default_alpha
        raw = raw[:6]
    if len(raw) != 6:
        return "#1f2937", default_alpha
    return "#" + raw.lower(), max(0.0, min(1.0, alpha))


def _pdf_color(value: str, *, opacity: float | None = None):
    """Return a reportlab Color for a hex string (cached import).

    ``colors.HexColor`` only understands opaque 6-digit hex; it silently
    mis-parses ``#RGB`` (3-digit) and ``#RRGGBBAA`` (8-digit) — the very
    shapes the browser colour pickers emit — which is why chosen colours
    used to fall back to near-black in the PDF. We normalise the hex and
    fold in any alpha (from the hex itself and/or an explicit ``opacity``)
    so the PDF matches the live preview.
    """
    from reportlab.lib import colors

    base_hex, hex_alpha = _split_hex_alpha(value)
    alpha = hex_alpha if opacity is None else max(0.0, min(1.0, float(opacity))) * hex_alpha
    try:
        color = colors.HexColor(base_hex)
    except Exception:
        color = colors.HexColor("#1f2937")
    if alpha >= 1.0:
        return color
    return colors.Color(color.red, color.green, color.blue, alpha=alpha)


def _pdf_background(pdf, bg: dict, cw: float, ch: float) -> None:
    """Draw the card's background: gradient (faked as a 2-stop split),
    optional bitmap image, optional decorative pattern.

    ReportLab does not have native linear-gradient support, so we
    approximate by stacking a horizontal band of intermediate
    colour stops. For most card uses the human eye reads this as a
    smooth gradient, and on the printed page it is indistinguishable
    from the SVG preview at the same scale.
    """
    from reportlab.lib import colors

    image_url = bg.get("image_data_url") or ""
    source = str(bg.get("source") or "preset")
    if source == "image" and image_url.startswith("data:image/") and ";base64," in image_url:
        try:
            # ملاحظة: لا نستورد BytesIO محليًا هنا — الاستيراد المحلي كان
            # يجعل الاسم محليًا للدالة كلها فيكسر فرع الزخرفة أدناه
            # (UnboundLocalError صامت داخل try). نستخدم استيراد الموديول.
            from reportlab.lib.utils import ImageReader

            mime_part, encoded = image_url.split(";base64,", 1)
            image_bytes = base64.b64decode(encoded)
            if mime_part == "data:image/webp":
                image_bytes = _convert_bitmap_for_reportlab(image_bytes)
            if mime_part in {"data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp"}:
                # ملاءمة الصورة (image_fit) — نفس هندسة SVG:
                #   cover   = قص مركزي لنسبة البطاقة (slice؛ الافتراضي —
                #             التمديد كان يشوّه التصميم المرفوع).
                #   contain = الصورة كاملة ممركزة بلا قص (meet).
                #   stretch = تمديد يملأ البطاقة (none).
                fit_mode = str(bg.get("image_fit") or "cover")
                if fit_mode == "cover":
                    image_bytes = _cover_crop_image_bytes(
                        image_bytes, aspect=(cw / ch) if ch else 0.0
                    )
                image = ImageReader(BytesIO(image_bytes))
                dx, dy, dw, dh = 0.0, 0.0, cw, ch
                if fit_mode == "contain":
                    try:
                        img_w, img_h = image.getSize()
                    except Exception:  # noqa: BLE001
                        img_w = img_h = 0
                    dx, dy, dw, dh = _contain_rect(img_w, img_h, 0, 0, cw, ch)
                opacity = max(0.0, min(1.0, float(bg.get("image_opacity") or 1.0)))
                pdf.saveState()
                pdf.drawImage(image, dx, dy, width=dw, height=dh,
                              preserveAspectRatio=False, mask="auto")
                if opacity < 1:
                    pdf.setFillColor(colors.Color(1, 1, 1, alpha=max(0, 1 - opacity)))
                    pdf.rect(dx, dy, dw, dh, stroke=0, fill=1)
                pdf.restoreState()
                return
        except Exception:
            pass

    start = _pdf_color(bg.get("gradient_start", "#0f172a"))
    end = _pdf_color(bg.get("gradient_end", "#22a7bd"))
    # تدرّج قطري حقيقي مطابق لمعاينة SVG: linearGradient من (0,0) إلى
    # (1,1). الشرائط الأفقية القديمة (24 شريطًا من أعلى لأسفل) أنتجت
    # تدرجًا «عموديًا» مختلف الاتجاه عن المعاينة القطرية — فبدت ألوان
    # البطاقة المصدَّرة موزعة بشكل مغاير للمعاينة (جزء من خلل المطابقة
    # المُبلَّغ). نرسمه الآن صورة نقطية بنفس معادلة SVG بالضبط.
    gradient_png = _build_diagonal_gradient_png(
        (start.red, start.green, start.blue),
        (end.red, end.green, end.blue),
        int(cw), int(ch),
    )
    if gradient_png is not None:
        try:
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(
                ImageReader(BytesIO(gradient_png)),
                0, 0, width=cw, height=ch,
                preserveAspectRatio=False,
            )
        except Exception:  # pragma: no cover — defensive
            gradient_png = None
    if gradient_png is None:
        # سقوط آمن: الشرائط الأفقية القديمة (لو غابت Pillow لأي سبب).
        bands = 24
        band_h = ch / bands
        for i in range(bands):
            t = i / max(bands - 1, 1)
            r = start.red   + (end.red   - start.red)   * t
            g = start.green + (end.green - start.green) * t
            b = start.blue  + (end.blue  - start.blue)  * t
            pdf.setFillColor(colors.Color(r, g, b))
            # PDF origin is bottom-left; band i (top→bottom in the model)
            # sits at (ch - (i+1)*band_h) in PDF space.
            pdf.rect(0, ch - (i + 1) * band_h, cw, band_h + 0.5, stroke=0, fill=1)

    # «خلفية من صورة» داخل تصميم النظام: image_data_url مع source='preset'
    # يصل فقط عند تفعيل preset_background_image (انظر _background). تُرسم
    # فوق التدرّج وتحت الزخرفة بشفافية مدموجة في PNG نفسه — مطابقة لمزج
    # SVG (opacity على <image> فوق rect التدرّج).
    if image_url.startswith("data:image/") and ";base64," in image_url:
        try:
            from reportlab.lib.utils import ImageReader

            mime_part, encoded = image_url.split(";base64,", 1)
            image_bytes = base64.b64decode(encoded)
            if mime_part == "data:image/webp":
                image_bytes = _convert_bitmap_for_reportlab(image_bytes)
            if mime_part in {"data:image/png", "data:image/jpeg",
                             "data:image/jpg", "data:image/webp"}:
                fit_mode = str(bg.get("image_fit") or "cover")
                if fit_mode == "cover":
                    image_bytes = _cover_crop_image_bytes(
                        image_bytes, aspect=(cw / ch) if ch else 0.0)
                faded = _faded_rgba_png(
                    image_bytes,
                    max(0.0, min(1.0, float(bg.get("image_opacity") or 0.82))))
                if faded is not None:
                    reader = ImageReader(BytesIO(faded))
                    dx, dy, dw, dh = 0.0, 0.0, cw, ch
                    if fit_mode == "contain":
                        try:
                            img_w, img_h = reader.getSize()
                        except Exception:  # noqa: BLE001
                            img_w = img_h = 0
                        dx, dy, dw, dh = _contain_rect(img_w, img_h, 0, 0, cw, ch)
                    pdf.drawImage(
                        reader, dx, dy,
                        width=dw, height=dh,
                        preserveAspectRatio=False, mask="auto")
        except Exception:  # pragma: no cover — defensive: never break export
            pass

    # Decorative pattern overlay (نمط الزخرفة). Drawn as a transparent
    # RGBA PNG and embedded via drawImage(mask="auto") instead of vector
    # shapes with alpha colours.
    #
    # WHY a bitmap: this whole background is drawn inside a ReportLab
    # Form XObject (`pdf.beginForm`). ReportLab silently DROPS the
    # transparency ExtGState inside forms, so the old vector path drew
    # the "faint white" pattern as 100% OPAQUE white — solid white grid
    # lines / signal bars / a hard white circle covering the gradient.
    # That is exactly the «نمط الزخرفة يختفي/الملف يطلع أبيض» export bug:
    # the live SVG preview showed a soft translucent pattern while the
    # exported PDF showed ugly opaque white. PNG alpha (SMask) survives
    # form reuse fine — the Arabic-text raster path in this module
    # already relies on it — so we rasterize the same geometry the SVG
    # adapter emits (same colour, same per-pattern legacy opacity) and
    # the export now matches the preview pixel-for-pixel.
    overlay_png = _build_pattern_overlay_png(bg, int(cw), int(ch))
    if overlay_png is not None:
        try:
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(
                ImageReader(BytesIO(overlay_png)),
                0, 0, width=cw, height=ch,
                preserveAspectRatio=False,
                mask="auto",
            )
        except Exception:  # pragma: no cover — defensive: never break export
            pass


# Bounded cache: export jobs render the same template background for
# hundreds of cards; the overlay only depends on (pattern, colour,
# opacity, canvas size) so one bitmap serves the whole job.
_pattern_overlay_png_cache: dict[tuple[Any, ...], bytes | None] = {}

# ذاكرة تدرّج الخلفية القطري — لون البداية/النهاية + المقاس فقط، فصورة
# واحدة تخدم مئات البطاقات في مهمة التصدير الواحدة.
_diagonal_gradient_png_cache: dict[tuple[Any, ...], bytes | None] = {}


def _build_diagonal_gradient_png(
    start_rgb: tuple[float, float, float],
    end_rgb: tuple[float, float, float],
    w: int,
    h: int,
) -> bytes | None:
    """يبني تدرّجًا قطريًا (0,0)→(1,1) مطابقًا لـlinearGradient في SVG.

    إسقاط SVG: المعامل t لكل نقطة = إسقاطها على متجه التدرج
    ((x/w)+(y/h))/2 في إحداثيات objectBoundingBox — نفس المعادلة هنا.
    يُحسب على شبكة مصغّرة ثم يُكبَّر خطيًا (التدرج خطي أصلًا فلا فرق
    بصري). None عند غياب Pillow → يسقط المستدعي للشرائط القديمة.
    """
    key = (
        tuple(round(c, 4) for c in start_rgb),
        tuple(round(c, 4) for c in end_rgb),
        int(w), int(h),
    )
    if key in _diagonal_gradient_png_cache:
        return _diagonal_gradient_png_cache[key]
    try:
        from PIL import Image
    except Exception:  # pragma: no cover — optional dependency safety
        return None
    scale = 4
    sw, sh = max(2, int(w) // scale), max(2, int(h) // scale)
    img = Image.new("RGB", (sw, sh))
    px = img.load()
    r0, g0, b0 = (max(0.0, min(1.0, c)) for c in start_rgb)
    r1, g1, b1 = (max(0.0, min(1.0, c)) for c in end_rgb)
    for yy in range(sh):
        fy = yy / (sh - 1)
        for xx in range(sw):
            fx = xx / (sw - 1)
            t = (fx + fy) / 2.0  # إسقاط على القطر (1,1)
            px[xx, yy] = (
                int(round((r0 + (r1 - r0) * t) * 255)),
                int(round((g0 + (g1 - g0) * t) * 255)),
                int(round((b0 + (b1 - b0) * t) * 255)),
            )
    img = img.resize((max(1, int(w)), max(1, int(h))), Image.Resampling.BILINEAR)
    buf = BytesIO()
    img.save(buf, format="PNG")
    result = buf.getvalue()
    if len(_diagonal_gradient_png_cache) > 16:
        _diagonal_gradient_png_cache.clear()
    _diagonal_gradient_png_cache[key] = result
    return result


def _build_pattern_overlay_png(bg: dict, w: int, h: int) -> bytes | None:
    """Rasterize the decorative pattern to a transparent PNG.

    Geometry, colour and opacity mirror `_svg_defs` / `_svg_background`
    exactly (same step sizes, same bottom-30% signal bars, same radial
    wave highlight, same legacy per-pattern alpha) so the exported PDF
    background is identical to the live designer preview. Returns None
    for "clean" / unknown patterns or when Pillow is unavailable.
    """
    pattern = str(bg.get("pattern") or "signal")
    if pattern not in {"grid", "signal", "wave"}:
        return None
    deco_hex = bg.get("pattern_color") or "#ffffff"
    deco_base, deco_hex_alpha = _split_hex_alpha(deco_hex)
    saved_opacity = bg.get("pattern_opacity")
    # Same legacy defaults the SVG adapter uses for untouched templates.
    legacy_overlay = {"grid": 0.20, "signal": 0.18, "wave": 0.30}
    overlay = saved_opacity if saved_opacity is not None else legacy_overlay.get(pattern, 0.30)
    alpha = max(0.0, min(1.0, float(overlay) * float(deco_hex_alpha)))
    if alpha <= 0:
        return None

    cache_key = (pattern, deco_base, round(alpha, 4), int(w), int(h))
    if cache_key in _pattern_overlay_png_cache:
        return _pattern_overlay_png_cache[cache_key]

    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover — optional dependency safety
        return None

    base = _pdf_color(deco_base)
    r = int(round(base.red * 255))
    g = int(round(base.green * 255))
    b = int(round(base.blue * 255))
    a = int(round(alpha * 255))
    fill = (r, g, b, a)

    image = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if pattern == "grid":
        # SVG: patternUnits tile of `step`, path stroke 1px → grid lines
        # every `step` px across the whole card.
        step = max(int(w * 0.045), 8)
        x = 0
        while x <= w:
            draw.rectangle([x, 0, x, h], fill=fill)
            x += step
        y = 0
        while y <= h:
            draw.rectangle([0, y, w, y], fill=fill)
            y += step
    elif pattern == "signal":
        # SVG: vertical bars in the bottom 30% of the card, tile width
        # max(2.5% of w, 6), bar width max(0.5% of w, 2).
        tile = max(int(w * 0.025), 6)
        bar_w = max(int(w * 0.005), 2)
        top = int(h * 0.7)
        x = 0
        while x <= w:
            draw.rectangle([x, top, x + bar_w - 1, h], fill=fill)
            x += tile
    elif pattern == "wave":
        # SVG: radialGradient cx=20% cy=30% r=55%, colour fades to 0 at
        # the 60% stop. Reproduce the soft highlight per-pixel on a
        # downscaled grid then upscale — fast and visually identical.
        scale = 4  # compute at 1/4 resolution, bilinear upscale hides it
        sw, sh = max(1, w // scale), max(1, h // scale)
        small = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        px = small.load()
        # Distances in normalized box coordinates: the SVG gradient uses
        # objectBoundingBox units, so on a non-square card the highlight
        # is elliptical (stretched with the rect) — mirror that here.
        fade_end = 0.55 * 0.60  # r=55% × colour fades out at the 60% stop
        for yy in range(sh):
            ny = (yy / max(sh - 1, 1)) - 0.30
            for xx in range(sw):
                nx = (xx / max(sw - 1, 1)) - 0.20
                dist = math.hypot(nx, ny)
                if dist >= fade_end:
                    continue
                t = 1.0 - (dist / fade_end)
                px[xx, yy] = (r, g, b, int(round(a * t)))
        image = small.resize((max(1, w), max(1, h)), Image.Resampling.BILINEAR)

    buf = BytesIO()
    image.save(buf, format="PNG")
    result = buf.getvalue()
    if len(_pattern_overlay_png_cache) > 16:
        _pattern_overlay_png_cache.clear()
    _pattern_overlay_png_cache[cache_key] = result
    return result


def _convert_bitmap_for_reportlab(raw: bytes) -> bytes:
    """Return PNG bytes for browser-friendly bitmap formats.

    The live SVG preview can embed any uploaded data URL the browser
    understands, including WebP. ReportLab is much stricter, so a WebP
    background looked correct in the designer but disappeared from the
    exported PDF. Converting through Pillow keeps the PDF path visually
    aligned with the browser preview without changing saved templates.
    """
    from PIL import Image

    with Image.open(BytesIO(raw)) as image:
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def _pdf_rect(pdf, el: dict, ch: float) -> None:
    """Filled rounded rect at model coordinates (top-left)."""
    pdf.setFillColor(_pdf_color(el.get("fill", "#ffffff")))
    pdf_y = ch - el["y"] - el["height"]
    rx = float(el.get("rx", 0))
    if rx > 0:
        pdf.roundRect(el["x"], pdf_y, el["width"], el["height"], rx,
                      stroke=0, fill=1)
    else:
        pdf.rect(el["x"], pdf_y, el["width"], el["height"],
                 stroke=0, fill=1)


def _pdf_image(pdf, el: dict, ch: float) -> None:
    """Draw a logo / decorative bitmap at model coordinates (top-left).

    Best-effort and fully guarded: any decode/draw failure silently skips
    the image so a bad logo never breaks the whole export. Aspect ratio is
    preserved inside the box, mirroring the SVG `xMidYMid meet`.
    """
    href = str(el.get("href") or "")
    if not href.startswith("data:image/") or ";base64," not in href:
        return
    try:
        from io import BytesIO
        from reportlab.lib.utils import ImageReader

        mime_part, encoded = href.split(";base64,", 1)
        image_bytes = base64.b64decode(encoded)
        if mime_part == "data:image/webp":
            image_bytes = _convert_bitmap_for_reportlab(image_bytes)
        if mime_part not in {"data:image/png", "data:image/jpeg",
                             "data:image/jpg", "data:image/webp"}:
            return
        image = ImageReader(BytesIO(image_bytes))
        pdf_y = ch - el["y"] - el["height"]
        pdf.drawImage(image, el["x"], pdf_y,
                      width=el["width"], height=el["height"],
                      preserveAspectRatio=True, mask="auto")
    except Exception:
        return


def _pdf_text(pdf, el: dict, ch: float) -> None:
    """Draw a text run. The model gives top-left of the text box; we
    convert to PDF baseline by dropping the cap-height (~0.78 × size).
    """
    from reportlab.lib import colors

    size = max(float(el.get("size", 12)), 1.0)
    weight = int(el.get("weight", 700))
    raw_text = str(el.get("text", ""))
    if not raw_text:
        return
    max_width = float(el.get("max_width") or 0)
    opacity = float(el.get("opacity", 1.0))
    # المسار النقطي يخدم العربية ورموز العملات معًا: Helvetica المدمجة
    # لا ترسم ₪/€/₺ — أي نص يحمل محرفًا فوق U+00FF يُرسم صورة بخط
    # مناسب (مع سقوط ذكي لخط يغطي الرمز — انظر _resolve_raster_font_for_text).
    align = el.get("align")
    if _needs_raster_text(raw_text):
        if _pdf_draw_arabic_text_image(
            pdf,
            raw_text,
            x=float(el["x"]),
            y=float(el["y"]),
            size=size,
            color=el.get("color", "#ffffff"),
            weight=weight,
            max_width=max_width,
            direction="rtl" if el.get("direction") == "rtl" else "ltr",
            opacity=opacity,
            ch=ch,
            halign="center" if align == "center" else "auto",
        ):
            return
    # Pick the right font for the text content and shape Arabic so
    # ReportLab gets the correctly-ordered presentation glyphs.
    font = _pick_pdf_font(raw_text, weight=weight)
    text = _shape_arabic(raw_text) if _has_arabic(raw_text) else raw_text
    pdf.setFont(font, size)
    color = _pdf_color(el.get("color", "#ffffff"))
    if opacity < 1.0:
        pdf.setFillColor(colors.Color(color.red, color.green, color.blue,
                                       alpha=max(0.0, min(1.0, opacity))))
    else:
        pdf.setFillColor(color)
    # SVG dominant-baseline="hanging" puts the text top at y. PDF's
    # drawString uses the baseline. Cap height ≈ 0.78 of font size for
    # Helvetica/Almarai, so drop y by that amount to align visually.
    baseline = ch - el["y"] - size * 0.78
    if max_width > 0:
        text = _shrink_to_fit(pdf, text, font, size, max_width)
    # Centred headings (portrait): anchor at the box centre. Otherwise RTL is
    # right-edge anchored; LTR left.
    if align == "center" and max_width > 0:
        pdf.drawCentredString(el["x"] + max_width / 2, baseline, text)
    elif el.get("direction") == "rtl":
        right_edge = el["x"] + (max_width if max_width > 0 else
                                  pdf.stringWidth(text, font, size))
        pdf.drawRightString(right_edge, baseline, text)
    else:
        pdf.drawString(el["x"], baseline, text)


def _pdf_pill(pdf, el: dict, ch: float, *, expose_password: bool) -> None:
    """Draw the surface rect + label + value of a USER/PASS pill."""
    centered = el.get("align") == "center"
    box_center_x = el["x"] + el["width"] / 2
    pdf_y = ch - el["y"] - el["height"]
    if el.get("surface_enabled", True):
        pdf.setFillColor(_pdf_color(el["surface"],
                                    opacity=float(el.get("surface_opacity", 0.95))))
        pdf.roundRect(el["x"], pdf_y, el["width"], el["height"],
                      el["height"] * 0.20, stroke=0, fill=1)
    label_box_x = el["x"] + el["padding_x"]
    label_box_w = max(1, el["width"] - 2 * el["padding_x"])
    if el.get("show_label", True):
        # Label (USER/PASS or Arabic labels).
        # نفس مرساة SVG حرفيًا: منتصف التسمية عند y + 0.36×الارتفاع
        # (dominant-baseline="middle") — كانت الصيغة القديمة (قمة عند
        # 0.18h ثم 0.78×الحجم) تزيح التسمية بضعة بكسلات عن المعاينة.
        label_raw = str(el["label"])
        # التسمية تُعرض بوزن 900 في معاينة SVG — نفس الوزن هنا.
        label_font = _pick_pdf_font(label_raw, weight=900)
        label_text = _shape_arabic(label_raw) if _has_arabic(label_raw) else label_raw
        label_size = max(float(el["label_font_size"]), 4.0)
        label_middle = el["y"] + el["height"] * 0.36
        label_direction = "rtl" if el.get("label_direction") == "rtl" else "ltr"
        if _needs_raster_text(label_raw) and _pdf_draw_arabic_text_image(
            pdf,
            label_raw,
            x=label_box_x,
            y=label_middle,
            size=label_size,
            color=el["label_color"],
            weight=900,
            max_width=label_box_w,
            direction=label_direction,
            opacity=1.0,
            ch=ch,
            anchor="middle",
            halign="center" if centered else "auto",
        ):
            pass
        else:
            pdf.setFont(label_font, label_size)
            pdf.setFillColor(_pdf_color(el["label_color"]))
            # خط القاعدة = المنتصف + 0.26×الحجم (نصف ارتفاع x) — نفس
            # تفسير المتصفح لـdominant-baseline="middle" في المعاينة.
            label_baseline = ch - (label_middle + label_size * 0.26)
            if centered:
                pdf.drawCentredString(box_center_x, label_baseline, label_text)
            elif label_direction == "rtl":
                pdf.drawRightString(el["x"] + el["width"] - el["padding_x"],
                                    label_baseline,
                                    label_text)
            else:
                pdf.drawString(el["x"] + el["padding_x"],
                               label_baseline,
                               label_text)
    # Value (the real credential — masked if expose_password is False
    # and this pill carries the password).
    raw_value = el["value"]
    if el.get("is_password") and not expose_password:
        raw_value = "•" * min(max(len(raw_value), 6), 10)
    # القيمة (اليوزر/الباس) وزنها 900 في المعاينة — نطابقه في التصدير.
    value_font = _pick_pdf_font(raw_value, weight=900)
    value_text = _shape_arabic(raw_value) if _has_arabic(raw_value) else raw_value
    value_size = max(float(el["value_font_size"]), 5.0)
    # منتصف القيمة في SVG: y + h×(0.72 مع تسمية | 0.54 بدونها).
    value_middle = el["y"] + el["height"] * (0.72 if el.get("show_label", True) else 0.54)
    max_value_width = el["width"] - 2 * el["padding_x"]
    if _needs_raster_text(raw_value) and _pdf_draw_arabic_text_image(
        pdf,
        raw_value,
        x=el["x"] + el["padding_x"],
        y=value_middle,
        size=value_size,
        color=el["ink"],
        weight=900,
        max_width=max_value_width,
        direction="rtl",
        opacity=1.0,
        ch=ch,
        anchor="middle",
        halign="center" if centered else "auto",
    ):
        return
    # بيانات الدخول لا تُبتر أبدًا: البتر بـ«…» كان يقصّ نصف رقم الكرت
    # عند تكبير الخط (شكوى client1) — بطاقة برقم ناقص عديمة القيمة.
    # بدل ذلك نصغّر حجم الخط حتى تتسع القيمة كاملة (أرضية 4pt).
    if max_value_width > 0:
        while (value_size > 4.0
               and pdf.stringWidth(value_text, value_font, value_size)
               > max_value_width):
            value_size -= 0.5
    pdf.setFont(value_font, value_size)
    pdf.setFillColor(_pdf_color(el["ink"]))
    value_baseline = ch - (value_middle + value_size * 0.26)
    if centered:
        pdf.drawCentredString(box_center_x, value_baseline, value_text)
    else:
        pdf.drawString(el["x"] + el["padding_x"], value_baseline, value_text)


def _pdf_qr(pdf, el: dict, ch: float) -> None:
    """Draw a QR symbol using the same QrCodeWidget as the SVG path.

    Tight white panel: `barBorder=0` strips the QrCodeWidget's built-in
    4-module quiet zone (which used to leave a large empty white band
    around the actual QR pattern). The remaining 4% inner padding plus
    the white background rectangle itself give the scanner enough
    quiet area without making the panel visually oversized.

    أنماط QR (نفس قيم معاينة SVG حرفيًا):
      - boxed «مربع واضح»: لوحة + وحدات مربعة (السلوك التاريخي).
      - rounded «ناعم»: لوحة + وحدات دائرية مرسومة يدويًا من نفس
        مصفوفة الوحدات.
      - clean «بسيط»: وحدات مربعة بلا لوحة/إطار خلف الرمز.
    """
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics import renderPDF
    from reportlab.graphics.shapes import Drawing

    size = float(el["size"])
    pdf_y_top = ch - el["y"]  # top of the QR box in PDF coords
    pdf_y_bottom = pdf_y_top - size
    style = _normalize_qr_style(el.get("style"))

    # «بسيط» clean: لا لوحة خلف الرمز — تبقى المنطقة الهادئة بنفس
    # المساحة (الحشوة الداخلية 4%) لكن بلا مستطيل أبيض/إطار.
    if style != "clean":
        # Rounded background sits at the model's allocated size.
        pdf.setFillColor(_pdf_color(el.get("bg", "#ffffff")))
        pdf.roundRect(el["x"], pdf_y_bottom, size, size, size * 0.10,
                      stroke=0, fill=1)

    payload = str(el.get("payload") or "—")
    inner = size * 0.92  # 4% padding each side — visually tight
    inner_x = el["x"] + (size - inner) / 2
    inner_y = pdf_y_bottom + (size - inner) / 2

    if style == "rounded":
        # «ناعم»: نرسم الوحدات دوائر يدويًا من نفس مصفوفة QrCodeWidget
        # المستعملة في معاينة SVG — فيتطابق الشكل المطبوع مع المعاينة.
        matrix = _qr_module_matrix(payload)
        if matrix:
            n = len(matrix)
            cell = inner / n
            radius = cell * 0.5
            pdf.setFillColor(_pdf_color(el.get("fg", "#0f172a")))
            for row_idx, row in enumerate(matrix):
                # محور Y في PDF من الأسفل؛ الصف الأول أعلى الرمز.
                cy = inner_y + inner - (row_idx + 0.5) * cell
                for col_idx, on in enumerate(row):
                    if not on:
                        continue
                    cx = inner_x + (col_idx + 0.5) * cell
                    if _qr_in_finder(row_idx, col_idx, n):
                        # مربعات التحديد تبقى مربعة (نفس معاينة SVG)
                        # حتى لا تفشل الماسحات في التقاط الرمز.
                        pdf.rect(cx - cell / 2, cy - cell / 2,
                                 cell * 1.02, cell * 1.02, stroke=0, fill=1)
                    else:
                        pdf.circle(cx, cy, radius, stroke=0, fill=1)
            return
        # فشل استخراج المصفوفة → نسقط للمسار المربع المعتاد أدناه.

    try:
        widget = QrCodeWidget(payload, barBorder=0)
        try:
            widget.barFillColor = _pdf_color(el.get("fg", "#0f172a"))
            widget.barStrokeColor = _pdf_color(el.get("fg", "#0f172a"))
        except Exception:
            pass
        bounds = widget.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        scale_x = inner / max(w, 1)
        scale_y = inner / max(h, 1)
        drawing = Drawing(inner, inner,
                          transform=[scale_x, 0, 0, scale_y, 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, pdf, inner_x, inner_y)
    except Exception:
        pass


def _pdf_safe_text(value: Any) -> str:
    """Legacy no-op kept for backward compatibility.

    Pre-Almarai this helper stripped non-Latin-1 characters so the
    default Helvetica font wouldn't crash on Arabic glyphs. Now that
    the renderer registers Almarai and shapes Arabic via
    arabic-reshaper + python-bidi, the strip is no longer needed and
    actively harmful (it would drop the very glyphs the new path
    knows how to render). The helper just coerces to str.
    """
    return str(value or "")


def _shrink_to_fit(pdf, text: str, font: str, size: float,
                    max_width: float) -> str:
    """Trim text with an ellipsis until it fits inside max_width."""
    if pdf.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    # Walk from the end, dropping one char at a time.
    out = text
    while out and pdf.stringWidth(out + ellipsis, font, size) > max_width:
        out = out[:-1]
    return (out + ellipsis) if out else ellipsis


# ───────────────────────────────────────────────────────────────────
# Internal helpers — model assembly
# ───────────────────────────────────────────────────────────────────

def _hydrate_layout(template: dict) -> dict:
    """Pull the JSON layout out of a template row in either shape."""
    layout = template.get("layout_json")
    if not isinstance(layout, dict):
        layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}
    return layout


def card_mm_box(layout: dict, canvas: tuple[float, float]) -> tuple[float, float]:
    """صندوق mm للبطاقة موجَّهًا باتجاه كانفس التصيير.

    الحفظ (operations._template_layout) يبدّل العرض/الارتفاع للبطاقات
    العمودية (85×54 ← 54×85) بينما معاينة غرفة التصميم الحية ترسل قيم
    الحقول كما هي بلا تبديل — فكانت قسمة mm→كسر تختلف بين ما يراه
    المستخدم قبل الحفظ وبين المعاينة/الطباعة بعده (انزياح العناصر
    للأعلى ولليمين على المحركات العمودية). توجيه الصندوق هنا مرة واحدة
    حسب اتجاه الكانفس يجعل كل المسارات تقسم على نفس البعدين مهما كان
    مصدر القيم (نموذج حي أو صف محفوظ). نفس المعادلة حرفيًا في
    cardSizeMm() بالواجهة وفي _effective_field_layout بالمسارات.
    """
    w = max(_float(layout.get("card_width_mm"), 85), 1.0)
    h = max(_float(layout.get("card_height_mm"), 54), 1.0)
    canvas_is_portrait = canvas[1] > canvas[0]
    if w != h and canvas_is_portrait != (h > w):
        w, h = h, w
    return w, h


def _resolve_positions(
    template: dict,
    layout: dict,
    canvas: tuple[int, int],
    *,
    render_direction: str = "ltr",
) -> dict[str, dict[str, float]]:
    """Map legacy mm-based positions into canvas fractions.

    Existing templates store username_x/y, password_x/y, qr_x/y at the
    top level of the template row, expressed in mm relative to a
    card_width_mm x card_height_mm card. We normalize them to canvas
    fractions so the same renderer handles old and new templates.

    If the legacy values are at their factory default (0, 0) we fall
    back to `_DEFAULT_POSITIONS`. This is the bug that made the old
    PDF stack USER/PASS/QR in the top-left corner.
    """
    card_w_mm, card_h_mm = card_mm_box(layout, canvas)

    orientation = "vertical" if canvas[1] > canvas[0] else "horizontal"
    positions = _engine_default_positions(render_direction, orientation=orientation)

    for legacy_key, target_key in (("username", "user"),
                                   ("password", "pass"),
                                   ("qr",       "qr")):
        raw_x = _float(template.get(f"{legacy_key}_x"), 0)
        raw_y = _float(template.get(f"{legacy_key}_y"), 0)
        if raw_x == 0 and raw_y == 0:
            continue  # keep defaults — that template never customised this
        fx = max(0.0, min(1.0, raw_x / card_w_mm))
        fy = max(0.0, min(1.0, raw_y / card_h_mm))
        positions[target_key]["x"] = fx
        positions[target_key]["y"] = fy

    qr_size_pct = _optional_positive_float(layout.get("qr_size_pct"))
    if qr_size_pct is not None:
        positions["qr"]["size"] = max(0.08, min(0.48, qr_size_pct / 100.0))

    return positions


def _engine_default_positions(
    render_direction: str,
    *,
    orientation: str = "horizontal",
) -> dict[str, dict[str, float]]:
    """Return default element positions for the selected language engine.

    Arabic engines are not just text-direction variants; the whole
    default composition is flipped so QR/barcode sits on the left and
    Arabic copy/pills sit on the right. Custom dragged coordinates are
    applied after these defaults and are treated as absolute positions
    in the active engine, so dragging never gets mirrored twice.
    """
    positions = {key: dict(value) for key, value in _DEFAULT_POSITIONS.items()}
    if orientation == "vertical":
        # Portrait cards need their own proportions. Reusing the
        # landscape text sizes makes headings huge and crops the lower
        # footer area once the whole card is scaled into a print cell.
        positions.update({
            "accent": {"x": 0.06, "y": 0.045, "width": 0.88, "height": 0.012},
            "brand":  {"x": 0.07, "y": 0.12, "size": 0.046},
            "title":  {"x": 0.07, "y": 0.20, "size": 0.056},
            "qr":     {"x": 0.62, "y": 0.30, "size": 0.24},
            "user":   {"x": 0.07, "y": 0.50, "width": 0.56, "height": 0.078},
            "pass":   {"x": 0.07, "y": 0.61, "width": 0.56, "height": 0.078},
            "meta":   {"x": 0.07, "y": 0.78, "size": 0.028},
            "footer": {"x": 0.07, "y": 0.86, "size": 0.027},
        })
        # Owner request (June 2026): the PORTRAIT card is CENTERED for both
        # languages — QR and credential pills sit on the horizontal centre
        # (x = (1 − width)/2). Text elements are centre-aligned in
        # build_card_render_model. No RTL side-mirror for vertical.
        for key in ("qr", "user", "pass"):
            pos = positions[key]
            span = pos.get("width") or pos.get("size") or 0.0
            if span:
                pos["x"] = max(0.0, (1.0 - float(span)) / 2.0)
        return positions
    if render_direction != "rtl":
        return positions
    # RTL true-mirror of the BOX elements (QR → left, credential pills →
    # right, accent bar): x → 1 − x − width. Horizontal only — vertical is
    # centred above. The TEXT headings (brand/title/meta/footer) are NOT
    # mirrored here — their visual width is the runtime `heading_width`/meta-
    # width (QR-dependent), known only in build_card_render_model, which
    # right-aligns them to the right margin so Arabic reads from the right.
    _text_keys = {"brand", "title", "meta", "footer"}
    for key, pos in positions.items():
        if key in _text_keys:
            continue
        span = pos.get("width") or pos.get("size") or 0.0
        if span:
            pos["x"] = max(0.0, min(1.0, 1.0 - float(pos.get("x", 0.0)) - float(span)))
    return positions


def _resolve_show_flags(layout: dict) -> dict[str, bool]:
    return {
        key: _boolish(layout.get(f"show_{key}"), default)
        for key, default in _DEFAULT_SHOW.items()
    }


def _credential_label(kind: str, language: str) -> str:
    if str(language or "").lower() == "arabic":
        return "اسم المستخدم" if kind == "user" else "كلمة المرور"
    return "USER" if kind == "user" else "PASS"


def _logo_element(layout: dict, canvas: tuple[int, int]) -> dict | None:
    """Build the optional logo image element, or None when absent.

    The logo is opt-in: it is only drawn when the layout carries a
    `logo_image_data_url` data URL. Position is expressed in mm in the
    `logo_x` / `logo_y` keys (same convention as the draggable pills);
    `logo_size_pct` is the logo box width as a percentage of the card
    width (0 / missing → a sensible 18% default). When position is the
    (0,0) sentinel the logo falls back to the top-left corner inset.
    """
    image_url = str(layout.get("logo_image_data_url") or "")
    if not image_url.startswith("data:image/"):
        return None
    canvas_w, canvas_h = canvas
    card_w_mm, card_h_mm = card_mm_box(layout, canvas)

    size_pct = _optional_positive_float(layout.get("logo_size_pct"))
    size_frac = max(0.05, min(0.6, (size_pct / 100.0))) if size_pct else 0.18
    box = size_frac * canvas_w

    raw_x = _float(layout.get("logo_x"), 0)
    raw_y = _float(layout.get("logo_y"), 0)
    if raw_x == 0 and raw_y == 0:
        # Default inset in the top-left corner.
        x = canvas_w * 0.06
        y = canvas_h * 0.06
    else:
        x = max(0.0, min(1.0, raw_x / card_w_mm)) * canvas_w
        y = max(0.0, min(1.0, raw_y / card_h_mm)) * canvas_h
    return {
        "kind": "image",
        "id": "logo",
        "href": image_url,
        "x": x,
        "y": y,
        "width": box,
        "height": box,
    }


def _background(layout: dict) -> dict:
    image_url = str(layout.get("background_image_data_url") or "")
    has_image = image_url.startswith("data:image/")
    raw_source = str(
        layout.get("background_source")
        or layout.get("background_style")
        or ""
    ).strip().lower()
    if raw_source in {"image", "stored_image", "photo", "upload", "uploaded"}:
        source = "image"
    elif raw_source in {"preset", "system", "graphics", "generated"}:
        source = "preset"
    elif raw_source == "gradient":
        source = "image" if has_image else "preset"
    else:
        source = "image" if has_image else "preset"
    if source == "image" and not has_image:
        source = "preset"
    # «خلفية من صورة» داخل تصميم النظام: علم صريح (لا مجرّد وجود صورة
    # محفوظة) كي لا يتغيّر رندر القوالب القديمة التي حُفظت بصورة ثم
    # رجعت لوضع النظام. الصورة تُرسم فوق التدرّج وتحت الزخرفة والطبقات.
    preset_bg_raw = layout.get("preset_background_image")
    preset_bg_enabled = (
        preset_bg_raw is True
        or str(preset_bg_raw or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    # Decorative pattern (lines / grid / signal bars / wave circle) colour
    # and transparency. Historically these were hardcoded to opaque-ish
    # white; the designer now lets the user pick both, so honour the saved
    # values and only fall back to the legacy white when nothing is set.
    pattern_color = _safe_hex(layout.get("pattern_color"), "#ffffff")
    pattern_opacity = layout.get("pattern_opacity")
    pattern_opacity = (
        max(0.0, min(1.0, _float(pattern_opacity, 1.0)))
        if pattern_opacity is not None and str(pattern_opacity) != ""
        else None
    )
    return {
        "source":         source,
        "gradient_start": _safe_hex(layout.get("gradient_start"), "#0f172a"),
        "gradient_end":   _safe_hex(layout.get("gradient_end"),   "#22a7bd"),
        "pattern":        str(layout.get("pattern_style") or "signal") if source == "preset" else "clean",
        "pattern_color":  pattern_color,
        # None means "use the per-pattern legacy default" so untouched
        # templates render exactly as before.
        "pattern_opacity": pattern_opacity,
        "image_data_url": image_url if has_image and (source == "image" or preset_bg_enabled) else "",
        "image_opacity":  1.0 if source == "image" else max(0.0, min(1.0, _float(layout.get("image_opacity"), 0.82))),
        # ملاءمة الصورة: cover (قصّ مركزي يملأ — الافتراضي التاريخي) /
        # contain (الصورة كاملة بلا قصّ، بأشرطة فارغة) / stretch (تمديد).
        # طلب المالك بعد شكوى «الصورة مقصوصة» لتصميم جاهز نسبته لا تطابق
        # نسبة البطاقة.
        "image_fit": (
            str(layout.get("image_fit") or "cover").strip().lower()
            if str(layout.get("image_fit") or "cover").strip().lower()
            in {"cover", "contain", "stretch"} else "cover"),
    }


def _is_uploaded_design(layout: dict) -> bool:
    bg = _background(layout)
    return bg.get("source") == "image" and bool(bg.get("image_data_url"))


def _qr_login_payload(layout: dict, username: str, password: str, card_id: str) -> str:
    """يبني محتوى رمز QR المطبوع على البطاقة.

    الأولوية لحقل القالب الجديد «رابط دخول الهوت سبوت (DNS)»
    (hotspot_login_url): عند تعبئته يصبح الرمز رابط دخول تلقائي
    بصيغة ميكروتك الرسمية:

        http://<العنوان>/login?username=<u>&password=<p>&u=<u>&p=<p>

    - username/password: تستهلكهما RouterOS مباشرة (HTTP-PAP) فيدخل
      الزبون فور المسح دون فتح صفحة الدخول.
    - u/p: مفتاحا الاحتياط اللذان تقرؤهما جافاسكربت الدخول التلقائي
      المحقونة في قوالب صفحات الهوت سبوت لدينا
      (QR_AUTOLOGIN_USER_KEY / QR_AUTOLOGIN_PASS_KEY في
      hotspot_templates.py) — إن رفض الراوتر الدخول عبر GET
      (CHAP فقط مثلًا) تُعرض صفحة الدخول فتعبّئ الجافاسكربت الحقول
      وترسل النموذج تلقائيًا.

    القوالب القديمة (بدون hotspot_login_url) تحافظ على سلوكها
    حرفيًا: login_url / hotspot_address / hotspot_url ← رابط
    /login?username=&password= فقط، وبدون أي عنوان ← اسم المستخدم
    نصًا كما كان.
    """
    user = str(username or card_id or "—")
    secret = str(password or "")
    # الحقل المخصص الجديد — يضيف مفتاحي u/p للتوافق مع جافاسكربت
    # صفحة الدخول إضافة إلى صيغة ميكروتك القياسية.
    autologin_host = str(layout.get("hotspot_login_url") or "").strip()
    host = autologin_host or str(
        layout.get("login_url")
        or layout.get("hotspot_address")
        or layout.get("hotspot_url")
        or ""
    ).strip()
    if not host:
        return user
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", host):
        host = "http://" + host
    base = host.rstrip("/")
    if not base.lower().endswith("/login"):
        base += "/login"
    params = {"username": user, "password": secret}
    if autologin_host:
        # مفتاحا الاحتياط لصفحات الدخول المنشورة من مصمم الهوت سبوت.
        params["u"] = user
        params["p"] = secret
    return base + "?" + urlencode(params)


def _override(overrides: dict, key: str, layout: dict, default: str) -> str:
    candidate = overrides.get(key)
    if candidate is None or not str(candidate).strip():
        candidate = layout.get(key)
    value = (candidate if candidate is not None else default)
    return str(value).strip()


def _extract_card_fields(card: dict | object | None) -> tuple[str, str, str]:
    # لا نُعيد قيمًا وهمية تظهر كاسم بطاقة حقيقي. عند انعدام البطاقة
    # نُعيد «—» علامةً محايدة، والمستدعي يقرّر هل يرسم البطاقة كمعاينة
    # هندسية أو يُظهر شارة «بدون بطاقات حقيقية بعد».
    if card is None:
        return "—", "********", ""
    if isinstance(card, dict):
        username = str(card.get("username") or "").strip()
        password = str(card.get("password") or "").strip()
        card_id  = str(card.get("id") or card.get("serial") or "").strip()
    else:
        username = str(getattr(card, "username", "") or "").strip()
        password = str(getattr(card, "password", "") or "").strip()
        card_id  = str(getattr(card, "id", "") or "").strip()
    return username or "—", password or "********", card_id


# ── Heading fit-to-width (no clipping in any of the 4 modes) ──────────
# The card title / brand must always fit fully inside the available width.
# Every render path constrains text to `max_width` (SVG clip-path, Arabic
# raster ellipsis-trim, PDF ellipsis-trim), so a too-long heading used to be
# CLIPPED — worst on the narrow VERTICAL card («بطاقة واي فاي» → «بطاقة وا
# فا»). We instead measure the run with the card's own font (Cairo, via PIL —
# the same family the SVG/raster paths use) and auto-shrink the font to fit;
# if it still won't fit at a sensible minimum we wrap onto two balanced lines.
# Works for both scripts: Arabic (shaped, RTL, connected glyphs) and Latin.
_HEADING_FIT_TARGET = 0.94   # leave a small safety gap inside max_width
_HEADING_LINE_STEP = 1.18    # line advance as a multiple of font size


def _measure_text_width(text: str, size_px: float, *, weight: int,
                        direction: str) -> float:
    """Rendered width (px) of `text` at `size_px` in the card font."""
    text = str(text or "")
    if not text.strip():
        return 0.0
    size_px = max(1.0, float(size_px))
    try:
        from PIL import ImageFont
        use_raqm = _pil_supports_raqm()
        path = (_arabic_raster_font_path(weight=int(weight)) if use_raqm
                else _font_path_for_arabic(bold=int(weight) >= 600))
        if path and os.path.isfile(path):
            font = ImageFont.truetype(path, max(1, int(round(size_px))))
            probe = text if use_raqm else _shape_arabic(text)
            d = "rtl" if direction == "rtl" else "ltr"
            bbox = _arabic_run_bbox(font, probe, use_raqm=use_raqm, direction=d)
            return float(bbox[2] - bbox[0])
    except Exception:  # pragma: no cover — measurement is best-effort
        pass
    # Heuristic fallback when PIL/font is unavailable: width ≈ chars × size
    # × per-script factor (Arabic glyphs are a touch wider on average).
    factor = 0.56 if _has_arabic(text) else 0.52
    return len(text) * size_px * factor


def _split_two_lines(text: str) -> tuple[str, str]:
    """Split into two length-balanced lines at a word boundary. A single
    unsplittable token returns (text, "")."""
    words = str(text or "").split()
    if len(words) < 2:
        return str(text or "").strip(), ""
    best_i, best_diff = 1, None
    for i in range(1, len(words)):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(left) - len(right))
        if best_diff is None or diff < best_diff:
            best_diff, best_i = diff, i
    return " ".join(words[:best_i]), " ".join(words[best_i:])


def _fit_heading(text: str, base_size_px: float, max_width_px: float, *,
                 weight: int, direction: str, min_size_px: float,
                 allow_wrap: bool = True,
                 two_line_size_cap: float | None = None) -> tuple[float, list[str]]:
    """Return (font_size_px, lines) so the heading fits inside max_width with
    no clipping. Shrink-to-fit first; wrap to two lines only as a fallback."""
    text = str(text or "")
    if max_width_px <= 0 or not text.strip():
        return base_size_px, [text]
    target = max_width_px * _HEADING_FIT_TARGET
    w = _measure_text_width(text, base_size_px, weight=weight, direction=direction)
    if w <= target:
        return base_size_px, [text]
    one_line = base_size_px * (target / w)
    if one_line >= min_size_px or not allow_wrap:
        return max(one_line, 1.0), [text]
    l1, l2 = _split_two_lines(text)
    if not l2:  # single long token — shrink below min rather than truncate
        return max(one_line, 1.0), [text]
    wmax = max(
        _measure_text_width(l1, base_size_px, weight=weight, direction=direction),
        _measure_text_width(l2, base_size_px, weight=weight, direction=direction),
    ) or 1.0
    two = base_size_px if wmax <= target else base_size_px * (target / wmax)
    if two_line_size_cap:
        two = min(two, two_line_size_cap)
    return max(two, 1.0), [l1, l2]


def _text_element(*, id: str, text: str, pos: dict, canvas: tuple[int, int],
                   color: str, weight: int, max_width_frac: float,
                   direction: str = "ltr", size_px: float | None = None,
                   y_px: float | None = None, x_px: float | None = None,
                   align: str | None = None) -> dict:
    cw, ch = canvas
    el = {
        "kind": "text",
        "id": id,
        "text": text,
        "x": x_px if x_px is not None else pos["x"] * cw,
        "y": y_px if y_px is not None else pos["y"] * ch,
        "size": size_px if size_px is not None else pos["size"] * ch,
        "color": color,
        "weight": weight,
        "max_width": cw * max_width_frac,
        "direction": direction,
    }
    if align:
        el["align"] = align
    return el


def _pill_element(*, id: str, label: str, value: str, pos: dict,
                   canvas: tuple[int, int], surface_color: str,
                   surface_enabled: bool = True,
                   surface_opacity: float = 0.95,
                   ink: str = "#0f172a",
                   label_color: str = "#64748b",
                   value_font_size: float | None = None,
                   label_font_size: float | None = None,
                   is_password: bool = False,
                   label_direction: str = "ltr",
                   show_label: bool = True,
                   align: str | None = None) -> dict:
    cw, ch = canvas
    width = pos.get("width", 0.46) * cw
    height = pos.get("height", 0.13) * ch
    # حجم خط مخصص أكبر من سقف الحبة كان يُقصّ سرًّا بحامي «عدم البتر»
    # فيبدو أن تغيير المقاس «لا يعمل» (شكوى المالك) — الآن الحبة تتوسع
    # مع الخط: الارتفاع يتبعه، والعرض يتسع للنص كاملًا (سقفا أمان:
    # 30% من ارتفاع الكانفس و96% من عرضه).
    if value_font_size:
        needed_h = float(value_font_size) / (0.52 if show_label else 0.54)
        if needed_h > height:
            height = min(needed_h, ch * 0.30)
        raw_value = str(value or "")
        if raw_value and not _has_arabic(raw_value):
            try:
                from reportlab.pdfbase.pdfmetrics import stringWidth
                pad = height * 0.32
                needed_w = stringWidth(raw_value, "Helvetica-Bold",
                                       float(value_font_size)) + 2 * pad
                if needed_w > width:
                    width = min(needed_w, cw * 0.96)
            except Exception:  # noqa: BLE001 — تقدير؛ لا يكسر النموذج
                pass
    x = pos["x"] * cw
    y = pos["y"] * ch
    # إبقاء الحبة داخل الكانفس بعد التوسّع.
    x = max(0.0, min(x, cw - width))
    y = max(0.0, min(y, ch - height))
    return {
        "kind": "pill",
        "id": id,
        "label": label,
        "value": value,
        "align": align,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "surface": surface_color,
        "surface_enabled": surface_enabled,
        "surface_opacity": surface_opacity,
        "ink": ink,
        "label_color": label_color,
        "is_password": is_password,
        "show_label": show_label,
        "label_direction": label_direction,
        "value_direction": "ltr",
        "value_font_size": value_font_size or height * 0.52,
        "label_font_size": label_font_size or height * 0.30,
        "padding_x": height * 0.32,
    }


# ───────────────────────────────────────────────────────────────────
# Internal helpers — SVG output
# ───────────────────────────────────────────────────────────────────

def _svg_defs(bg: dict, w: int, h: int, *, bg_id: str, pattern_id: str) -> Iterable[str]:
    yield (
        f'<linearGradient id="{bg_id}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{_xml(bg.get("gradient_start", "#0f172a"))}"/>'
        f'<stop offset="100%" stop-color="{_xml(bg.get("gradient_end", "#22a7bd"))}"/>'
        f'</linearGradient>'
    )
    # Decorative pattern overlays. Colour + per-stop alpha come from the
    # saved layout (pattern_color / pattern_opacity); the structural
    # opacity on the overlay rect is applied in `_svg_background`.
    pattern = bg.get("pattern") or "signal"
    deco = _xml(bg.get("pattern_color") or "#ffffff")
    if pattern == "grid":
        step = max(int(w * 0.045), 8)
        yield (
            f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" '
            f'width="{step}" height="{step}">'
            f'<path d="M{step} 0 L0 0 0 {step}" fill="none" '
            f'stroke="{deco}" stroke-width="1"/>'
            f'</pattern>'
        )
    elif pattern == "wave":
        # Two faint radial highlights, drawn as a single SVG pattern.
        yield (
            f'<radialGradient id="{pattern_id}" cx="20%" cy="30%" r="55%">'
            f'<stop offset="0%"  stop-color="{deco}"/>'
            f'<stop offset="60%" stop-color="{deco}" stop-opacity="0"/>'
            f'</radialGradient>'
        )
    elif pattern == "signal":
        # Vertical signal bars at the bottom 30 % of the card.
        yield (
            f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" '
            f'width="{max(int(w*0.025),6)}" height="{h}">'
            f'<rect x="0" y="{int(h*0.7)}" width="{max(int(w*0.005),2)}" '
            f'height="{int(h*0.3)}" fill="{deco}"/>'
            f'</pattern>'
        )
    # "clean" emits no overlay.


def _svg_fit_par(bg: dict) -> str:
    """image_fit → preserveAspectRatio (cover=slice / contain=meet / stretch=none)."""
    fit = str(bg.get("image_fit") or "cover")
    if fit == "contain":
        return "xMidYMid meet"
    if fit == "stretch":
        return "none"
    return "xMidYMid slice"


def _svg_background(bg: dict, w: int, h: int, *, bg_id: str, pattern_id: str) -> Iterable[str]:
    image_url = bg.get("image_data_url") or ""
    if str(bg.get("source") or "preset") == "image" and image_url:
        opacity = bg.get("image_opacity", 1.0)
        yield (
            f'<image href="{_xml(image_url)}" x="0" y="0" '
            f'width="{w}" height="{h}" '
            f'preserveAspectRatio="{_svg_fit_par(bg)}" opacity="{opacity:.2f}"/>'
        )
        return

    yield f'<rect x="0" y="0" width="{w}" height="{h}" fill="url(#{bg_id})"/>'
    # «خلفية من صورة» داخل تصميم النظام: فوق التدرّج وتحت الزخرفة/الطبقات،
    # بشفافية image_opacity (تصل هنا فقط عند تفعيل preset_background_image).
    if image_url.startswith("data:image/"):
        opacity = bg.get("image_opacity", 0.82)
        yield (
            f'<image href="{_xml(image_url)}" x="0" y="0" '
            f'width="{w}" height="{h}" '
            f'preserveAspectRatio="{_svg_fit_par(bg)}" opacity="{opacity:.2f}"/>'
        )
    pattern = bg.get("pattern") or "signal"
    # Legacy per-pattern visual alpha (def-stop alpha × overlay alpha) so a
    # template that never set pattern_opacity looks identical to before.
    legacy_overlay = {"grid": 0.20, "signal": 0.18, "wave": 0.30}
    saved_opacity = bg.get("pattern_opacity")
    overlay = saved_opacity if saved_opacity is not None else legacy_overlay.get(pattern, 0.30)
    if pattern in {"grid", "signal"}:
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#{pattern_id})" opacity="{overlay:.3f}"/>'
        )
    elif pattern == "wave":
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#{pattern_id})" opacity="{overlay:.3f}"/>'
        )


def _svg_rect(el: dict) -> str:
    return (
        f'<rect x="{el["x"]:.1f}" y="{el["y"]:.1f}" '
        f'width="{el["width"]:.1f}" height="{el["height"]:.1f}" '
        f'rx="{el.get("rx", 0):.1f}" ry="{el.get("rx", 0):.1f}" '
        f'fill="{_xml(el.get("fill", "#fff"))}"/>'
    )


def _svg_image(el: dict) -> str:
    """Render a logo / decorative image element.

    Wrapped in `<g class="card-logo">` so the live designer can attach a
    drag handle the same way it does for `.card-pill` / `.card-qr`. The
    image keeps its aspect ratio inside the box (xMidYMid meet).
    """
    opacity = float(el.get("opacity", 1.0))
    return (
        f'<g class="card-logo">'
        f'<image href="{_xml(el["href"])}" '
        f'x="{el["x"]:.1f}" y="{el["y"]:.1f}" '
        f'width="{el["width"]:.1f}" height="{el["height"]:.1f}" '
        f'preserveAspectRatio="xMidYMid meet" opacity="{opacity:.2f}"/>'
        f'</g>'
    )


def _svg_motif(el: dict) -> str:
    """يَرسم رمز قِطاعي (icon أو watermark) — يُغلَّف في <g> بصنف يَدلّ
    على دَوره كي يَستطيع المُصمِّم الحَيّ عَزله أو إخفاءه إن لَزم.

    الـmotif يأتي من card_motifs.motif_svg ويُعيد عناصر SVG داخليّة
    (paths/circles/rects)، فنُلفّها بـ<g> بِالـclass المناسب.
    """
    from .card_motifs import motif_svg
    kind = "card-watermark" if el.get("kind") == "watermark" else "card-icon"
    body = motif_svg(
        str(el.get("motif") or "wifi"),
        float(el["cx"]), float(el["cy"]), float(el["size"]),
        color=str(el.get("color") or "#ffffff"),
        opacity=float(el.get("opacity") or 1.0),
    )
    return f'<g class="{kind}" data-motif="{_xml(str(el.get("motif") or ""))}">{body}</g>'


def _svg_pattern_bg(el: dict) -> str:
    """يَرسم خَلفيّة نَمطيّة قِطاعيّة كاملة الكَنفاس عبر SVG <pattern>.

    يُولّد ‎<defs><pattern>‎ مَع motifs الـvertical (يَتَكَرّر تلقائيًّا)
    و‎<rect>‎ كامل الكَنفاس بـ‎fill="url(#hr-pat-…)"‎. الـopacity على
    الـrect كي تَتأثّر الـpaths كَكَتلة واحدة (لا حاجة لـpath-level
    opacity). نَستعمل id فَريد per-element لتَفادي تَصادم مَع cards
    مُتعَدّدة في نَفس الصَفحة.
    """
    from . import card_motif_patterns
    vertical = str(el.get("vertical") or "generic").strip().lower()
    color = str(el.get("color") or "#ffffff")
    opacity = float(el.get("opacity") or 0.06)
    cw = float(el.get("canvas_w") or 1000)
    ch = float(el.get("canvas_h") or 600)
    # id فَريد per element عبر hash للـvertical+color+canvas (يَكفي للتَمايز)
    pat_id = f"hr-pat-{vertical}-{int(cw)}x{int(ch)}"
    pattern_svg = card_motif_patterns.build_pattern_svg(
        vertical, pattern_id=pat_id)
    # CSS لِتَلوين currentColor: نُحَدّد color على الـ<g> الخارجي.
    return (
        f'<g class="card-pattern-bg" data-vertical="{_xml(vertical)}" '
        f'style="color:{_xml(color)}">'
        f'<defs>{pattern_svg}</defs>'
        f'<rect x="0" y="0" width="{cw:.1f}" height="{ch:.1f}" '
        f'fill="url(#{pat_id})" opacity="{opacity:.3f}"/>'
        f'</g>'
    )


def _pdf_pattern_bg(pdf, el: dict, ch: float) -> None:
    """يَرسم نَفس الخَلفيّة النَمطيّة على ReportLab canvas — يُحَوّل
    الـSVG pattern (يَتَطلّب libcairo/wand) إلى عَدّة دَعَوات رَسم عبر
    svglib المُتاحة، أو يَلجأ لِتَكرار يَدوي للـtile."""
    try:
        from svglib.svglib import svg2rlg
    except ImportError:
        return
    from io import StringIO
    from reportlab.graphics import renderPDF
    from . import card_motif_patterns

    vertical = str(el.get("vertical") or "generic").strip().lower()
    color = str(el.get("color") or "#ffffff")
    opacity = float(el.get("opacity") or 0.06)
    cw = float(el.get("canvas_w") or 1000)
    ch_canvas = float(el.get("canvas_h") or ch)
    # ReportLab مَع svglib لا يَدعم SVG <pattern> فيلْفيًا — نَستعمل
    # تَكرار يَدويّ: نَبني tile واحد (220×220) ثم نَرسمه مَرّات عَديدة
    # عبر transforms (translate). أرخص من توليد ‎<pattern>‎ ضَخم.
    tile_size = 220.0
    paths = card_motif_patterns.build_tile_paths(vertical,
                                                    tile_size=tile_size)
    tile_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{tile_size:.0f}" height="{tile_size:.0f}" '
        f'viewBox="0 0 {tile_size:.0f} {tile_size:.0f}" '
        f'color="{color}" fill="none" stroke="{color}">'
        f'{paths.replace("currentColor", color)}'
        f'</svg>'
    )
    try:
        tile_drawing = svg2rlg(StringIO(tile_svg))
        if tile_drawing is None:
            return
        pdf.saveState()
        pdf.setFillAlpha(opacity)
        pdf.setStrokeAlpha(opacity)
        # تَكرار في شَبكة تَغطّي كامل الكَنفاس. ReportLab y bottom-up.
        cols = int(cw // tile_size) + 1
        rows = int(ch_canvas // tile_size) + 1
        for r in range(rows):
            for c in range(cols):
                tx = c * tile_size
                ty = ch - (r + 1) * tile_size  # bottom-up
                renderPDF.draw(tile_drawing, pdf, tx, ty)
        pdf.restoreState()
    except Exception:
        pass


def _pdf_motif(pdf, el: dict, ch: float) -> None:
    """يَرسم نَفس رمز motif على ReportLab canvas بالـtransform المُناسب
    (PDF محور y مَقلوب: top→bottom). نَستعمل نَفس مُولّد SVG ثم نُحوّل
    إلى رسم PDF مُكافئ عبر renderPDF.drawToString — لكن أبسط: نَستعمل
    Drawing بأشكال أصليّة. هنا نَلجأ لخدمة renderPDF.drawToFileLike
    لسهولة الصيانة، أو نَرسم بأشكال ReportLab أصليّة. للحَفاظ على
    البساطة نَستعمل svglib لتَحويل النَصّ نفسه."""
    try:
        from svglib.svglib import svg2rlg
    except ImportError:
        svg2rlg = None  # type: ignore
    from io import StringIO
    from reportlab.graphics import renderPDF
    from .card_motifs import motif_svg

    size = float(el["size"])
    cx = float(el["cx"])
    cy = float(el["cy"])
    color = str(el.get("color") or "#ffffff")
    opacity = float(el.get("opacity") or 1.0)
    motif_key = str(el.get("motif") or "wifi")

    if svg2rlg is None:
        # fallback مُبسَّط: دائرة بحجم box (يَبقى التَصدير قابلًا للقراءة
        # حتى لو لم تكن svglib مُتاحة في البيئة المُختبرة).
        pdf.saveState()
        pdf.setFillColor(_pdf_color(color), alpha=opacity)
        pdf.setStrokeColor(_pdf_color(color), alpha=opacity)
        pdf_y = ch - cy
        pdf.circle(cx, pdf_y, size * 0.3, stroke=1, fill=0)
        pdf.restoreState()
        return

    # نَبني SVG مُستقلّ بإحداثيّات local (motif مَركّز عند (cx, cy)
    # في نَفس فَضاء الكَنفاس) ثم نُحوّله إلى Drawing ونَرسمه عند 0,0
    # بإحداثيّات PDF — renderPDF.draw يَأخذ x/y سُفلى-يَسار.
    inner = motif_svg(motif_key, cx, cy, size,
                       color=color, opacity=opacity)
    # canvas svg = نَفس أبعاد الكَنفاس الأصليّة كي يَستقرّ الـmotif في مَكانه
    # عند الـoverlay على الـPDF form (الذي يُستعمل بنفس قَياسات الكَنفاس).
    canvas_w = max(1.0, cx + size)
    canvas_h = max(1.0, cy + size)
    full = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{canvas_w:.1f}" height="{canvas_h:.1f}" '
        f'viewBox="0 0 {canvas_w:.1f} {canvas_h:.1f}">{inner}</svg>'
    )
    try:
        drawing = svg2rlg(StringIO(full))
        if drawing is None:
            return
        # رَسم في نَفس مَوضع الكَنفاس بقَلب محور y (المَوضع SVG y →
        # ReportLab y = ch - y بَعد الـtranslate).
        # نُطَبّق الـopacity على الـgraphics state — renderPDF.draw لا
        # يَقرأ ‎opacity=""‎ من svg attribute فيَخرج solid على ‎renderPDF‎.
        pdf.saveState()
        if opacity < 1.0:
            pdf.setFillAlpha(opacity)
            pdf.setStrokeAlpha(opacity)
        renderPDF.draw(drawing, pdf, 0, ch - canvas_h)
        pdf.restoreState()
    except Exception:
        # لا نَكسر التَصدير على فَشل غير مُتوقّع — الرمز زَخرفيّ.
        pass


def _svg_text(el: dict, *, uid: str) -> str:
    weight = el.get("weight", 700)
    opacity = el.get("opacity", 1.0)
    direction = "rtl" if el.get("direction") == "rtl" else "ltr"
    text = str(el.get("text", ""))
    is_arabic = _has_arabic(text)
    display_text = _shape_arabic(text) if is_arabic else text
    # This SVG is the source snapshot for PDF export. Do not rely on
    # the SVG rasterizer to shape Arabic; emit visual glyph order here
    # and keep the logical text only in data-original.
    #
    # Geometry is ALWAYS laid out left-to-right and right-alignment for
    # RTL engines is expressed purely by the anchor (`text-anchor="end"`
    # at the box's right edge below). This mirrors the PDF adapter, which
    # right-aligns every run with `drawRightString(x + max_width, …)`
    # regardless of script. Leaving `direction="rtl"` on the <text> made
    # the browser re-anchor/re-order pure-Latin runs (brand "HobeRadius",
    # footer "support@hobe.net") to a different x than the PDF — so they
    # walked off the canvas in the live preview while the export drew them
    # correctly. Forcing ltr here keeps preview == print == export for
    # Latin copy on Arabic (vertical and horizontal) cards. Arabic runs
    # are already shaped into visual order above, so ltr is correct for
    # them too; `bidi-override` simply locks that order.
    svg_direction = "ltr"
    unicode_bidi = "bidi-override" if is_arabic else "embed"
    max_width = float(el.get("max_width") or 0)
    x = float(el["x"])
    anchor = "start"
    if el.get("align") == "center":
        # Centred headings (portrait cards): anchor at the box's horizontal
        # centre. Bidi/shaping unchanged — this is alignment only.
        x = x + max_width / 2 if max_width > 0 else x
        anchor = "middle"
    elif direction == "rtl":
        x = x + max_width if max_width > 0 else x
        anchor = "end"
    clip_id = _svg_id(f"{uid}-clip-text", el.get("id", "text"))
    clip_rect = ""
    clip_attr = ""
    if max_width > 0:
        clip_x = float(el["x"])
        clip_h = float(el["size"]) * 1.35
        clip_rect = (
            f'<clipPath id="{clip_id}">'
            f'<rect x="{clip_x:.1f}" y="{float(el["y"]):.1f}" '
            f'width="{max_width:.1f}" height="{clip_h:.1f}"/>'
            f'</clipPath>'
        )
        clip_attr = f' clip-path="url(#{clip_id})"'
    return (
        f'{clip_rect}'
        f'<text x="{x:.1f}" y="{el["y"]:.1f}" '
        f'{clip_attr} '
        f'data-original="{_xml(text)}" '
        f'data-render-direction="{direction}" '
        f'direction="{svg_direction}" unicode-bidi="{unicode_bidi}" '
        f'font-family="\'Cairo\', \'Almarai\', \'Noto Kufi Arabic\', Tahoma, Arial, sans-serif" '
        f'font-size="{el["size"]:.1f}" font-weight="{weight}" '
        f'fill="{_xml(el.get("color", "#fff"))}" opacity="{opacity:.2f}" '
        f'dominant-baseline="hanging" text-anchor="{anchor}" xml:space="preserve">'
        f'{_xml(display_text)}'
        f'</text>'
    )


def _svg_pill(el: dict, *, mask_password: bool, uid: str) -> str:
    value = str(el["value"])
    if mask_password and el.get("is_password"):
        value = "•" * min(max(len(value), 6), 10)
    display_value = _shape_arabic(value) if _has_arabic(value) else value
    label_size = el["label_font_size"]
    value_size = el["value_font_size"]
    pad = el["padding_x"]
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    # بيانات الدخول لا تُقصّ: clipPath كان يقطع القيمة بصمت عند تكبير
    # الخط. نصغّر الحجم حتى تتسع كاملة — بنفس مقياس Helvetica-Bold الذي
    # يستعمله تصدير PDF فتتطابق المعاينة مع الملف (أرضية 4pt).
    inner_w = max(w - 2 * pad, 1.0)
    if display_value and not _has_arabic(value):
        try:
            from reportlab.pdfbase.pdfmetrics import stringWidth
            _vs = float(value_size)
            while _vs > 4.0 and stringWidth(display_value, "Helvetica-Bold", _vs) > inner_w:
                _vs -= 0.5
            value_size = _vs
        except Exception:  # noqa: BLE001 — قياس تقريبي؛ لا يكسر المعاينة
            pass
    label_y = y + h * 0.36
    show_label = bool(el.get("show_label", True))
    value_y = y + h * (0.72 if show_label else 0.54)
    label_dir = "rtl" if el.get("label_direction") == "rtl" else "ltr"
    label_text = str(el.get("label", ""))
    label_is_arabic = _has_arabic(label_text)
    display_label = _shape_arabic(label_text) if label_is_arabic else label_text
    svg_label_dir = "ltr" if label_is_arabic else label_dir
    label_unicode_bidi = "bidi-override" if label_is_arabic else "embed"
    centered = el.get("align") == "center"
    if centered:
        label_x = x + w / 2
        label_anchor = "middle"
    else:
        label_x = x + w - pad if label_dir == "rtl" else x + pad
        label_anchor = "end" if label_dir == "rtl" else "start"
    value_x = x + w / 2 if centered else x + pad
    value_anchor = "middle" if centered else "start"
    clip_id = _svg_id(f"{uid}-clip-pill", el.get("id", "pill"))
    text_clip = (
        f'<clipPath id="{clip_id}">'
        f'<rect x="{x+pad:.1f}" y="{y:.1f}" width="{max(w-2*pad, 1):.1f}" height="{h:.1f}"/>'
        f'</clipPath>'
    )
    surface_fill, surface_opacity = _svg_fill_opacity(
        el["surface"], opacity=float(el.get("surface_opacity", 0.95))
    )
    surface = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{h*0.20:.1f}" ry="{h*0.20:.1f}" '
        f'fill="{_xml(surface_fill)}" opacity="{surface_opacity:.3f}"/>'
        if el.get("surface_enabled", True) else ""
    )
    label_svg = (
        f'<text x="{label_x:.1f}" y="{label_y:.1f}" clip-path="url(#{clip_id})" '
        f'data-original="{_xml(label_text)}" '
        f'data-render-direction="{label_dir}" '
        f'direction="{svg_label_dir}" unicode-bidi="{label_unicode_bidi}" '
        f'font-family="\'Cairo\', \'Almarai\', \'Noto Kufi Arabic\', Tahoma, Arial, sans-serif" '
        f'font-size="{label_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["label_color"])}" '
        f'dominant-baseline="middle" text-anchor="{label_anchor}" xml:space="preserve">'
        f'{_xml(display_label)}</text>'
        if show_label else ""
    )
    return (
        f'<g class="card-pill">'
        f'{text_clip}'
        f'{surface}'
        f'{label_svg}'
        # خط القيمة Helvetica/Arial — نفس Helvetica-Bold التي يرسم بها
        # تصدير PDF اليوزر/الباس فعلًا. كان monospace (Menlo/Consolas)
        # فظهرت الحروف في المعاينة بشكل وعرض مختلفين عن الملف المصدَّر
        # (الخلل المُبلَّغ: «الخط يختلف»). التصدير هو الحقيقة فطُوبقت
        # المعاينة عليه.
        f'<text x="{value_x:.1f}" y="{value_y:.1f}" clip-path="url(#{clip_id})" '
        f'direction="ltr" '
        f'font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{value_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["ink"])}" '
        f'dominant-baseline="middle" text-anchor="{value_anchor}" xml:space="preserve">'
        f'{_xml(display_value)}</text>'
        f'</g>'
    )


def _qr_in_finder(row: int, col: int, n: int) -> bool:
    """هل الوحدة داخل أحد مربعات التحديد الثلاثة (7×7 في الزوايا)؟

    في النمط «الناعم» نُبقي مربعات التحديد مربعةً صلبة ونرسم وحدات
    البيانات فقط دوائر — هذا ما تفعله مولدات QR المنقّطة الشائعة لأن
    الماسحات تعتمد على حواف مربعات التحديد الحادة لالتقاط الرمز.
    """
    return (
        (row < 7 and col < 7)
        or (row < 7 and col >= n - 7)
        or (row >= n - 7 and col < 7)
    )


def _qr_module_matrix(payload: str) -> list[list[bool]] | None:
    """يستخرج مصفوفة وحدات QR (صح/خطأ) لنفس المكتبة في المسارين.

    نفس QrCodeWidget الذي يستعمله محوّل PDF — فيتطابق الرمز المعروض
    في المعاينة مع المطبوع. ترجع None عند أي فشل (حمولة طويلة جدًا…)
    فيتكفّل المستدعي بالرسم البديل.
    """
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget

        widget = QrCodeWidget(payload)
        widget.getBounds()
        # `.qr.modules`: كل صف إما سلسلة "1"/"0" أو قائمة قيم منطقية
        # حسب نسخة reportlab — نوحّد الشكلين إلى bool.
        qr = getattr(widget, "qr", None)
        modules = getattr(qr, "modules", None) if qr is not None else None
        if not modules or len(modules) <= 0:
            return None
        matrix: list[list[bool]] = []
        for row in modules:
            matrix.append([
                bool(int(value)) if isinstance(value, str) else bool(value)
                for value in row
            ])
        return matrix
    except Exception:
        return None


def _svg_qr_placeholder(el: dict) -> str:
    """Render the QR as inline SVG.

    Walks the QrCode bit matrix from reportlab.graphics.barcode.qr —
    the SAME library the PDF adapter uses — and emits one big
    `<rect>` for the white quiet-zone background plus one `<rect>`
    per dark module. This guarantees the preview and the PDF show
    the same QR symbol for the same payload.

    أنماط QR الثلاثة (نمط QR في المصمم):
      - boxed «مربع واضح»: لوحة بيضاء بزوايا مدوّرة + وحدات مربعة —
        السلوك التاريخي بلا تغيير.
      - rounded «ناعم»: نفس اللوحة لكن الوحدات دوائر ناعمة.
      - clean «بسيط»: بلا لوحة/إطار خلف الرمز إطلاقًا — الوحدات فقط
        فوق خلفية البطاقة (تبقى المنطقة الهادئة محفوظة بالمساحة نفسها).

    Falls back to a labelled placeholder square if the QR engine
    fails for any reason (e.g. extremely long payload). The card
    layout never depends on the QR shape — only on the slot.
    """
    payload = el["payload"]
    size = max(float(el["size"]), 16.0)
    x = float(el["x"]); y = float(el["y"])
    bg = el.get("bg", "#fff")
    fg = el.get("fg", "#0f172a")
    style = _normalize_qr_style(el.get("style"))
    # 4 % inner padding to match the PDF adapter — keeps the white
    # panel hugging the QR symbol instead of floating around it.
    pad = size * 0.04
    inner = _qr_inline_svg(payload, x + pad, y + pad, size - 2 * pad, fg,
                           style=style)
    # «بسيط»: لا نرسم لوحة الخلفية إطلاقًا — الوحدات مباشرة فوق البطاقة.
    panel = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" '
        f'rx="{size*0.10:.1f}" ry="{size*0.10:.1f}" '
        f'fill="{_xml(bg)}"/>'
        if style != "clean" else ""
    )
    return (
        f'<g class="card-qr">'
        f'{panel}'
        f'{inner}'
        f'</g>'
    )


def _qr_inline_svg(payload: str, x: float, y: float, size: float, fg: str,
                   *, style: str = "boxed") -> str:
    """Generate the dark-module shapes of a QR symbol for `payload`.

    boxed/clean ← مربعات (نفس الهندسة التاريخية)، rounded ← دوائر
    ناعمة بقطر يساوي الخلية تقريبًا فتبقى أنماط التحديد قابلة للمسح.
    """
    matrix = _qr_module_matrix(payload)
    if not matrix:
        return _svg_placeholder_grid(x, y, size, fg)
    n = len(matrix)
    cell = size / n
    rects: list[str] = []
    rounded = style == "rounded"
    radius = cell * 0.5
    for row_idx, row in enumerate(matrix):
        for col_idx, on in enumerate(row):
            if not on:
                continue
            rx = x + col_idx * cell
            ry = y + row_idx * cell
            if rounded and not _qr_in_finder(row_idx, col_idx, n):
                # «ناعم»: دوائر لوحدات البيانات فقط — مربعات التحديد
                # الثلاثة تبقى مربعة حتى يلتقطها الماسح بثقة.
                rects.append(
                    f'<circle cx="{rx + cell/2:.2f}" cy="{ry + cell/2:.2f}" '
                    f'r="{radius:.2f}" fill="{_xml(fg)}"/>'
                )
            else:
                # Slight overlap (cell * 1.02) prevents thin white
                # hairlines between modules at fractional zoom levels.
                rects.append(
                    f'<rect x="{rx:.2f}" y="{ry:.2f}" '
                    f'width="{cell*1.02:.2f}" height="{cell*1.02:.2f}" '
                    f'fill="{_xml(fg)}"/>'
                )
    return "".join(rects)


def _svg_placeholder_grid(x: float, y: float, size: float, fg: str) -> str:
    """A neutral 7x7 grid used when QR generation fails."""
    cell = size / 7
    out: list[str] = []
    for r in range(7):
        for c in range(7):
            if (r + c) % 2 == 0:
                out.append(
                    f'<rect x="{x+c*cell:.2f}" y="{y+r*cell:.2f}" '
                    f'width="{cell:.2f}" height="{cell:.2f}" '
                    f'fill="{_xml(fg)}" opacity="0.35"/>'
                )
    return "".join(out)


# ───────────────────────────────────────────────────────────────────
# Tiny utility helpers
# ───────────────────────────────────────────────────────────────────

def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _safe_hex(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = "#" + raw
    if not _HEX_RE.match(raw):
        return fallback
    return raw


def _normalize_qr_style(value: Any) -> str:
    """يطبّع نمط QR المحفوظ إلى واحدة من القيم الثلاث المدعومة.

    «مربع واضح» boxed (الافتراضي/السلوك التاريخي)، «ناعم» rounded،
    «بسيط» clean. أي قيمة غريبة أو قديمة تسقط إلى boxed حتى لا تتغير
    القوالب المحفوظة قبل دعم الأنماط.
    """
    raw = str(value or "").strip().lower()
    if raw in {"rounded", "soft", "circle", "dots"}:
        return "rounded"
    if raw in {"clean", "plain", "minimal", "borderless"}:
        return "clean"
    return "boxed"


def _svg_fill_opacity(value: str, *, opacity: float = 1.0) -> tuple[str, float]:
    """Return an opaque hex fill + the effective SVG fill-opacity.

    Mirrors `_pdf_color`: any alpha carried in an 8-digit `#RRGGBBAA`
    colour is multiplied with the explicit `opacity` so the SVG preview
    shows exactly the transparency the PDF will, regardless of which way
    the designer expressed it (alpha hex or a separate opacity slider).
    """
    base_hex, hex_alpha = _split_hex_alpha(value)
    eff = max(0.0, min(1.0, float(opacity))) * hex_alpha
    return base_hex, eff


_XML_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)


def _xml(value: Any) -> str:
    text = str(value or "")
    for raw, escaped in _XML_ESCAPES:
        text = text.replace(raw, escaped)
    return text


def _svg_id(prefix: str, value: Any) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "x")).strip("-") or "x"
    return f"{prefix}-{raw}"
