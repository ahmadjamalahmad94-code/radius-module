"""device_health_poll_worker — الفحص الدوري التلقائي لتتبع حالة الأجهزة.

كان الفحص الدوري معطّلًا افتراضيًا (يتطلب env: HOBERADIUS_DEVICE_HEALTH_POLL
وبفترة ثابتة 60s) — فلا «تتبع» فعليًا دون زر يدوي. هذا الـworker يقلب
المعادلة: يعمل افتراضيًا ويحترم إعدادات كل مستأجر من الواجهة:

  tenant_settings:
    device_health.poll_enabled  → "1"/"0" (افتراضي مفعّل)
    device_health.poll_minutes  → الفترة بالدقائق (افتراضي 5، أدنى 1)

كل دورة (دقّة 60s): لكل مستأجر فعّال لديه أجهزة مُراقَبة، إن كان فحصه
الدوري مفعّلًا وانقضت فترته منذ آخر فحص دوري مسجَّل — يشغّل
device_health_poller.tick(tenant) الذي يدوّن النتيجة في سجل
network_device_health_checks (source=poller) ويُطلق التنبيهات الذكية.

متغيّرات البيئة:
  HOBERADIUS_DEVICE_HEALTH_POLL_WORKER_ENABLED (افتراضي 1 → مُفعَّل)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "device_health_poll_worker"

_started = False
_started_lock = threading.Lock()

_TICK_SEC = 60
_STATE_TRUE = ("1", "true", "t", "on", "yes")
_DEFAULT_MINUTES = 5


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_DEVICE_HEALTH_POLL_WORKER_ENABLED")
           or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def poll_settings(tenant_id: int) -> dict:
    """إعدادات الفحص الدوري للمستأجر — نفس المفاتيح التي تحفظها الواجهة."""
    from app.radius.db.repos import tenants_repo

    enabled_raw = tenants_repo.get_setting(
        int(tenant_id), "device_health.poll_enabled", "1")
    minutes_raw = tenants_repo.get_setting(
        int(tenant_id), "device_health.poll_minutes", str(_DEFAULT_MINUTES))
    try:
        minutes = int(str(minutes_raw or "").strip() or _DEFAULT_MINUTES)
    except ValueError:
        minutes = _DEFAULT_MINUTES
    return {
        "enabled": str(enabled_raw or "1").strip().lower() in _STATE_TRUE,
        "minutes": max(1, min(24 * 60, minutes)),
    }


def _poll_due(tenant_id: int, settings: dict) -> bool:
    """هل حان موعد الفحص الدوري لهذا المستأجر؟ يقارن آخر فحص دوري مسجَّل
    بفترة poll_minutes. لا سجل/جدول قديم ⇒ نعم (يفحص الآن)."""
    if not settings.get("enabled", True):
        return False
    minutes = max(1, int(settings.get("minutes") or _DEFAULT_MINUTES))
    try:
        from app.radius.db.repos import device_health_checks_repo
        last = device_health_checks_repo.last_check_at(
            int(tenant_id), source="poller")
    except Exception:  # noqa: BLE001
        return True
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.rstrip("Z"))
    except ValueError:
        return True
    return datetime.utcnow() - last_dt >= timedelta(minutes=minutes)


def _tenants_with_devices() -> list[int]:
    from app.radius.db.connection import db
    cur = db().execute(
        "SELECT DISTINCT tenant_id FROM network_device_monitor_devices "
        "WHERE monitoring_enabled = 1 AND deleted_at IS NULL")
    return [int(r["tenant_id"]) for r in cur.fetchall()]


def _tenants_with_routers() -> list[int]:
    from app.radius.db.connection import db
    cur = db().execute(
        "SELECT DISTINCT tenant_id FROM nas_devices "
        "WHERE enabled = 1 AND (deleted_at IS NULL OR deleted_at = '')")
    return [int(r["tenant_id"]) for r in cur.fetchall()]


def _sweep_routers() -> int:
    """كنس انقطاع/عودة الراوترات (NAS) كل دورة — لا يخضع لـ poll_minutes كي يكون
    كشف الانقطاع سريعاً (≤ دورة واحدة). يحترم نفس مفتاح التفعيل لكل مستأجر
    (device_health.poll_enabled). مستقلّ عن وجود أجهزة مُراقَبة (حالة ccr3).
    يُرجع عدد التنبيهات المُرسَلة."""
    alerts = 0
    for tenant_id in _tenants_with_routers():
        if not poll_settings(tenant_id).get("enabled", True):
            continue
        try:
            from app.radius.services import router_health_monitor
            alerts += int(
                router_health_monitor.sweep_once(tenant_id).get("alerts") or 0)
        except Exception:  # noqa: BLE001 — كنس الراوترات لا يكسر دورة الأجهزة
            _LOG.exception("device_health_poll_worker: router sweep failed t=%d",
                           tenant_id)
    return alerts


# سحب موارد الراوتر (CPU/حرارة/ذاكرة/قرص/حركة) أثقل من فحص TCP (3 نداءات API
# لكل راوتر)، فنُخفّف وتيرته إلى كل دقيقتين بدل كل دورة (60s) — كافٍ للعرض
# والعتبات، ويُعطي نافذة معقولة لاشتقاق معدّل الحركة.
_RESOURCE_INTERVAL_SEC = 120
_last_resource_sweep = 0.0


def _sweep_router_resources() -> int:
    """يَسحب موارد الراوترات ويُطلق تنبيهات العتبات — مُخفَّف الوتيرة (كل ~دقيقتين).
    يحترم device_health.poll_enabled لكل مستأجر. يُرجع عدد التنبيهات."""
    global _last_resource_sweep
    nowm = time.monotonic()
    if nowm - _last_resource_sweep < _RESOURCE_INTERVAL_SEC:
        return 0
    _last_resource_sweep = nowm
    alerts = 0
    for tenant_id in _tenants_with_routers():
        if not poll_settings(tenant_id).get("enabled", True):
            continue
        try:
            from app.radius.services import router_resource_monitor
            alerts += int(
                router_resource_monitor.sweep_once(tenant_id).get("alerts") or 0)
        except Exception:  # noqa: BLE001 — سحب الموارد لا يكسر دورة الأجهزة
            _LOG.exception("device_health_poll_worker: resource sweep failed t=%d",
                           tenant_id)
    return alerts


def poll_once() -> dict:
    """دورة واحدة لكل المستأجرين المستحقين. تُعيد إحصاءات للنبضة."""
    stats = {"tenants": 0, "polled": 0, "not_due": 0, "scanned": 0,
             "router_alerts": 0, "resource_alerts": 0}
    for tenant_id in _tenants_with_devices():
        stats["tenants"] += 1
        settings = poll_settings(tenant_id)
        if not _poll_due(tenant_id, settings):
            stats["not_due"] += 1
            continue
        from app.radius.services import device_health_poller as poller
        summary = poller.tick(tenant_id=tenant_id, log_source="poller")
        stats["polled"] += 1
        stats["scanned"] += int(summary.get("scanned") or 0)
    # كنس الراوترات (انقطاع/عودة ccr3) — كل دورة، مستقلّ عن الأجهزة المُراقَبة.
    stats["router_alerts"] = _sweep_routers()
    # سحب موارد الراوتر + تنبيهات العتبات — مُخفَّف (كل ~دقيقتين).
    stats["resource_alerts"] = _sweep_router_resources()
    return stats


def _run_loop() -> None:
    _LOG.info("device_health_poll_worker started — tick=%ds", _TICK_SEC)
    while True:
        stats = {}
        try:
            stats = poll_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("device_health_poll_worker tick failed")
        beat(_NAME, info={
            "tick_sec": _TICK_SEC,
            "last_tenants": stats.get("tenants", 0),
            "last_polled": stats.get("polled", 0),
            "last_scanned": stats.get("scanned", 0),
        })
        time.sleep(_TICK_SEC)


def start_device_health_poll_worker() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("device_health_poll_worker disabled by env")
            return
        t = threading.Thread(target=_run_loop, daemon=True,
                             name="hr-device-health-poll")
        t.start()
        _started = True
