"""قرارُ ما بعد نفاد الكوتة اليوميّة: قطعٌ أم سرعةٌ مخفَّضة؟

الكوتةُ اليوميّة نفسُها يَملكها :mod:`quota_period` (نوافذُ محلّيّة بخطّ
أساس تتجدّد منتصفَ الليل بتوقيت المستأجر). هذه الوحدةُ لا تَحسب استهلاكًا
ولا تُنشئ عدّادًا — تُجيب سؤالًا واحدًا: **ماذا نفعل بمن نفدت كوتتُه
اليوميّة؟**

  ``disconnect``    رفضٌ كما كان (الافتراض) حتّى تتجدّد الكوتة.
  ``reduce_speed``  سماحٌ بسرعةِ التخفيف العامّة للمستأجر.
  ``custom_speed``  سماحٌ بسرعةٍ يكتبها المشغّل بالميجابت لهذه الباقة/المشترك.

الأولويّة: تجاوزُ المشترك ← الباقة ← ``disconnect``. وعَلَمُ دفعةِ
البطاقات ``on_quota_exhaust`` يبقى صاحبَ القرار للبطاقات كما كان.

محصَّنة بالكامل: أيّ خطأ ⇒ ``disconnect`` (السلوك التاريخيّ)، فلا يَفتح
عطبٌ في القراءة بابًا لسرعةٍ لم يَطلبها أحد.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

_LOG = logging.getLogger(__name__)

ACTION_DISCONNECT = "disconnect"
ACTION_REDUCE = "reduce_speed"
ACTION_CUSTOM = "custom_speed"
ACTIONS = (ACTION_DISCONNECT, ACTION_REDUCE, ACTION_CUSTOM)

_DEFAULT_REDUCE_KBPS = (512, 256)                 # (نزول، صعود)
_SETTING_REDUCE = "quota.daily_reduce_rate_kbps"  # "down/up"


def effective_action(sub: Any, plan: Any) -> str:
    """القرارُ الساري: تجاوزُ المشترك ثمّ الباقة ثمّ القطع."""
    for src in (sub, plan):
        try:
            raw = str(getattr(src, "daily_quota_action", "") or "").strip().lower()
        except Exception:  # noqa: BLE001
            raw = ""
        if raw in ACTIONS:
            return raw
    return ACTION_DISCONNECT


def _reduce_rate_kbps(tenant_id: int) -> Tuple[int, int]:
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
    """(نزول، صعود) بالكيلوبت بعد النفاد، أو ``None`` حين القرارُ قطع."""
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
        _LOG.warning("daily_quota_action: custom_speed بلا رقمٍ مضبوط (user=%r)"
                     " — يُطبَّق التخفيفُ العامّ",
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


def allows_connection(sub: Any, plan: Any) -> bool:
    """هل يُسمح بالاتّصال بعد نفاد الكوتة اليوميّة (بسرعةٍ مخفَّضة)؟"""
    return effective_action(sub, plan) != ACTION_DISCONNECT


def seconds_until_local_midnight(tenant_id: int) -> int:
    """ما تبقّى حتّى تجديدِ الكوتة — يُقصّ به Session-Timeout فيَستعيد
    المشتركُ سرعتَه الكاملة بإعادة مصادقةٍ تلقائيّة بلا انتظارِ CoA."""
    try:
        from datetime import timedelta
        from ..core import system_config
        now = system_config.local_now(int(tenant_id))
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return max(60, int((nxt - now).total_seconds()))
    except Exception:  # noqa: BLE001
        return 3600


def set_state(tenant_id: int, username: str, state: str) -> None:
    """ختمُ حالةِ الإنفاذ الحيّ ('' | 'throttled') كي لا يتكرّر CoA."""
    try:
        from ..db.connection import db
        db().execute(
            "UPDATE subscribers SET daily_quota_state = ? "
            " WHERE tenant_id = ? AND username = ?",
            (str(state or ""), int(tenant_id), str(username)))
    except Exception:  # noqa: BLE001
        _LOG.warning("daily_quota_action: تعذّر حفظُ الحالة (user=%r)",
                     username, exc_info=True)
