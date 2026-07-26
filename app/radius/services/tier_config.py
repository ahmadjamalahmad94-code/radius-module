"""فئات الاشتراك الديناميكيّة — MT47/MT49.

كانت الفئات ثلاثًا ثابتةً في الكود. صارت **قائمةً كاملة قابلة للتحكّم**:
يُضيف المزوّد ويحذف ويعدّل ما شاء (اسمًا وأيقونةً وكل الحدود) بلا سقفٍ
على العدد.

التخزين: قائمة JSON على مساحة المزوّد (الشبكة ١)، تُبذَر أوّل مرّة من
الثلاث المدمجة كي لا تُفاجأ نسخةٌ قائمة بقائمةٍ فارغة. المفتاح (``key``)
مُعرّفٌ ثابت يُخزَّن على كل شبكة (``plan_tier``) ولا يتغيّر بإعادة
التسمية؛ وحذفُ فئةٍ لا يَكسر شبكاتها لأنّ حدودها نُسخت وقت الإنشاء.

مبدأ لا ترفع أبدًا: أيّ خطأ قراءة/تحليل يرجع إلى المدمجة.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.tenant import (TENANT_TIER_ENTERPRISE, TENANT_TIER_PRO,
                           TENANT_TIER_STARTER, TIER_LIMITS)

_SETTING_KEY = "platform.tiers"
_OFFERS_KEY = "platform.offers"      # القائمة القديمة المنفصلة — تُدمَج مرّةً
_PLATFORM_TID = 1

_FIELDS = ("max_subscribers", "max_nas", "api_rpm")
_MAX = {"max_subscribers": 10_000_000, "max_nas": 10_000, "api_rpm": 100_000}

# MT62 — حقول التسعير: الباقة صارت **شيئًا واحدًا** — ما يراه العميل على
# صفحة المنصّة هو نفسه ما يُفرَض على شبكته. كان النظامان منفصلين (فئات
# للحدود + عروض للتسويق) فيشتري العميل «٥٠ اتصالًا» وتُنشأ شبكته بحدّ
# «٢٠٠ مشترك» — رقمان لا علاقة بينهما.
_PRICE_FIELDS = ("concurrent", "price_monthly", "currency", "is_free",
                 "trial_days", "highlight", "visible", "note", "discounts")

_MAX_CONCURRENT = 1_000_000
_MAX_PRICE = 1_000_000.0
_MAX_MONTHS = 120

# البذرة: الثلاث المدمجة (تُكتب أوّل مرّة، ثمّ يتحكّم المزوّد بها بحرّية).
_SEED = [
    {"key": TENANT_TIER_STARTER, "label": "أساسية", "icon": "seedling",
     **TIER_LIMITS[TENANT_TIER_STARTER]},
    {"key": TENANT_TIER_PRO, "label": "احترافية", "icon": "rocket",
     **TIER_LIMITS[TENANT_TIER_PRO]},
    {"key": TENANT_TIER_ENTERPRISE, "label": "مؤسسية", "icon": "city",
     **TIER_LIMITS[TENANT_TIER_ENTERPRISE]},
]

_ICON_ALLOWED = re.compile(r"^[a-z0-9-]{1,40}$")


# تسعير البذرة (ما يُعرَض للزوّار قبل أن يَحفظ المزوّد شيئًا). ينسكب على
# الباقة المطابقة بالمفتاح، وما لا مقابل له يصير باقةً ترث حدود الأولى.
_SEED_PRICING = [
    {"key": "free", "label": "العرض المجاني", "icon": "bolt", "concurrent": 100,
     "price_monthly": 0.0, "is_free": True, "trial_days": 7, "highlight": True,
     "visible": True, "note": "تجربة كاملة لمدة 7 أيام", "discounts": []},
    {"key": "cafes", "label": "حزمة الكافيهات", "icon": "mug-saucer", "concurrent": 50,
     "price_monthly": 10.0, "visible": True,
     "discounts": [{"months": 3, "percent": 10}, {"months": 6, "percent": 15},
                   {"months": 12, "percent": 20}]},
    {"key": TENANT_TIER_STARTER, "label": "حزمة البداية", "icon": "seedling",
     "concurrent": 100, "price_monthly": 17.0, "visible": True,
     "discounts": [{"months": 3, "percent": 10}, {"months": 6, "percent": 15},
                   {"months": 12, "percent": 20}]},
]


def _seed() -> list[dict[str, Any]]:
    """البذرة **منقّاةً دائمًا** عبر ``_clean_tier``.

    مزلقٌ وقعنا فيه: كانت تُرجع صفوفًا خامًّا بلا حقول تسعير، وهي مسار
    السقوط الوحيد حين لا إعدادات محفوظة (وهو حال أيّ نسخةٍ لم يَحفظ فيها
    المزوّد شيئًا بعد) — فاختفى قسم الأسعار من صفحة المنصّة تمامًا. الآن
    كل صفٍّ يمرّ بالمنقّي فيحمل الحقول كاملةً بقيمٍ سليمة."""
    rows = [dict(x) for x in _SEED]
    by_key = {r["key"]: r for r in rows}
    for off in _SEED_PRICING:
        k = off["key"]
        if k in by_key:
            by_key[k].update({f: v for f, v in off.items() if f != "key"})
        else:
            row = {f: rows[0][f] for f in _FIELDS}
            row.update(off)
            rows.append(row)
    out, seen = [], set()
    for r in rows:
        t = _clean_tier(r, seen)
        seen.add(t["key"])
        out.append(t)
    return out


def _slugify_key(label: str, existing: set[str]) -> str:
    """يُولّد مفتاحًا ثابتًا من الاسم (لاتينيّ)، فريدًا. عربيٌّ خالص →
    ``tier`` + رقم، فالمفتاح تقنيّ لا يُعرَض للمستخدم."""
    base = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    base = base or "tier"
    key = base
    i = 2
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


def _clean_tier(raw: dict, existing: set[str], *, key: str | None = None) -> dict:
    label = str(raw.get("label") or "").strip()[:80] or "باقة"
    icon = str(raw.get("icon") or "layer-group").strip().lower()
    if not _ICON_ALLOWED.match(icon):
        icon = "layer-group"
    out = {"key": key or str(raw.get("key") or "").strip()[:40], "label": label, "icon": icon}
    if not out["key"] or out["key"] in existing:
        out["key"] = _slugify_key(label, existing)
    for f in _FIELDS:
        try:
            v = int(float(raw.get(f, 0)))
        except (TypeError, ValueError):
            v = 0
        out[f] = max(1, min(v or 1, _MAX[f]))
    # ── التسعير (MT62) — باقةٌ بلا سعر = حدودٌ داخليّة فقط (visible=0) ──
    is_free = bool(raw.get("is_free"))
    out["concurrent"] = int(max(1, min(_num(raw.get("concurrent"), 0) or out["max_subscribers"],
                                       _MAX_CONCURRENT)))
    out["price_monthly"] = (0.0 if is_free else
                            round(max(0.0, min(_num(raw.get("price_monthly"), 0.0),
                                               _MAX_PRICE)), 2))
    out["currency"] = str(raw.get("currency") or "USD").strip()[:8] or "USD"
    out["is_free"] = is_free
    out["trial_days"] = int(max(0, min(_num(raw.get("trial_days"), 0), 3650)))
    out["highlight"] = bool(raw.get("highlight"))
    out["visible"] = bool(raw.get("visible"))
    out["note"] = str(raw.get("note") or "").strip()[:160]
    out["discounts"] = [] if is_free else _clean_discounts(raw.get("discounts"))
    return out


def _merge_legacy_offers(plans: list[dict], raw_rows: list) -> list[dict]:
    """MT62 — دمجٌ لمرّة واحدة للقائمة المنفصلة القديمة (``platform.offers``).

    نُبقي مفاتيح الباقات كما هي (الشبكات القائمة تُشير إليها بـ``plan_tier``)
    ونَسكب تسعير العرض المطابق فيها؛ وما لا مقابل له يُضاف باقةً جديدة
    ترث حدود أوّل باقة. فلا يضيع تسعيرٌ ولا تنكسر شبكة."""
    has_price = any(("concurrent" in r) for r in (raw_rows or []) if isinstance(r, dict))
    if has_price:
        return plans          # مدموجة سلفًا — لا تُكرّر
    try:
        from ..db.repos import tenants_repo
        legacy = json.loads(tenants_repo.get_setting(_PLATFORM_TID, _OFFERS_KEY, "") or "[]")
    except Exception:  # noqa: BLE001
        return plans
    if not isinstance(legacy, list) or not legacy:
        return plans
    by_key = {p["key"]: p for p in plans}
    base_limits = {f: plans[0][f] for f in _FIELDS} if plans else {}
    seen = set(by_key)
    for off in legacy:
        if not isinstance(off, dict):
            continue
        k = str(off.get("key") or "").strip()[:40]
        if k in by_key:                      # نفس المفتاح ⇒ اسكب التسعير فقط
            tgt = by_key[k]
            for f in _PRICE_FIELDS:
                if f in off:
                    tgt[f] = off[f]
            tgt["label"] = off.get("label") or tgt["label"]
            tgt["icon"] = off.get("icon") or tgt["icon"]
        else:                                # عرضٌ بلا فئة ⇒ باقةٌ جديدة
            row = dict(base_limits)
            row.update(off)
            p = _clean_tier(row, seen)
            seen.add(p["key"])
            plans.append(p)
    return plans


def get_tiers() -> list[dict[str, Any]]:
    """قائمة الباقات الحاليّة (مخزَّنة، أو المبذورة إن غابت). لا ترفع."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(_PLATFORM_TID, _SETTING_KEY, "")
        if not raw:
            return _merge_legacy_offers(_seed(), [])
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed:
            return _merge_legacy_offers(_seed(), [])
        out, seen = [], set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            t = _clean_tier(row, seen)
            seen.add(t["key"])
            out.append(t)
        return _merge_legacy_offers(out, parsed) if out else _merge_legacy_offers(_seed(), [])
    except Exception:  # noqa: BLE001
        return _seed()


def visible_plans() -> list[dict[str, Any]]:
    """الباقات المعروضة على صفحة المنصّة (بصفوف مددها وسعر وحدتها جاهزة)."""
    out = []
    for p in get_tiers():
        if not p.get("visible"):
            continue
        p = dict(p)
        p["rows"] = plan_rows(p)
        p["unit"] = unit_price(p)
        out.append(p)
    return out


def period_total(plan: dict, months: int, percent: float) -> float:
    """إجماليّ مدّةٍ بعد الخصم — **محسوبٌ** من الشهريّ لا مخزَّن، فتغييرُ
    السعر يُحدّث كل المدد ولا تتناقض الأرقام. يُقرَّب لعددٍ صحيح (صفحات
    الأسعار بأرقامٍ نظيفة: ١٧×١٢−٢٠% = ١٦٣٫٢ تُعرَض ١٦٣)."""
    base = _num(plan.get("price_monthly")) * max(1, int(months))
    return float(round(base * (1.0 - max(0.0, min(_num(percent), 90.0)) / 100.0)))


def plan_rows(plan: dict) -> list[dict[str, Any]]:
    return [{"months": d["months"], "percent": d["percent"],
             "total": period_total(plan, d["months"], d["percent"])}
            for d in (plan.get("discounts") or [])]


def unit_price(plan: dict) -> float:
    c = int(plan.get("concurrent") or 0)
    return round(_num(plan.get("price_monthly")) / c, 3) if c > 0 else 0.0


def save_tiers(rows: list[dict], *, by: int = 0) -> list[dict]:
    """يَحفظ القائمة كاملةً (استبدال). يُنقّي كل فئة ويَضمن مفاتيح فريدة.
    قائمةٌ فارغة مرفوضة — نُبقي المبذورة كي لا يُقفَل الإنشاء."""
    clean, seen = [], set()
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        t = _clean_tier(row, seen)
        seen.add(t["key"])
        clean.append(t)
    if not clean:
        clean = _seed()
    from ..db.repos import tenants_repo
    tenants_repo.set_setting(_PLATFORM_TID, _SETTING_KEY,
                             json.dumps(clean, ensure_ascii=False), by=by)
    return clean


def add_tier(*, label: str, icon: str = "layer-group",
             max_subscribers: int = 100, max_nas: int = 1, api_rpm: int = 10,
             concurrent: int = 0, price_monthly: float = 0.0,
             currency: str = "USD", is_free: bool = False, trial_days: int = 0,
             visible: bool = False, note: str = "",
             discounts: list | None = None, by: int = 0) -> dict:
    tiers = get_tiers()
    existing = {t["key"] for t in tiers}
    t = _clean_tier({"label": label, "icon": icon,
                     "max_subscribers": max_subscribers, "max_nas": max_nas,
                     "api_rpm": api_rpm, "concurrent": concurrent,
                     "price_monthly": price_monthly, "currency": currency,
                     "is_free": is_free, "trial_days": trial_days,
                     "visible": visible, "note": note,
                     "discounts": discounts if discounts is not None else [
                         {"months": 3, "percent": 10}, {"months": 6, "percent": 15},
                         {"months": 12, "percent": 20}]}, existing)
    tiers.append(t)
    save_tiers(tiers, by=by)
    return t


def delete_tier(key: str, *, by: int = 0) -> bool:
    """يَحذف فئةً. الشبكات التي تستخدمها لا تُكسَر (حدودها منسوخة سلفًا)،
    لكن لا نَسمح بحذف الأخيرة — لا بدّ من فئةٍ واحدة على الأقلّ للإنشاء."""
    tiers = get_tiers()
    if len(tiers) <= 1:
        return False
    remaining = [t for t in tiers if t["key"] != key]
    if len(remaining) == len(tiers):
        return False
    save_tiers(remaining, by=by)
    return True


def tiers_in_use() -> dict[str, int]:
    """كم شبكة على كل مفتاح فئة (لتحذير الحذف/التعديل)."""
    out: dict[str, int] = {}
    try:
        from ..db.connection import db
        for r in db().execute(
                "SELECT plan_tier, COUNT(*) AS n FROM tenants WHERE id!=1 GROUP BY plan_tier"):
            out[str(r["plan_tier"])] = int(r["n"])
    except Exception:  # noqa: BLE001
        pass
    return out


def limits_for(key: str) -> dict[str, int]:
    """حدود فئةٍ بمفتاحها. إن كان المفتاح غريبًا (فئة محذوفة) رجعنا لأوّل
    فئة موجودة — فلا يَنكسر إنشاءٌ بمفتاحٍ قديم."""
    tiers = get_tiers()
    for t in tiers:
        if t["key"] == key:
            return {f: t[f] for f in _FIELDS}
    first = tiers[0]
    return {f: first[f] for f in _FIELDS}


# ── توافق خلفيّ: أسطحٌ قديمة تُنادي هذه ──────────────────────────────
def get_tier_limits() -> dict[str, dict[str, int]]:
    return {t["key"]: {f: t[f] for f in _FIELDS} for t in get_tiers()}


def tier_meta() -> dict[str, dict[str, str]]:
    """{key: {label, icon}} — يستهلكها نموذج الإنشاء."""
    return {t["key"]: {"label": t["label"], "icon": t["icon"]} for t in get_tiers()}
