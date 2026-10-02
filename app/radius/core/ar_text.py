"""صياغة عربيّة للأعداد — مصدر واحد للجمع (F08-L).

كانت الواجهة تكتب «3 إعدادًا» و«3 بطاقة» و«3 كرت»: اسم المعدود بصيغة واحدة
أيًّا كان العدد. القاعدة العربيّة (نفس فئات CLDR للعربيّة):

* 1، و0، و100–102، 200… (الباقي على 100 = 0/1/2 لما فوق المئة) → **المفرد**
  («1 بطاقة»، «100 بطاقة»).
* 2 → المثنّى إن أُعطي، وإلّا الجمع («2 بطاقات» مقبولة مع الأرقام).
* الباقي على 100 من 3 إلى 10 → **الجمع** («3 بطاقات»، «105 بطاقات»).
* الباقي على 100 من 11 إلى 99 → **المفرد المنصوب** («11 بطاقةً»، «25 إعدادًا»).

``ar_count(n, "بطاقة", "بطاقات")`` ⇒ «3 بطاقات». الصيغ الجاهزة في ``NOUNS``
تُستعمل بالمفتاح: ``ar_count(n, "card")``. متاحة في القوالب فلترًا:
``{{ n|ar_count('card') }}``.
"""
from __future__ import annotations

from typing import Any

# المفتاح → (مفرد، جمع، مفرد منصوب 11–99، مثنّى)
NOUNS: dict[str, tuple[str, str, str, str]] = {
    "card": ("بطاقة", "بطاقات", "بطاقةً", "بطاقتان"),
    "kart": ("كرت", "كروت", "كرتًا", "كرتان"),
    "setting": ("إعداد", "إعدادات", "إعدادًا", "إعدادان"),
    "batch": ("حزمة", "حزم", "حزمةً", "حزمتان"),
    "subscriber": ("مشترك", "مشتركين", "مشتركًا", "مشتركان"),
    "event": ("حدث", "أحداث", "حدثًا", "حدثان"),
    "day": ("يوم", "أيام", "يومًا", "يومان"),
    "item": ("عنصر", "عناصر", "عنصرًا", "عنصران"),
    "admin": ("مدير", "مدراء", "مديرًا", "مديران"),
    "device": ("جهاز", "أجهزة", "جهازًا", "جهازان"),
    "router": ("راوتر", "راوترات", "راوترًا", "راوتران"),
    "message": ("رسالة", "رسائل", "رسالةً", "رسالتان"),
    "operation": ("عملية", "عمليات", "عمليةً", "عمليتان"),
}


def ar_plural_form(n: Any, one: str, few: str, many: str | None = None,
                   two: str | None = None) -> str:
    """الصيغة المناسبة لاسم المعدود بحسب العدد (بلا العدد نفسه)."""
    try:
        k = abs(int(float(n)))
    except (TypeError, ValueError):
        return one
    r = k % 100
    if k == 2:
        return two or few
    if 3 <= r <= 10:
        return few
    if 11 <= r <= 99:
        return many or one
    return one


def ar_count(n: Any, one: str, few: str | None = None, many: str | None = None,
             two: str | None = None) -> str:
    """«<العدد> <المعدود بصيغته>». ``one`` قد يكون مفتاحًا من ``NOUNS``.

    المثنّى مع الأرقام يُكتب جمعًا («2 بطاقات») ما لم يُمرَّر ``two`` صراحةً —
    «2 بطاقتان» ركيكة مع رقم."""
    if few is None and one in NOUNS:
        one, few, many, _two = NOUNS[one]
    few = few or one
    try:
        num = int(float(n)) if float(n) == int(float(n)) else n
    except (TypeError, ValueError):
        num = n
    return f"{num} {ar_plural_form(n, one, few, many, two)}"


__all__ = ["NOUNS", "ar_count", "ar_plural_form"]
