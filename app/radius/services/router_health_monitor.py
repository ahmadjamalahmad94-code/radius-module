"""router_health_monitor — كشف انقطاع/عودة الراوترات (NAS) وتنبيهها.

لماذا (فجوة التغطية — حالة ccr3):
  تنبيهات «التنبيهات الذكية» لانقطاع الراوتر (smart_alerts.sweep_offline) كانت:
    • كسولة — تُقيَّم فقط عند فتح صفحة التنبيهات أو عند وصول دفعة مقاييس، بلا
      عامل خلفي دائم؛ فنافذة انقطاع 10 دقائق قد تمرّ بصمت.
    • مشروطة بدفع المقاييس — راوتر لم يُركَّب فيه عميل الدفع (أو خلف نفق إدارة
      فقط) لا «أساس» له فيُتخطّى تماماً.
  لذا راوتر مثل ccr3 يَسقط دون أي تنبيه تلجرام — بينما جهاز «المتابعة» (ping)
  يُنبّه بثقة عبر العامل الخلفي device_health_poll_worker.

ماذا نفعل:
  نَفحص كل راوتر مفعّل بنشاط كل دورة (TCP على عنوانه المُحلّ — آمن للنفق عبر
  resolve_connection_address)، ونُطلق عند الانتقال متصل↔غير متصل عبر
  device_health_alerts.dispatch — نفس مسار تلجرام القانوني + الجرس الذي يستعمله
  مراقب الأجهزة (بلا بوّابة notif.*.enabled). يعمل داخل
  device_health_poll_worker الخلفي.

إزالة التكرار: ننبّه فقط حين تختلف الحالة الجديدة عن المحفوظة
  (nas_devices.last_check_status)؛ والحالة تُحفَظ، فالدورة التالية لا تُكرّر ما
  لم تتغيّر فعلاً (متصل→غير متصل ثم غير متصل→متصل = تنبيهان فقط).

ملاحظة (CPU/الحرارة/الكهرباء): مقاييس الراوتر المدفوعة (router_metric_samples)
  تحمل uptime + حركة الواجهات فقط — لا CPU ولا حرارة ولا جهد/كهرباء. فلا يمكن
  وصل عتبات لها قبل إضافة جمعها في عميل الدفع (عمل لاحق). هنا نَصِل انقطاع/عودة
  الراوتر (التغطية المطلوبة لحالة ccr3).
"""
from __future__ import annotations

import logging
import socket
from typing import Callable, Optional

from ..db.connection import db
from ..db.repos import nas_repo
from . import device_health_alerts as dha
from .nas_connection import resolve_connection_address

_LOG = logging.getLogger(__name__)

_PROBE_TIMEOUT_SEC = 2.0
# حالات فحص الوصول كما يكتبها devices_test / record_check.
_KNOWN = ("reachable", "timeout", "unreachable")


def _reach_to_state(status: str) -> str:
    """reachable → up ؛ timeout/unreachable → down ؛ غير ذلك → unknown."""
    s = (status or "").strip().lower()
    if s == "reachable":
        return "up"
    if s in ("timeout", "unreachable"):
        return "down"
    return "unknown"


def _probe(address: str, port) -> str:
    """فحص وصول TCP (مثل devices_test) — reachable/timeout/unreachable."""
    try:
        with socket.create_connection((address, int(port or 8728)),
                                      timeout=_PROBE_TIMEOUT_SEC):
            return "reachable"
    except socket.timeout:
        return "timeout"
    except OSError:
        return "unreachable"


def _enabled_routers(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT id, name, address, description, api_port, connection_mode, "
        "       vpn_peer_address, last_check_status "
        "FROM nas_devices "
        "WHERE tenant_id=? AND enabled=1 "
        "  AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY id",
        (int(tenant_id),)).fetchall()
    return [dict(r) for r in rows]


def sweep_once(tenant_id: int, *, probe: Optional[Callable] = None) -> dict:
    """يفحص كل راوتر مفعّل ويُطلق تنبيهاً على انتقال متصل↔غير متصل.

    `probe(address, port) -> 'reachable'|'timeout'|'unreachable'` قابلة للحقن
    في الاختبارات (بلا شبكة). يُرجع إحصاء {checked, online, offline, alerts}.
    آمن: أي خطأ على راوتر لا يُسقِط بقية الكنس.
    """
    probe = probe or _probe
    tid = int(tenant_id)
    stats = {"checked": 0, "online": 0, "offline": 0, "alerts": 0}
    for r in _enabled_routers(tid):
        try:
            stats["checked"] += 1
            addr = (resolve_connection_address(r) or r.get("address") or "").strip()
            if not addr:
                continue
            prev_raw = (r.get("last_check_status") or "").strip().lower()
            prev_state = _reach_to_state(prev_raw) if prev_raw in _KNOWN else "unknown"

            status = probe(addr, r.get("api_port"))
            new_state = _reach_to_state(status)
            # سجّل الحالة الجديدة دائماً (تُغذّي العرض + أساس الانتقال القادم).
            try:
                nas_repo.record_check(tid, int(r["id"]), status=status)
            except Exception:  # noqa: BLE001
                _LOG.debug("router_health: record_check failed for %s", r.get("id"))

            stats["online" if new_state == "up" else "offline"] += 1

            # تنبيه فقط على انتقال حقيقي من حالة معروفة (لا أساس ⇒ لا إنذار كاذب).
            if prev_state == "unknown" or prev_state == new_state:
                continue
            name = r.get("name") or f"#{r['id']}"
            desc = (r.get("description") or "").strip()
            alert_type = "router_offline" if new_state == "down" else "router_online"
            message = dha.format_alert_message(
                alert_type, name=name, ip=addr, description=desc)
            ok, _reason = dha.dispatch(
                tid, alert_type=alert_type, message=message, name=name,
                link="/admin/radius/mt/operations")
            if ok:
                stats["alerts"] += 1
        except Exception:  # noqa: BLE001 — راوتر واحد لا يكسر الكنس
            _LOG.exception("router_health sweep failed for router %s", r.get("id"))
    return stats


def _tenants_with_routers() -> list[int]:
    try:
        rows = db().execute(
            "SELECT DISTINCT tenant_id FROM nas_devices "
            "WHERE enabled=1 AND (deleted_at IS NULL OR deleted_at='')"
        ).fetchall()
        return [int(r["tenant_id"]) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def sweep_all() -> dict:
    """كنس راوترات كل المستأجرين — نقطة دخول العامل الخلفي."""
    total = {"tenants": 0, "checked": 0, "alerts": 0}
    for tid in _tenants_with_routers():
        s = sweep_once(tid)
        total["tenants"] += 1
        total["checked"] += s["checked"]
        total["alerts"] += s["alerts"]
    return total
