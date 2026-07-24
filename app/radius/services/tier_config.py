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
_PLATFORM_TID = 1

_FIELDS = ("max_subscribers", "max_nas", "api_rpm")
_MAX = {"max_subscribers": 10_000_000, "max_nas": 10_000, "api_rpm": 100_000}

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


def _seed() -> list[dict[str, Any]]:
    return [dict(x) for x in _SEED]


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


def _clean_tier(raw: dict, existing: set[str], *, key: str | None = None) -> dict:
    label = str(raw.get("label") or "").strip()[:60] or "فئة"
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
    return out


def get_tiers() -> list[dict[str, Any]]:
    """قائمة الفئات الحاليّة (مخزَّنة، أو المبذورة إن غابت). لا ترفع."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(_PLATFORM_TID, _SETTING_KEY, "")
        if not raw:
            return _seed()
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed:
            return _seed()
        out, seen = [], set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            t = _clean_tier(row, seen)
            seen.add(t["key"])
            out.append(t)
        return out or _seed()
    except Exception:  # noqa: BLE001
        return _seed()


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
             by: int = 0) -> dict:
    tiers = get_tiers()
    existing = {t["key"] for t in tiers}
    t = _clean_tier({"label": label, "icon": icon,
                     "max_subscribers": max_subscribers, "max_nas": max_nas,
                     "api_rpm": api_rpm}, existing)
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
