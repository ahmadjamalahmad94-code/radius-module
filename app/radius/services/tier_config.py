"""فئات الاشتراك القابلة للتعديل — MT47.

كانت حدود الفئات (أساسية/احترافية/مؤسسية) مثبّتةً في ``TIER_LIMITS``
بالكود بلا صفحة إدارة. هذه الطبقة تجعلها **قابلة للتعديل** دون فقدان
الاحتياط: القيم تُخزَّن كإعداد منصّة JSON (على الشبكة ١ = مساحة المزوّد)،
وإن غاب الإعداد رجعنا إلى ``TIER_LIMITS`` المدمجة.

مبدأ مهمّ (موثَّق للمستخدم): الحدود الفعليّة تُنسَخ إلى كل شبكة وقت
إنشائها. فتعديل فئةٍ هنا **يسري على الشبكات الجديدة فقط**، ولا يُفاجئ
شبكةً قائمةً بحدٍّ جديد. التغيير على شبكةٍ بعينها يكون من ملفّها (تجاوز
مستقلّ) لا من هنا.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.tenant import (TENANT_TIER_ENTERPRISE, TENANT_TIER_PRO,
                           TENANT_TIER_STARTER, TIER_LIMITS)

_SETTING_KEY = "platform.tier_limits"
_PLATFORM_TID = 1   # مساحة المزوّد — إعداد منصّة لا شبكة

# تسميات عربيّة ثابتة للعرض (لا تُخزَّن — مفاتيح الفئات ثابتة).
TIER_LABELS = {
    TENANT_TIER_STARTER: "أساسية",
    TENANT_TIER_PRO: "احترافية",
    TENANT_TIER_ENTERPRISE: "مؤسسية",
}
TIER_ICONS = {
    TENANT_TIER_STARTER: "seedling",
    TENANT_TIER_PRO: "rocket",
    TENANT_TIER_ENTERPRISE: "city",
}
_FIELDS = ("max_subscribers", "max_nas", "api_rpm")
_MAX = {"max_subscribers": 10_000_000, "max_nas": 10_000, "api_rpm": 100_000}


def _defaults() -> dict[str, dict[str, int]]:
    return {k: dict(v) for k, v in TIER_LIMITS.items()}


def get_tier_limits() -> dict[str, dict[str, int]]:
    """حدود الفئات الحاليّة (مخزَّنة إن وُجدت، وإلّا المدمجة).

    لا ترفع أبدًا: أيّ خطأ قراءة/تحليل يرجع إلى المدمجة."""
    out = _defaults()
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(_PLATFORM_TID, _SETTING_KEY, "")
        if not raw:
            return out
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return out
        for tier in out:
            row = parsed.get(tier)
            if isinstance(row, dict):
                for f in _FIELDS:
                    v = row.get(f)
                    if isinstance(v, (int, float)) and v > 0:
                        out[tier][f] = min(int(v), _MAX[f])
    except Exception:  # noqa: BLE001
        return _defaults()
    return out


def save_tier_limits(new_limits: dict[str, dict[str, Any]], *, by: int = 0) -> dict:
    """يَحفظ حدود الفئات بعد تنقيةٍ صارمة. يُعيد المحفوظ الفعليّ."""
    clean = _defaults()
    for tier in clean:
        row = new_limits.get(tier) or {}
        for f in _FIELDS:
            try:
                v = int(float(row.get(f, clean[tier][f])))
            except (TypeError, ValueError):
                v = clean[tier][f]
            clean[tier][f] = max(1, min(v, _MAX[f]))
    from ..db.repos import tenants_repo
    tenants_repo.set_setting(_PLATFORM_TID, _SETTING_KEY,
                             json.dumps(clean, ensure_ascii=False), by=by)
    return clean


def limits_for(tier: str) -> dict[str, int]:
    """حدود فئةٍ بعينها (المخزَّنة)، مع الرجوع لـstarter إن كان المفتاح غريبًا."""
    all_ = get_tier_limits()
    return all_.get(tier, all_.get(TENANT_TIER_STARTER, {"max_subscribers": 200, "max_nas": 1, "api_rpm": 10}))
