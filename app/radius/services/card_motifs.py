# -*- coding: utf-8 -*-
"""card_motifs — رموز SVG أصلية (نقّ + خطّ + كتلة) لكل قطاع لقوالب الكروت.

لا تَستعمل أيّ شعار محميّ — كلّ رمز هنا مَرسوم يدويًا بأشكال أوّلية
(دائرة + مستطيل + مسار) أصلية. يَنتج كلّ رمز:

  • شَكلًا صغيرًا بجانب الـbrand على الكَرت (icon)
  • نَفس الشَكل كَبيرًا بشفافيّة مُنخفضة خَلف المحتوى (watermark)

كلّ رمز هو دالّة ‎``draw(cx, cy, size, color, opacity)``‎ تُعيد string من
عناصر SVG بنفس نظام إحداثيّات الـcanvas. الشَكل مَركّز حول (cx, cy)
ويَملأ box بأبعاد ‎``size × size``‎. الـcolor + opacity مُمَرَّران كي يَتطابق
الـicon والـwatermark على نفس الكَرت لو طُلب لون مُختلف.

الـmotifs مَفهرسة بـvertical key (cafe/restaurant/clinic/shop/isp/hotel/
salon/gym/school/events/mosque/charity/gaming/generic). دالّة
‎``motif_svg(motif, ...)``‎ تَختار الشَكل المُناسب — fallback إلى
"generic" لأيّ مفتاح غير مُعرَّف.
"""
from __future__ import annotations

from typing import Callable

# نوع كل draw function: (cx, cy, size, color, opacity, weight) -> str
_MotifFn = Callable[[float, float, float, str, float, float], str]


def _xml(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


# ─── أدوات بناء عناصر SVG منخفضة المستوى ─────────────────────────

def _circle(cx: float, cy: float, r: float, *, fill: str = "none",
            stroke: str = "none", sw: float = 0,
            opacity: float = 1.0) -> str:
    o = f' opacity="{opacity:.3f}"' if opacity < 1 else ""
    s = f' stroke="{_xml(stroke)}" stroke-width="{sw:.2f}"' if stroke != "none" else ""
    return (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{_xml(fill)}"{s}{o}/>')


def _rect(x: float, y: float, w: float, h: float, *, fill: str = "none",
          stroke: str = "none", sw: float = 0, rx: float = 0,
          opacity: float = 1.0) -> str:
    o = f' opacity="{opacity:.3f}"' if opacity < 1 else ""
    s = f' stroke="{_xml(stroke)}" stroke-width="{sw:.2f}"' if stroke != "none" else ""
    r = f' rx="{rx:.2f}" ry="{rx:.2f}"' if rx else ""
    return (f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{_xml(fill)}"{s}{o}{r}/>')


def _path(d: str, *, fill: str = "none", stroke: str = "none",
          sw: float = 0, opacity: float = 1.0,
          linecap: str = "round", linejoin: str = "round") -> str:
    o = f' opacity="{opacity:.3f}"' if opacity < 1 else ""
    s = (f' stroke="{_xml(stroke)}" stroke-width="{sw:.2f}" '
         f'stroke-linecap="{linecap}" stroke-linejoin="{linejoin}"'
         if stroke != "none" else "")
    return f'<path d="{d}" fill="{_xml(fill)}"{s}{o}/>'


# ─── الرموز ──────────────────────────────────────────────────────
# كلّ رمز يَأخذ (cx, cy, size, color, opacity, sw_factor) ويُعيد SVG.
# sw_factor تَحديد سُمك الخطوط = size * sw_factor (افتراضي 0.08).


def _coffee(cx: float, cy: float, sz: float, color: str,
             op: float, sw_f: float) -> str:
    """كوب قهوة: فنجان + مَقبض + بُخار."""
    sw = sz * sw_f
    half = sz / 2
    x = cx - half
    y = cy - half * 0.3
    cup_w = sz * 0.65
    cup_h = sz * 0.55
    cup_x = x + (sz - cup_w) / 2
    # كوب (مستطيل + ركن أسفل مُدوَّر)
    cup = _path(
        f"M{cup_x:.2f} {y:.2f} "
        f"L{cup_x + cup_w:.2f} {y:.2f} "
        f"L{cup_x + cup_w * 0.86:.2f} {y + cup_h:.2f} "
        f"L{cup_x + cup_w * 0.14:.2f} {y + cup_h:.2f} Z",
        stroke=color, sw=sw, opacity=op,
    )
    # مَقبض (نصف دائرة على اليمين)
    h_cx = cup_x + cup_w + sz * 0.04
    h_cy = y + cup_h * 0.45
    handle = _path(
        f"M{h_cx:.2f} {h_cy - sz * 0.12:.2f} "
        f"A {sz * 0.13:.2f} {sz * 0.13:.2f} 0 0 1 "
        f"{h_cx:.2f} {h_cy + sz * 0.12:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    # بُخار (موجتان متدلّيتان)
    steam_y = y - sz * 0.05
    steam = _path(
        f"M{cx - sz * 0.13:.2f} {steam_y:.2f} "
        f"q {sz * 0.06:.2f} {-sz * 0.10:.2f} 0 {-sz * 0.20:.2f} "
        f"M{cx + sz * 0.13:.2f} {steam_y:.2f} "
        f"q {sz * 0.06:.2f} {-sz * 0.10:.2f} 0 {-sz * 0.20:.2f}",
        stroke=color, sw=sw * 0.85, opacity=op,
    )
    return cup + handle + steam


def _fork_knife(cx: float, cy: float, sz: float, color: str,
                 op: float, sw_f: float) -> str:
    """شَوكة وسكّينة."""
    sw = sz * sw_f
    half = sz / 2
    # شَوكة (يَسار)
    fx = cx - sz * 0.18
    fork = _path(
        f"M{fx:.2f} {cy - half:.2f} L{fx:.2f} {cy - sz * 0.12:.2f} "
        f"M{fx - sz * 0.08:.2f} {cy - half:.2f} L{fx - sz * 0.08:.2f} {cy - sz * 0.18:.2f} "
        f"M{fx + sz * 0.08:.2f} {cy - half:.2f} L{fx + sz * 0.08:.2f} {cy - sz * 0.18:.2f} "
        f"M{fx:.2f} {cy - sz * 0.12:.2f} L{fx:.2f} {cy + half:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    # سكّينة (يَمين) — نَصل + مَقبض
    kx = cx + sz * 0.18
    knife = _path(
        f"M{kx - sz * 0.10:.2f} {cy - half:.2f} "
        f"L{kx + sz * 0.06:.2f} {cy - half * 0.5:.2f} "
        f"L{kx + sz * 0.02:.2f} {cy:.2f} "
        f"L{kx - sz * 0.06:.2f} {cy:.2f} Z "
        f"M{kx - sz * 0.02:.2f} {cy:.2f} L{kx - sz * 0.02:.2f} {cy + half:.2f}",
        stroke=color, sw=sw, fill="none", opacity=op,
    )
    return fork + knife


def _medical_cross(cx: float, cy: float, sz: float, color: str,
                    op: float, sw_f: float) -> str:
    """صَليب طبّي داخل دائرة."""
    sw = sz * sw_f
    r = sz * 0.45
    circle = _circle(cx, cy, r, stroke=color, sw=sw, opacity=op)
    # صَليب
    arm = sz * 0.22
    th = sz * 0.10
    cross = (
        _rect(cx - arm, cy - th / 2, arm * 2, th,
              fill=color, opacity=op)
        + _rect(cx - th / 2, cy - arm, th, arm * 2,
                fill=color, opacity=op)
    )
    return circle + cross


def _shopping_bag(cx: float, cy: float, sz: float, color: str,
                   op: float, sw_f: float) -> str:
    """كيس تَسوّق: مَقبضان + جسم."""
    sw = sz * sw_f
    half = sz / 2
    bag_w = sz * 0.72
    bag_h = sz * 0.62
    x = cx - bag_w / 2
    y = cy - bag_h / 2 + sz * 0.06
    bag = _path(
        f"M{x:.2f} {y:.2f} "
        f"L{x + bag_w:.2f} {y:.2f} "
        f"L{x + bag_w * 0.93:.2f} {y + bag_h:.2f} "
        f"L{x + bag_w * 0.07:.2f} {y + bag_h:.2f} Z",
        stroke=color, sw=sw, opacity=op,
    )
    # مَقابض (قَوسان فَوق)
    handle_h = sz * 0.22
    handles = _path(
        f"M{x + bag_w * 0.22:.2f} {y:.2f} "
        f"a {bag_w * 0.13:.2f} {handle_h:.2f} 0 0 1 {bag_w * 0.26:.2f} 0 "
        f"M{x + bag_w * 0.52:.2f} {y:.2f} "
        f"a {bag_w * 0.13:.2f} {handle_h:.2f} 0 0 1 {bag_w * 0.26:.2f} 0",
        stroke=color, sw=sw, opacity=op,
    )
    return bag + handles


def _wifi(cx: float, cy: float, sz: float, color: str,
          op: float, sw_f: float) -> str:
    """رمز واي‑فاي: ثلاث أقواس + نُقطة."""
    sw = sz * sw_f
    # نُقطة سُفلى
    dot = _circle(cx, cy + sz * 0.30, sz * 0.06, fill=color, opacity=op)
    # ثلاث أقواس
    arc = (
        _path(
            f"M{cx - sz * 0.13:.2f} {cy + sz * 0.13:.2f} "
            f"a {sz * 0.18:.2f} {sz * 0.18:.2f} 0 0 1 {sz * 0.26:.2f} 0",
            stroke=color, sw=sw, opacity=op,
        )
        + _path(
            f"M{cx - sz * 0.25:.2f} {cy:.2f} "
            f"a {sz * 0.30:.2f} {sz * 0.30:.2f} 0 0 1 {sz * 0.50:.2f} 0",
            stroke=color, sw=sw, opacity=op,
        )
        + _path(
            f"M{cx - sz * 0.36:.2f} {cy - sz * 0.13:.2f} "
            f"a {sz * 0.43:.2f} {sz * 0.43:.2f} 0 0 1 {sz * 0.72:.2f} 0",
            stroke=color, sw=sw, opacity=op,
        )
    )
    return arc + dot


def _hotel_bed(cx: float, cy: float, sz: float, color: str,
                op: float, sw_f: float) -> str:
    """سَرير: مَخدّة + جسم + قواعد."""
    sw = sz * sw_f
    bed_w = sz * 0.80
    bed_h = sz * 0.30
    x = cx - bed_w / 2
    y = cy - sz * 0.05
    # هيكل
    body = _rect(x, y, bed_w, bed_h, stroke=color, sw=sw, opacity=op)
    # مَخدّة (مستطيل صغير يَسار)
    pillow = _rect(x + sz * 0.04, y - sz * 0.16, sz * 0.22, sz * 0.16,
                    stroke=color, sw=sw * 0.85, opacity=op, rx=sz * 0.03)
    # غطاء (خَطّ يَمتدّ)
    cover = _path(
        f"M{x + sz * 0.30:.2f} {y + sz * 0.08:.2f} L{x + bed_w:.2f} {y + sz * 0.08:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    # قواعد
    legs = _path(
        f"M{x:.2f} {y + bed_h:.2f} L{x:.2f} {y + bed_h + sz * 0.10:.2f} "
        f"M{x + bed_w:.2f} {y + bed_h:.2f} L{x + bed_w:.2f} {y + bed_h + sz * 0.10:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    return body + pillow + cover + legs


def _scissors(cx: float, cy: float, sz: float, color: str,
               op: float, sw_f: float) -> str:
    """مَقصّ: دائرتان + شَفرتان."""
    sw = sz * sw_f
    half = sz / 2
    r = sz * 0.12
    # دائرتان
    c1 = _circle(cx - sz * 0.22, cy + sz * 0.18, r,
                  stroke=color, sw=sw, opacity=op)
    c2 = _circle(cx + sz * 0.22, cy + sz * 0.18, r,
                  stroke=color, sw=sw, opacity=op)
    # شَفرتان (خطّان من الدائرتين إلى نُقطة فَوق)
    blades = _path(
        f"M{cx - sz * 0.14:.2f} {cy + sz * 0.10:.2f} L{cx + sz * 0.10:.2f} {cy - half:.2f} "
        f"M{cx + sz * 0.14:.2f} {cy + sz * 0.10:.2f} L{cx - sz * 0.10:.2f} {cy - half:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    return c1 + c2 + blades


def _dumbbell(cx: float, cy: float, sz: float, color: str,
               op: float, sw_f: float) -> str:
    """دامبل: قَضيب + وَزنان."""
    sw = sz * sw_f
    half = sz / 2
    # قَضيب
    bar = _rect(cx - sz * 0.32, cy - sz * 0.04, sz * 0.64, sz * 0.08,
                 fill=color, opacity=op)
    # وَزنان كَبيران
    plate_l = _rect(cx - half, cy - sz * 0.18, sz * 0.10, sz * 0.36,
                     fill=color, opacity=op, rx=sz * 0.02)
    plate_r = _rect(cx + half - sz * 0.10, cy - sz * 0.18, sz * 0.10, sz * 0.36,
                     fill=color, opacity=op, rx=sz * 0.02)
    # وَزنان أصغر
    inner_l = _rect(cx - half + sz * 0.13, cy - sz * 0.12, sz * 0.07, sz * 0.24,
                     fill=color, opacity=op, rx=sz * 0.01)
    inner_r = _rect(cx + half - sz * 0.20, cy - sz * 0.12, sz * 0.07, sz * 0.24,
                     fill=color, opacity=op, rx=sz * 0.01)
    return plate_l + inner_l + bar + inner_r + plate_r


def _grad_cap(cx: float, cy: float, sz: float, color: str,
               op: float, sw_f: float) -> str:
    """قُبّعة تَخرّج: مُربّع مَقلوب + قَضيب + شُرّابة."""
    sw = sz * sw_f
    half = sz / 2
    # المُربّع المَقلوب (مَعَيِّن)
    diamond = _path(
        f"M{cx:.2f} {cy - sz * 0.28:.2f} "
        f"L{cx + half:.2f} {cy:.2f} "
        f"L{cx:.2f} {cy + sz * 0.18:.2f} "
        f"L{cx - half:.2f} {cy:.2f} Z",
        fill=color, opacity=op,
    )
    # القاعدة (صَفّ خَلف)
    base = _path(
        f"M{cx - sz * 0.22:.2f} {cy + sz * 0.05:.2f} "
        f"L{cx - sz * 0.22:.2f} {cy + sz * 0.26:.2f} "
        f"q 0 {sz * 0.08:.2f} {sz * 0.22:.2f} {sz * 0.08:.2f} "
        f"q {sz * 0.22:.2f} 0 {sz * 0.22:.2f} {-sz * 0.08:.2f} "
        f"L{cx + sz * 0.22:.2f} {cy + sz * 0.05:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    # شُرّابة
    tassel = _path(
        f"M{cx + sz * 0.36:.2f} {cy:.2f} L{cx + sz * 0.36:.2f} {cy + sz * 0.20:.2f}",
        stroke=color, sw=sw, opacity=op,
    )
    return diamond + base + tassel


def _balloons(cx: float, cy: float, sz: float, color: str,
               op: float, sw_f: float) -> str:
    """بالونات: ثَلاثة بَالونات + خُيوط."""
    sw = sz * sw_f
    r = sz * 0.17
    b1 = _circle(cx - sz * 0.22, cy - sz * 0.10, r, fill=color, opacity=op * 0.85)
    b2 = _circle(cx + sz * 0.04, cy - sz * 0.22, r, fill=color, opacity=op)
    b3 = _circle(cx + sz * 0.26, cy - sz * 0.06, r * 0.92, fill=color, opacity=op * 0.7)
    strings = _path(
        f"M{cx - sz * 0.22:.2f} {cy + sz * 0.07:.2f} L{cx:.2f} {cy + sz * 0.40:.2f} "
        f"M{cx + sz * 0.04:.2f} {cy - sz * 0.05:.2f} L{cx:.2f} {cy + sz * 0.40:.2f} "
        f"M{cx + sz * 0.26:.2f} {cy + sz * 0.11:.2f} L{cx:.2f} {cy + sz * 0.40:.2f}",
        stroke=color, sw=sw * 0.6, opacity=op,
    )
    return b1 + b2 + b3 + strings


def _mosque(cx: float, cy: float, sz: float, color: str,
             op: float, sw_f: float) -> str:
    """مَسجد: قُبّة + مَئذنة + هلال."""
    sw = sz * sw_f
    half = sz / 2
    # قُبّة (نصف دائرة)
    dome = _path(
        f"M{cx - sz * 0.30:.2f} {cy:.2f} "
        f"q 0 {-sz * 0.36:.2f} {sz * 0.30:.2f} {-sz * 0.36:.2f} "
        f"q {sz * 0.30:.2f} 0 {sz * 0.30:.2f} {sz * 0.36:.2f} Z",
        fill=color, opacity=op * 0.75, stroke=color, sw=sw,
    )
    # قاعدة
    base = _rect(cx - sz * 0.36, cy, sz * 0.72, sz * 0.22,
                  fill=color, opacity=op * 0.6, stroke=color, sw=sw)
    # مَئذنة (مُستطيل عَمودي يَمين)
    minaret = _rect(cx + sz * 0.30, cy - sz * 0.28, sz * 0.10, sz * 0.50,
                     fill=color, opacity=op * 0.7, stroke=color, sw=sw * 0.7)
    # هلال فَوق القُبّة
    crescent = _path(
        f"M{cx:.2f} {cy - sz * 0.42:.2f} "
        f"a {sz * 0.08:.2f} {sz * 0.08:.2f} 0 1 1 0.01 0",
        stroke=color, sw=sw, opacity=op,
    )
    return base + dome + minaret + crescent


def _heart_hands(cx: float, cy: float, sz: float, color: str,
                  op: float, sw_f: float) -> str:
    """قَلب بين كَفّين."""
    sw = sz * sw_f
    half = sz / 2
    # قَلب
    heart = _path(
        f"M{cx:.2f} {cy + sz * 0.10:.2f} "
        f"c {-sz * 0.30:.2f} {-sz * 0.10:.2f} {-sz * 0.38:.2f} {-sz * 0.38:.2f} 0 {-sz * 0.32:.2f} "
        f"c {sz * 0.38:.2f} {-sz * 0.06:.2f} {sz * 0.30:.2f} {sz * 0.22:.2f} 0 {sz * 0.32:.2f} Z",
        fill=color, opacity=op,
    )
    # كَفّان (قَوسان أسفل)
    hands = _path(
        f"M{cx - sz * 0.40:.2f} {cy + sz * 0.18:.2f} "
        f"q {sz * 0.40:.2f} {sz * 0.30:.2f} {sz * 0.80:.2f} 0",
        stroke=color, sw=sw, opacity=op,
    )
    return heart + hands


def _gamepad(cx: float, cy: float, sz: float, color: str,
              op: float, sw_f: float) -> str:
    """يَدّ تَحكّم: هيكل + D-pad + زِرّان."""
    sw = sz * sw_f
    half = sz / 2
    pad_w = sz * 0.86
    pad_h = sz * 0.46
    x = cx - pad_w / 2
    y = cy - pad_h / 2
    # هيكل بأطراف مُدوَّرة
    body = _rect(x, y, pad_w, pad_h, stroke=color, sw=sw, opacity=op,
                  rx=sz * 0.10)
    # D-pad يَسار: صَليب
    dp_cx = cx - sz * 0.22
    dp = (
        _rect(dp_cx - sz * 0.10, cy - sz * 0.04, sz * 0.20, sz * 0.08,
              fill=color, opacity=op)
        + _rect(dp_cx - sz * 0.04, cy - sz * 0.10, sz * 0.08, sz * 0.20,
                 fill=color, opacity=op)
    )
    # زِرّان يَمين
    b_cx = cx + sz * 0.22
    btns = (
        _circle(b_cx - sz * 0.07, cy + sz * 0.04, sz * 0.05,
                 fill=color, opacity=op)
        + _circle(b_cx + sz * 0.07, cy - sz * 0.04, sz * 0.05,
                   fill=color, opacity=op)
    )
    return body + dp + btns


def _qr_motif(cx: float, cy: float, sz: float, color: str,
               op: float, sw_f: float) -> str:
    """نَمط QR: ثَلاثة مُربّعات finder + بعض الـmodules."""
    sw = sz * sw_f
    half = sz / 2
    box = sz * 0.30
    th = sz * 0.07
    # ثَلاث مُربّعات finder
    def _finder(fx: float, fy: float) -> str:
        return (
            _rect(fx, fy, box, box, stroke=color, sw=sw, opacity=op)
            + _rect(fx + box * 0.30, fy + box * 0.30, box * 0.40, box * 0.40,
                     fill=color, opacity=op)
        )
    f1 = _finder(cx - half, cy - half)
    f2 = _finder(cx + half - box, cy - half)
    f3 = _finder(cx - half, cy + half - box)
    # نَقاط داخليّة (modules)
    dots = ""
    for dx, dy in ((0.20, -0.10), (0.10, 0.10), (-0.10, 0.20), (0.25, 0.25),
                    (0.0, 0.0), (-0.20, 0.05), (0.30, 0.40)):
        dots += _rect(cx + sz * dx, cy + sz * dy, sz * 0.06, sz * 0.06,
                       fill=color, opacity=op)
    return f1 + f2 + f3 + dots


# ─── الفِهرس ─────────────────────────────────────────────────────
_REGISTRY: dict[str, _MotifFn] = {
    "coffee":       _coffee,
    "fork_knife":   _fork_knife,
    "stethoscope":  _medical_cross,   # alias for clinic
    "medical":      _medical_cross,
    "shopping_bag": _shopping_bag,
    "shop":         _shopping_bag,
    "wifi":         _wifi,
    "signal":       _wifi,
    "bed":          _hotel_bed,
    "hotel":        _hotel_bed,
    "scissors":     _scissors,
    "dumbbell":     _dumbbell,
    "grad_cap":     _grad_cap,
    "balloons":     _balloons,
    "events":       _balloons,
    "mosque":       _mosque,
    "crescent":     _mosque,
    "heart":        _heart_hands,
    "charity":      _heart_hands,
    "gamepad":      _gamepad,
    "qr":           _qr_motif,
    "generic":      _wifi,
}


# ─── خَريطة vertical → motif افتراضيّ ────────────────────────────
# يُستعمل لو الـpreset لم يَذكر icon صراحةً. كَلّ vertical في
# card_template_gallery له افتراضيّ مُتوافق مع طَبيعتها.

VERTICAL_TO_MOTIF: dict[str, str] = {
    "cafe":        "coffee",
    "restaurant":  "fork_knife",
    "clinic":      "medical",
    "shop":        "shopping_bag",
    "isp":         "wifi",
    "hotel":       "bed",
    "salon":       "scissors",
    "gym":         "dumbbell",
    "school":      "grad_cap",
    "events":      "balloons",
    "mosque":      "mosque",
    "charity":     "heart",
    "gaming":      "gamepad",
    "generic":     "wifi",
}


def resolve_motif(motif_or_vertical: str) -> str:
    """يُرجع مفتاح motif سَليم: لو وُرد motif صريح في الـregistry نَستعمله،
    وإلّا نَعتبره vertical ونَستخدم خريطة VERTICAL_TO_MOTIF، وإلّا generic."""
    key = (motif_or_vertical or "").strip().lower()
    if key in _REGISTRY:
        return key
    if key in VERTICAL_TO_MOTIF:
        return VERTICAL_TO_MOTIF[key]
    return "generic"


def motif_svg(motif: str, cx: float, cy: float, size: float, *,
               color: str = "#ffffff", opacity: float = 1.0,
               stroke_weight: float = 0.08) -> str:
    """يَبني عناصر SVG لرمز motif في box ‎``size × size``‎ مَركّز عند
    ‎(cx, cy)‎. الـcolor يُمَرَّر لكلّ stroke/fill. الـstroke_weight نِسبيّ
    لحجم الـbox (افتراضي 8%). يُرجع نَصّ SVG (ليس وَسم svg كامل — فقط
    العناصر، لتَضمين داخل SVG أكبر)."""
    fn = _REGISTRY.get(resolve_motif(motif), _wifi)
    return fn(cx, cy, max(1.0, size), color, max(0.0, min(1.0, opacity)),
               max(0.01, stroke_weight))


def motif_symbol_paths(motif: str) -> str:
    """يَبني الـpaths للـmotif في box ‎100 × 100‎ بـ ``currentColor`` كي
    يَكون قابلًا للإعادة الاستعمال داخل ‎<symbol id="…" viewBox="0 0 100 100">‎
    وتَلوينه بـCSS من خارج. مُختصر مَخصوص لصفحات الـhotspot المُكتفية
    ذاتيًّا (walled-garden) — لا fills/strokes جامدة فيُحدّد اللون من
    خارج، فلا حاجة لتَكرار الـmarkup لمَواضع مُختلفة بألوان مُختلفة."""
    return motif_svg(motif, 50, 50, 100, color="currentColor", opacity=1.0)


__all__ = [
    "motif_svg",
    "motif_symbol_paths",
    "resolve_motif",
    "VERTICAL_TO_MOTIF",
]
