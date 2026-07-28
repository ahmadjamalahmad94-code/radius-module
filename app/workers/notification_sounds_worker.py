"""notification_sounds_worker — سحب أصوات الإشعارات من لوحة التراخيص دوريًّا.

المزوّد يرفع الأصوات مرّةً في اللوحة، وكلّ نسخة ريديوس تسحبها فتصير
افتراضيّها بلا أيّ إجراءٍ من مالك النسخة (MT92).

يتبع نمط العمّال في المشروع: ``poll_once()`` نقيّةٌ قابلة للاختبار + خيطٌ
خفيّ محروسٌ بـ``HOBERADIUS_NO_WORKER``. طبقة القاعدة محلّيّة-الخيط، فلا
حاجة لسياق Flask.

الإيقاع: ساعةٌ افتراضيًّا، وأرضيّتها ٥ دقائق. السحبة الأولى بعد ٤٥ ثانية من
الإقلاع — لا فورًا: الإقلاع مزدحمٌ بالهجرات والعمّال، وصوتُ إشعارٍ لا يستحقّ
مزاحمتها على قاعدةٍ يكتبها ثلاثة.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "notification_sounds_worker"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 60 * 60       # ساعة
_MIN_INTERVAL = 5 * 60            # ٥ دقائق
_BOOT_DELAY = 45


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_NOTIF_SOUNDS_INTERVAL_SECONDS", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL


def _tenant_ids() -> list[int]:
    try:
        from app.radius.db.connection import db
        cur = db().execute("SELECT id FROM tenants ORDER BY id")
        ids = [int(r["id"]) for r in cur.fetchall()]
        return ids or [1]
    except Exception:  # noqa: BLE001
        return [1]


def poll_once() -> dict:
    """سحبةٌ لكلّ جهة. نقيّة وقابلة للاختبار؛ لا ترمي أبدًا."""
    from app.radius.services import notification_sounds_sync as sync

    updated = failed = tenants = 0
    for tid in _tenant_ids():
        try:
            rep = sync.sync_once(tid)
            tenants += 1
            updated += int(rep.get("updated") or 0)
            failed += int(rep.get("failed") or 0)
        except Exception:  # noqa: BLE001 — فشل جهةٍ لا يوقف البقيّة
            _LOG.warning("sound sync failed for tenant %s", tid, exc_info=True)
    return {"tenants": tenants, "updated": updated, "failed": failed}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("notification_sounds worker started (interval=%ss)", interval_sec)
    time.sleep(_BOOT_DELAY)
    while True:
        stats = {"tenants": 0, "updated": 0, "failed": 0}
        try:
            stats = poll_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("notification_sounds worker tick failed")
        interval = _interval_sec()
        beat(_NAME, info={"interval_sec": interval, **stats})
        time.sleep(interval)


def start_notification_sounds_worker(flask_app=None) -> None:  # noqa: ANN001
    """يبدأ خيط السحب الدوريّ (مرّة واحدة لكلّ عمليّة)."""
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    with _started_lock:
        if _started:
            return
        _started = True
    interval = _interval_sec()
    t = threading.Thread(
        target=_run_loop, kwargs={"interval_sec": interval},
        name=_NAME, daemon=True)
    t.start()
