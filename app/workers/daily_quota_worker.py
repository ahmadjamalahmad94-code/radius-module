"""daily_quota_worker — إنفاذُ الكوتة اليوميّة على الجلسات الحيّة.

المصادقةُ وحدَها لا تكفي: المشترك الذي بَلغ سقفَه **وهو متّصل** يَبقى على
سرعته الكاملة حتّى يُعيد الدخول. هذا العامل يُغلق الفجوة:

* بَلغ السقفَ وقرارُه «قطع» ⇒ فصلُ الجلسة (ويَرفض المحرّكُ عودتَه).
* بَلغ السقفَ وقرارُه «تخفيف/سرعة مخصّصة» ⇒ CoA بالسرعة المخفَّضة **مرّةً
  واحدة** (تُختَم الحالةُ ``throttled`` فلا تتكرّر كلَّ دورة).
* تجدَّد يومُه (أو زِيد سقفُه أو صُفِّر عدّادُه) وهو مختوم ⇒ CoA يُعيد
  سرعتَه الطبيعيّة ويُنظَّف الختم.

الاستعادةُ لا تعتمد على هذا العامل وحدَه: ردُّ القبول يَقصّ Session-Timeout
على ما تبقّى حتّى منتصفِ الليل، فلو تعطّل العاملُ أو سقط CoA يَستعيد
المشتركُ سرعتَه عند إعادة المصادقة على كلّ حال.

التعطيل: ``HOBERADIUS_DAILY_QUOTA_ENABLED=0`` · المدّة:
``HOBERADIUS_DAILY_QUOTA_INTERVAL_SEC`` (أدناها 10 ثوانٍ).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "daily_quota"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 30
_MIN_INTERVAL = 10


def _interval_sec() -> int:
    try:
        return max(int(os.environ.get("HOBERADIUS_DAILY_QUOTA_INTERVAL_SEC", "")),
                   _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_DAILY_QUOTA_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001
        return [1]


def _online_usernames(tenant_id: int) -> list[str]:
    """أسماءُ من لهم جلسةٌ مفتوحة الآن (مصدرُ الحقيقة radacct)."""
    from app.radius.db.connection import db
    try:
        rows = db().execute(
            "SELECT DISTINCT username FROM radacct "
            " WHERE tenant_id = ? AND acctstoptime IS NULL",
            (int(tenant_id),)).fetchall()
        return [str(r["username"]) for r in rows if r["username"]]
    except Exception:  # noqa: BLE001
        return []


def _normal_rate(sub, plan) -> str:
    """السرعةُ الطبيعيّة (تجاوزُ المشترك ثمّ الباقة) لاستعادتها بعد التجديد."""
    try:
        from app.radius.services import bandwidth_rate
        rate = bandwidth_rate.effective_rate_limit(
            int(sub.tenant_id), str(sub.username))
        if rate:
            return rate
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.radius.services.live_session_control import build_mikrotik_rate_limit
        dn = int(getattr(sub, "download_speed_kbps", 0) or 0) or int(
            getattr(plan, "speed_down_kbps", 0) or 0)
        up = int(getattr(sub, "upload_speed_kbps", 0) or 0) or int(
            getattr(plan, "speed_up_kbps", 0) or 0)
        if dn > 0 and up > 0:
            return build_mikrotik_rate_limit(dn, up)
    except Exception:  # noqa: BLE001
        pass
    return ""


def enforce_once() -> dict:
    """دورةٌ واحدة على كلّ المستأجرين. عامّةٌ كي تَستدعيها الاختبارات."""
    from app.radius.db.repos import plans_repo, subscribers_repo
    from app.radius.services import daily_quota

    stats = {"checked": 0, "throttled": 0, "disconnected": 0, "restored": 0}
    for tenant_id in _all_tenants():
        for username in _online_usernames(tenant_id):
            try:
                sub = subscribers_repo.get_subscriber(tenant_id, username)
                if sub is None:
                    continue
                plan = (plans_repo.get_plan(tenant_id, int(sub.plan_id))
                        if getattr(sub, "plan_id", 0) else None)
                if daily_quota.effective_cap_mb(sub, plan) <= 0:
                    continue
                stats["checked"] += 1
                state = str(getattr(sub, "daily_quota_state", "") or "")
                exhausted = daily_quota.is_exhausted(sub, plan)

                if not exhausted:
                    # تجدَّد اليومُ أو زِيد السقفُ ⇒ استعادةُ السرعة مرّةً.
                    if state:
                        rate = _normal_rate(sub, plan)
                        if rate:
                            _coa_rate(tenant_id, username, rate)
                        daily_quota.set_state(tenant_id, username, "")
                        stats["restored"] += 1
                    continue

                action = daily_quota.effective_action(sub, plan)
                if action == daily_quota.ACTION_DISCONNECT:
                    if state != "blocked":
                        _coa_disconnect(tenant_id, username)
                        daily_quota.set_state(tenant_id, username, "blocked")
                        stats["disconnected"] += 1
                    continue
                if state != "throttled":
                    rate = daily_quota.throttle_rate_limit(sub, plan)
                    if rate:
                        _coa_rate(tenant_id, username, rate)
                        daily_quota.set_state(tenant_id, username, "throttled")
                        stats["throttled"] += 1
            except Exception:  # noqa: BLE001 — مشتركٌ واحد لا يُوقف الدورة
                _LOG.exception("daily_quota: إنفاذٌ فشل لـ%r", username)
    return stats


def _coa_rate(tenant_id: int, username: str, rate: str) -> None:
    try:
        from app.radius.integration import radius_coa
        radius_coa.change_user_rate(int(tenant_id), str(username),
                                    new_rate_limit=str(rate))
        _LOG.info("daily_quota: CoA سرعة %s ← %s", username, rate)
    except Exception:  # noqa: BLE001
        _LOG.exception("daily_quota: CoA السرعة فشل لـ%r", username)


def _coa_disconnect(tenant_id: int, username: str) -> None:
    try:
        from app.radius.integration import radius_coa
        radius_coa.disconnect_user(int(tenant_id), str(username))
        _LOG.info("daily_quota: فصلٌ لنفادِ الكوتة اليوميّة — %s", username)
    except Exception:  # noqa: BLE001
        _LOG.exception("daily_quota: الفصل فشل لـ%r", username)


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("daily_quota started — interval=%ds", interval_sec)
    while True:
        stats = {}
        try:
            stats = enforce_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("daily_quota tick failed")
        beat(_NAME, info={"interval_sec": interval_sec, **stats})
        time.sleep(interval_sec)


def start_daily_quota() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("daily_quota disabled by HOBERADIUS_DAILY_QUOTA_ENABLED")
            return
        t = threading.Thread(target=_run_loop,
                             kwargs={"interval_sec": _interval_sec()},
                             daemon=True, name="hr-daily-quota")
        t.start()
        _started = True


__all__ = ["start_daily_quota", "enforce_once"]
