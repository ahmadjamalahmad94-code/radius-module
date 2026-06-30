"""connected_live — «المتصلون الآن» مشتقًّا من الحالة الحيّة للراوتر.

سياسة المالك (الحالة الحيّة = المصدر الوحيد):
  • راوتر غير قابل للوصول → جلساته لا تُعَدّ ولا تُعرَض (لا يمكن التحقّق →
    لا بيانات). إن كانت كلّ الراوترات غير متصلة → العدّاد 0 + إشارة «غير متصل».
  • راوتر قابل للوصول → يُعرَض عدده الحيّ (radacct يُصالَح إليه كلّ استطلاع
    فيتطابق الجدول مع الراوتر).
  • عند عودة الراوتر → ``refresh_and_reconcile`` يَستطلع فورًا (مهلة قصيرة)
    ويُصالح radacct حالًا (إغلاق اليتامى + إدراج المرئيّ) فالعرض فوريّ.

طبقة العرض تقرأ سجلّ ``nas_liveness`` الذي يُحدّثه المُستطلِع الخلفيّ — بلا
شبكة وقت الرسم (fail-fast، لا تعليق). الارتداد الآمن: حين لا يوجد أيّ سجلّ
liveness للمستأجر (المُستطلِع متوقّف/الاختبارات/راوتر بلا API)، نَرتدّ إلى
عدّ radacct بنافذة الحياة (السلوك السابق) كي لا ينكسر شيء.

ملاحظة المطابقة (آمنة للنفق): liveness مُفتاحه IP الذي يُستطلَع به الراوتر
(= nas_devices.address)، بينما radacct.nasipaddress قد يكون العنوان العامّ
أو IP نفق الواير جارد (vpn_peer_address). لذا نَنشر قابليّة الوصول على
**كلا** عنواني الجهاز.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import nas_liveness

_LOG = logging.getLogger(__name__)

# مهلة قصيرة لاستطلاع الراوتر وقت رسم الصفحة كي لا تتعلّق اللوحة (ثوانٍ).
_PAGE_PROBE_TIMEOUT_SEC = 4


def _devices(tenant_id: int) -> list[dict]:
    """راوترات المستأجر مع عنوانيها (العام + نفق WG) واسمها وحالة التفعيل."""
    from ..db.connection import db
    try:
        rows = db().execute(
            "SELECT id, name, address, vpn_peer_address, enabled "
            "  FROM nas_devices "
            " WHERE tenant_id=? AND (deleted_at IS NULL OR deleted_at='')",
            (int(tenant_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _match_ips(dev: dict) -> list[str]:
    out: list[str] = []
    for k in ("address", "vpn_peer_address"):
        v = str(dev.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def reachability_by_ip(tenant_id: int) -> dict[str, Optional[bool]]:
    """خريطة كلّ IP قد يَظهر في radacct.nasipaddress → قابليّة الوصول
    (True/False/None). نَنشر حالة الجهاز على عنوانه العام ونفق WG معًا."""
    out: dict[str, Optional[bool]] = {}
    for dev in _devices(int(tenant_id)):
        host = str(dev.get("address") or "").strip()
        reachable = nas_liveness.is_reachable(int(tenant_id), host) if host else None
        for ip in _match_ips(dev):
            out[ip] = reachable
    return out


def unreachable_router_labels(tenant_id: int) -> list[str]:
    """أسماء الراوترات غير القابلة للوصول الآن (للإشارة في الواجهة)."""
    labels: list[str] = []
    for dev in _devices(int(tenant_id)):
        host = str(dev.get("address") or "").strip()
        if host and nas_liveness.is_reachable(int(tenant_id), host) is False:
            name = str(dev.get("name") or "").strip() or host
            labels.append(name)
    return labels


def connected_count(tenant_id: int) -> dict[str, Any]:
    """«المتصلون الآن» وفق الحالة الحيّة. يُرجع:
      {count, source, reachable, unreachable_routers}

    - source="live"   → مشتقّ من الراوترات القابلة للوصول (المصدر الحيّ).
    - source="radacct"→ ارتداد آمن حين لا سجلّ liveness (المُستطلِع متوقّف).
    """
    tid = int(tenant_id)
    unreachable = unreachable_router_labels(tid)
    if not nas_liveness.has_data(tid):
        # لا بيانات liveness إطلاقًا → ارتداد إلى نافذة radacct (السلوك السابق).
        from . import live_sessions
        return {
            "count": live_sessions.tenant_active_count(tid),
            "source": "radacct",
            "reachable": None,
            "unreachable_routers": unreachable,
        }
    count = nas_liveness.live_connected_count(tid)
    any_reachable = any(
        v is True for v in reachability_by_ip(tid).values()
    )
    return {
        "count": count,
        "source": "live",
        "reachable": any_reachable,
        "unreachable_routers": unreachable,
    }


def connected_now(tenant_id: int) -> int:
    """اختصار للعدّاد فقط (للمسارات التي تريد رقمًا)."""
    return int(connected_count(int(tenant_id)).get("count") or 0)


def refresh_and_reconcile(tenant_id: int, *,
                          timeout_sec: int = _PAGE_PROBE_TIMEOUT_SEC) -> dict:
    """استطلاع فوريّ سريع (مهلة قصيرة) لكلّ راوتر مُفعَّل + تسجيل قابليّة
    الوصول + مصالحة radacct فورًا للراوترات القابلة للوصول (إغلاق اليتامى +
    إدراج المرئيّ). هذا يُحقّق «المصالحة فور العودة» + «الفراغ فور الانقطاع»
    على صفحة /online دون تعليق (المهلة قصيرة، لا يرفع أبدًا).

    يُرجع {probed, reachable, unreachable, closed, inserted}.
    """
    tid = int(tenant_id)
    stats = {"probed": 0, "reachable": 0, "unreachable": 0,
             "closed": 0, "inserted": 0}
    try:
        from app.workers import mt_reconciler as mr
    except Exception:  # noqa: BLE001
        return stats
    try:
        configs = mr._collect_router_configs(tid)
    except Exception:  # noqa: BLE001
        configs = []
    for cfg in configs:
        host = str(cfg.get("host") or "").strip()
        if not host:
            continue
        stats["probed"] += 1
        probe = dict(cfg)
        try:
            probe["timeout_sec"] = min(int(cfg.get("timeout_sec") or 20),
                                       int(timeout_sec))
        except (TypeError, ValueError):
            probe["timeout_sec"] = int(timeout_sec)
        try:
            rows = mr._fetch_active_rows(probe)
        except Exception:  # noqa: BLE001 — لا نكسر الصفحة على فشل استطلاع
            rows = None
        if rows is None:
            nas_liveness.record_unreachable(tid, host)
            stats["unreachable"] += 1
            continue
        nas_liveness.record_reachable(tid, host, active_count=len(rows))
        stats["reachable"] += 1
        # مصالحة فوريّة لهذا الراوتر (نفس مسار mt_reconciler — لا تكرار منطق).
        try:
            stats["closed"] += mr._reconcile_nas(tid, host, mr._keys_from_rows(rows))
            stats["inserted"] += mr._materialize_nas(tid, host, rows).get("inserted", 0)
        except Exception:  # noqa: BLE001
            _LOG.exception("connected_live: reconcile after probe failed nas=%s", host)
    return stats


__all__ = [
    "reachability_by_ip", "unreachable_router_labels",
    "connected_count", "connected_now", "refresh_and_reconcile",
]
