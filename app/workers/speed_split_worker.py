"""speed_split_worker — إعادة توزيع «تقسيم السرعة على الأجهزة» عند تغيّر الجلسات.

لماذا عامل خلفيّ؟ FreeRADIUS يكتب المحاسبة في radacct **مباشرةً عبر SQL**
(sites-enabled: «accounting: detail + sql») ولا يستدعي اللوحة — فخطّافا
accounting_events._start/_stop لا يريان اتصال/فصل الأجهزة في الإنتاج. هذا
العامل يغطّي كلّ المسارات (فصل طبيعيّ، طرد PoD، اتصال جهاز جديد) من مصدر
الحقيقة نفسه: عدد جلسات radacct المفتوحة.

التصميم — **transition-based مثل bandwidth_schedule_worker** (لا CoA لكلّ tick):

  * كلّ tick يستعلم، لكلّ مستأجر، المشتركين المفعِّلين للتقسيم
    (equal_share_download/upload) مع عدد جلساتهم المفتوحة (استعلام GROUP BY
    واحد — رخيص حتى مع آلاف المشتركين لأنّ المفعِّلين قلّة عادةً).
  * يقارن العدّ بخريطة العدّ السابقة (process-local). **تغيّر العدد فقط** يدفع
    السرعة الفعّالة (المقسومة على العدد الجديد) لكلّ جلسات المشترك عبر CoA
    (bandwidth_apply.apply_users_effective — نفس مسار السرعة المؤقتة العامل).
  * أوّل رؤية للمشترك (إقلاع العامل) تدفع مرّة واحدة idempotent — تصلح أيّ
    انجراف حدث والعامل متوقّف (نفس فلسفة re-engage في عامل الجداول).
  * عدّ 0 = لا جلسات → لا CoA (لا هدف)؛ يُنسى حتى يتّصل ثانية (المصادقة
    ستعطيه السرعة الصحيحة عبر policy_engine).

الإيقاع: HOBERADIUS_SPLIT_WORKER_INTERVAL_SEC (افتراضيّ 15s، أدنى 5) — فصل
جهاز يُعاد توزيعه خلال ~15 ثانية. تعطيل: HOBERADIUS_SPLIT_WORKER_ENABLED=0.
يُتخطّى تحت HOBERADIUS_NO_WORKER/pytest ككلّ العمّال (حارس _start_workers).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "speed_split"

_started = False
_started_lock = threading.Lock()

# (tenant_id, username) → آخر عدد جلسات مفتوحة معروف.
_counts: dict[tuple[int, str], int] = {}
_counts_lock = threading.Lock()

_DEFAULT_INTERVAL = 15
_MIN_INTERVAL = 5


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_SPLIT_WORKER_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_SPLIT_WORKER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001 — قد يغيب الجدول مبكّرًا جدًّا في الإقلاع
        return [1]


def _split_session_counts(tenant_id: int) -> dict[str, int]:
    """{username: open_session_count} لكلّ مشترك مفعِّل للتقسيم (قد يكون 0)."""
    from app.radius.db.connection import db
    rows = db().execute(
        """
        SELECT s.username AS username, COUNT(r.radacctid) AS n
          FROM subscribers s
          LEFT JOIN radacct r
                 ON r.tenant_id = s.tenant_id
                AND r.username = s.username
                AND r.acctstoptime IS NULL
         WHERE s.tenant_id = ?
           AND (s.equal_share_download = 1 OR s.equal_share_upload = 1)
           AND COALESCE(s.deleted_at, '') = ''
         GROUP BY s.username
        """,
        (tenant_id,),
    ).fetchall()
    return {str(r["username"]): int(r["n"] or 0) for r in rows if r["username"]}


def _tenant_tick(tenant_id: int) -> int:
    """يدفع CoA للمشتركين الذين تغيّر عدد أجهزتهم. يعيد عدد المدفوع لهم."""
    from app.radius.services.bandwidth_apply import apply_users_effective

    current = _split_session_counts(tenant_id)
    to_push: list[str] = []
    with _counts_lock:
        for username, n in current.items():
            key = (tenant_id, username)
            prev = _counts.get(key)
            if n <= 0:
                # لا جلسات → لا هدف للـCoA؛ ننسى الحالة حتى الاتصال القادم.
                _counts.pop(key, None)
                continue
            if prev != n:
                to_push.append(username)
            _counts[key] = n
        # مشترك عُطّل تقسيمه/حُذف: أزل حالته (تعطيل التقسيم يُعاد تطبيقه من
        # مسار حفظ المشترك نفسه، لا من هنا).
        stale = [k for k in _counts
                 if k[0] == tenant_id and k[1] not in current]
        for k in stale:
            _counts.pop(k, None)

    if not to_push:
        return 0
    try:
        stats = apply_users_effective(tenant_id, to_push)
        _LOG.info("speed_split rebalance tenant=%s users=%s applied=%s",
                  tenant_id, to_push, stats.get("applied"))
        return int(stats.get("applied") or 0)
    except Exception:  # noqa: BLE001 — فشل الدفع لا يوقف العامل
        _LOG.exception("speed_split rebalance failed for %s", to_push)
        return 0


def tick_once() -> dict:
    """جولة واحدة عبر المستأجرين — عامّة import-safe للاختبارات."""
    pushed = 0
    for tenant_id in _all_tenants():
        try:
            pushed += _tenant_tick(tenant_id)
        except Exception:  # noqa: BLE001 — مستأجر لا يوقف البقيّة
            _LOG.exception("speed_split tick failed for tenant %s", tenant_id)
    return {"pushed": pushed}


def reset_state_for_tests() -> None:
    with _counts_lock:
        _counts.clear()


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("speed_split worker started — interval=%ds", interval_sec)
    while True:
        stats = {"pushed": 0}
        try:
            stats = tick_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("speed_split tick failed")
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_pushed": stats.get("pushed", 0)})
        time.sleep(interval_sec)


def start_speed_split_worker() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("speed_split worker disabled by HOBERADIUS_SPLIT_WORKER_ENABLED")
            return
        t = threading.Thread(
            target=_run_loop, kwargs={"interval_sec": _interval_sec()},
            daemon=True, name="hr-speed-split",
        )
        t.start()
        _started = True


__all__ = ["start_speed_split_worker", "tick_once", "reset_state_for_tests"]
