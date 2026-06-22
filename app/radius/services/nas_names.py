"""nas_names — حلّ اسم الراوتر/البرج الودود من nas_devices لعرضه بجوار الـIP.

سطح موحّد لكل صفحة تعرض NAS بالـIP فقط («إحصائيات المتصلين»، «المتصلون الآن»،
التجميعات حسب NAS). المطابقة على nas_devices.address أو vpn_peer_address
(عنوان النفق) = قيمة nasipaddress في الجلسة. حين لا جهاز مطابق → الـIP كما هو.

الهدف: «اسم البرج (10.10.0.2)» — الاسم أساسي والـIP بين قوسين ثانوي، مع
الارتداد للـIP وحده عند غياب الاسم. التسمية تجميليّة بحتة فلا تكسر الصفحة لو
فشل الاستعلام (تُعيد خريطة فارغة).
"""
from __future__ import annotations

from ..db.connection import db


def nas_name_map(tenant_id: int) -> dict[str, str]:
    """عنوان NAS (IP مباشر أو عنوان نفق) → اسم الراوتر الودّي.

    يطابق كلا العمودين address و vpn_peer_address على نفس الاسم، فتُحلّ
    جلسة قادمة عبر النفق أو مباشرةً. أوّل اسم غير فارغ يفوز لكل عنوان."""
    out: dict[str, str] = {}
    try:
        for r in db().execute(
            "SELECT name, address, vpn_peer_address FROM nas_devices "
            "WHERE tenant_id=? AND (deleted_at IS NULL OR deleted_at='')",
            (int(tenant_id),),
        ).fetchall():
            name = str(r["name"] or "").strip()
            if not name:
                continue
            for ip in (str(r["address"] or "").strip(),
                       str(r["vpn_peer_address"] or "").strip()):
                if ip:
                    out.setdefault(ip, name)
    except Exception:  # noqa: BLE001 — التسمية تجميليّة، لا تكسر الصفحة
        pass
    return out


def nas_label(value, name_map: dict[str, str], *, fallback: str = "غير معروف") -> str:
    """«الاسم (القيمة)» إن طابقت القيمةُ جهازًا، وإلا القيمة الخام، وإلا fallback.

    value قد تكون IP (جلسات radacct) أو اسمًا/معرّفًا نصّيًا (radpostauth.nas).
    إن كانت أصلًا اسمًا غير موجود في الخريطة تُعرض كما هي بلا تكرار."""
    v = str(value or "").strip()
    if not v:
        return fallback
    name = name_map.get(v)
    if name and name != v:
        return f"{name} ({v})"
    return v


__all__ = ["nas_name_map", "nas_label"]
