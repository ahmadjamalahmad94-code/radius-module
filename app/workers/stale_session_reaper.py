"""
stale_session_reaper — يُغلق جلسات radacct الزومبي التي توقف وصول
interim-update لها منذ مدة طويلة (RFC 2866 §4.3 — Acct-Interim-Interval).

السبب (R12.1):
  MikroTik (وكل NAS تقريبًا) قد لا يُرسل Acct-Stop في عدة سيناريوهات:
    1. المستخدم يُغلق الجهاز/البطارية تفرغ → keepalive timeout على MT.
    2. NAS reboot دون إرسال Accounting-On packet.
    3. الـ UDP لـ Acct-Stop يُفقد على الشبكة (RADIUS لا يحتوي retry للـ
       accounting في الـ wire-level).
    4. DB locked وقت كتابة Acct-Stop.
    5. session_id mismatch بين Start و Stop.

  النتيجة: rows في radacct بـ acctstoptime IS NULL تبقى "حيّة" أبدًا،
  وتظهر في /admin/radius/online كمتصلين رغم أنّ الجلسة ميتة فعلاً.

الحلّ:
  MT يُرسل Interim-Update كل `Acct-Interim-Interval` (افتراضيًا 5 دقائق
  لو مُفعَّل). نُغلق كل row لم يصلها interim-update منذ
  STALE_THRESHOLD_SEC. الـ acctstoptime يُعيَّن إلى آخر acctupdatetime
  معروف (أو acctstarttime لو لم يصل interim-update أصلاً)، و
  acctterminatecause = 'Stale-Session-Timeout'.

الـ threshold:
  افتراضي 900s (15 دقيقة) = 3× الـ interim interval القياسي، مما يعطي
  هامش أمان كافٍ لـ UDP loss عرَضي دون قتل جلسات حيّة.
  يمكن override عبر HOBERADIUS_STALE_SESSION_SEC env var.

ـ يُبدأ مرّة واحدة من _start_workers في app/__init__.py ـ
"""
from __future__ import annotations

import logging
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "stale_session_reaper"

_started = False
_started_lock = threading.Lock()

# defaults — قابلة للـ override عبر env
_DEFAULT_INTERVAL_SEC = 60.0     # كل دقيقة


def _stale_threshold_sec() -> int:
    """العتبة (ثوانٍ) — المصدر الموحّد في session_reconciler:
    HOBERADIUS_SESSION_STALE_MINUTES (افتراضي 20د) ثمّ القديم
    HOBERADIUS_STALE_SESSION_SEC للتوافق الخلفيّ."""
    from app.radius.services.session_reconciler import stale_threshold_sec
    return stale_threshold_sec()


def reap_once(*, threshold_sec: int) -> int:
    """يُغلق الـ rows الميتة (قاعدة المهلة) عبر مسار الإغلاق القانوني الموحّد
    في ``session_reconciler``. يُرجع عدد الـ rows التي أُغلقت.

    المنطق (مفوَّض إلى ``reconcile_stale_interim``):
      - الصفوف المفتوحة فقط (``acctstoptime IS NULL``).
      - آخر إشارة حياة ``COALESCE(acctupdatetime, acctstarttime)`` أقدم من
        ``threshold_sec``.
      - acctstoptime = آخر إشارة حياة معروفة (لا datetime('now')) فمدّة
        الجلسة تبقى دقيقة، **و** acctsessiontime يُحسَب (start→stop) — وهو ما
        كان ناقصًا في الإصدار القديم فظهرت الجلسات المُغلقة بمدّة صفر.
      - acctterminatecause = 'Stale-Session-Timeout'.

    idempotent ومُحصَّن لكلّ صفّ على حدة، آمن للتشغيل المتزامن مع
    SqliteAdapter.disconnect (كلاهما WHERE acctstoptime IS NULL).
    """
    from app.radius.services.session_reconciler import (
        CAUSE_INTERIM, reconcile_stale_interim,
    )
    return reconcile_stale_interim(threshold_sec=threshold_sec, cause=CAUSE_INTERIM)


def _run_loop(*, interval_sec: float, threshold_sec: int) -> None:
    _LOG.info("stale_session_reaper started, interval=%.1fs threshold=%ds",
              interval_sec, threshold_sec)
    while True:
        reaped = 0
        try:
            reaped = reap_once(threshold_sec=threshold_sec)
            if reaped:
                _LOG.info("Reaped %d stale session(s)", reaped)
        except Exception:  # noqa: BLE001
            _LOG.exception("stale_session_reaper tick failed")
        beat(_NAME, info={
            "interval_sec": interval_sec,
            "threshold_sec": threshold_sec,
            "last_reaped": reaped,
        })
        time.sleep(interval_sec)


def start_stale_session_reaper(
    *, interval_sec: float = _DEFAULT_INTERVAL_SEC,
) -> None:
    global _started
    with _started_lock:
        if _started:
            return
        threshold = _stale_threshold_sec()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval_sec, "threshold_sec": threshold},
            daemon=True, name="hr-stale-reaper",
        )
        t.start()
        _started = True
