# -*- coding: utf-8 -*-
"""card_motif_patterns — أنماط SVG قابلة للتَكرار من رُسوم خطّيّة دَقيقة
لكل قِطاع (cafe/clinic/restaurant/…) لاستعمالها كَخَلفيّة هَامِسة على
الكروت وصَفحات الـhotspot — الإلهام: نَمط مَقهى مَتكَرّر من فناجين/
حُبوب/مَلاعق صَغيرة بخُطوط رَفيعة.

كل قطاع له «طَقم» (set) من ~5-7 motifs مَرسومة بِخُطوط فقط (no fills)
في box ‎32×32‎. الـtile composer يَأخذ الطَقم ويُولّد ‎<pattern>‎ SVG
‎220×220‎ يَحوي 6-8 مَواضع مُتعَرّجة (brick layout) للموتيفات بحَجم
‎28-36px‎. الـSVG ‎<pattern>‎ يَتَكَرّر تلقائيًّا عبر الـfill — تَعريف
واحد، خَلفيّة كاملة، حَجم تَخزينيّ ضَئيل (مَطلوب لِـwalled-garden).

الألوان: المَوتيفات تَستعمل ``currentColor`` فيَتَلوّن الـpattern من
لون الـcontainer (CSS) أو من ‎color=""‎ على الـ<g> الحاوي.

كل draw fn يَأخذ (x, y, sz, sw) حيث x, y زاوية فَوق-يَسار، sz حَجم
الـbox، sw عَرض الخَطّ. يُعيد قَطعة SVG داخليّة (لا ‎<svg>‎ كامل).
"""
from __future__ import annotations

from typing import Callable

_StrokeFn = Callable[[float, float, float, float], str]


def _xml(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def _path(d: str, sw: float, *, cap: str = "round", join: str = "round",
          fill: str = "none") -> str:
    return (f'<path d="{d}" fill="{fill}" stroke="currentColor" '
            f'stroke-width="{sw:.2f}" stroke-linecap="{cap}" '
            f'stroke-linejoin="{join}"/>')


def _circle(cx: float, cy: float, r: float, sw: float,
             fill: str = "none") -> str:
    return (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="currentColor" '
            f'stroke-width="{sw:.2f}"/>')


def _ellipse(cx: float, cy: float, rx: float, ry: float, sw: float,
              fill: str = "none") -> str:
    return (f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{rx:.2f}" '
            f'ry="{ry:.2f}" fill="{fill}" stroke="currentColor" '
            f'stroke-width="{sw:.2f}"/>')


# ════════════════════════════════════════════════════════════════════
# CAFE — كوب ذهاب، كوب طاولة، حُبوب، مِلعقة، مكعّب سُكّر، ورقة، إبريق
# ════════════════════════════════════════════════════════════════════

def cafe_to_go_cup(x: float, y: float, sz: float, sw: float) -> str:
    """كوب ذَهاب: شَكل tapered + غِطاء + شُريط (به فُتحة شُرب)."""
    s = sz
    # غِطاء (مستطيل أُفقي رَقيق فَوق)
    lid = _path(
        f"M{x + s*0.18:.2f} {y + s*0.20:.2f} L{x + s*0.82:.2f} {y + s*0.20:.2f}",
        sw)
    # فُتحة شُرب
    sip = _path(
        f"M{x + s*0.55:.2f} {y + s*0.10:.2f} L{x + s*0.70:.2f} {y + s*0.18:.2f}",
        sw * 0.8)
    # جسم الكوب
    body = _path(
        f"M{x + s*0.20:.2f} {y + s*0.20:.2f} "
        f"L{x + s*0.28:.2f} {y + s*0.86:.2f} "
        f"L{x + s*0.72:.2f} {y + s*0.86:.2f} "
        f"L{x + s*0.80:.2f} {y + s*0.20:.2f}",
        sw, fill="none")
    # شَريط منتصف
    band = _path(
        f"M{x + s*0.24:.2f} {y + s*0.50:.2f} L{x + s*0.76:.2f} {y + s*0.50:.2f}",
        sw * 0.7)
    return lid + sip + body + band


def cafe_cup_saucer(x: float, y: float, sz: float, sw: float) -> str:
    """فُنجان + طَبق + مَقبض."""
    s = sz
    cup_y = y + s * 0.30
    cup_h = s * 0.34
    cup = _path(
        f"M{x + s*0.22:.2f} {cup_y:.2f} "
        f"L{x + s*0.26:.2f} {cup_y + cup_h:.2f} "
        f"L{x + s*0.66:.2f} {cup_y + cup_h:.2f} "
        f"L{x + s*0.70:.2f} {cup_y:.2f} Z",
        sw, join="round")
    # مَقبض (قَوس على اليَمين)
    handle = _path(
        f"M{x + s*0.70:.2f} {cup_y + s*0.06:.2f} "
        f"a {s*0.10:.2f} {s*0.10:.2f} 0 1 1 0 {s*0.18:.2f}",
        sw)
    # طَبق
    saucer = _path(
        f"M{x + s*0.10:.2f} {cup_y + cup_h:.2f} "
        f"L{x + s*0.78:.2f} {cup_y + cup_h:.2f}",
        sw)
    return cup + handle + saucer


def cafe_beans(x: float, y: float, sz: float, sw: float) -> str:
    """ثَلاث حُبوب قهوة مُتلاصِقة + خَطّ شَقّ مَركزي لكلّ منها."""
    s = sz
    out = ""
    for cx_, cy_ in ((x + s*0.25, y + s*0.35),
                       (x + s*0.55, y + s*0.55),
                       (x + s*0.35, y + s*0.70)):
        out += _ellipse(cx_, cy_, s*0.13, s*0.20, sw)
        # شَقّ مَركزي (قَوس صَغير)
        out += _path(
            f"M{cx_:.2f} {cy_ - s*0.16:.2f} "
            f"q {s*0.04:.2f} {s*0.08:.2f} 0 {s*0.32:.2f}",
            sw * 0.7)
    return out


def cafe_spoon(x: float, y: float, sz: float, sw: float) -> str:
    """مِلعقة قَهوة: قَبضة + رَأس بَيضويّ."""
    s = sz
    head = _ellipse(x + s*0.30, y + s*0.30, s*0.12, s*0.16, sw)
    handle = _path(
        f"M{x + s*0.30:.2f} {y + s*0.46:.2f} L{x + s*0.78:.2f} {y + s*0.84:.2f}",
        sw)
    return head + handle


def cafe_sugar(x: float, y: float, sz: float, sw: float) -> str:
    """مُكعّب سُكّر: مُربّع + خَطّ قُطر يُعطي مَنظور 3D خَفيف."""
    s = sz
    sq = _path(
        f"M{x + s*0.28:.2f} {y + s*0.36:.2f} "
        f"l {s*0.36:.2f} 0 l 0 {s*0.36:.2f} "
        f"l {-s*0.36:.2f} 0 Z",
        sw)
    diag = _path(
        f"M{x + s*0.28:.2f} {y + s*0.36:.2f} "
        f"L{x + s*0.38:.2f} {y + s*0.24:.2f} "
        f"L{x + s*0.74:.2f} {y + s*0.24:.2f} "
        f"L{x + s*0.64:.2f} {y + s*0.36:.2f} "
        f"M{x + s*0.74:.2f} {y + s*0.24:.2f} "
        f"L{x + s*0.74:.2f} {y + s*0.60:.2f} "
        f"L{x + s*0.64:.2f} {y + s*0.72:.2f}",
        sw * 0.7)
    return sq + diag


def cafe_leaf(x: float, y: float, sz: float, sw: float) -> str:
    """ورقة نَعناع: شَكل ميمّ + عِرق وُسطى."""
    s = sz
    leaf = _path(
        f"M{x + s*0.50:.2f} {y + s*0.18:.2f} "
        f"q {s*0.30:.2f} {s*0.20:.2f} 0 {s*0.60:.2f} "
        f"q {-s*0.30:.2f} {-s*0.20:.2f} 0 {-s*0.60:.2f} Z",
        sw)
    vein = _path(
        f"M{x + s*0.50:.2f} {y + s*0.22:.2f} L{x + s*0.50:.2f} {y + s*0.74:.2f}",
        sw * 0.7)
    return leaf + vein


def cafe_kettle(x: float, y: float, sz: float, sw: float) -> str:
    """إبريق صَغير: بَدن دائريّ + مَقبض + صَنبور."""
    s = sz
    body = _circle(x + s*0.50, y + s*0.55, s*0.22, sw)
    handle = _path(
        f"M{x + s*0.50:.2f} {y + s*0.32:.2f} q {s*0.10:.2f} {-s*0.18:.2f} {s*0.22:.2f} 0",
        sw)
    spout = _path(
        f"M{x + s*0.26:.2f} {y + s*0.50:.2f} L{x + s*0.14:.2f} {y + s*0.36:.2f}",
        sw)
    return body + handle + spout


# ════════════════════════════════════════════════════════════════════
# RESTAURANT — شَوكة، سكّينة، مِلعقة، طَبق، قُبّعة شِيف، شَريحة
# ════════════════════════════════════════════════════════════════════

def resto_fork(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    handle = _path(
        f"M{x + s*0.50:.2f} {y + s*0.40:.2f} L{x + s*0.50:.2f} {y + s*0.86:.2f}",
        sw)
    tines = _path(
        f"M{x + s*0.34:.2f} {y + s*0.16:.2f} L{x + s*0.34:.2f} {y + s*0.38:.2f} "
        f"M{x + s*0.50:.2f} {y + s*0.16:.2f} L{x + s*0.50:.2f} {y + s*0.38:.2f} "
        f"M{x + s*0.66:.2f} {y + s*0.16:.2f} L{x + s*0.66:.2f} {y + s*0.38:.2f}",
        sw * 0.9)
    base = _path(
        f"M{x + s*0.30:.2f} {y + s*0.38:.2f} L{x + s*0.70:.2f} {y + s*0.38:.2f}",
        sw)
    return tines + base + handle


def resto_knife(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    blade = _path(
        f"M{x + s*0.40:.2f} {y + s*0.14:.2f} "
        f"L{x + s*0.62:.2f} {y + s*0.32:.2f} "
        f"L{x + s*0.56:.2f} {y + s*0.52:.2f} "
        f"L{x + s*0.42:.2f} {y + s*0.52:.2f} Z",
        sw)
    handle = _path(
        f"M{x + s*0.48:.2f} {y + s*0.52:.2f} L{x + s*0.48:.2f} {y + s*0.86:.2f}",
        sw)
    return blade + handle


def resto_spoon(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    bowl = _ellipse(x + s*0.50, y + s*0.30, s*0.16, s*0.20, sw)
    handle = _path(
        f"M{x + s*0.50:.2f} {y + s*0.50:.2f} L{x + s*0.50:.2f} {y + s*0.86:.2f}",
        sw)
    return bowl + handle


def resto_plate(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    outer = _circle(x + s*0.50, y + s*0.50, s*0.32, sw)
    inner = _circle(x + s*0.50, y + s*0.50, s*0.22, sw * 0.7)
    return outer + inner


def resto_chef_hat(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    # حَافة (مستطيل أسفل)
    band = _path(
        f"M{x + s*0.28:.2f} {y + s*0.62:.2f} L{x + s*0.72:.2f} {y + s*0.62:.2f} "
        f"L{x + s*0.72:.2f} {y + s*0.78:.2f} L{x + s*0.28:.2f} {y + s*0.78:.2f} Z",
        sw)
    # ثَلاث فُقاعات فَوق
    bubbles = (
        _circle(x + s*0.36, y + s*0.40, s*0.12, sw)
        + _circle(x + s*0.50, y + s*0.32, s*0.14, sw)
        + _circle(x + s*0.64, y + s*0.40, s*0.12, sw)
    )
    return bubbles + band


def resto_slice(x: float, y: float, sz: float, sw: float) -> str:
    """شَريحة بيتزا: مُثلَّث + قُشرة + نَقطة (طَبق صَغير)."""
    s = sz
    slice_ = _path(
        f"M{x + s*0.50:.2f} {y + s*0.18:.2f} "
        f"L{x + s*0.20:.2f} {y + s*0.76:.2f} "
        f"L{x + s*0.80:.2f} {y + s*0.76:.2f} Z",
        sw)
    cheese = _circle(x + s*0.45, y + s*0.50, s*0.05, sw * 0.7)
    pep = _circle(x + s*0.58, y + s*0.62, s*0.05, sw * 0.7)
    return slice_ + cheese + pep


# ════════════════════════════════════════════════════════════════════
# CLINIC — صَليب، سَمّاعة، حَبّة، قَلب، حَقن، ميزان حَرارة
# ════════════════════════════════════════════════════════════════════

def clinic_cross(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    return _path(
        f"M{x + s*0.50:.2f} {y + s*0.20:.2f} L{x + s*0.50:.2f} {y + s*0.80:.2f} "
        f"M{x + s*0.20:.2f} {y + s*0.50:.2f} L{x + s*0.80:.2f} {y + s*0.50:.2f}",
        sw * 1.2)


def clinic_stethoscope(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    # سَمّاعة-قُرص
    disc = _circle(x + s*0.62, y + s*0.70, s*0.12, sw)
    # أنبوب
    tube = _path(
        f"M{x + s*0.62:.2f} {y + s*0.58:.2f} "
        f"q 0 {-s*0.18:.2f} {-s*0.18:.2f} {-s*0.18:.2f} "
        f"q {-s*0.18:.2f} 0 {-s*0.18:.2f} {s*0.18:.2f}",
        sw)
    return tube + disc


def clinic_pill(x: float, y: float, sz: float, sw: float) -> str:
    """كَبسولة مَمدودة + خَطّ تَقسيم."""
    s = sz
    cap = _path(
        f"M{x + s*0.22:.2f} {y + s*0.50:.2f} "
        f"a {s*0.14:.2f} {s*0.14:.2f} 0 0 1 {s*0.28:.2f} 0 "
        f"l 0 {s*0.00:.2f} l {-s*0.28:.2f} 0 Z",
        sw)
    cap2 = _path(
        f"M{x + s*0.50:.2f} {y + s*0.50:.2f} "
        f"l {s*0.28:.2f} 0 "
        f"a {s*0.14:.2f} {s*0.14:.2f} 0 0 1 {-s*0.28:.2f} 0",
        sw)
    return cap + cap2


def clinic_heart(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    return _path(
        f"M{x + s*0.50:.2f} {y + s*0.78:.2f} "
        f"C {x + s*0.20:.2f} {y + s*0.55:.2f}, "
        f"{x + s*0.20:.2f} {y + s*0.25:.2f}, "
        f"{x + s*0.50:.2f} {y + s*0.40:.2f} "
        f"C {x + s*0.80:.2f} {y + s*0.25:.2f}, "
        f"{x + s*0.80:.2f} {y + s*0.55:.2f}, "
        f"{x + s*0.50:.2f} {y + s*0.78:.2f} Z",
        sw)


def clinic_syringe(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    # برميل
    barrel = _path(
        f"M{x + s*0.30:.2f} {y + s*0.40:.2f} L{x + s*0.60:.2f} {y + s*0.40:.2f} "
        f"L{x + s*0.60:.2f} {y + s*0.60:.2f} L{x + s*0.30:.2f} {y + s*0.60:.2f} Z",
        sw)
    # إبرة
    needle = _path(
        f"M{x + s*0.60:.2f} {y + s*0.50:.2f} L{x + s*0.82:.2f} {y + s*0.50:.2f}",
        sw)
    # مِكبَس
    plunger = _path(
        f"M{x + s*0.30:.2f} {y + s*0.45:.2f} L{x + s*0.18:.2f} {y + s*0.45:.2f} "
        f"M{x + s*0.30:.2f} {y + s*0.55:.2f} L{x + s*0.18:.2f} {y + s*0.55:.2f} "
        f"M{x + s*0.18:.2f} {y + s*0.42:.2f} L{x + s*0.18:.2f} {y + s*0.58:.2f}",
        sw)
    return barrel + needle + plunger


def clinic_thermometer(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    bulb = _circle(x + s*0.50, y + s*0.78, s*0.10, sw)
    stem = _path(
        f"M{x + s*0.46:.2f} {y + s*0.22:.2f} L{x + s*0.46:.2f} {y + s*0.70:.2f} "
        f"M{x + s*0.54:.2f} {y + s*0.22:.2f} L{x + s*0.54:.2f} {y + s*0.70:.2f}",
        sw)
    return bulb + stem


# ════════════════════════════════════════════════════════════════════
# SHOP — كيس، عَربة، تاج سِعر، صُندوق، هَدية
# ════════════════════════════════════════════════════════════════════

def shop_bag(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.28:.2f} {y + s*0.40:.2f} L{x + s*0.72:.2f} {y + s*0.40:.2f} "
        f"L{x + s*0.68:.2f} {y + s*0.82:.2f} L{x + s*0.32:.2f} {y + s*0.82:.2f} Z",
        sw)
    handle = _path(
        f"M{x + s*0.38:.2f} {y + s*0.40:.2f} q 0 {-s*0.20:.2f} {s*0.12:.2f} {-s*0.20:.2f} "
        f"q {s*0.12:.2f} 0 {s*0.12:.2f} {s*0.20:.2f}",
        sw)
    return body + handle


def shop_cart(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    basket = _path(
        f"M{x + s*0.20:.2f} {y + s*0.36:.2f} L{x + s*0.74:.2f} {y + s*0.36:.2f} "
        f"L{x + s*0.66:.2f} {y + s*0.62:.2f} L{x + s*0.32:.2f} {y + s*0.62:.2f} Z",
        sw)
    handle = _path(
        f"M{x + s*0.20:.2f} {y + s*0.36:.2f} L{x + s*0.12:.2f} {y + s*0.22:.2f}",
        sw)
    wheels = (
        _circle(x + s*0.36, y + s*0.74, s*0.05, sw)
        + _circle(x + s*0.62, y + s*0.74, s*0.05, sw)
    )
    return basket + handle + wheels


def shop_tag(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    tag = _path(
        f"M{x + s*0.62:.2f} {y + s*0.20:.2f} L{x + s*0.20:.2f} {y + s*0.62:.2f} "
        f"L{x + s*0.40:.2f} {y + s*0.82:.2f} L{x + s*0.82:.2f} {y + s*0.40:.2f} Z",
        sw)
    hole = _circle(x + s*0.68, y + s*0.34, s*0.04, sw)
    return tag + hole


def shop_box(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.22:.2f} {y + s*0.36:.2f} l {s*0.56:.2f} 0 "
        f"l 0 {s*0.46:.2f} l {-s*0.56:.2f} 0 Z",
        sw)
    lid = _path(
        f"M{x + s*0.22:.2f} {y + s*0.36:.2f} L{x + s*0.50:.2f} {y + s*0.24:.2f} "
        f"L{x + s*0.78:.2f} {y + s*0.36:.2f}",
        sw)
    return body + lid


def shop_gift(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    box = _path(
        f"M{x + s*0.26:.2f} {y + s*0.42:.2f} l {s*0.48:.2f} 0 "
        f"l 0 {s*0.42:.2f} l {-s*0.48:.2f} 0 Z",
        sw)
    ribbon = _path(
        f"M{x + s*0.50:.2f} {y + s*0.42:.2f} L{x + s*0.50:.2f} {y + s*0.84:.2f} "
        f"M{x + s*0.26:.2f} {y + s*0.62:.2f} L{x + s*0.74:.2f} {y + s*0.62:.2f}",
        sw)
    bow = _path(
        f"M{x + s*0.50:.2f} {y + s*0.42:.2f} "
        f"q {-s*0.14:.2f} {-s*0.14:.2f} {-s*0.06:.2f} {-s*0.20:.2f} "
        f"q {s*0.10:.2f} {-s*0.04:.2f} {s*0.06:.2f} {s*0.20:.2f} "
        f"M{x + s*0.50:.2f} {y + s*0.42:.2f} "
        f"q {s*0.14:.2f} {-s*0.14:.2f} {s*0.06:.2f} {-s*0.20:.2f} "
        f"q {-s*0.10:.2f} {-s*0.04:.2f} {-s*0.06:.2f} {s*0.20:.2f}",
        sw * 0.8)
    return box + ribbon + bow


# ════════════════════════════════════════════════════════════════════
# ISP / NETWORK — wifi، signal bars، globe، router، antenna
# ════════════════════════════════════════════════════════════════════

def isp_wifi(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    dot = _circle(x + s*0.50, y + s*0.74, s*0.04, sw, fill="currentColor")
    arcs = _path(
        f"M{x + s*0.30:.2f} {y + s*0.50:.2f} "
        f"a {s*0.20:.2f} {s*0.20:.2f} 0 0 1 {s*0.40:.2f} 0 "
        f"M{x + s*0.22:.2f} {y + s*0.40:.2f} "
        f"a {s*0.28:.2f} {s*0.28:.2f} 0 0 1 {s*0.56:.2f} 0 "
        f"M{x + s*0.16:.2f} {y + s*0.30:.2f} "
        f"a {s*0.34:.2f} {s*0.34:.2f} 0 0 1 {s*0.68:.2f} 0",
        sw)
    return arcs + dot


def isp_signal_bars(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    return _path(
        f"M{x + s*0.24:.2f} {y + s*0.70:.2f} L{x + s*0.24:.2f} {y + s*0.60:.2f} "
        f"M{x + s*0.40:.2f} {y + s*0.70:.2f} L{x + s*0.40:.2f} {y + s*0.48:.2f} "
        f"M{x + s*0.56:.2f} {y + s*0.70:.2f} L{x + s*0.56:.2f} {y + s*0.36:.2f} "
        f"M{x + s*0.72:.2f} {y + s*0.70:.2f} L{x + s*0.72:.2f} {y + s*0.24:.2f}",
        sw * 1.3, cap="round")


def isp_globe(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    outer = _circle(x + s*0.50, y + s*0.50, s*0.28, sw)
    meridian = _ellipse(x + s*0.50, y + s*0.50, s*0.14, s*0.28, sw * 0.8)
    equator = _path(
        f"M{x + s*0.22:.2f} {y + s*0.50:.2f} L{x + s*0.78:.2f} {y + s*0.50:.2f}",
        sw * 0.8)
    return outer + meridian + equator


def isp_router(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.20:.2f} {y + s*0.54:.2f} L{x + s*0.80:.2f} {y + s*0.54:.2f} "
        f"L{x + s*0.80:.2f} {y + s*0.74:.2f} L{x + s*0.20:.2f} {y + s*0.74:.2f} Z",
        sw)
    leds = (
        _circle(x + s*0.32, y + s*0.64, s*0.03, sw * 0.7, fill="currentColor")
        + _circle(x + s*0.44, y + s*0.64, s*0.03, sw * 0.7, fill="currentColor")
        + _circle(x + s*0.56, y + s*0.64, s*0.03, sw * 0.7, fill="currentColor")
    )
    antennas = _path(
        f"M{x + s*0.32:.2f} {y + s*0.54:.2f} L{x + s*0.26:.2f} {y + s*0.30:.2f} "
        f"M{x + s*0.68:.2f} {y + s*0.54:.2f} L{x + s*0.74:.2f} {y + s*0.30:.2f}",
        sw * 0.9)
    return body + leds + antennas


def isp_antenna(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    pole = _path(
        f"M{x + s*0.50:.2f} {y + s*0.20:.2f} L{x + s*0.50:.2f} {y + s*0.78:.2f}",
        sw)
    bursts = _path(
        f"M{x + s*0.36:.2f} {y + s*0.36:.2f} "
        f"a {s*0.18:.2f} {s*0.18:.2f} 0 0 1 {s*0.28:.2f} 0 "
        f"M{x + s*0.28:.2f} {y + s*0.26:.2f} "
        f"a {s*0.26:.2f} {s*0.26:.2f} 0 0 1 {s*0.44:.2f} 0",
        sw)
    return pole + bursts


# ════════════════════════════════════════════════════════════════════
# HOTEL — سَرير، مِفتاح، نَجمة، جَرس، حَقيبة
# ════════════════════════════════════════════════════════════════════

def hotel_bed(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    head = _path(
        f"M{x + s*0.18:.2f} {y + s*0.50:.2f} L{x + s*0.18:.2f} {y + s*0.72:.2f}",
        sw)
    body = _path(
        f"M{x + s*0.18:.2f} {y + s*0.66:.2f} L{x + s*0.82:.2f} {y + s*0.66:.2f} "
        f"L{x + s*0.82:.2f} {y + s*0.72:.2f}",
        sw)
    pillow = _path(
        f"M{x + s*0.22:.2f} {y + s*0.50:.2f} L{x + s*0.42:.2f} {y + s*0.50:.2f} "
        f"L{x + s*0.42:.2f} {y + s*0.62:.2f}",
        sw * 0.8)
    return head + body + pillow


def hotel_key(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    bow = _circle(x + s*0.30, y + s*0.50, s*0.12, sw)
    shaft = _path(
        f"M{x + s*0.42:.2f} {y + s*0.50:.2f} L{x + s*0.82:.2f} {y + s*0.50:.2f}",
        sw)
    tooth1 = _path(
        f"M{x + s*0.70:.2f} {y + s*0.50:.2f} L{x + s*0.70:.2f} {y + s*0.60:.2f}",
        sw)
    tooth2 = _path(
        f"M{x + s*0.78:.2f} {y + s*0.50:.2f} L{x + s*0.78:.2f} {y + s*0.60:.2f}",
        sw)
    return bow + shaft + tooth1 + tooth2


def hotel_star(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    cx = x + s*0.50
    cy = y + s*0.50
    r1 = s*0.28
    r2 = s*0.12
    import math as _m
    pts = []
    for i in range(10):
        a = -_m.pi/2 + i * _m.pi / 5
        r = r1 if i % 2 == 0 else r2
        pts.append((cx + r * _m.cos(a), cy + r * _m.sin(a)))
    d = "M" + " L".join(f"{px:.2f} {py:.2f}" for px, py in pts) + " Z"
    return _path(d, sw)


def hotel_bell(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.24:.2f} {y + s*0.66:.2f} "
        f"q 0 {-s*0.34:.2f} {s*0.26:.2f} {-s*0.34:.2f} "
        f"q {s*0.26:.2f} 0 {s*0.26:.2f} {s*0.34:.2f}",
        sw)
    base = _path(
        f"M{x + s*0.20:.2f} {y + s*0.66:.2f} L{x + s*0.80:.2f} {y + s*0.66:.2f}",
        sw)
    knob = _circle(x + s*0.50, y + s*0.26, s*0.04, sw, fill="currentColor")
    return body + base + knob


def hotel_suitcase(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.20:.2f} {y + s*0.40:.2f} L{x + s*0.80:.2f} {y + s*0.40:.2f} "
        f"L{x + s*0.80:.2f} {y + s*0.78:.2f} L{x + s*0.20:.2f} {y + s*0.78:.2f} Z",
        sw)
    handle = _path(
        f"M{x + s*0.40:.2f} {y + s*0.40:.2f} L{x + s*0.40:.2f} {y + s*0.30:.2f} "
        f"L{x + s*0.60:.2f} {y + s*0.30:.2f} L{x + s*0.60:.2f} {y + s*0.40:.2f}",
        sw)
    line = _path(
        f"M{x + s*0.20:.2f} {y + s*0.56:.2f} L{x + s*0.80:.2f} {y + s*0.56:.2f}",
        sw * 0.7)
    return body + handle + line


# ════════════════════════════════════════════════════════════════════
# SALON — مَقصّ، مُشط، مِرآة، مُجَفّف، طِلاء
# ════════════════════════════════════════════════════════════════════

def salon_scissors(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    c1 = _circle(x + s*0.28, y + s*0.68, s*0.10, sw)
    c2 = _circle(x + s*0.72, y + s*0.68, s*0.10, sw)
    blade1 = _path(
        f"M{x + s*0.36:.2f} {y + s*0.62:.2f} L{x + s*0.60:.2f} {y + s*0.22:.2f}",
        sw)
    blade2 = _path(
        f"M{x + s*0.64:.2f} {y + s*0.62:.2f} L{x + s*0.40:.2f} {y + s*0.22:.2f}",
        sw)
    return c1 + c2 + blade1 + blade2


def salon_comb(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    spine = _path(
        f"M{x + s*0.18:.2f} {y + s*0.40:.2f} L{x + s*0.82:.2f} {y + s*0.40:.2f}",
        sw)
    teeth = ""
    for tx in (0.24, 0.32, 0.40, 0.48, 0.56, 0.64, 0.72):
        teeth += _path(
            f"M{x + s*tx:.2f} {y + s*0.40:.2f} "
            f"L{x + s*tx:.2f} {y + s*0.70:.2f}",
            sw * 0.7)
    return spine + teeth


def salon_mirror(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    frame = _circle(x + s*0.50, y + s*0.40, s*0.22, sw)
    inner = _circle(x + s*0.50, y + s*0.40, s*0.14, sw * 0.6)
    handle = _path(
        f"M{x + s*0.50:.2f} {y + s*0.62:.2f} L{x + s*0.50:.2f} {y + s*0.86:.2f}",
        sw)
    return frame + inner + handle


def salon_dryer(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    barrel = _path(
        f"M{x + s*0.20:.2f} {y + s*0.38:.2f} L{x + s*0.62:.2f} {y + s*0.38:.2f} "
        f"L{x + s*0.62:.2f} {y + s*0.62:.2f} L{x + s*0.20:.2f} {y + s*0.62:.2f} Z",
        sw)
    nozzle = _path(
        f"M{x + s*0.62:.2f} {y + s*0.46:.2f} L{x + s*0.78:.2f} {y + s*0.42:.2f} "
        f"L{x + s*0.78:.2f} {y + s*0.58:.2f} L{x + s*0.62:.2f} {y + s*0.54:.2f}",
        sw)
    handle = _path(
        f"M{x + s*0.30:.2f} {y + s*0.62:.2f} L{x + s*0.30:.2f} {y + s*0.82:.2f} "
        f"L{x + s*0.42:.2f} {y + s*0.82:.2f} L{x + s*0.42:.2f} {y + s*0.62:.2f}",
        sw)
    return barrel + nozzle + handle


def salon_polish(x: float, y: float, sz: float, sw: float) -> str:
    """قارورة طِلاء أظافر."""
    s = sz
    body = _path(
        f"M{x + s*0.32:.2f} {y + s*0.46:.2f} L{x + s*0.68:.2f} {y + s*0.46:.2f} "
        f"L{x + s*0.68:.2f} {y + s*0.82:.2f} L{x + s*0.32:.2f} {y + s*0.82:.2f} Z",
        sw)
    cap = _path(
        f"M{x + s*0.40:.2f} {y + s*0.46:.2f} L{x + s*0.40:.2f} {y + s*0.24:.2f} "
        f"L{x + s*0.60:.2f} {y + s*0.24:.2f} L{x + s*0.60:.2f} {y + s*0.46:.2f}",
        sw)
    return body + cap


# ════════════════════════════════════════════════════════════════════
# GYM — دامبل، كَتلبيل، زُجاجة، حِذاء، قُرص وَزن
# ════════════════════════════════════════════════════════════════════

def gym_dumbbell(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    bar = _path(
        f"M{x + s*0.30:.2f} {y + s*0.50:.2f} L{x + s*0.70:.2f} {y + s*0.50:.2f}",
        sw * 1.4)
    pl = _path(
        f"M{x + s*0.20:.2f} {y + s*0.38:.2f} L{x + s*0.20:.2f} {y + s*0.62:.2f} "
        f"M{x + s*0.30:.2f} {y + s*0.34:.2f} L{x + s*0.30:.2f} {y + s*0.66:.2f}",
        sw * 1.2)
    pr = _path(
        f"M{x + s*0.80:.2f} {y + s*0.38:.2f} L{x + s*0.80:.2f} {y + s*0.62:.2f} "
        f"M{x + s*0.70:.2f} {y + s*0.34:.2f} L{x + s*0.70:.2f} {y + s*0.66:.2f}",
        sw * 1.2)
    return pl + bar + pr


def gym_kettlebell(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    handle = _path(
        f"M{x + s*0.36:.2f} {y + s*0.42:.2f} q 0 {-s*0.20:.2f} {s*0.14:.2f} {-s*0.20:.2f} "
        f"q {s*0.14:.2f} 0 {s*0.14:.2f} {s*0.20:.2f}",
        sw)
    body = _path(
        f"M{x + s*0.32:.2f} {y + s*0.46:.2f} "
        f"a {s*0.20:.2f} {s*0.22:.2f} 0 1 0 {s*0.36:.2f} 0 Z",
        sw)
    return handle + body


def gym_bottle(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.36:.2f} {y + s*0.34:.2f} L{x + s*0.64:.2f} {y + s*0.34:.2f} "
        f"L{x + s*0.64:.2f} {y + s*0.82:.2f} L{x + s*0.36:.2f} {y + s*0.82:.2f} Z",
        sw)
    cap = _path(
        f"M{x + s*0.40:.2f} {y + s*0.34:.2f} L{x + s*0.40:.2f} {y + s*0.22:.2f} "
        f"L{x + s*0.60:.2f} {y + s*0.22:.2f} L{x + s*0.60:.2f} {y + s*0.34:.2f}",
        sw)
    line = _path(
        f"M{x + s*0.36:.2f} {y + s*0.48:.2f} L{x + s*0.64:.2f} {y + s*0.48:.2f}",
        sw * 0.7)
    return body + cap + line


def gym_shoe(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    sole = _path(
        f"M{x + s*0.16:.2f} {y + s*0.70:.2f} "
        f"q {s*0.06:.2f} {-s*0.20:.2f} {s*0.30:.2f} {-s*0.20:.2f} "
        f"q {s*0.16:.2f} 0 {s*0.36:.2f} {s*0.20:.2f} "
        f"L{x + s*0.16:.2f} {y + s*0.70:.2f}",
        sw)
    lace = _path(
        f"M{x + s*0.36:.2f} {y + s*0.58:.2f} L{x + s*0.48:.2f} {y + s*0.58:.2f} "
        f"M{x + s*0.40:.2f} {y + s*0.62:.2f} L{x + s*0.52:.2f} {y + s*0.62:.2f}",
        sw * 0.7)
    return sole + lace


def gym_weight(x: float, y: float, sz: float, sw: float) -> str:
    """قُرص وَزن — دائرة + حَفر مَركزي."""
    s = sz
    outer = _circle(x + s*0.50, y + s*0.50, s*0.26, sw)
    inner = _circle(x + s*0.50, y + s*0.50, s*0.06, sw)
    return outer + inner


# ════════════════════════════════════════════════════════════════════
# SCHOOL — قُبّعة، كِتاب، قَلم، مِسطرة، تُفّاحة
# ════════════════════════════════════════════════════════════════════

def school_cap(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    diamond = _path(
        f"M{x + s*0.50:.2f} {y + s*0.28:.2f} "
        f"L{x + s*0.82:.2f} {y + s*0.46:.2f} "
        f"L{x + s*0.50:.2f} {y + s*0.62:.2f} "
        f"L{x + s*0.18:.2f} {y + s*0.46:.2f} Z",
        sw)
    base = _path(
        f"M{x + s*0.30:.2f} {y + s*0.54:.2f} L{x + s*0.30:.2f} {y + s*0.70:.2f} "
        f"q 0 {s*0.06:.2f} {s*0.20:.2f} {s*0.06:.2f} "
        f"q {s*0.20:.2f} 0 {s*0.20:.2f} {-s*0.06:.2f} "
        f"L{x + s*0.70:.2f} {y + s*0.54:.2f}",
        sw)
    tassel = _path(
        f"M{x + s*0.82:.2f} {y + s*0.46:.2f} L{x + s*0.82:.2f} {y + s*0.62:.2f}",
        sw * 0.7)
    return diamond + base + tassel


def school_book(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    cover = _path(
        f"M{x + s*0.24:.2f} {y + s*0.28:.2f} L{x + s*0.76:.2f} {y + s*0.28:.2f} "
        f"L{x + s*0.76:.2f} {y + s*0.78:.2f} L{x + s*0.24:.2f} {y + s*0.78:.2f} Z",
        sw)
    spine = _path(
        f"M{x + s*0.32:.2f} {y + s*0.28:.2f} L{x + s*0.32:.2f} {y + s*0.78:.2f}",
        sw * 0.7)
    lines = _path(
        f"M{x + s*0.40:.2f} {y + s*0.44:.2f} L{x + s*0.68:.2f} {y + s*0.44:.2f} "
        f"M{x + s*0.40:.2f} {y + s*0.54:.2f} L{x + s*0.68:.2f} {y + s*0.54:.2f}",
        sw * 0.7)
    return cover + spine + lines


def school_pencil(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.22:.2f} {y + s*0.70:.2f} L{x + s*0.66:.2f} {y + s*0.26:.2f} "
        f"L{x + s*0.78:.2f} {y + s*0.38:.2f} L{x + s*0.34:.2f} {y + s*0.82:.2f} Z",
        sw)
    tip = _path(
        f"M{x + s*0.66:.2f} {y + s*0.26:.2f} L{x + s*0.74:.2f} {y + s*0.18:.2f} "
        f"L{x + s*0.86:.2f} {y + s*0.30:.2f} L{x + s*0.78:.2f} {y + s*0.38:.2f}",
        sw)
    return body + tip


def school_ruler(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.18:.2f} {y + s*0.42:.2f} L{x + s*0.82:.2f} {y + s*0.42:.2f} "
        f"L{x + s*0.82:.2f} {y + s*0.58:.2f} L{x + s*0.18:.2f} {y + s*0.58:.2f} Z",
        sw)
    ticks = _path(
        f"M{x + s*0.28:.2f} {y + s*0.42:.2f} L{x + s*0.28:.2f} {y + s*0.50:.2f} "
        f"M{x + s*0.38:.2f} {y + s*0.42:.2f} L{x + s*0.38:.2f} {y + s*0.48:.2f} "
        f"M{x + s*0.48:.2f} {y + s*0.42:.2f} L{x + s*0.48:.2f} {y + s*0.52:.2f} "
        f"M{x + s*0.58:.2f} {y + s*0.42:.2f} L{x + s*0.58:.2f} {y + s*0.48:.2f} "
        f"M{x + s*0.68:.2f} {y + s*0.42:.2f} L{x + s*0.68:.2f} {y + s*0.50:.2f}",
        sw * 0.7)
    return body + ticks


def school_apple(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.50:.2f} {y + s*0.34:.2f} "
        f"C {x + s*0.20:.2f} {y + s*0.32:.2f}, "
        f"{x + s*0.22:.2f} {y + s*0.80:.2f}, "
        f"{x + s*0.50:.2f} {y + s*0.82:.2f} "
        f"C {x + s*0.78:.2f} {y + s*0.80:.2f}, "
        f"{x + s*0.80:.2f} {y + s*0.32:.2f}, "
        f"{x + s*0.50:.2f} {y + s*0.34:.2f}",
        sw)
    stem = _path(
        f"M{x + s*0.50:.2f} {y + s*0.32:.2f} L{x + s*0.50:.2f} {y + s*0.22:.2f}",
        sw)
    leaf = _path(
        f"M{x + s*0.52:.2f} {y + s*0.26:.2f} q {s*0.10:.2f} {-s*0.02:.2f} {s*0.10:.2f} {-s*0.10:.2f} "
        f"q {-s*0.10:.2f} 0 {-s*0.10:.2f} {s*0.10:.2f}",
        sw * 0.7)
    return body + stem + leaf


# ════════════════════════════════════════════════════════════════════
# EVENTS — بالونات، قُصاصات، هَدية، كَعكة، نَغمة
# ════════════════════════════════════════════════════════════════════

def event_balloons(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    b1 = _ellipse(x + s*0.35, y + s*0.36, s*0.12, s*0.16, sw)
    b2 = _ellipse(x + s*0.62, y + s*0.40, s*0.12, s*0.16, sw)
    strings = _path(
        f"M{x + s*0.35:.2f} {y + s*0.52:.2f} L{x + s*0.40:.2f} {y + s*0.82:.2f} "
        f"M{x + s*0.62:.2f} {y + s*0.56:.2f} L{x + s*0.58:.2f} {y + s*0.82:.2f}",
        sw * 0.7)
    return b1 + b2 + strings


def event_confetti(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    sparkles = ""
    for cx_, cy_, a in (
        (x + s*0.25, y + s*0.28, 20), (x + s*0.65, y + s*0.30, -30),
        (x + s*0.30, y + s*0.62, -15), (x + s*0.70, y + s*0.60, 45),
        (x + s*0.50, y + s*0.42, 0), (x + s*0.45, y + s*0.78, 10),
    ):
        sparkles += (
            f'<g transform="rotate({a},{cx_:.2f},{cy_:.2f})">'
            f'<path d="M{cx_ - s*0.05:.2f} {cy_:.2f} L{cx_ + s*0.05:.2f} {cy_:.2f}" '
            f'stroke="currentColor" stroke-width="{sw * 0.8:.2f}" '
            f'stroke-linecap="round"/></g>'
        )
    return sparkles


def event_gift(x: float, y: float, sz: float, sw: float) -> str:
    return shop_gift(x, y, sz, sw)


def event_cake(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    layer = _path(
        f"M{x + s*0.22:.2f} {y + s*0.50:.2f} L{x + s*0.78:.2f} {y + s*0.50:.2f} "
        f"L{x + s*0.78:.2f} {y + s*0.78:.2f} L{x + s*0.22:.2f} {y + s*0.78:.2f} Z",
        sw)
    drip = _path(
        f"M{x + s*0.22:.2f} {y + s*0.56:.2f} q {s*0.08:.2f} {s*0.08:.2f} {s*0.14:.2f} 0 "
        f"q {s*0.08:.2f} {s*0.08:.2f} {s*0.14:.2f} 0 "
        f"q {s*0.08:.2f} {s*0.08:.2f} {s*0.14:.2f} 0 "
        f"q {s*0.08:.2f} {s*0.08:.2f} {s*0.14:.2f} 0",
        sw * 0.7)
    candle = _path(
        f"M{x + s*0.50:.2f} {y + s*0.50:.2f} L{x + s*0.50:.2f} {y + s*0.32:.2f}",
        sw)
    flame = _path(
        f"M{x + s*0.50:.2f} {y + s*0.28:.2f} q {s*0.04:.2f} {-s*0.08:.2f} 0 {-s*0.10:.2f}",
        sw * 0.8)
    return layer + drip + candle + flame


def event_note(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    head = _ellipse(x + s*0.36, y + s*0.66, s*0.10, s*0.08, sw, fill="currentColor")
    stem = _path(
        f"M{x + s*0.46:.2f} {y + s*0.66:.2f} L{x + s*0.46:.2f} {y + s*0.26:.2f}",
        sw)
    flag = _path(
        f"M{x + s*0.46:.2f} {y + s*0.26:.2f} q {s*0.18:.2f} {s*0.04:.2f} {s*0.18:.2f} {s*0.18:.2f}",
        sw)
    return head + stem + flag


# ════════════════════════════════════════════════════════════════════
# MOSQUE — مَسجد، هلال، فَانوس، نَجمة، سُبحة
# ════════════════════════════════════════════════════════════════════

def mosque_dome(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    dome = _path(
        f"M{x + s*0.26:.2f} {y + s*0.60:.2f} "
        f"q 0 {-s*0.32:.2f} {s*0.24:.2f} {-s*0.32:.2f} "
        f"q {s*0.24:.2f} 0 {s*0.24:.2f} {s*0.32:.2f} Z",
        sw)
    base = _path(
        f"M{x + s*0.20:.2f} {y + s*0.60:.2f} L{x + s*0.80:.2f} {y + s*0.60:.2f} "
        f"L{x + s*0.80:.2f} {y + s*0.80:.2f} L{x + s*0.20:.2f} {y + s*0.80:.2f} Z",
        sw)
    return dome + base


def mosque_crescent(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    return _path(
        f"M{x + s*0.50:.2f} {y + s*0.24:.2f} "
        f"a {s*0.26:.2f} {s*0.26:.2f} 0 1 0 0 {s*0.52:.2f} "
        f"a {s*0.20:.2f} {s*0.20:.2f} 0 1 1 0 {-s*0.52:.2f} Z",
        sw)


def mosque_lantern(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    top = _path(
        f"M{x + s*0.42:.2f} {y + s*0.22:.2f} L{x + s*0.58:.2f} {y + s*0.22:.2f} "
        f"L{x + s*0.58:.2f} {y + s*0.30:.2f} L{x + s*0.42:.2f} {y + s*0.30:.2f} Z",
        sw)
    body = _path(
        f"M{x + s*0.32:.2f} {y + s*0.34:.2f} L{x + s*0.68:.2f} {y + s*0.34:.2f} "
        f"L{x + s*0.64:.2f} {y + s*0.74:.2f} L{x + s*0.36:.2f} {y + s*0.74:.2f} Z",
        sw)
    bars = _path(
        f"M{x + s*0.44:.2f} {y + s*0.34:.2f} L{x + s*0.42:.2f} {y + s*0.74:.2f} "
        f"M{x + s*0.56:.2f} {y + s*0.34:.2f} L{x + s*0.58:.2f} {y + s*0.74:.2f}",
        sw * 0.7)
    base = _path(
        f"M{x + s*0.30:.2f} {y + s*0.78:.2f} L{x + s*0.70:.2f} {y + s*0.78:.2f}",
        sw)
    return top + body + bars + base


def mosque_star(x: float, y: float, sz: float, sw: float) -> str:
    return hotel_star(x, y, sz, sw)


def mosque_beads(x: float, y: float, sz: float, sw: float) -> str:
    """سُبحة: عَلامة + خَيط دائريّ من نِقاط صَغيرة."""
    s = sz
    cx = x + s*0.50
    cy = y + s*0.50
    r = s*0.24
    import math as _m
    out = ""
    for i in range(12):
        a = -_m.pi/2 + i * (2 * _m.pi / 12)
        bx = cx + r * _m.cos(a)
        by = cy + r * _m.sin(a)
        out += _circle(bx, by, s*0.03, sw * 0.7, fill="currentColor")
    return out


# ════════════════════════════════════════════════════════════════════
# CHARITY — قَلب، كَفّان، حَمامة، شَريط
# ════════════════════════════════════════════════════════════════════

def charity_heart(x: float, y: float, sz: float, sw: float) -> str:
    return clinic_heart(x, y, sz, sw)


def charity_hands(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    palm_l = _path(
        f"M{x + s*0.16:.2f} {y + s*0.60:.2f} "
        f"q {s*0.04:.2f} {-s*0.12:.2f} {s*0.18:.2f} {-s*0.10:.2f} "
        f"L{x + s*0.40:.2f} {y + s*0.60:.2f}",
        sw)
    palm_r = _path(
        f"M{x + s*0.84:.2f} {y + s*0.60:.2f} "
        f"q {-s*0.04:.2f} {-s*0.12:.2f} {-s*0.18:.2f} {-s*0.10:.2f} "
        f"L{x + s*0.60:.2f} {y + s*0.60:.2f}",
        sw)
    arms = _path(
        f"M{x + s*0.20:.2f} {y + s*0.60:.2f} L{x + s*0.20:.2f} {y + s*0.82:.2f} "
        f"M{x + s*0.80:.2f} {y + s*0.60:.2f} L{x + s*0.80:.2f} {y + s*0.82:.2f}",
        sw)
    return palm_l + palm_r + arms


def charity_dove(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.30:.2f} {y + s*0.60:.2f} "
        f"q {s*0.06:.2f} {-s*0.16:.2f} {s*0.26:.2f} {-s*0.16:.2f} "
        f"q {s*0.20:.2f} 0 {s*0.20:.2f} {s*0.16:.2f} "
        f"L{x + s*0.30:.2f} {y + s*0.60:.2f}",
        sw)
    wing = _path(
        f"M{x + s*0.50:.2f} {y + s*0.44:.2f} q {s*0.08:.2f} {-s*0.20:.2f} {s*0.20:.2f} 0",
        sw)
    return body + wing


def charity_ribbon(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    return _path(
        f"M{x + s*0.50:.2f} {y + s*0.22:.2f} "
        f"q {-s*0.18:.2f} {s*0.30:.2f} 0 {s*0.40:.2f} "
        f"q {s*0.18:.2f} {-s*0.10:.2f} 0 {-s*0.40:.2f} "
        f"M{x + s*0.50:.2f} {y + s*0.62:.2f} L{x + s*0.42:.2f} {y + s*0.78:.2f} "
        f"M{x + s*0.50:.2f} {y + s*0.62:.2f} L{x + s*0.58:.2f} {y + s*0.78:.2f}",
        sw)


# ════════════════════════════════════════════════════════════════════
# GAMING — يَدّ تَحكّم، joystick، سَمّاعة، نَرد
# ════════════════════════════════════════════════════════════════════

def gaming_gamepad(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.20:.2f} {y + s*0.40:.2f} L{x + s*0.80:.2f} {y + s*0.40:.2f} "
        f"L{x + s*0.84:.2f} {y + s*0.60:.2f} L{x + s*0.16:.2f} {y + s*0.60:.2f} Z",
        sw)
    dpad = _path(
        f"M{x + s*0.30:.2f} {y + s*0.50:.2f} L{x + s*0.40:.2f} {y + s*0.50:.2f} "
        f"M{x + s*0.35:.2f} {y + s*0.46:.2f} L{x + s*0.35:.2f} {y + s*0.54:.2f}",
        sw * 0.9)
    btns = (
        _circle(x + s*0.60, y + s*0.48, s*0.03, sw * 0.7, fill="currentColor")
        + _circle(x + s*0.68, y + s*0.52, s*0.03, sw * 0.7, fill="currentColor")
    )
    return body + dpad + btns


def gaming_joystick(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    base = _ellipse(x + s*0.50, y + s*0.72, s*0.22, s*0.07, sw)
    pole = _path(
        f"M{x + s*0.50:.2f} {y + s*0.72:.2f} L{x + s*0.50:.2f} {y + s*0.38:.2f}",
        sw)
    knob = _circle(x + s*0.50, y + s*0.34, s*0.07, sw)
    return base + pole + knob


def gaming_headset(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    band = _path(
        f"M{x + s*0.22:.2f} {y + s*0.54:.2f} "
        f"q 0 {-s*0.36:.2f} {s*0.28:.2f} {-s*0.36:.2f} "
        f"q {s*0.28:.2f} 0 {s*0.28:.2f} {s*0.36:.2f}",
        sw)
    pad_l = _path(
        f"M{x + s*0.16:.2f} {y + s*0.54:.2f} l 0 {s*0.20:.2f} l {s*0.10:.2f} 0 l 0 {-s*0.20:.2f} Z",
        sw)
    pad_r = _path(
        f"M{x + s*0.74:.2f} {y + s*0.54:.2f} l 0 {s*0.20:.2f} l {s*0.10:.2f} 0 l 0 {-s*0.20:.2f} Z",
        sw)
    mic = _path(
        f"M{x + s*0.74:.2f} {y + s*0.74:.2f} q {s*0.06:.2f} {s*0.04:.2f} {s*0.06:.2f} {s*0.10:.2f}",
        sw * 0.8)
    return band + pad_l + pad_r + mic


def gaming_dice(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    body = _path(
        f"M{x + s*0.26:.2f} {y + s*0.34:.2f} L{x + s*0.74:.2f} {y + s*0.34:.2f} "
        f"L{x + s*0.74:.2f} {y + s*0.82:.2f} L{x + s*0.26:.2f} {y + s*0.82:.2f} Z",
        sw)
    pips = (
        _circle(x + s*0.36, y + s*0.46, s*0.03, sw * 0.7, fill="currentColor")
        + _circle(x + s*0.50, y + s*0.58, s*0.03, sw * 0.7, fill="currentColor")
        + _circle(x + s*0.64, y + s*0.70, s*0.03, sw * 0.7, fill="currentColor")
    )
    return body + pips


# ════════════════════════════════════════════════════════════════════
# GENERIC — wifi، signal، نِقاط QR
# ════════════════════════════════════════════════════════════════════

def generic_wifi(x: float, y: float, sz: float, sw: float) -> str:
    return isp_wifi(x, y, sz, sw)


def generic_signal(x: float, y: float, sz: float, sw: float) -> str:
    return isp_signal_bars(x, y, sz, sw)


def generic_qr_dots(x: float, y: float, sz: float, sw: float) -> str:
    s = sz
    out = ""
    for dx, dy in ((0.30, 0.30), (0.50, 0.30), (0.70, 0.30),
                    (0.30, 0.50), (0.70, 0.50),
                    (0.30, 0.70), (0.50, 0.70), (0.70, 0.70)):
        out += _path(
            f"M{x + s*dx - s*0.05:.2f} {y + s*dy:.2f} "
            f"l {s*0.10:.2f} 0 l 0 {s*0.10:.2f} l {-s*0.10:.2f} 0 Z",
            sw * 0.8)
    return out


# ════════════════════════════════════════════════════════════════════
# الفِهرس: vertical → list of motif draw fns
# ════════════════════════════════════════════════════════════════════

VERTICAL_SETS: dict[str, list[_StrokeFn]] = {
    "cafe":       [cafe_to_go_cup, cafe_cup_saucer, cafe_beans, cafe_spoon,
                    cafe_sugar, cafe_leaf, cafe_kettle],
    "restaurant": [resto_fork, resto_knife, resto_spoon, resto_plate,
                    resto_chef_hat, resto_slice],
    "clinic":     [clinic_cross, clinic_stethoscope, clinic_pill,
                    clinic_heart, clinic_syringe, clinic_thermometer],
    "shop":       [shop_bag, shop_cart, shop_tag, shop_box, shop_gift],
    "isp":        [isp_wifi, isp_signal_bars, isp_globe, isp_router,
                    isp_antenna],
    "hotel":      [hotel_bed, hotel_key, hotel_star, hotel_bell,
                    hotel_suitcase],
    "salon":      [salon_scissors, salon_comb, salon_mirror, salon_dryer,
                    salon_polish],
    "gym":        [gym_dumbbell, gym_kettlebell, gym_bottle, gym_shoe,
                    gym_weight],
    "school":     [school_cap, school_book, school_pencil, school_ruler,
                    school_apple],
    "events":     [event_balloons, event_confetti, event_gift, event_cake,
                    event_note],
    "mosque":     [mosque_dome, mosque_crescent, mosque_lantern,
                    mosque_star, mosque_beads],
    "charity":    [charity_heart, charity_hands, charity_dove,
                    charity_ribbon],
    "gaming":     [gaming_gamepad, gaming_joystick, gaming_headset,
                    gaming_dice],
    "generic":    [generic_wifi, generic_signal, generic_qr_dots],
}


# ════════════════════════════════════════════════════════════════════
# Tile composer + <pattern> wrapper
# ════════════════════════════════════════════════════════════════════
#
# نَستعمل brick layout: 6 مَواضع داخل tile ‎220×220‎، الـmotifs بحَجم
# ‎48px‎، يَتَكَرّر الـpattern تلقائيًّا عبر patternUnits="userSpaceOnUse"
# على ‎<rect>‎ كامل الكَنفاس. الـtile نَفسه يَنتمي لطَقم القطاع — لو فيه
# 7 motifs، نُدير 6 منها في tile واحد (يُغطّي التَنَوُّع البَصري).


# تَنسيق الـ6 مَواضع داخل tile (نِسبة لـtile size). تَصميم brick
# offset كي تَتوزّع الـmotifs بِشَكل مُتَنَوّع عند التَكرار:
_TILE_POSITIONS: tuple[tuple[float, float], ...] = (
    (0.06, 0.04), (0.46, 0.10), (0.78, 0.02),
    (0.22, 0.36), (0.58, 0.42), (0.08, 0.66),
    (0.50, 0.70), (0.80, 0.62),
)


def build_tile_paths(vertical: str, *,
                      tile_size: float = 220.0,
                      motif_size: float = 48.0,
                      stroke_width: float = 1.4) -> str:
    """يَبني الـpaths الداخليّة لـsymbol/pattern قِطاعيّ.

    الـpaths تَستعمل ``currentColor`` كي تَتَلوّن من خارج (CSS أو fill
    على الـ<g> الحاوي).

    يُعيد string من عَناصر SVG (بلا ‎<pattern>‎ wrapper) قابل للوَضع داخل
    أيّ container.
    """
    motifs = VERTICAL_SETS.get(vertical) or VERTICAL_SETS["generic"]
    n = len(motifs)
    out = ""
    for i, (rx, ry) in enumerate(_TILE_POSITIONS):
        motif_fn = motifs[i % n]
        cx_ = rx * tile_size
        cy_ = ry * tile_size
        out += motif_fn(cx_, cy_, motif_size, stroke_width)
    return out


def build_pattern_svg(vertical: str, pattern_id: str = "hr-pat", *,
                       tile_size: float = 220.0,
                       motif_size: float = 48.0,
                       stroke_width: float = 1.4) -> str:
    """يَبني ‎<pattern>‎ كامل قابل للوَضع داخل ‎<defs>‎ لأي SVG. يَستعمل
    ‎patternUnits="userSpaceOnUse"‎ كي يَتَكَرّر بحَجم ثابت بغَضّ النَظر
    عن أبعاد الـfill المُتلقّي."""
    paths = build_tile_paths(vertical, tile_size=tile_size,
                              motif_size=motif_size,
                              stroke_width=stroke_width)
    return (
        f'<pattern id="{_xml(pattern_id)}" patternUnits="userSpaceOnUse" '
        f'width="{tile_size:.1f}" height="{tile_size:.1f}">{paths}</pattern>'
    )


def list_verticals() -> list[str]:
    return list(VERTICAL_SETS.keys())


def motif_count(vertical: str) -> int:
    return len(VERTICAL_SETS.get(vertical) or VERTICAL_SETS["generic"])


__all__ = [
    "VERTICAL_SETS",
    "build_tile_paths",
    "build_pattern_svg",
    "list_verticals",
    "motif_count",
]
