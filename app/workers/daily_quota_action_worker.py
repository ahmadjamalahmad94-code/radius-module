"""daily_quota_action_worker — إنفاذُ قرارِ الكوتة اليوميّة على جلسةٍ حيّة.

المصادقةُ وحدَها لا تكفي: من بَلغ سقفَه اليوميّ **وهو متّصل** يَبقى على
سرعته الكاملة حتّى يُعيد الدخول. هذا العامل يُغلق الفجوة:

* نفدت كوتتُه اليوميّة وقرارُه «تخفيف/سرعة مخصّصة» ⇒ CoA بالسرعة الجديدة
  **مرّةً واحدة** (تُختَم الحالةُ ``throttled`` فلا يتكرّر الدفع).
* تجدَّدت كوتتُه (منتصفَ الليل أو بإضافةِ كوتة) وهو مختوم ⇒ CoA يُعيد
  سرعتَه الطبيعيّة ويُنظَّف الختم.
* قرارُ «قطع» لا يحتاج عاملًا: المحرّكُ يَرفض المصادقةَ، والمُصالِحُ
  الدوريّ يُغلق الجلسةَ — لا نُكرّر عملَه.

الاستعادةُ لا تعتمد على هذا العامل وحدَه: ردُّ القبول يَقصّ
Session-Timeout على ما تبقّى حتّى منتصفِ الليل، فلو تعطّل العاملُ أو سقط
CoA يَستعيد المشتركُ سرعتَه عند إعادة المصادقة على كلّ حال.

التعطيل: ``HOBERADIUS_DAILY_QUOTA_ACTION_ENABLED=0`` · المدّة:
``HOBERADIUS_DAILY_QUOTA_ACTION_INTERVAL_SEC`` (أدناها 15 ثانية).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "daily_quota_action"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 60
_MIN_INTERVAL = 15


def _interval_sec() -> int:
    try:
        return max(int(os.environ.get(
            "HOBERADIUS_DAILY_QUOTA_ACTION_INTERVAL_SEC", "")), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_DAILY_QUOTA_ACTION_ENABLED")
           or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001
        return [1]


def _online_usernames(tenant_id: int) -> list[str]:
    from app.radius.db.connection import db
    try:
        rows = db().execute(
            "SELECT DISTINCT username FROM radacct "
            " WHERE tenant_id = ? AND acctstoptime IS NULL",
            (int(tenant_id),)).fetchall()
        return [str(r["username"]) for r in rows if r["username"]]
    except Exception:  # noqa: BLE001
        return []


def _normal_rate(tenant_id: int, username: str) -> str:
    """السرعةُ الطبيعيّة (تجاوز/جدول/باقة) لاستعادتها بعد التجديد."""
    try:
        from app.radius.services import bandwidth_rate
        return bandwidth_rate.effective_rate_limit(int(tenant_id), str(username)) or ""
    except Exception:  # noqa: BLE001
        return ""


def _coa_rate(tenant_id: int, username: str, rate: str) -> None:
    try:
        from app.radius.integration import radius_coa
        radius_coa.change_user_rate(int(tenant_id), str(username),
                                    new_rate_limit=str(rate))
        _LOG.info("daily_quota_action: CoA سرعة %s ← %s", username, rate)
    except Exception:  # noqa: BLE001
        _LOG.exception("daily_quota_action: CoA فشل لـ%r", username)


def enforce_once() -> dict:
    """دورةٌ واحدة على كلّ المستأجرين. عامّةٌ كي تَستدعيها الاختبارات."""
    from app.radius.db.repos import plans_repo, subscribers_repo
    from app.radius.services import daily_quota_action as dqa
    from app.radius.services import quota_period

    stats = {"checked": 0, "throttled": 0, "restored": 0}
    for tenant_id in _all_tenants():
        for username in _online_usernames(tenant_id):
            try:
                sub = subscribers_repo.get_subscriber(tenant_id, username)
                if sub is None:
                    continue
                plan = (plans_repo.get_plan(tenant_id, int(sub.plan_id))
                        if getattr(sub, "plan_id", 0) else None)
                action = dqa.effective_action(sub, plan)
                state = str(getattr(sub, "daily_quota_state", "") or "")
                if action == dqa.ACTION_DISCONNECT and not state:
                    continue            # القطعُ شأنُ المحرّك والمُصالِح
                exhausted = quota_period.window_exhaustion(sub, plan) == "daily"
                stats["checked"] += 1

                if exhausted and action != dqa.ACTION_DISCONNECT:
                    if state != "throttled":
                        rate = dqa.throttle_rate_limit(sub, plan)
                        if rate:
                            _coa_rate(tenant_id, username, rate)
                            dqa.set_state(tenant_id, username, "throttled")
                            stats["throttled"] += 1
                elif state:
                    # تجدَّدت الكوتةُ (أو غُيّر القرار) ⇒ استعادةٌ مرّةً واحدة.
                    rate = _normal_rate(tenant_id, username)
                    if rate:
                        _coa_rate(tenant_id, username, rate)
                    dqa.set_state(tenant_id, username, "")
                    stats["restored"] += 1
            except Exception:  # noqa: BLE001 — مشتركٌ واحد لا يُوقف الدورة
                _LOG.exception("daily_quota_action: إنفاذٌ فشل لـ%r", username)
    return stats


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("daily_quota_action started — interval=%ds", interval_sec)
    while True:
        stats = {}
        try:
            stats = enforce_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("daily_quota_action tick failed")
        beat(_NAME, info={"interval_sec": interval_sec, **stats})
        time.sleep(interval_sec)


def start_daily_quota_action() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("daily_quota_action disabled by env")
            return
        t = threading.Thread(target=_run_loop,
                             kwargs={"interval_sec": _interval_sec()},
                             daemon=True, name="hr-daily-quota-action")
        t.start()
        _started = True


__all__ = ["start_daily_quota_action", "enforce_once"]
