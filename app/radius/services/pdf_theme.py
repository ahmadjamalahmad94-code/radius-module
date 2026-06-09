# -*- coding: utf-8 -*-
"""pdf_theme — الثيم الموحّد الفاخر لتصديرات PDF في HobeRadius.

لماذا هذا الملف؟
----------------
كل تقارير PDF الجدولية في النظام (التقارير المالية، حِزَم الكروت، ...)
كانت تُبنى يدويًا بخط Helvetica فيظهر النص العربي مربعات (tofu) وبلا أي
هوية بصرية. هذا الموديول يجمع كل «اللمسة الفاخرة» في مكان واحد:

* تسجيل خط Cairo العربي المودرن المشحون مع التطبيق
  (app/static/fonts/Cairo-*.ttf) لدى ReportLab.
* دالة ``ar()`` لتشكيل العربية (arabic-reshaper + python-bidi) حتى
  تُرسم الحروف متصلة وبالاتجاه الصحيح RTL.
* لوحة ألوان العلامة (بنفسجي ‎#6B5AED‎ وعائلته).
* رسّام رأس/تذييل فاخر: شريط علامة علوي، عنوان عربي، التاريخ،
  شعار Hobe Hub، وأرقام صفحات في التذييل.
* بنّاء جداول مُنسّقة: ترتيب أعمدة RTL، صفوف زيبرا، رأس بنفسجي
  بخط Cairo-Bold أبيض، وصف إجماليات مميّز.

الاستخدام النموذجي::

    from .pdf_theme import (
        ar, build_premium_pdf, styled_table, kpi_row, fmt_money,
    )

    pdf_bytes = build_premium_pdf(
        title="تقرير المبيعات اليومية",
        subtitle="آخر 60 يومًا",
        story=[styled_table(headers, rows, totals_row=totals)],
        landscape_mode=True,
    )

ملاحظة مهمّة: الأرقام تبقى بالأرقام اللاتينية (1234.56) عمدًا — أوضح
في القراءة المالية ولا تحتاج تشكيلًا، لذلك لا تمرّر الأرقام على ``ar()``.
"""

from __future__ import annotations

import io
import os
import re
from datetime import datetime
from typing import Any, Iterable, Sequence

# ─── مسارات خطوط Cairo المشحونة مع التطبيق ─────────────────────────
_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static", "fonts",
)
_CAIRO_REGULAR = os.path.join(_FONTS_DIR, "Cairo-Regular.ttf")
_CAIRO_BOLD = os.path.join(_FONTS_DIR, "Cairo-Bold.ttf")
_CAIRO_BLACK = os.path.join(_FONTS_DIR, "Cairo-Black.ttf")

FONT_AR = "HobeCairo"
FONT_AR_BOLD = "HobeCairo-Bold"
FONT_AR_BLACK = "HobeCairo-Black"
FONT_LATIN = "Helvetica"
FONT_LATIN_BOLD = "Helvetica-Bold"

# ─── لوحة ألوان العلامة (Hobe Hub) ─────────────────────────────────
BRAND_PRIMARY = "#6B5AED"      # البنفسجي الأساسي
BRAND_PRIMARY_DARK = "#4C3FD1"  # درجة أغمق لشريط الرأس
BRAND_INK = "#241E4E"          # حبر داكن للنصوص الرئيسية
BRAND_MUTED = "#7C7A99"        # رمادي بنفسجي للنصوص الثانوية
BRAND_LAVENDER = "#F3F1FE"     # خلفية صف الزيبرا الفاتحة
BRAND_LAVENDER_2 = "#E9E5FC"   # خلفية صف الإجماليات
BRAND_LINE = "#DDD8F5"         # خطوط شبكة الجدول الناعمة
BRAND_GOLD = "#F2B441"         # لمسة ذهبية للتمييز (شارة/شريط رفيع)
BRAND_WHITE = "#FFFFFF"

_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

# ─── بدائل المحارف التي لا يغطيها خط Cairo ─────────────────────────
# خط Cairo (نسخة Google Fonts المشحونة) لا يتضمن بعض رموز العملات
# فتظهر مربع tofu في PDF (مثل «10 ⛝» في عمود المبلغ عندما تكون
# عملة النظام شيكل). نستبدلها بكلمة عربية مقروءة قبل التشكيل —
# تُطبَّق على كل نص يمرّ عبر ar() (خلايا الجداول/الرؤوس/KPI).
_GLYPH_SUBS = {
    "₪": "شيكل",   # U+20AA — شيكل (غير موجود في Cairo)
    "₺": "ليرة",    # U+20BA — ليرة تركية (غير موجود في Cairo)
    "﷼": "ريال",    # U+FDFC — علامة الريال (غير موجودة في Cairo)
    "₨": "Rs",      # U+20A8 — روبية (غير موجودة في Cairo)
}


def _substitute_missing_glyphs(s: str) -> str:
    """استبدال المحارف الغائبة عن Cairo بنص مقروء بدل مربع tofu."""
    for ch, alt in _GLYPH_SUBS.items():
        if ch in s:
            s = s.replace(ch, alt)
    return s


_fonts_ready: bool | None = None
_reshaper = None  # نسخة ArabicReshaper مهيّأة — تُبنى مرة واحدة


def _get_reshaper():
    """مُشكِّل عربي مهيّأ خصيصًا لخط Cairo.

    لماذا ``use_unshaped_instead_of_isolated``؟ خطوط Cairo (وAlmarai)
    من Google Fonts لا تتضمن كل «الأشكال المعزولة» من كتلة
    Arabic Presentation Forms (مثل ﺍ U+FE8D و ﺕ U+FE95)، فلو خرجت
    من المُشكِّل تظهر مربعات tofu. الحرف المعزول شكله مطابق لحرفه
    الأساسي (U+0627 ...) الموجود حتمًا في الخط، فنطلب من المُشكِّل
    إبقاء الحرف الأساسي بدل الشكل المعزول — تختفي المربعات نهائيًا.
    """
    global _reshaper
    if _reshaper is None:
        import arabic_reshaper

        _reshaper = arabic_reshaper.ArabicReshaper(configuration={
            "use_unshaped_instead_of_isolated": True,
            "delete_harakat": False,
        })
    return _reshaper


def _ensure_fonts() -> bool:
    """تسجيل عائلة Cairo لدى ReportLab — مرة واحدة فقط (كسولة).

    لو فشل التسجيل لأي سبب (ملف ناقص مثلًا) نرجع False ويسقط
    الاستدعاء على Helvetica بدل ما ينهار التصدير كله.
    """
    global _fonts_ready
    if _fonts_ready is not None:
        return _fonts_ready
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        if not (os.path.isfile(_CAIRO_REGULAR) and os.path.isfile(_CAIRO_BOLD)):
            _fonts_ready = False
            return False
        pdfmetrics.registerFont(TTFont(FONT_AR, _CAIRO_REGULAR))
        pdfmetrics.registerFont(TTFont(FONT_AR_BOLD, _CAIRO_BOLD))
        if os.path.isfile(_CAIRO_BLACK):
            pdfmetrics.registerFont(TTFont(FONT_AR_BLACK, _CAIRO_BLACK))
        else:  # احتياط: لو Black غير موجود استخدم Bold مكانه
            pdfmetrics.registerFont(TTFont(FONT_AR_BLACK, _CAIRO_BOLD))
        _fonts_ready = True
    except Exception:  # pragma: no cover — دفاعي
        _fonts_ready = False
    return _fonts_ready


def has_arabic(text: str) -> bool:
    """هل تحتوي السلسلة على حروف عربية؟"""
    return bool(text) and bool(_ARABIC_RE.search(str(text)))


def ar(text: Any) -> str:
    """تشكيل النص العربي ليُرسم صحيحًا في ReportLab.

    arabic-reshaper يحوّل الحروف المعزولة إلى أشكالها المتصلة
    (ابتدائية/وسطية/نهائية)، ثم python-bidi يعيد ترتيبها بصريًا
    حتى يقرأها الإنسان من اليمين لليسار. النصوص اللاتينية والأرقام
    تمرّ كما هي بدون أي تغيير.
    """
    if text is None:
        return ""
    # أولًا: استبدال محارف العملات الغائبة عن Cairo (₪/₺/﷼/₨) بنص
    # مقروء — قبل فحص العربية لأن «10 ₪» وحدها لا تحوي حروفًا عربية
    # وكانت تمرّ كما هي فيظهر مربع tofu في عمود المبلغ.
    s = _substitute_missing_glyphs(str(text))
    if not has_arabic(s):
        return s
    try:
        from bidi.algorithm import get_display

        return get_display(_get_reshaper().reshape(s))
    except Exception:  # pragma: no cover — دفاعي
        return s


def pick_font(text: str, *, bold: bool = False) -> str:
    """اختيار الخط المناسب: Cairo للعربي، وكذلك للّاتيني حتى تتوحّد
    الشخصية البصرية (Cairo يغطي اللاتينية بشكل ممتاز)."""
    if _ensure_fonts():
        return FONT_AR_BOLD if bold else FONT_AR
    return FONT_LATIN_BOLD if bold else FONT_LATIN


def fmt_money(value: Any, *, decimals: int = 2) -> str:
    """تنسيق مبلغ مالي بأرقام لاتينية مع فواصل آلاف: 1,234.50"""
    try:
        return f"{float(value or 0):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value or "")


def fmt_int(value: Any) -> str:
    """تنسيق عدد صحيح بفواصل آلاف."""
    try:
        return f"{int(float(value or 0)):,}"
    except (TypeError, ValueError):
        return str(value or "")


def _hex(c: str):
    from reportlab.lib import colors
    return colors.HexColor(c)


# ─── رسّام الرأس والتذييل ───────────────────────────────────────────

class _PageChrome:
    """يرسم «إطار» الصفحة الفاخر على كل صفحة: شريط العلامة العلوي،
    العنوان، التاريخ، شعار Hobe Hub، وتذييل بأرقام الصفحات."""

    HEADER_H = 64  # ارتفاع شريط الرأس بالنقاط

    def __init__(self, *, title: str, subtitle: str = "",
                 brand_mark: str = "Hobe Hub", footer_note: str = ""):
        self.title = title
        self.subtitle = subtitle
        self.brand_mark = brand_mark
        self.footer_note = footer_note
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    def __call__(self, canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize
        fonts_ok = _ensure_fonts()
        f_black = FONT_AR_BLACK if fonts_ok else FONT_LATIN_BOLD
        f_bold = FONT_AR_BOLD if fonts_ok else FONT_LATIN_BOLD
        f_reg = FONT_AR if fonts_ok else FONT_LATIN

        # ── شريط الرأس: طبقتان بنفسجيتان لإيحاء التدرّج + خيط ذهبي ──
        top = height
        canvas.setFillColor(_hex(BRAND_PRIMARY_DARK))
        canvas.rect(0, top - self.HEADER_H, width, self.HEADER_H, stroke=0, fill=1)
        canvas.setFillColor(_hex(BRAND_PRIMARY))
        # شريحة مائلة فاتحة على يسار الشريط تكسر الرتابة
        p = canvas.beginPath()
        p.moveTo(0, top)
        p.lineTo(width * 0.42, top)
        p.lineTo(width * 0.34, top - self.HEADER_H)
        p.lineTo(0, top - self.HEADER_H)
        p.close()
        canvas.drawPath(p, stroke=0, fill=1)
        # خيط ذهبي رفيع أسفل الشريط — اللمسة الفاخرة
        canvas.setFillColor(_hex(BRAND_GOLD))
        canvas.rect(0, top - self.HEADER_H - 2.5, width, 2.5, stroke=0, fill=1)

        # ── العنوان العربي (يمين الصفحة لأن الاتجاه RTL) ──
        canvas.setFillColor(_hex(BRAND_WHITE))
        canvas.setFont(f_black, 17)
        canvas.drawRightString(width - 28, top - 30, ar(self.title))
        if self.subtitle:
            canvas.setFont(f_reg, 9.5)
            canvas.setFillColorRGB(1, 1, 1, alpha=0.85)
            canvas.drawRightString(width - 28, top - 47, ar(self.subtitle))

        # ── شعار العلامة + التاريخ (يسار الشريط) ──
        canvas.setFillColor(_hex(BRAND_WHITE))
        canvas.setFont(f_bold, 13)
        canvas.drawString(28, top - 30, self.brand_mark)
        canvas.setFont(f_reg, 8.5)
        canvas.setFillColorRGB(1, 1, 1, alpha=0.8)
        # نرسم التاريخ كنص لاتيني مستقل حتى لا يقلب خوارزم bidi ترتيب
        # «التاريخ ثم الوقت». الترتيب RTL طبيعي: الوسم العربي يمينًا
        # والقيمة على يساره — فنرسم القيمة أولًا ثم الوسم بعدها.
        canvas.drawString(28, top - 45, self.generated_at)
        from reportlab.pdfbase.pdfmetrics import stringWidth
        value_w = stringWidth(self.generated_at, f_reg, 8.5)
        canvas.drawString(28 + value_w + 5, top - 45, ar("تاريخ الإصدار:"))

        # ── التذييل: خط بنفسجي رفيع + رقم الصفحة + توقيع ──
        canvas.setStrokeColor(_hex(BRAND_LINE))
        canvas.setLineWidth(0.8)
        canvas.line(28, 34, width - 28, 34)
        canvas.setFillColor(_hex(BRAND_MUTED))
        canvas.setFont(f_reg, 8.5)
        canvas.drawRightString(width - 28, 21, ar(f"صفحة {canvas.getPageNumber()}"))
        note = self.footer_note or "HobeRadius • Hobe Hub"
        canvas.drawString(28, 21, ar(note))
        canvas.restoreState()


# ─── بنّاء الجداول الفاخرة ──────────────────────────────────────────

def styled_table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *,
                 totals_row: Sequence[Any] | None = None,
                 col_widths: Sequence[float] | None = None,
                 font_size: float = 8.5,
                 rtl: bool = True,
                 align_map: dict[int, str] | None = None):
    """بناء جدول Platypus بهوية HobeRadius.

    * ``rtl=True`` يعكس ترتيب الأعمدة حتى يبدأ أول عمود من اليمين —
      هكذا يقرأ المستخدم العربي الجدول طبيعيًا.
    * رأس بنفسجي بخط Cairo-Bold أبيض، صفوف زيبرا لافندر، وخطوط
      شبكة ناعمة بدل الشبكة السوداء القاسية.
    * ``totals_row`` (اختياري) يُلحق كصف أخير بخلفية مميّزة وخط أسمك.
    * كل الخلايا تمرّ على ``ar()`` تلقائيًا فلا يظهر tofu أبدًا.
    * ``align_map`` (اختياري): فهرس العمود *قبل* العكس → 'LEFT'/'RIGHT'/'CENTER'.
    """
    from reportlab.platypus import Table, TableStyle

    fonts_ok = _ensure_fonts()
    f_bold = FONT_AR_BOLD if fonts_ok else FONT_LATIN_BOLD
    f_reg = FONT_AR if fonts_ok else FONT_LATIN

    def _cell(v: Any) -> str:
        return ar("—" if v is None or v == "" else v)

    head = [_cell(h) for h in headers]
    body = [[_cell(v) for v in row] for row in rows]
    tail = [_cell(v) for v in totals_row] if totals_row else None

    n = len(head)
    if rtl:
        head = list(reversed(head))
        body = [list(reversed(r)) for r in body]
        if tail:
            tail = list(reversed(tail))
        if col_widths:
            col_widths = list(reversed(list(col_widths)))

    data = [head] + body + ([tail] if tail else [])

    style = [
        # رأس الجدول — بنفسجي العلامة مع نص أبيض عريض
        ("BACKGROUND", (0, 0), (-1, 0), _hex(BRAND_PRIMARY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), _hex(BRAND_WHITE)),
        ("FONTNAME", (0, 0), (-1, 0), f_bold),
        ("FONTSIZE", (0, 0), (-1, 0), font_size + 0.5),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        # جسم الجدول
        ("FONTNAME", (0, 1), (-1, -1), f_reg),
        ("FONTSIZE", (0, 1), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 1), (-1, -1), _hex(BRAND_INK)),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        # خطوط أفقية ناعمة فقط (بلا شبكة عمودية قاسية)
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, _hex(BRAND_LINE)),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, _hex(BRAND_PRIMARY)),
    ]
    if body:
        # صفوف الزيبرا — أبيض/لافندر بالتناوب
        last_body = len(body)  # فهرس آخر صف بيانات
        style.append((
            "ROWBACKGROUNDS", (0, 1), (-1, last_body),
            [_hex(BRAND_WHITE), _hex(BRAND_LAVENDER)],
        ))
    if tail:
        # صف الإجماليات — خلفية لافندر أغمق وخط عريض وحدّ علوي بنفسجي
        style += [
            ("BACKGROUND", (0, -1), (-1, -1), _hex(BRAND_LAVENDER_2)),
            ("FONTNAME", (0, -1), (-1, -1), f_bold),
            ("TEXTCOLOR", (0, -1), (-1, -1), _hex(BRAND_PRIMARY_DARK)),
            ("LINEABOVE", (0, -1), (-1, -1), 1.0, _hex(BRAND_PRIMARY)),
            ("TOPPADDING", (0, -1), (-1, -1), 7),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
        ]
    if align_map:
        for idx, alignment in align_map.items():
            col = (n - 1 - idx) if rtl else idx
            style.append(("ALIGN", (col, 1), (col, -1), alignment))

    table = Table(data, repeatRows=1, colWidths=list(col_widths) if col_widths else None)
    table.setStyle(TableStyle(style))
    return table


def kpi_row(items: Sequence[tuple[str, str]], *, page_width: float):
    """صف بطاقات KPI خفيف: [(عنوان, قيمة), ...] — يُرسم كجدول بطاقات.

    البطاقات تُعرض من اليمين لليسار (نعكس الترتيب) بخلفية لافندر
    وقيمة بنفسجية كبيرة بخط Cairo-Bold.
    """
    from reportlab.platypus import Table, TableStyle

    fonts_ok = _ensure_fonts()
    f_bold = FONT_AR_BOLD if fonts_ok else FONT_LATIN_BOLD
    f_reg = FONT_AR if fonts_ok else FONT_LATIN

    shown = list(reversed(list(items)))
    values = [ar(v) for _t, v in shown]
    titles = [ar(t) for t, _v in shown]
    gap = 10
    count = max(len(shown), 1)
    card_w = (page_width - gap * (count - 1)) / count

    cells_data = [values, titles]
    widths: list[float] = []
    for i in range(count):
        widths.append(card_w)
    t = Table(cells_data, colWidths=widths)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), f_bold),
        ("FONTSIZE", (0, 0), (-1, 0), 14),
        ("TEXTCOLOR", (0, 0), (-1, 0), _hex(BRAND_PRIMARY_DARK)),
        ("FONTNAME", (0, 1), (-1, 1), f_reg),
        ("FONTSIZE", (0, 1), (-1, 1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, 1), _hex(BRAND_MUTED)),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
    ]
    for i in range(count):
        style += [
            ("BACKGROUND", (i, 0), (i, 1), _hex(BRAND_LAVENDER)),
            ("LINEABOVE", (i, 0), (i, 0), 2.0, _hex(BRAND_PRIMARY)),
            ("LINEBELOW", (i, 1), (i, 1), 0.5, _hex(BRAND_LINE)),
        ]
    t.setStyle(TableStyle(style))
    return t


def section_title(text: str):
    """عنوان قسم عربي بخط Cairo-Bold بنفسجي مع مسافة مناسبة."""
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    fonts_ok = _ensure_fonts()
    style = ParagraphStyle(
        "HobeSection",
        fontName=FONT_AR_BOLD if fonts_ok else FONT_LATIN_BOLD,
        fontSize=12.5,
        leading=18,
        alignment=TA_RIGHT,
        textColor=_hex(BRAND_INK),
        spaceBefore=6,
        spaceAfter=6,
    )
    return Paragraph(ar(text), style)


def empty_state(text: str = "لا توجد بيانات لعرضها في هذا التقرير"):
    """رسالة «لا توجد بيانات» أنيقة بدل جدول فارغ قبيح."""
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    fonts_ok = _ensure_fonts()
    style = ParagraphStyle(
        "HobeEmpty",
        fontName=FONT_AR if fonts_ok else FONT_LATIN,
        fontSize=11,
        leading=20,
        alignment=TA_CENTER,
        textColor=_hex(BRAND_MUTED),
        backColor=_hex(BRAND_LAVENDER),
        borderPadding=18,
        spaceBefore=24,
    )
    return Paragraph(ar(text), style)


def build_premium_pdf(*, title: str, story: list, subtitle: str = "",
                      landscape_mode: bool = True,
                      footer_note: str = "") -> bytes:
    """تجميع مستند PDF كامل بهوية HobeRadius وإرجاع البايتات.

    يضبط الهوامش بحيث لا يتداخل المحتوى مع شريط الرأس ولا التذييل،
    ويرسم «الإطار» (الرأس/التذييل) على كل صفحة تلقائيًا.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate

    _ensure_fonts()
    pagesize = landscape(A4) if landscape_mode else A4
    out = io.BytesIO()
    chrome = _PageChrome(title=title, subtitle=subtitle, footer_note=footer_note)
    doc = SimpleDocTemplate(
        out,
        pagesize=pagesize,
        leftMargin=28,
        rightMargin=28,
        topMargin=_PageChrome.HEADER_H + 18,
        bottomMargin=46,
        title=title,
        author="HobeRadius",
    )
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return out.getvalue()


# ─── تصدير حِزَم الكروت (تستخدمه واجهة الويب و API معًا) ────────────

# حالات الحزمة التشغيلية → عربي (نُبقي القيمة الخام لو ظهرت حالة جديدة)
_BATCH_STATUS_AR = {
    "active": "نشطة",
    "available": "متاحة",
    "draft": "مسودة",
    "archived": "مؤرشفة",
    "pending_archive": "بانتظار الأرشفة",
    "depleted": "مستنفدة",
    "expired": "منتهية",
    "revoked": "ملغاة",
    "deleted": "محذوفة",
    "in_use": "قيد الاستخدام",
}


def build_batches_pdf(rows: list[dict]) -> bytes:
    """بناء PDF فاخر لقائمة حِزَم الكروت من صفوف list_batch_operations.

    نعرض الأعمدة التشغيلية المهمة فقط (التفاصيل الكاملة في CSV/XLSX)،
    مع بطاقات KPI أعلى الصفحة وصف إجماليات أسفل الجدول.
    """
    from reportlab.platypus import Spacer

    headers = [
        "رمز الحزمة",
        "الباقة",
        "الخطة",
        "الحالة",
        "المولّدة",
        "المتاحة",
        "النشطة",
        "المتبقية",
        "سعر الكرت",
        "القيمة التقديرية",
        "الموزع",
        "تاريخ الإنشاء",
    ]

    def _money_value(item: dict) -> float:
        unit_price = float(item.get("estimated_unit_price") or 0)
        configured = float(item.get("total_price") or 0)
        if configured <= 0:
            configured = unit_price * int(item.get("generated") or 0)
        return configured

    body: list[list[str]] = []
    total_generated = total_available = total_active = total_remaining = 0
    total_value = 0.0
    # نحدّ صفوف PDF بـ 100 حزمة حفاظًا على خفة الملف؛ البيانات الكاملة
    # متاحة دومًا عبر CSV/XLSX.
    for item in rows[:100]:
        generated = int(item.get("generated") or 0)
        available = int(item.get("available_count") or 0)
        active = int(item.get("active_count") or 0)
        remaining = int(item.get("remaining_count") or 0)
        value = _money_value(item)
        total_generated += generated
        total_available += available
        total_active += active
        total_remaining += remaining
        total_value += value
        status_raw = str(item.get("operational_status") or "").strip()
        created = str(item.get("created_at") or "")[:16].replace("T", " ")
        body.append([
            str(item.get("batch_code") or item.get("id") or ""),
            str(item.get("package_name") or "—"),
            str(item.get("plan_name") or "—"),
            _BATCH_STATUS_AR.get(status_raw, status_raw or "—"),
            fmt_int(generated),
            fmt_int(available),
            fmt_int(active),
            fmt_int(remaining),
            fmt_money(float(item.get("estimated_unit_price") or 0)),
            fmt_money(value),
            str(item.get("distributor_display_name")
                or item.get("distributor_name") or "—"),
            created or "—",
        ])

    story: list = []
    if not body:
        story.append(empty_state("لا توجد حِزَم كروت مطابقة لعوامل التصفية"))
    else:
        totals_row = [
            "الإجمالي", "", "", "",
            fmt_int(total_generated),
            fmt_int(total_available),
            fmt_int(total_active),
            fmt_int(total_remaining),
            "",
            fmt_money(total_value),
            "", "",
        ]
        kpis = [
            ("عدد الحِزَم", fmt_int(len(body))),
            ("كروت مولّدة", fmt_int(total_generated)),
            ("كروت متاحة", fmt_int(total_available)),
            ("القيمة التقديرية", fmt_money(total_value)),
        ]
        story.append(kpi_row(kpis, page_width=content_width(landscape_mode=True)))
        story.append(Spacer(1, 14))
        story.append(styled_table(headers, body, totals_row=totals_row, font_size=8))

    subtitle = f"عدد الحِزَم المعروضة: {len(body)}"
    if len(rows) > 100:
        subtitle += f" من أصل {len(rows)} (التفاصيل الكاملة في CSV/XLSX)"
    return build_premium_pdf(
        title="تقرير حِزَم الكروت",
        subtitle=subtitle,
        story=story,
        landscape_mode=True,
        footer_note="HobeRadius • إدارة الكروت",
    )


def content_width(*, landscape_mode: bool = True) -> float:
    """عرض منطقة المحتوى المتاح (لحساب عرض الأعمدة وبطاقات KPI)."""
    from reportlab.lib.pagesizes import A4, landscape

    pagesize = landscape(A4) if landscape_mode else A4
    return pagesize[0] - 56  # هامشا 28 يمين ويسار
