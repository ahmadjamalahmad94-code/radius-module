"""عروض الأسعار المعروضة على صفحة المنصّة — MT57.

قسمٌ بيعيٌّ **يتحكّم به المزوّد كاملًا** من لوحته: يُضيف عرضًا ويحذف
ويُعدّل (اسمًا وسعرًا واتصالاتٍ متزامنة وخصومات المدد وترتيبًا وظهورًا)
بلا سقفٍ على العدد وبلا لمس كود.

التخزين: قائمة JSON على مساحة المزوّد (الشبكة ١) — نفس نمط
[[tier_config]]. تُبذَر أوّل مرّة بعروض المالك المعلنة كي لا تظهر صفحة
الهبوط فارغة على نسخةٍ قائمة.

**الأسعار محسوبة لا مخزَّنة**: إجماليّ كل مدّة = الشهريّ × الأشهر ناقص
نسبة الخصم، فتغييرُ السعر الشهريّ يُحدّث كل المدد تلقائيًّا ولا تتناقض
الأرقام المعروضة للعميل.

مبدأ لا ترفع أبدًا: أيّ خطأ قراءة/تحليل يرجع إلى المبذورة.
"""
from __future__ import annotations

import json
import re
from typing import Any

_SETTING_KEY = "platform.offers"
_PLATFORM_TID = 1

_ICON_ALLOWED = re.compile(r"^[a-z0-9-]{1,40}$")
_MAX_CONCURRENT = 1_000_000
_MAX_PRICE = 1_000_000.0
_MAX_MONTHS = 120

# البذرة: عروض المالك المعلنة (تجربة ٧ أيّام + الكافيهات + البداية).
_SEED = [
    {"key": "free", "label": "العرض المجاني", "icon": "bolt",
     "concurrent": 100, "price_monthly": 0.0, "currency": "USD",
     "is_free": True, "trial_days": 7, "highlight": True, "visible": True,
     "note": "تجربة كاملة لمدة 7 أيام", "discounts": []},
    {"key": "cafes", "label": "حزمة الكافيهات", "icon": "mug-saucer",
     "concurrent": 50, "price_monthly": 10.0, "currency": "USD",
     "is_free": False, "trial_days": 0, "highlight": False, "visible": True,
     "note": "", "discounts": [{"months": 3, "percent": 10},
                               {"months": 6, "percent": 15},
                               {"months": 12, "percent": 20}]},
    {"key": "starter", "label": "حزمة البداية", "icon": "seedling",
     "concurrent": 100, "price_monthly": 17.0, "currency": "USD",
     "is_free": False, "trial_days": 0, "highlight": False, "visible": True,
     "note": "", "discounts": [{"months": 3, "percent": 10},
                               {"months": 6, "percent": 15},
                               {"months": 12, "percent": 20}]},
]


def _seed() -> list[dict[str, Any]]:
    return [json.loads(json.dumps(x)) for x in _SEED]


def _slugify_key(label: str, existing: set[str]) -> str:
    """مفتاحٌ لاتينيّ ثابت من الاسم، فريد. عربيٌّ خالص → ``offer`` + رقم."""
    base = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    base = base or "offer"
    key, i = base, 2
    while key in existing:
        key = f"{base}-{i}"
        i += 1
    return key[:40]


def _num(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _clean_discounts(raw: Any) -> list[dict[str, int]]:
    """مدد الخصم: أشهر فريدة تصاعديًّا، نسبة 0..90. المكرَّر يُطرح."""
    out, seen = [], set()
    for d in (raw or []):
        if not isinstance(d, dict):
            continue
        months = int(max(1, min(_num(d.get("months"), 1), _MAX_MONTHS)))
        pct = int(max(0, min(_num(d.get("percent"), 0), 90)))
        if months in seen:
            continue
        seen.add(months)
        out.append({"months": months, "percent": pct})
    return sorted(out, key=lambda x: x["months"])


def _clean_offer(raw: dict, existing: set[str], *, key: str | None = None) -> dict:
    label = str(raw.get("label") or "").strip()[:80] or "عرض"
    icon = str(raw.get("icon") or "tag").strip().lower()
    if not _ICON_ALLOWED.match(icon):
        icon = "tag"
    is_free = bool(raw.get("is_free"))
    out: dict[str, Any] = {
        "key": key or str(raw.get("key") or "").strip()[:40],
        "label": label,
        "icon": icon,
        "concurrent": int(max(1, min(_num(raw.get("concurrent"), 1), _MAX_CONCURRENT))),
        "price_monthly": (0.0 if is_free
                          else round(max(0.0, min(_num(raw.get("price_monthly"), 0.0),
                                                  _MAX_PRICE)), 2)),
        "currency": (str(raw.get("currency") or "USD").strip()[:8] or "USD"),
        "is_free": is_free,
        "trial_days": int(max(0, min(_num(raw.get("trial_days"), 0), 3650))),
        "highlight": bool(raw.get("highlight")),
        "visible": bool(raw.get("visible", True)),
        "note": str(raw.get("note") or "").strip()[:160],
        "discounts": ([] if is_free else _clean_discounts(raw.get("discounts"))),
    }
    if not out["key"] or out["key"] in existing:
        out["key"] = _slugify_key(label, existing)
    return out


def get_offers(*, visible_only: bool = False) -> list[dict[str, Any]]:
    """العروض الحاليّة (مخزَّنة، أو المبذورة إن غابت). لا ترفع.

    ``visible_only`` لصفحة الهبوط العامّة — المخفيّ يبقى في اللوحة."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(_PLATFORM_TID, _SETTING_KEY, "")
        rows = json.loads(raw) if raw else None
        if not isinstance(rows, list) or not rows:
            out = _seed()
        else:
            out, seen = [], set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                o = _clean_offer(row, seen)
                seen.add(o["key"])
                out.append(o)
            out = out or _seed()
    except Exception:  # noqa: BLE001
        out = _seed()
    if visible_only:
        out = [o for o in out if o.get("visible")]
    return out


def save_offers(rows: list[dict], *, by: int = 0) -> list[dict]:
    """يَحفظ القائمة كاملةً (استبدال). قائمةٌ فارغة **مسموحة** هنا —
    المزوّد قد يريد إخفاء القسم كلّه (بخلاف الفئات التي يلزم منها واحدة)."""
    clean, seen = [], set()
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        o = _clean_offer(row, seen)
        seen.add(o["key"])
        clean.append(o)
    from ..db.repos import tenants_repo
    tenants_repo.set_setting(_PLATFORM_TID, _SETTING_KEY,
                             json.dumps(clean, ensure_ascii=False), by=by)
    return clean


def add_offer(*, label: str, icon: str = "tag", concurrent: int = 50,
              price_monthly: float = 0.0, currency: str = "USD",
              is_free: bool = False, trial_days: int = 0,
              highlight: bool = False, note: str = "",
              discounts: list | None = None, by: int = 0) -> dict:
    offers = get_offers()
    existing = {o["key"] for o in offers}
    o = _clean_offer({
        "label": label, "icon": icon, "concurrent": concurrent,
        "price_monthly": price_monthly, "currency": currency,
        "is_free": is_free, "trial_days": trial_days, "highlight": highlight,
        "visible": True, "note": note,
        "discounts": discounts if discounts is not None else [
            {"months": 3, "percent": 10}, {"months": 6, "percent": 15},
            {"months": 12, "percent": 20}],
    }, existing)
    offers.append(o)
    save_offers(offers, by=by)
    return o


def delete_offer(key: str, *, by: int = 0) -> bool:
    offers = get_offers()
    remaining = [o for o in offers if o["key"] != key]
    if len(remaining) == len(offers):
        return False
    save_offers(remaining, by=by)
    return True


def move_offer(key: str, direction: str, *, by: int = 0) -> bool:
    """يُحرّك عرضًا في الترتيب (up/down) — ترتيب العرض على صفحة الهبوط."""
    offers = get_offers()
    idx = next((i for i, o in enumerate(offers) if o["key"] == key), -1)
    if idx < 0:
        return False
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(offers):
        return False
    offers[idx], offers[j] = offers[j], offers[idx]
    save_offers(offers, by=by)
    return True


def period_total(offer: dict, months: int, percent: float) -> float:
    """إجماليّ مدّةٍ بعد الخصم — محسوبٌ من الشهريّ فلا يتناقض مع السعر.

    يُقرَّب لأقرب عددٍ صحيح: صفحات الأسعار تُعرَض بأرقامٍ نظيفة (١٧×١٢−٢٠%
    = ١٦٣٫٢ تُعرَض ١٦٣)، والفرق كسورٌ لا تُحصَّل. السعر الشهريّ نفسه يبقى
    بدقّته كما أدخله المزوّد."""
    base = _num(offer.get("price_monthly")) * max(1, int(months))
    return float(round(base * (1.0 - max(0.0, min(_num(percent), 90.0)) / 100.0)))


def offer_rows(offer: dict) -> list[dict[str, Any]]:
    """صفوف المدد الجاهزة للعرض: {months, percent, total}."""
    return [{"months": d["months"], "percent": d["percent"],
             "total": period_total(offer, d["months"], d["percent"])}
            for d in (offer.get("discounts") or [])]


def unit_price(offer: dict) -> float:
    """سعر الاتصال المتزامن الواحد شهريًّا (يُعرَض تحت السعر)."""
    c = int(offer.get("concurrent") or 0)
    if c <= 0:
        return 0.0
    return round(_num(offer.get("price_monthly")) / c, 3)
