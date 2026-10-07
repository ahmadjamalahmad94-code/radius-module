"""Human display of INFO results for clients that render `data` as raw key → value rows (the mobile app).

The model keeps the stable English keys / ``6h 16m`` / ``YYYY-MM-DDTHH:MM`` values it is prompted with; only
the copy sent to a client is relabelled. The web chat has its own label table (_ops_chat.html) and applies the
same value formatting in ops_assistant.js (fmtValue).
"""

from __future__ import annotations

import re
from typing import Any

from app.i18n_text import N_, _tr

LABELS = {
    "card": N_("رقم البطاقة"), "status": N_("الحالة"), "batch_id": N_("رقم الحزمة"),
    "batch_name": N_("الحزمة"), "plan_name": N_("الباقة"), "plan": N_("الباقة"),
    "first_login_local": N_("أوّل دخول"), "expires_local": N_("ينتهي"), "remaining": N_("المتبقّي"),
    "card_time": N_("وقت البطاقة"), "counting": N_("طريقة الاحتساب"),
    "used_time": N_("وقت الاتصال الفعليّ (كل الجلسات)"),
    "elapsed": N_("مضى من وقت البطاقة (منذ أوّل اتصال)"), "sessions": N_("عدد الجلسات"),
    "online_now": N_("أجهزة متّصلة الآن"), "devices_used": N_("أجهزة استُخدمت"),
    "last_seen_local": N_("آخر ظهور"), "price": N_("السعر"), "currency": N_("العملة"), "quota": N_("الحصّة"),
    "username": N_("اسم المستخدم"), "full_name": N_("الاسم الكامل"), "mobile": N_("الجوال"),
    "balance": N_("الرصيد"), "open_debt": N_("الدين المفتوح"), "online": N_("متّصل الآن"),
    "online_sessions": N_("جلسات حيّة"), "code": N_("كود الحزمة"), "created_local": N_("تاريخ الإنشاء"),
    "total_cards": N_("كل الكروت"), "available_count": N_("متاحة (غير مستعملة)"),
    "active_count": N_("قيد الاستعمال"), "expired_count": N_("منتهية"), "revoked_count": N_("ملغاة"),
    "archived_count": N_("مؤرشفة"), "total": N_("العدد الكلّيّ"), "name": N_("الاسم"),
    "when_local": N_("الوقت"), "action": N_("الإجراء"), "target": N_("على"), "outcome": N_("النتيجة"),
}
VALUES = {
    "active": N_("فعّال"), "unused": N_("غير مستعملة"), "expired": N_("منتهٍ"), "disabled": N_("معطّل"),
    "deleted": N_("محذوفة"), "archived": N_("مؤرشفة"), "exhausted": N_("نفدت كروتها"),
    "cancelled": N_("ملغاة"), "revoked": N_("ملغاة"), "enabled": N_("فعّال"), "suspended": N_("موقوف"),
    "from_first_connect": N_("تبدأ من أوّل اتصال"), "by_seconds": N_("بالثانية (وقت الاتصال فقط)"),
    "true": N_("نعم"), "false": N_("لا"),
}
_DUR = re.compile(r"^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?$")
_LOCAL = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")


def duration_ar(text: str) -> str:
    m = _DUR.match(str(text).strip())
    if not m or not any(m.groups()):
        return str(text)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    parts = []
    if d:
        parts.append(_tr("%(n)s يوم", n=d))
    if h:
        parts.append(_tr("%(n)s ساعة", n=h))
    if mi or not parts:
        parts.append(_tr("%(n)s دقيقة", n=mi))
    return _tr(" و").join(parts) if len(parts) > 1 else parts[0]


def value_text(key: str, v: Any) -> Any:
    if isinstance(v, bool):
        return _tr(VALUES["true" if v else "false"])
    if not isinstance(v, str):
        return v
    if key.endswith("_local"):
        m = _LOCAL.match(v)
        return f"{m.group(1)} {m.group(2)}" if m else v
    if key in ("remaining", "card_time", "used_time", "elapsed"):
        return duration_ar(v)
    if key in ("status", "counting") and v in VALUES:
        return _tr(VALUES[v])
    return v


def humanize(data: Any) -> Any:
    """{english_key: raw} → {Arabic label: readable value}; nested lists/dicts (items) are kept as-is."""
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if k in ("items", "query", "truncated", "mine") or isinstance(v, (dict, list)):
            out[k] = v
            continue
        out[_tr(LABELS[k]) if k in LABELS else k] = value_text(k, v)
    return out


def for_client(replies: list[dict]) -> list[dict]:
    return [{**r, "data": humanize(r.get("data"))} if r.get("type") == "result" else r for r in replies]


__all__ = ["LABELS", "VALUES", "humanize", "for_client", "duration_ar", "value_text"]
