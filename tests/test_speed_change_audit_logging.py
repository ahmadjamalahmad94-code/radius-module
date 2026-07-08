# -*- coding: utf-8 -*-
"""Speed changes must land in ALL THREE logs — manual AND scheduled.

Owner bug: neither the manual/temporary speed change nor the automatic
bandwidth-schedule change was recorded in any of:

  1. the unified MikroTik-actions feed (`mikrotik_actions.fetch_mikrotik_actions`),
  2. the per-manager audit log  (audit_log rows target_type='subscriber' — the
     «الأحداث الإداريّة» pane on the subscriber profile),
  3. the per-subscriber event timeline (same subscriber-targeted audit rows —
     the «أحداث المشترك» pane).

All three read `audit_log`; a speed change now writes ONE subscriber-targeted,
router-resolved row (via `mt_action_log.record_speed_change`) that every reader
surfaces. These tests assert that row exists for both a manual and a scheduled
change, and that the MikroTik-actions feed shows it with a resolved router.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_TEMP_SPEED_APPLY_MODE", raising=False)
    db_file = os.path.join(tempfile.mkdtemp(), f"spd_{uuid4().hex}.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        tenants_repo.set_setting(1, "billing.timezone", "UTC")
    return app


def _seed_common(app, *, username, plan_down=50000, plan_up=25000):
    """A plan (id=1), a subscriber on it, one live session, one router."""
    with app.app_context():
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute(
            "INSERT OR IGNORE INTO access_plans(id, tenant_id, name, "
            "speed_down_kbps, speed_up_kbps, created_at) VALUES (1,1,?,?,?,?)",
            ("Plan", plan_down, plan_up, now))
        db().execute(
            "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
            "created_at) VALUES (1,?,?,?,?)",
            (username, "pw", 1, now))
        db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
            "username, nasipaddress, framedipaddress, callingstationid, "
            "acctstarttime) VALUES (1,?,?,?,?,?,?,?)",
            (f"s_{username}", f"u_{username}", username, "10.10.0.9",
             "10.20.30.40", "AA:BB:CC:DD:EE:01", now))
        db().execute(
            "INSERT OR IGNORE INTO nas_devices(id, tenant_id, name, address, "
            "secret, vendor, nas_type, enabled, created_at) "
            "VALUES (1,1,?,?,?,?,?,1,?)",
            ("Main Router", "10.10.0.9", "sec12345", "mikrotik", "hotspot", now))
        db().commit()


def _seed_card(app, *, username, plan_down=50000, plan_up=25000,
               card_down=0, card_up=0):
    """A plan (id=1), a card on it (NO subscriber mirror), a live card session,
    a router. Mirrors the owner's real card targets (2183369 / 58997392)."""
    with app.app_context():
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute(
            "INSERT OR IGNORE INTO access_plans(id, tenant_id, name, "
            "speed_down_kbps, speed_up_kbps, created_at) VALUES (1,1,?,?,?,?)",
            ("Plan", plan_down, plan_up, now))
        db().execute(
            "INSERT OR IGNORE INTO card_batches(id, tenant_id, batch_code, "
            "package_name, plan_id, count, generated, created_at) "
            "VALUES (901,1,'B-CARD','Cards',1,1,1,?)", (now,))
        db().execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            "card_speed_down_kbps, card_speed_up_kbps, used, created_at) "
            "VALUES (1,901,?,?,1,?,?,0,?)",
            (username, "secret", card_down, card_up, now))
        db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
            "username, nasipaddress, framedipaddress, callingstationid, "
            "acctstarttime) VALUES (1,?,?,?,?,?,?,?)",
            (f"s_{username}", f"u_{username}", username, "10.10.0.9",
             "10.20.30.41", "AA:BB:CC:DD:EE:02", now))
        db().execute(
            "INSERT OR IGNORE INTO nas_devices(id, tenant_id, name, address, "
            "secret, vendor, nas_type, enabled, created_at) "
            "VALUES (1,1,?,?,?,?,?,1,?)",
            ("Main Router", "10.10.0.9", "sec12345", "mikrotik", "hotspot", now))
        db().commit()


def _stub_coa_ok(monkeypatch):
    """Make change_user_rate a no-UDP success so the audit records نجاح."""
    from app.radius.integration import radius_coa

    class _Res:
        ok = True
        code_name = "coa_ack"

    monkeypatch.setattr(radius_coa, "change_user_rate",
                        lambda *a, **k: _Res())


def _audit_rows(app, *, target_id, action):
    with app.app_context():
        from app.radius.db.connection import db
        return [dict(r) for r in db().execute(
            "SELECT * FROM audit_log WHERE tenant_id=1 AND target_type='subscriber' "
            "AND target_id=? AND action=? ORDER BY id DESC",
            (target_id, action)).fetchall()]


# ─────────────────────────── manual / temporary ───────────────────────────
def test_manual_temp_speed_writes_all_three_logs(app, monkeypatch):
    _seed_common(app, username="ahmad")
    _stub_coa_ok(monkeypatch)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        apply_temp_speed(tenant_id=1, actor="manager1", username="ahmad",
                         down_kbps=5000, up_kbps=2000, duration_minutes=30)

    # (2)+(3) manager audit log + subscriber timeline: ONE subscriber-targeted
    # row (both panes filter target_type='subscriber' AND target_id==username).
    rows = _audit_rows(app, target_id="ahmad", action="temporary_speed.apply")
    assert len(rows) == 1, "manual temp-speed must write a subscriber audit row"
    row = rows[0]
    assert row["result_status"] == "success"
    assert row["actor"] == "manager1"
    assert '"rate_limit"' in (row["before_json"] or "")   # old → new diff present
    assert "2000k/5000k" in (row["after_json"] or "")     # Rate-Limit is up/down
    assert row["router_id"] == 1                           # real router resolved

    # (1) MikroTik-actions feed shows it under «تغيير السرعة» with the router.
    with app.app_context():
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions
        feed = fetch_mikrotik_actions(1, section="speed")
    mine = [r for r in feed["rows"] if r["subject"] == "ahmad"]
    assert mine, "manual temp-speed must appear in the MikroTik-actions feed"
    assert mine[0]["router_ip"] == "10.10.0.9"
    assert mine[0]["ok"] is True


def test_manual_temp_speed_revert_writes_subscriber_row(app, monkeypatch):
    _seed_common(app, username="ahmad")
    _stub_coa_ok(monkeypatch)
    with app.app_context():
        from app.radius.services.temp_speed import (
            apply_temp_speed, cancel_temp_speed)
        apply_temp_speed(tenant_id=1, actor="manager1", username="ahmad",
                         down_kbps=5000, up_kbps=2000, duration_minutes=30)
        cancel_temp_speed(tenant_id=1, actor="manager1", username="ahmad")
    rows = _audit_rows(app, target_id="ahmad", action="temporary_speed.revert")
    assert len(rows) == 1, "revert must write a subscriber audit row too"


# ─────────────────────────── scheduled (worker) ───────────────────────────
def _mk_schedule(app, *, start, end):
    with app.app_context():
        from app.radius.services.operations import get_operations_service
        return get_operations_service().create_bandwidth_schedule(
            tenant_id=1, actor="t", data={
                "name": "Night", "target_type": "plan", "plan_id": 1,
                "priority": 5, "starts_at_time": start, "ends_at_time": end,
                "speed_down_kbps": 80000, "speed_up_kbps": 40000,
                "restore_mode": "profile_default", "enabled": True})


def test_scheduled_speed_change_writes_all_three_logs(app, monkeypatch):
    _seed_common(app, username="sara")
    _stub_coa_ok(monkeypatch)
    _mk_schedule(app, start="02:00", end="06:00")

    from app.workers import bandwidth_schedule_worker as w
    with app.app_context():
        w.reset_state_for_tests()
        # 02:00:30 is just inside the window → ENGAGE; a minute earlier (used for
        # the «previous» rate) is outside → base rate, so old != new.
        stats = w.tick_once(now=datetime(2026, 6, 28, 2, 0, 30))
    assert stats["engaged"] == 1

    # (2)+(3) subscriber-targeted row with a system actor.
    rows = _audit_rows(app, target_id="sara", action="bandwidth_schedule.engage")
    assert len(rows) == 1, "scheduled change must write a subscriber audit row"
    row = rows[0]
    assert row["actor"] == "system:scheduler"
    assert "80000k" in (row["after_json"] or "")          # schedule rate applied
    assert row["router_id"] == 1

    # (1) MikroTik-actions feed shows it as an automatic schedule speed change.
    with app.app_context():
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions
        feed = fetch_mikrotik_actions(1, section="speed")
    mine = [r for r in feed["rows"] if r["subject"] == "sara"]
    assert mine, "scheduled change must appear in the MikroTik-actions feed"
    assert mine[0]["action_label"] == "تغيير السرعة (جدولة تلقائية)"


# ─────────────────── CARD coverage (بطايق) — root-cause of 0/N ───────────────────
def test_effective_rate_limit_resolves_card_without_subscriber_mirror(app):
    """A live CARD (no subscriber-mirror row) must resolve a non-empty rate, else
    the CoA/scheduler computes '' and applies to 0 sessions (the «0/3» bug)."""
    _seed_card(app, username="2183369")           # plan 50000/25000, no override
    with app.app_context():
        from app.radius.services.bandwidth_rate import effective_rate_limit
        rate = effective_rate_limit(1, "2183369")
    assert rate == "25000k/50000k", f"card must resolve its plan rate, got {rate!r}"


def test_card_override_beats_plan_in_effective_rate(app):
    _seed_card(app, username="58997392", card_down=7680, card_up=3840)  # 7.5 Mbps
    with app.app_context():
        from app.radius.services.bandwidth_rate import effective_rate_limit
        rate = effective_rate_limit(1, "58997392")
    assert rate == "3840k/7680k", f"per-card override must win, got {rate!r}"


def test_online_scope_includes_live_card_on_selected_plan(app):
    """Scoped speed policy enumeration must target live CARD sessions too."""
    _seed_card(app, username="2183369")
    with app.app_context():
        from app.radius.services.operations_speed_center import (
            OperationsSpeedCenterService)
        scope = OperationsSpeedCenterService(tenant_id=1)._online_usernames_in_scope(
            profile_ids=[1])
    assert "2183369" in scope, "a live card on the selected plan must be in scope"


def test_scheduled_speed_change_reaches_card_users(app, monkeypatch):
    """The scheduled worker applies + logs the speed change for a CARD user."""
    _seed_card(app, username="2183369")
    _stub_coa_ok(monkeypatch)
    _mk_schedule(app, start="02:00", end="06:00")

    from app.workers import bandwidth_schedule_worker as w
    with app.app_context():
        w.reset_state_for_tests()
        stats = w.tick_once(now=datetime(2026, 6, 28, 2, 0, 30))
    assert stats["engaged"] == 1

    rows = _audit_rows(app, target_id="2183369", action="bandwidth_schedule.engage")
    assert len(rows) == 1, "card user must get the scheduled speed-change row"
    assert "80000k" in (rows[0]["after_json"] or "")

    with app.app_context():
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions
        feed = fetch_mikrotik_actions(1, section="speed")
    assert any(r["subject"] == "2183369" for r in feed["rows"]), \
        "card speed change must appear in the MikroTik-actions feed"


# ─────────────────── fallback: CoA fails → reauth so it actually applies ───────
def test_coa_failure_falls_back_to_reauth_disconnect(app, monkeypatch):
    """When the live rate-CoA is not ACK'd, a reauth-disconnect makes the new
    rate apply on re-auth, and «طُبّق X/N» counts it honestly (method=reauth)."""
    _seed_common(app, username="ahmad")
    from app.radius.integration import radius_coa

    class _Fail:
        ok = False
        code_name = "all_failed"

    class _Ok:
        ok = True
        code_name = "all_ok"

    monkeypatch.setattr(radius_coa, "change_user_rate", lambda *a, **k: _Fail())
    monkeypatch.setattr(radius_coa, "disconnect_user", lambda *a, **k: _Ok())
    with app.app_context():
        from app.radius.services.bandwidth_apply import apply_users_effective
        stats = apply_users_effective(1, ["ahmad"], fallback_disconnect=True)
    assert stats["applied"] == 1, "reauth fallback must count as applied"
    assert stats["results"][0]["method"] == "reauth"


def test_no_fallback_when_disabled(app, monkeypatch):
    _seed_common(app, username="ahmad")
    monkeypatch.setenv("HOBERADIUS_SPEED_COA_FALLBACK_DISCONNECT", "0")
    from app.radius.integration import radius_coa
    disc = {"called": False}

    class _Fail:
        ok = False
        code_name = "all_failed"

    def _disc(*a, **k):
        disc["called"] = True
        class _R: ok = True
        return _R()

    monkeypatch.setattr(radius_coa, "change_user_rate", lambda *a, **k: _Fail())
    monkeypatch.setattr(radius_coa, "disconnect_user", _disc)
    with app.app_context():
        from app.radius.services.bandwidth_apply import apply_users_effective
        stats = apply_users_effective(1, ["ahmad"], fallback_disconnect=True)
    assert stats["applied"] == 0
    assert disc["called"] is False, "kill-switch must suppress the reauth fallback"
