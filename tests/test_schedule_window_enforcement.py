"""Enforce connection-schedule / allowed-hours windows on ALREADY-ACTIVE sessions.

Root cause the fix addresses: the schedule/allowed-hours window was checked ONLY
at RADIUS authorize (a new login). A session opened INSIDE the window stayed
online indefinitely after the window closed — nothing terminated a live session
at the boundary, and the plan/offer hour check compared against UTC (not the
tenant-local timezone) and ignored the offer's «ساعات العرض».

Covered here:
  1. seconds_until_window_end → Session-Timeout at authorize (login 03:30,
     cutoff 04:00 → 1800), and it caps an already-present timeout.
  2. is_out_of_window / effective_schedule: window ends 04:00, now 08:00 → out;
     inside → not out; no schedule → never out; plan offer-hours respected.
  3. Timezone: the plan/offer hour window is compared in tenant-local time
     (UTC+3), not UTC.
  4. enforce_active_session_windows: an out-of-window live session is
     CoA-disconnected with reason «خارج وقت السماح»; an in-window one is left
     alone; a no-schedule (unlimited) one is left alone.
  5. authorize sets Session-Timeout to seconds-to-window-end.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_TZ3 = timezone(timedelta(hours=3))   # tenant-local (UTC+3, panel default)


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_test_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def _win(from_hm, to_hm, days=None):
    return json.dumps({"windows": [
        {"days": days or [], "from": from_hm, "to": to_hm}]})


def _seed_live_session(tenant_id, username, *, session_id="sess-1"):
    """One live radacct row (open, fresh update time so it counts as live)."""
    from app.radius.db.connection import transaction
    ts = datetime.utcnow().isoformat() + "Z"
    with transaction() as conn:
        conn.execute(
            "INSERT INTO radacct(tenant_id, username, acctsessionid, "
            "acctstarttime, acctupdatetime, acctsessiontime, callingstationid, "
            "nasipaddress, acctinputoctets, acctoutputoctets) "
            "VALUES(?,?,?,?,?,?,?,?,0,0)",
            (tenant_id, username, session_id, ts, ts, 60, "AA:BB:CC:DD:EE:01",
             "10.0.0.1"),
        )


class _FakeOutcome:
    def __init__(self, ok=True, nas_ip="10.0.0.1"):
        self.ok = ok
        self.nas_ip = nas_ip
        self.reply_message = ""
        self.code_name = ""


# ───────────────────────── 1. seconds_until_window_end ─────────────────────


def test_seconds_to_window_end_matches_boundary():
    """Login 03:30 with a 04:00 cutoff → Session-Timeout seconds = 1800."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import schedule_window as sw

        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", connection_schedule=_win("00:00", "04:00"))
        now = datetime(2026, 7, 8, 3, 30, tzinfo=_TZ3)
        secs = sw.seconds_until_window_end(sub, None, now)
        assert secs == 1800, secs


def test_seconds_none_when_no_schedule():
    """No schedule (unlimited) → no schedule-based Session-Timeout."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import schedule_window as sw

        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled")
        now = datetime(2026, 7, 8, 3, 30, tzinfo=_TZ3)
        assert sw.seconds_until_window_end(sub, None, now) is None


# ───────────────────────── 2. is_out_of_window ─────────────────────────────


def test_out_of_window_after_cutoff():
    """Window ends 04:00; at 08:00 the session is OUT of window."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import schedule_window as sw

        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", connection_schedule=_win("00:00", "04:00"))
        assert sw.is_out_of_window(sub, None,
                                   datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3)) is True
        assert sw.is_out_of_window(sub, None,
                                   datetime(2026, 7, 8, 3, 0, tzinfo=_TZ3)) is False


def test_no_schedule_never_out_of_window():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import schedule_window as sw

        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled")
        assert sw.is_out_of_window(sub, None,
                                   datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3)) is False


def test_offer_hours_from_plan_respected():
    """The offer window «ساعات العرض من–إلى» (offer_hours_*) on the plan governs
    a subscriber that has no personal schedule."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.services import schedule_window as sw

        plan = AccessPlan(id=1, tenant_id=1, name="Algerian",
                          offer_hours_from="20:00", offer_hours_to="04:00")
        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", plan_id=1)
        # 20:00–04:00 wraps midnight → 08:00 is OUT, 22:00 is IN.
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3)) is True
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 22, 0, tzinfo=_TZ3)) is False


def test_subscriber_schedule_overrides_plan_window():
    """A personal schedule fully overrides the plan/offer window."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.services import schedule_window as sw

        plan = AccessPlan(id=1, tenant_id=1, name="p",
                          offer_hours_from="20:00", offer_hours_to="04:00")
        # personal window allows all day → in-window even at 08:00.
        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", plan_id=1,
                         connection_schedule=_win("00:00", ""))
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3)) is False


# ───────────────────────── 3. timezone (UTC+3) ─────────────────────────────


def test_plan_hours_compared_in_local_tz_not_utc(monkeypatch):
    """Offer hours 06:00–07:00: at local 06:30 (UTC 03:30) authorize ACCEPTS —
    proving the window is compared in tenant-local time, not UTC (a UTC
    comparison would see 03:30 and reject)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core import system_config
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Morning", plan_type="time",
            offer_hours_from="06:00", offer_hours_to="07:00", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="tzs", password="p",
            status="enabled", plan_id=plan.id))

        monkeypatch.setattr(system_config, "local_now",
                            lambda *_a, **_k: datetime(2026, 7, 8, 6, 30, tzinfo=_TZ3))
        d = authorize(AuthRequest(username="tzs", password="p", tenant_id=1))
        assert d.ok is True, f"{d.reason}: {d.message}"

        monkeypatch.setattr(system_config, "local_now",
                            lambda *_a, **_k: datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3))
        d2 = authorize(AuthRequest(username="tzs", password="p", tenant_id=1))
        assert d2.ok is False and d2.reason == "outside_hours", d2.reason


# ───────────────────────── 4. active-session sweep ─────────────────────────


def test_sweep_disconnects_out_of_window_session(monkeypatch):
    """A live session whose window closed (ends 04:00, now 08:00) is
    CoA-disconnected with reason «خارج وقت السماح»; an in-window session and a
    no-schedule (unlimited) session are left alone."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core import system_config
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import live_session_control as lsc
        from app.radius.services import schedule_window as sw
        from app.radius.services.mikrotik_actions import disconnect_reason_label

        # ends at 04:00 → out of window at 08:00
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="out", password="p",
            status="enabled", connection_schedule=_win("00:00", "04:00")))
        _seed_live_session(1, "out", session_id="sess-out")
        # covers 08:00 → in window
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="inw", password="p",
            status="enabled", connection_schedule=_win("06:00", "20:00")))
        _seed_live_session(1, "inw", session_id="sess-in")
        # no schedule → unlimited, never touched
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="free", password="p",
            status="enabled"))
        _seed_live_session(1, "free", session_id="sess-free")

        monkeypatch.setattr(system_config, "local_now",
                            lambda *_a, **_k: datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3))
        killed = []
        monkeypatch.setattr(
            lsc, "disconnect_live",
            lambda **kw: (killed.append(kw), _FakeOutcome(ok=True))[1])

        stats = sw.enforce_active_session_windows(tenant_id=1)

        assert stats["out_of_window"] == 1, stats
        assert stats["disconnected"] == 1, stats
        assert [k["username"] for k in killed] == ["out"], killed
        assert killed[0]["session_id"] == "sess-out"
        # the reason surfaced to the operator feed maps to «خارج وقت السماح».
        assert disconnect_reason_label("out_of_window") == "خارج وقت السماح"


# ───────────────────────── 5. authorize Session-Timeout ────────────────────


def test_authorize_sets_session_timeout_to_window_end(monkeypatch):
    """A subscriber inside a window that ends at 04:00, authorizing at 03:30,
    gets Session-Timeout = 1800 (seconds to the boundary)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core import system_config
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="st", password="p",
            status="enabled", connection_schedule=_win("00:00", "04:00")))
        monkeypatch.setattr(system_config, "local_now",
                            lambda *_a, **_k: datetime(2026, 7, 8, 3, 30, tzinfo=_TZ3))
        d = authorize(AuthRequest(username="st", password="p", tenant_id=1))
        assert d.ok is True, f"{d.reason}: {d.message}"
        assert d.reply_attrs.get("Session-Timeout") == "1800", d.reply_attrs
