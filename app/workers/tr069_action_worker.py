"""tr069_action_worker — يدفع أوامر TR-069 المُصفَّفة إلى GenieACS ويُزامن حالة
الأجهزة دوريًّا. وحدة تجريبيّة — معطّلة ما لم يُفعَّل HOBERADIUS_TR069_ENABLED.

بنمط العمّال القائم: خيط داخليّ + time.sleep + heartbeat، محصَّن (لا يرفع أبدًا).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "tr069_action"
_started = False
_started_lock = threading.Lock()


def _interval_sec() -> int:
    try:
        return max(10, int(os.environ.get("HOBERADIUS_TR069_INTERVAL_SEC") or 30))
    except ValueError:
        return 30


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001
        return [1]


def sweep_once() -> dict:
    """جولة واحدة: معالجة الطابور + مزامنة الأجهزة عبر كل المستأجرين. آمنة للاستيراد
    والاستدعاء المباشر في الاختبارات."""
    from app.radius.services.tr069 import config as tr069_config
    from app.radius.services.tr069 import sync_service
    if not tr069_config.tr069_enabled():
        return {"actions": 0, "reconciled": 0, "skipped": True}
    actions = reconciled = 0
    for tid in _all_tenants():
        try:
            actions += sync_service.process_queued_actions(tid)
            reconciled += sync_service.reconcile_from_genieacs(tid)
        except Exception:  # noqa: BLE001 — مستأجر واحد لا يُعطّل البقيّة
            _LOG.exception("tr069 sweep failed for tenant %s", tid)
    return {"actions": actions, "reconciled": reconciled, "skipped": False}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("tr069_action_worker started — interval=%ds", interval_sec)
    while True:
        stats = {"actions": 0, "reconciled": 0}
        try:
            stats = sweep_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("tr069_action tick failed")
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_actions": stats.get("actions", 0),
                          "last_reconciled": stats.get("reconciled", 0)})
        time.sleep(interval_sec)


def start_tr069_action_worker() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        t = threading.Thread(target=_run_loop, kwargs={"interval_sec": _interval_sec()},
                             daemon=True, name="hr-tr069-action")
        t.start()
        _started = True


__all__ = ["start_tr069_action_worker", "sweep_once"]
