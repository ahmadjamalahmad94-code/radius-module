"""حارسُ عدّاد الجلسة — يقارن ما يحمله الراوترُ بما نسمح به فعلًا.

**لماذا يلزم حارسٌ أصلًا؟** لأنّ ``Session-Timeout`` يُرسَل **مرّةً واحدةً
عند القبول** ولا سبيلَ لتصحيحه بعدها: جرّبنا CoA فردّ مايكروتيك
``CoA-NAK · Unsupported-Extension`` — يقبل الطردَ لا التعديل. فأيُّ تغييرٍ
يطرأ على نافذة البطاقة **بعد** دخولها (تقصيرُ مدّةٍ خاطئة، سحبُ وقتٍ ممنوح)
يبقى الراوترُ جاهلًا به، ويكمل الزبونُ على العدّاد القديم.

وهذا ليس افتراضًا: 2026-08-26 عند «عبد أبو هاشم» قُصّرت 226 بطاقةً من عشر
ساعاتٍ إلى ثمانٍ بعد أن دخل أصحابُها، فبقي الراوترُ على العشر.

**الحارسُ يقرأ ولا يلمس افتراضًا.** الطردُ — وهو الوسيلةُ الوحيدة المتاحة
لتصحيح جلسةٍ قائمة — يبقى **اختياريًّا صريحًا**
(``HOBERADIUS_SESSION_TIMER_GUARD_ENFORCE=1``): قطعُ زبونٍ يدفع ثمنَ خطأٍ
عندنا قرارٌ للمشغّل لا لعاملٍ صامت.

**وحين يُشغَّل الطرد فهو لِـ``expired`` وحدَه** — أي مَن انتهت نافذتُه فعلًا.
أمّا ``missing`` (لا عدّادَ على الراوتر) فعطبٌ عندنا، وطردٌ عليه يقطع أصحابَ
البطاقات السارية.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

#: تفاوتٌ مسموحٌ بين عدّاد الراوتر وحسابنا (ثوانٍ). الرزمُ تتأخّر والساعاتُ
#: تنحرف قليلًا؛ أقلُّ من هذا ليس خللًا يستحقّ ذكرًا.
DEFAULT_TOLERANCE_SEC = 600

_UNIT_SEC = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
_DUR_RE = re.compile(r"(\d+)([wdhms])")

#: تصنيفاتُ التفاوت
KIND_MISSING = "missing"        # لا عدّادَ أصلًا ⇒ الراوترُ لن يقطعه أبدًا
KIND_OVER = "over"              # عدّادُ الراوتر أطولُ ممّا نسمح
KIND_EXPIRED_LIVE = "expired"   # البطاقةُ انتهت والجلسةُ قائمة


def parse_duration(raw: Any) -> int:
    """``"7h23m33s"`` → ثوانٍ. يُرجع 0 لغير المفهوم أو الفارغ.

    صيغةُ RouterOS تُسقط الوحداتِ الصفريّة (``"2h5s"``) وقد تبلغ الأسابيع
    (``"24w6d21h"``) — فالتحليلُ بالوحدات لا بالمواضع.
    """
    if raw is None:
        return 0
    s = str(raw).strip().lower()
    if not s or s in ("0", "none", "false"):
        return 0
    total = sum(int(n) * _UNIT_SEC[u] for n, u in _DUR_RE.findall(s))
    return total


def classify(router_secs: int, ours_secs: int, *,
             tolerance_sec: int = DEFAULT_TOLERANCE_SEC) -> Optional[str]:
    """تصنيفُ جلسةٍ واحدة، أو None حين لا تفاوتَ يستحقّ.

    ``ours_secs`` = ما تبقّى من نافذة البطاقة عندنا (قد يكون سالبًا).
    ``router_secs`` = عدّاد الراوتر (0 = لا عدّاد).
    """
    if ours_secs <= 0:
        return KIND_EXPIRED_LIVE
    if router_secs <= 0:
        return KIND_MISSING
    if router_secs - ours_secs > tolerance_sec:
        return KIND_OVER
    return None


def _tolerance() -> int:
    from ..core import env_settings
    try:
        return max(60, int(str(env_settings.env(
            "HOBERADIUS_SESSION_TIMER_TOLERANCE_SEC",
            str(DEFAULT_TOLERANCE_SEC))).strip() or DEFAULT_TOLERANCE_SEC))
    except Exception:  # noqa: BLE001
        return DEFAULT_TOLERANCE_SEC


def enforce_enabled() -> bool:
    """الطردُ **مُطفأٌ افتراضًا** — انظر شرحَ الوحدة أعلاه."""
    from ..core import env_settings
    raw = str(env_settings.env(
        "HOBERADIUS_SESSION_TIMER_GUARD_ENFORCE", "0") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _parse_dt(raw: Any) -> Optional[datetime]:
    from ..db.helpers import parse_dt
    try:
        return parse_dt(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def audit(tenant_id: int = 1, *, enforce: Optional[bool] = None) -> dict[str, Any]:
    """يمرّ على كلّ راوترٍ مُفعَّلٍ ويقارن جلساتِه الحيّة بنوافذنا.

    يُرجع تقريرًا: العدّاداتِ والتفاصيل. لا يقطع شيئًا إلّا حين ``enforce``.
    محصَّنٌ لكلّ راوترٍ على حدة — راوترٌ لا يُقرأ لا يُسقط الجولةَ كلَّها.
    """
    from ..db.connection import db
    from ..integration.mikrotik.client import MikrotikClient

    if enforce is None:
        enforce = enforce_enabled()
    tol = _tolerance()
    now = datetime.utcnow()
    rep: dict[str, Any] = {
        "checked": 0, "routers": 0, "routers_failed": 0,
        KIND_MISSING: 0, KIND_OVER: 0, KIND_EXPIRED_LIVE: 0,
        "kicked": 0, "details": [], "enforce": bool(enforce),
        "tolerance_sec": tol,
    }
    rows = db().execute(
        "SELECT id, name, address, api_user, api_password, api_port "
        "  FROM nas_devices "
        " WHERE tenant_id = ? AND deleted_at IS NULL AND enabled = 1 "
        "   AND COALESCE(api_user,'') <> '' AND COALESCE(address,'') <> ''",
        (int(tenant_id),)).fetchall()
    for r in rows:
        nas_id, nas_name = r[0], r[1]
        try:
            c = MikrotikClient(host=r[2], username=r[3], password=r[4],
                               port=int(r[5] or 8728), use_tls=False,
                               connect_timeout=12)
            c.connect()
            active = list(c.print_("/ip/hotspot/active/print"))
        except Exception:  # noqa: BLE001
            rep["routers_failed"] += 1
            _LOG.warning("session_timer_guard: تعذّرت قراءةُ الراوتر %s", nas_name,
                         exc_info=True)
            continue
        rep["routers"] += 1
        for a in active:
            user = str(a.get("user") or "").strip()
            if not user:
                continue
            card = db().execute(
                "SELECT expire_at FROM cards "
                " WHERE tenant_id = ? AND username = ? AND deleted_at IS NULL "
                " LIMIT 1", (int(tenant_id), user)).fetchone()
            if not card:
                continue          # ليس من بطاقاتنا — خارجَ ولايتنا
            exp = _parse_dt(card[0] if not isinstance(card, dict) else card["expire_at"])
            if exp is None:
                continue          # بلا نافذة — لا شيءَ نقارنه
            rep["checked"] += 1
            ours = int((exp - now).total_seconds())
            kind = classify(parse_duration(a.get("session-time-left")), ours,
                            tolerance_sec=tol)
            if not kind:
                continue
            rep[kind] += 1
            rep["details"].append({
                "user": user, "nas": nas_name, "nas_id": nas_id, "kind": kind,
                "router_left": a.get("session-time-left") or "",
                "ours_left_sec": ours,
            })
            # 🔴 الطردُ لِما انتهت نافذتُه **وحدَه**. لا يُطرَد `missing`:
            # غيابُ العدّاد على الراوتر عطبٌ عندنا لا ذنبٌ للزبون — ولو طُرد
            # عليه لقُطع كلُّ صاحبِ بطاقةٍ سارية. (قِيس على خادم سمير
            # 2026-09-03: كلُّ الجلسات الحيّة كانت `missing` بسبب ثغرة
            # Session-Timeout عند أوّل دخول، ومنها بطاقاتٌ بقي لها ساعتان.)
            # و`over` ليس أوانَه بعد: عدّادُ الراوتر أطولُ ممّا نسمح، فينتهي
            # عندنا خلال دقائق ويُلتقط `expired` في الجولة التالية.
            if enforce and kind == KIND_EXPIRED_LIVE and a.get(".id"):
                try:
                    c.run("/ip/hotspot/active/remove", attrs={".id": a[".id"]})
                    rep["kicked"] += 1
                    _LOG.info("session_timer_guard: طُرد %s (%s) على %s",
                              user, kind, nas_name)
                except Exception:  # noqa: BLE001
                    _LOG.warning("session_timer_guard: تعذّر طردُ %s", user,
                                 exc_info=True)
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    return rep


__all__ = ["audit", "classify", "parse_duration", "enforce_enabled",
           "KIND_MISSING", "KIND_OVER", "KIND_EXPIRED_LIVE",
           "DEFAULT_TOLERANCE_SEC"]
