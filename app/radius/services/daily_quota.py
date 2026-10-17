"""الكوتة اليوميّة — سقفٌ يتجدّد منتصفَ الليل المحلّيّ، وقرارٌ بعد النفاد.

ثلاثة قرارات ممكنة بعد بلوغ السقف (``daily_quota_action``):

``disconnect``    قطعُ الجلسة ورفضُ المصادقة حتّى منتصف الليل.
``reduce_speed``  السماحُ بسرعةٍ مخفَّضة عامّة (إعداد المستأجر).
``custom_speed``  السماحُ بسرعةٍ يكتبها المشغّل بالميجابت لهذه الباقة/المشترك.

**لماذا عدّادٌ على صفّ المشترك ولا جدولَ يوميّ؟** لأنّ التجديد يجب أن يقع
بلا مهمّةٍ مجدولة: يُختَم العدّادُ بيومه المحلّيّ، فأوّلُ حركةٍ بعد منتصف
الليل تجد ختمًا قديمًا فتصفّر. لا كرون ينسى، ولا انجرافَ إن توقّفت اللوحة،
ولا فرقَ بين خادمٍ أُعيد تشغيله وآخرَ لم يُعَد.

**اليوم محلّيّ لا UTC**: المستأجر قد يكون Asia/Gaza (+2/+3)، ومنتصفُ الليل
عنده ليس منتصفَ ليل UTC. كلُّ ختمٍ هنا من ``system_config.local_today``.

محصَّنة بالكامل: أيُّ خطأٍ في القراءة أو الكتابة لا يَقطع خدمةً — يُعامَل
كأنّ لا سقفَ يوميًّا.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

_LOG = logging.getLogger(__name__)

ACTION_DISCONNECT = "disconnect"
ACTION_REDUCE = "reduce_speed"
ACTION_CUSTOM = "custom_speed"
_ACTIONS = (ACTION_DISCONNECT, ACTION_REDUCE, ACTION_CUSTOM)

# سرعةُ التخفيف العامّة حين يُختار reduce_speed ولم يَضبط المستأجر إعدادَه.
_DEFAULT_REDUCE_KBPS = (512, 256)   # (نزول، صعود)
_SETTING_REDUCE = "quota.daily_reduce_rate_kbps"   # "down/up"

_MB = 1_048_576


# ─────────────────────────── اليوم المحلّيّ ───────────────────────────

def local_day(tenant_id: int) -> str:
    """ختمُ اليوم المحلّيّ ``YYYY-MM-DD`` للمستأجر. محصَّن: عند أيّ خطأ
    يُستعمل تاريخُ UTC كي لا تنكسر المحاسبة."""
    try:
        from ..core import system_config
        return system_config.local_today(int(tenant_id)).isoformat()
    except Exception:  # noqa: BLE001
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()


def seconds_until_midnight(tenant_id: int) -> int:
    """الثواني المتبقّية حتّى منتصفِ الليل المحلّيّ (≥60 دائمًا)."""
    try:
        from datetime import timedelta
        from ..core import system_config
        now = system_config.local_now(int(tenant_id))
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return max(60, int((nxt - now).total_seconds()))
    except Exception:  # noqa: BLE001
        return 3600


# ─────────────────────────── السقف والقرار ───────────────────────────

def effective_cap_mb(sub: Any, plan: Any) -> int:
    """السقفُ اليوميّ الفعّال بالميجابايت. تجاوزُ المشترك يَغلب الباقة.
    0 = لا سقفَ يوميّ."""
    try:
        own = int(getattr(sub, "daily_quota_mb", 0) or 0)
        if own > 0:
            return own
        if plan is None:
            return 0
        for field in ("daily_combined_quota_mb", "quota_daily_mb"):
            val = int(getattr(plan, field, 0) or 0)
            if val > 0:
                return val
        # السقفان المنفصلان (تنزيل/رفع) يُجمعان حين لا يوجد مدمج.
        dn = int(getattr(plan, "daily_download_quota_mb", 0) or 0)
        up = int(getattr(plan, "daily_upload_quota_mb", 0) or 0)
        return dn + up
    except Exception:  # noqa: BLE001
        return 0


def effective_action(sub: Any, plan: Any) -> str:
    """قرارُ ما بعد النفاد: تجاوزُ المشترك ثمّ الباقة ثمّ ``disconnect``."""
    for src in (sub, plan):
        try:
            raw = str(getattr(src, "daily_quota_action", "") or "").strip().lower()
        except Exception:  # noqa: BLE001
            raw = ""
        if raw in _ACTIONS:
            return raw
    return ACTION_DISCONNECT


def _reduce_rate_kbps(tenant_id: int) -> Tuple[int, int]:
    """سرعةُ التخفيف العامّة للمستأجر (نزول، صعود) بالكيلوبت."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(int(tenant_id), _SETTING_REDUCE, "")
        if raw:
            dn, _, up = str(raw).partition("/")
            d, u = int(dn or 0), int(up or 0)
            if d > 0 and u > 0:
                return d, u
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_REDUCE_KBPS


def throttle_kbps(sub: Any, plan: Any) -> Optional[Tuple[int, int]]:
    """السرعةُ المطبَّقة بعد النفاد (نزول، صعود) بالكيلوبت، أو ``None``
    حين يكون القرارُ قطعًا."""
    action = effective_action(sub, plan)
    if action == ACTION_DISCONNECT:
        return None
    if action == ACTION_CUSTOM:
        for src in (sub, plan):
            try:
                dn = int(getattr(src, "daily_quota_speed_down_kbps", 0) or 0)
                up = int(getattr(src, "daily_quota_speed_up_kbps", 0) or 0)
            except Exception:  # noqa: BLE001
                dn = up = 0
            if dn > 0:
                return dn, (up if up > 0 else dn)
        # سرعةٌ مخصّصة بلا رقم = لا نَخترع سقفًا؛ نَنزل للتخفيف العامّ.
        _LOG.warning("daily_quota: custom_speed بلا سرعةٍ مضبوطة (user=%r) — "
                     "يُطبَّق التخفيف العامّ",
                     getattr(sub, "username", "?"))
    return _reduce_rate_kbps(int(getattr(sub, "tenant_id", 0) or 0))


def throttle_rate_limit(sub: Any, plan: Any) -> str:
    """نصُّ ``Mikrotik-Rate-Limit`` بعد النفاد (فارغٌ عند القطع)."""
    pair = throttle_kbps(sub, plan)
    if not pair:
        return ""
    try:
        from .live_session_control import build_mikrotik_rate_limit
        return build_mikrotik_rate_limit(int(pair[0]), int(pair[1]))
    except Exception:  # noqa: BLE001
        return f"{int(pair[1])}k/{int(pair[0])}k"


# ─────────────────────────── عدّادُ اليوم ───────────────────────────

def used_bytes_today(sub: Any) -> int:
    """استهلاكُ اليوم بالبايت. ختمٌ قديم ⇒ صفر (تجدَّد منتصفَ الليل)."""
    try:
        stamp = str(getattr(sub, "daily_usage_day", "") or "")
        if stamp != local_day(int(getattr(sub, "tenant_id", 0) or 0)):
            return 0
        return int(getattr(sub, "daily_used_bytes", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def is_exhausted(sub: Any, plan: Any) -> bool:
    """هل بَلغ استهلاكُ اليومِ السقفَ اليوميّ؟"""
    cap = effective_cap_mb(sub, plan)
    if cap <= 0:
        return False
    return used_bytes_today(sub) >= cap * _MB


def remaining_bytes(sub: Any, plan: Any) -> Optional[int]:
    """المتبقّي اليومَ بالبايت، أو ``None`` حين لا سقف."""
    cap = effective_cap_mb(sub, plan)
    if cap <= 0:
        return None
    return max(0, cap * _MB - used_bytes_today(sub))


def add_usage(tenant_id: int, username: str, delta_bytes: int) -> None:
    """يُضيف فرقَ الاستهلاك إلى عدّادِ اليوم، ويصفّره إن تغيّر ختمُ اليوم.

    تُستدعى من مسار المحاسبة (interim/stop) بفرقِ العدّاد لا بقيمته، فلا
    تُحتسب بايتاتُ الجلسةِ مرّتين. محصَّنة: أيّ خطأ يُبتلع.
    """
    try:
        delta = int(delta_bytes or 0)
        if delta <= 0:
            return
        from ..db.connection import db
        day = local_day(int(tenant_id))
        db().execute(
            "UPDATE subscribers "
            "   SET daily_used_bytes = CASE WHEN daily_usage_day = ? "
            "                               THEN daily_used_bytes + ? ELSE ? END,"
            "       daily_usage_day  = ? "
            " WHERE tenant_id = ? AND username = ?",
            (day, delta, delta, day, int(tenant_id), str(username)),
        )
    except Exception:  # noqa: BLE001 — المحاسبة لا تسقط بسبب عدّادٍ يوميّ
        _LOG.warning("daily_quota: تعذّر تحديثُ عدّادِ اليوم (user=%r)",
                     username, exc_info=True)


def reset(tenant_id: int, username: str) -> None:
    """تصفيرٌ يدويّ لعدّادِ اليوم (زرُّ المشغّل)."""
    try:
        from ..db.connection import db
        db().execute(
            "UPDATE subscribers SET daily_used_bytes = 0, daily_usage_day = ?,"
            "       daily_quota_state = '' "
            " WHERE tenant_id = ? AND username = ?",
            (local_day(int(tenant_id)), int(tenant_id), str(username)),
        )
    except Exception:  # noqa: BLE001
        _LOG.warning("daily_quota: تعذّر تصفيرُ عدّادِ اليوم (user=%r)",
                     username, exc_info=True)


def set_state(tenant_id: int, username: str, state: str) -> None:
    """يُثبّت حالةَ الإنفاذ ('' | 'throttled' | 'blocked') لمنعِ تكرارِ CoA."""
    try:
        from ..db.connection import db
        db().execute(
            "UPDATE subscribers SET daily_quota_state = ? "
            " WHERE tenant_id = ? AND username = ?",
            (str(state or ""), int(tenant_id), str(username)),
        )
    except Exception:  # noqa: BLE001
        _LOG.warning("daily_quota: تعذّر حفظُ حالةِ الإنفاذ (user=%r)",
                     username, exc_info=True)


def summary(sub: Any, plan: Any) -> dict:
    """ملخَّصٌ للعرض في اللوحة والتطبيق."""
    cap_mb = effective_cap_mb(sub, plan)
    used = used_bytes_today(sub)
    return {
        "enabled": cap_mb > 0,
        "cap_mb": cap_mb,
        "used_mb": round(used / _MB, 2),
        "remaining_mb": round(max(0, cap_mb * _MB - used) / _MB, 2) if cap_mb else None,
        "percent": (min(100, round(used * 100 / (cap_mb * _MB), 1)) if cap_mb else 0),
        "exhausted": is_exhausted(sub, plan),
        "action": effective_action(sub, plan),
        "throttle_rate": throttle_rate_limit(sub, plan),
        "state": str(getattr(sub, "daily_quota_state", "") or ""),
        "resets_in_seconds": seconds_until_midnight(
            int(getattr(sub, "tenant_id", 0) or 0)),
    }
