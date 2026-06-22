"""qr_svg — رمز QR → SVG لرابط ربط تيليجرام العميق.

نحتاج رمز QR لرابط ``https://t.me/<bot>?start=<code>`` كي يمسحه المالك/المشترك
بهاتفه فيُفتح البوت ويُرسَل ``/start <code>`` بلمسة. نُخرِج SVG (صورة متجهة)
لأنّه يُعرض بدقّة على أي شاشة ويبدو سليمًا عند 390px، ويُنسَّق بألوان نظام
التصميم (خلفية فاتحة بزوايا دائرية + وحدات داكنة مدموجة في مسار واحد).

التوليد عبر مكتبة ``segno`` (بايثون خالص، MIT، بلا اعتماديات متعدّية) — نفس
المرجع الذي تُقارَن به الاختبارات، فالصحّة مضمونة. إن غابت المكتبة (بيئة لم
تُثبَّت فيها بعد) نُعيد لوحة بديلة أنيقة بدل الانهيار، فيبقى زرّ الرابط العميق
هو نقطة الربط الأساسية بأي حال.

الواجهة العامة:
    qr_svg(data, *, box=6, quiet=4, dark, light, ecc="M") -> str   # نص SVG
    qr_available() -> bool   # هل مولّد QR الحقيقي متاح؟
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)


def qr_available() -> bool:
    """True إن كانت مكتبة توليد QR متاحة (segno)."""
    try:
        import segno  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _matrix(data: str, ecc: str):
    """مصفوفة وحدات QR (صفوف من 0/1) للبيانات، أو None إن تعذّر التوليد."""
    try:
        import segno
        qr = segno.make(data, error=ecc.lower(), micro=False)
        return [[1 if c else 0 for c in row] for row in qr.matrix]
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("qr_svg: تعذّر توليد QR (%s)", exc)
        return None


def _placeholder_svg(box: int, quiet: int, dark: str, light: str) -> str:
    """لوحة بديلة أنيقة عند غياب مولّد QR — لا انهيار، والزرّ يبقى البديل."""
    dim = (25 + quiet * 2) * box
    cx = dim / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" role="img" aria-label="QR placeholder">'
        f'<rect width="{dim}" height="{dim}" rx="{max(10, box*2)}" fill="{light}" '
        f'stroke="{dark}" stroke-dasharray="6 6" stroke-width="2"/>'
        f'<text x="{cx}" y="{cx-6}" text-anchor="middle" font-size="{dim*0.12:.0f}" '
        f'fill="{dark}">✈</text>'
        f'<text x="{cx}" y="{cx+dim*0.16:.0f}" text-anchor="middle" '
        f'font-family="Cairo,sans-serif" font-size="{dim*0.045:.0f}" fill="{dark}">'
        f'استخدم الزرّ بالأسفل</text>'
        f'</svg>'
    )


def qr_svg(data: str, *, box: int = 6, quiet: int = 4,
           dark: str = "#0f172a", light: str = "#ffffff", ecc: str = "M") -> str:
    """يُعيد نص SVG لرمز QR يُرمّز ``data`` (UTF-8، مستوى التصحيح ``ecc``).

    box: حجم وحدة الـQR بالبكسل. quiet: هامش المنطقة الهادئة بالوحدات.
    الوحدات الداكنة تُدمج في مستطيلات صفّيّة متجاورة فيبقى الـSVG صغيرًا.
    """
    mods = _matrix(data, ecc)
    if not mods:
        return _placeholder_svg(box, quiet, dark, light)
    n = len(mods)
    dim = (n + quiet * 2) * box
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges" '
        f'role="img" aria-label="QR">',
        f'<rect width="{dim}" height="{dim}" rx="{max(8, box)}" fill="{light}"/>',
    ]
    for r in range(n):
        c = 0
        while c < n:
            if mods[r][c]:
                start = c
                while c < n and mods[r][c]:
                    c += 1
                x = (quiet + start) * box
                y = (quiet + r) * box
                w = (c - start) * box
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" '
                             f'height="{box}" fill="{dark}"/>')
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)
