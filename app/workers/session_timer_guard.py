"""حارسُ عدّاد الجلسة — عاملٌ دوريّ.

يقرأ ولا يلمس افتراضًا. التفاصيلُ ولماذا يلزم أصلًا في
``app/radius/services/session_timer_guard.py``.

ـ يُبدأ مرّة واحدة من _start_workers في app/__init__.py ـ
"""
from __future__ import annotations

import logging
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "session_timer_guard"

_started = False
_started_lock = threading.Lock()

#: 🆕 السقفُ الأقصى بين الفحوص (لا فترةٌ ثابتة). العاملُ ينام **تكيّفيًّا**
#: حتى أقربِ لحظةِ انتهاءٍ فيَطرد المتأثّرَ في وقته بالضبط (جدولةٌ دقيقة)،
#: ولا يتجاوز هذا السقفَ كي يلتقطَ تغييراتِ الباقات/المجموعات خلال ≤20 ثانية.
_DEFAULT_INTERVAL_SEC = 20.0


def _interval() -> float:
    """السقفُ الأقصى بين الفحوص (ثوانٍ) — لا فترةٌ ثابتة (النومُ تكيّفيّ)."""
    from app.radius.core import env_settings
    try:
        v = float(str(env_settings.env(
            "HOBERADIUS_SESSION_TIMER_GUARD_SEC",
            str(_DEFAULT_INTERVAL_SEC))).strip() or _DEFAULT_INTERVAL_SEC)
        return max(5.0, v)
    except Exception:  # noqa: BLE001
        return _DEFAULT_INTERVAL_SEC


def _run_loop(*, interval_sec: float) -> None:
    from app.radius.services import session_timer_guard as g
    _LOG.info("session_timer_guard started — نومٌ تكيّفيّ · السقف=%.0fs · enforce=%s",
              interval_sec, g.enforce_enabled())
    while True:
        rep = {}
        try:
            rep = g.audit(1)
            found = (rep.get(g.KIND_MISSING, 0) + rep.get(g.KIND_OVER, 0)
                     + rep.get(g.KIND_EXPIRED_LIVE, 0))
            if found:
                _LOG.warning(
                    "session_timer_guard: %d تفاوتًا من %d جلسة "
                    "(بلا عدّاد=%d · أطول=%d · منتهية=%d · طُرد=%d)",
                    found, rep.get("checked", 0), rep.get(g.KIND_MISSING, 0),
                    rep.get(g.KIND_OVER, 0), rep.get(g.KIND_EXPIRED_LIVE, 0),
                    rep.get("kicked", 0))
                for d in rep.get("details", [])[:10]:
                    _LOG.warning("   %s · %s · راوتر=%s · الراوتر=%s · نحن=%ds",
                                 d.get("user"), d.get("kind"), d.get("nas"),
                                 d.get("router_left") or "لا شيء",
                                 d.get("ours_left_sec"))
            else:
                _LOG.info("session_timer_guard: %d جلسةً مفحوصة · لا تفاوت",
                          rep.get("checked", 0))
        except Exception:  # noqa: BLE001
            _LOG.exception("session_timer_guard tick failed")
        beat(_NAME, info={
            "interval_sec": interval_sec,
            "checked": rep.get("checked", 0),
            "missing": rep.get("missing", 0),
            "over": rep.get("over", 0),
            "expired": rep.get("expired", 0),
            "kicked": rep.get("kicked", 0),
            "next_secs": rep.get("next_secs"),
        })
        # ── نومٌ تكيّفيّ: حتى أقربِ انتهاءٍ بالضبط (+ثانيةُ هامش) دون تجاوزِ
        #    السقف. فمشتركٌ ينتهي بعد 47 ثانية ⇒ نستيقظ عند الـ48 ونطرده فورًا،
        #    ولا انتظارَ دورةٍ كاملة. وغيابُ انتهاءٍ قريب ⇒ ننام السقفَ لالتقاطِ
        #    تغييراتِ الباقات/المجموعات. ──
        nxt = rep.get("next_secs")
        if nxt is None:
            sleep_for = interval_sec
        else:
            sleep_for = min(float(nxt) + 1.0, interval_sec)
        time.sleep(max(3.0, sleep_for))


def start_session_timer_guard(*, interval_sec: float | None = None) -> None:
    global _started
    with _started_lock:
        if _started:
            return
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval_sec or _interval()},
            daemon=True, name="hr-session-timer-guard",
        )
        t.start()
        _started = True
