"""Enforce connection-schedule / allowed-hours windows on ALREADY-ACTIVE sessions.

Root cause the fix addresses: the schedule/allowed-hours window was checked ONLY
at RADIUS authorize (a new login). A session opened INSIDE the window stayed
online indefinitely after the window closed — nothing terminated a live session
at the boundary, and the plan/offer hour check compared against UTC (not the
tenant-local timezone) and ignored the offer's «ساعات العرض».

Covered here:
  1. seconds_until_window_end → Session-Timeout at authorize (login 03:30,
     cutoff 04:00 → 1800), and it caps an already-present timeout.
  2. is_out_of_window (schedule ∩ offer-hours): window ends 04:00, now 08:00 → out;
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


def test_offer_hours_intersect_subscriber_schedule():
    """Offer-hours and the subscriber schedule INTERSECT (both must allow) — a
    personal all-day schedule does NOT hide the plan's offer-hours window. This
    is the core fix: previously the subscriber schedule overrode the plan
    entirely, so a 07:00/08:00 login against a 04:00 offer cutoff was accepted."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.services import schedule_window as sw

        plan = AccessPlan(id=1, tenant_id=1, name="p",
                          offer_hours_from="20:00", offer_hours_to="04:00")
        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", plan_id=1,
                         connection_schedule=_win("00:00", ""))   # personal: all day
        # offer 20:00–04:00 still bites at 08:00 despite the all-day schedule …
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 8, 0, tzinfo=_TZ3)) is True
        # … and both allow at 22:00 → in window.
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 22, 0, tzinfo=_TZ3)) is False


def test_half_open_offer_window_enforced():
    """A half-open offer window (only «إلى 04:00», «من» blank) is enforced —
    00:00→04:00 → OUT at 07:00, IN at 02:00. Previously ignored (both bounds
    were required), which let a 07:00 login through."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.services import schedule_window as sw

        plan = AccessPlan(id=1, tenant_id=1, name="p",
                          offer_hours_from="", offer_hours_to="04:00")
        sub = Subscriber(id=None, tenant_id=1, username="s", password="p",
                         status="enabled", plan_id=1)
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 7, 0, tzinfo=_TZ3)) is True
        assert sw.is_out_of_window(sub, plan,
                                   datetime(2026, 7, 8, 2, 0, tzinfo=_TZ3)) is False


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
        assert d2.ok is False and d2.reason == "out_of_window", d2.reason
        assert d2.message.startswith("خارج وقت السماح"), d2.message


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


# ─────────────── 6. authorize REJECT for out-of-window logins ───────────────


def _authorize_at(monkeypatch, username, local_dt):
    from app.radius.core import system_config
    from app.radius.services.policy_engine import AuthRequest, authorize
    monkeypatch.setattr(system_config, "local_now", lambda *_a, **_k: local_dt)
    return authorize(AuthRequest(username=username, password="p", tenant_id=1))


def test_authorize_rejects_offer_hours_login_at_0700(monkeypatch):
    """The owner's exact scenario: offer «ساعات العرض» ends 04:00; a NEW login at
    07:00 is DENIED with «خارج وقت السماح» (Access-Reject) — not accepted."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Algerian", plan_type="time",
            offer_hours_from="20:00", offer_hours_to="04:00", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="off", password="p",
            status="enabled", plan_id=plan.id))
        d = _authorize_at(monkeypatch, "off",
                          datetime(2026, 7, 8, 7, 0, tzinfo=_TZ3))
        assert d.ok is False and d.reason == "out_of_window", d.reason
        assert d.message.startswith("خارج وقت السماح"), d.message


def test_authorize_rejects_offer_hours_even_with_subscriber_schedule(monkeypatch):
    """Offer 20:00–04:00 + a subscriber all-day connection_schedule: a 07:00
    login is STILL denied (intersection — the schedule does not hide the offer)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="AlgerianSched", plan_type="time",
            offer_hours_from="20:00", offer_hours_to="04:00", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="offsch", password="p",
            status="enabled", plan_id=plan.id,
            connection_schedule=_win("00:00", "")))   # personal: all day
        d = _authorize_at(monkeypatch, "offsch",
                          datetime(2026, 7, 8, 7, 0, tzinfo=_TZ3))
        assert d.ok is False and d.reason == "out_of_window", d.reason


def test_authorize_rejects_connection_schedule_login_at_0700(monkeypatch):
    """Same via the «الجدولة» source: a subscriber connection_schedule window
    ending 04:00 → a 07:00 login is denied (Access-Reject)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="sch", password="p",
            status="enabled", connection_schedule=_win("00:00", "04:00")))
        d = _authorize_at(monkeypatch, "sch",
                          datetime(2026, 7, 8, 7, 0, tzinfo=_TZ3))
        assert d.ok is False, f"{d.reason}: {d.message}"
        assert d.reason == "outside_schedule", d.reason


def test_authorize_accepts_inside_offer_window_with_session_timeout(monkeypatch):
    """A login INSIDE the offer window (03:30, cutoff 04:00) is accepted and
    carries Session-Timeout = seconds to the window end (1800)."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="AlgIn", plan_type="time",
            offer_hours_from="20:00", offer_hours_to="04:00", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ins", password="p",
            status="enabled", plan_id=plan.id))
        d = _authorize_at(monkeypatch, "ins",
                          datetime(2026, 7, 8, 3, 30, tzinfo=_TZ3))
        assert d.ok is True, f"{d.reason}: {d.message}"
        assert d.reply_attrs.get("Session-Timeout") == "1800", d.reply_attrs


def test_overnight_offer_window_accepts_late_night(monkeypatch):
    """Overnight offer window 22:00→04:00 accepts a 23:00 login (crosses
    midnight correctly) and rejects a 12:00 login."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Night", plan_type="time",
            offer_hours_from="22:00", offer_hours_to="04:00", enabled=True))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ni", password="p",
            status="enabled", plan_id=plan.id))
        assert _authorize_at(monkeypatch, "ni",
                             datetime(2026, 7, 8, 23, 0, tzinfo=_TZ3)).ok is True
        assert _authorize_at(monkeypatch, "ni",
                             datetime(2026, 7, 8, 12, 0, tzinfo=_TZ3)).ok is False
