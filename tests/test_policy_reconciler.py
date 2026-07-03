# -*- coding: utf-8 -*-
"""FIX B+E — «أي عملية حفظ يصير إعادة مطابقة وأي شي مش مطابق طرد».

One central hook (policy_reconciler.reconcile_active_sessions_against_policy)
re-runs the SAME policy_engine checks the RADIUS authorize path uses against
the ACTIVE sessions in scope after any access-rule save, and fires a RADIUS
Disconnect for every session that now violates policy — immediately, not at
next re-auth («عملت تعطيل ليوزر ما أرسل أمر قطع اتصال، ظل متصل»).

Covered here: disable-subscriber kicks (FIX B), plan day-schedule edit kicks
the violator, lowered device limit kicks the newest excess session, compliant
sessions untouched, save-sites wiring, and unreachable-NAS never errors.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_reconcile_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    yield created
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _now_iso(minutes_ago: int = 0) -> str:
    return (_dt.datetime.utcnow()
            - _dt.timedelta(minutes=minutes_ago)).isoformat() + "Z"


def _mk_plan(**kw):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    plan = AccessPlan(id=None, name=kw.pop("name", "خطة"), tenant_id=1, **kw)
    return plans_repo.upsert_plan(plan)


def _mk_sub(username, *, plan_id=None, status="enabled", **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    sub = Subscriber(id=None, username=username, password="p", tenant_id=1,
                     plan_id=plan_id, status=status, **kw)
    return subscribers_repo.upsert_subscriber(sub)


def _mk_session(username, sid, *, minutes_ago=0):
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, callingstationid, acctstarttime, acctupdatetime, "
        "acctstoptime) VALUES(1,?,?,?,?,?,?,?,NULL)",
        (sid, sid + "-u", username, "203.0.113.9", "AA:BB:CC:00:11:22",
         _now_iso(minutes_ago), _now_iso(minutes_ago)))


@pytest.fixture
def kicked(monkeypatch, app):
    """Spy replacing live_session_control.disconnect_live — records calls,
    returns ok. Patched on the module the reconciler resolves at runtime."""
    calls: list[dict] = []
    with app.app_context():
        from app.radius.services import live_session_control as lsc

        def _spy(*, tenant_id, username, session_id="", session_row=None):
            calls.append({"tenant_id": tenant_id, "username": username,
                          "session_id": session_id})
            return SimpleNamespace(ok=True)

        monkeypatch.setattr(lsc, "disconnect_live", _spy)
    return calls


def _run(usernames=None, plan_id=None):
    from app.radius.services.policy_reconciler import (
        reconcile_active_sessions_against_policy,
    )
    return reconcile_active_sessions_against_policy(
        1, usernames=usernames, plan_id=plan_id, background=False,
        reason="test")


# ───────────────────────── FIX B: disable → kick ─────────────────────────

def test_disabled_subscriber_live_session_kicked(app, kicked):
    with app.app_context():
        _mk_sub("ahmad", status="disabled")
        _mk_session("ahmad", "s1")
        stats = _run(usernames=["ahmad"])
    assert stats["violations"] == 1
    assert stats["disconnected"] == 1
    assert kicked and kicked[0]["username"] == "ahmad"
    assert kicked[0]["session_id"] == "s1"


def test_compliant_session_untouched(app, kicked):
    with app.app_context():
        _mk_sub("ali", status="enabled")
        _mk_session("ali", "s2")
        stats = _run(usernames=["ali"])
    assert stats == {"checked": 1, "violations": 0,
                     "disconnected": 0, "failed": 0}
    assert kicked == []


# ───────────────── FIX E: plan schedule edit → kick violators ─────────────

def test_plan_day_restriction_kicks_current_session(app, kicked):
    """The owner's scenario: the plan is edited so TODAY is no longer an
    allowed day → the active session on that plan is disconnected on save."""
    with app.app_context():
        day_map = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        today = day_map[_dt.datetime.utcnow().weekday()]
        allowed = tuple(d for d in day_map if d != today)
        plan = _mk_plan(name="عرض الجمعة", allowed_days=allowed)
        _mk_sub("omar", plan_id=plan.id)
        _mk_session("omar", "s3")
        stats = _run(plan_id=plan.id)
    assert stats["violations"] == 1 and stats["disconnected"] == 1
    assert kicked[0]["username"] == "omar"


def test_plan_edit_leaves_compliant_plan_users_alone(app, kicked):
    with app.app_context():
        plan = _mk_plan(name="عرض مفتوح")           # all days allowed
        _mk_sub("sara", plan_id=plan.id)
        _mk_session("sara", "s4")
        stats = _run(plan_id=plan.id)
    assert stats["violations"] == 0
    assert kicked == []


# ───────────────── FIX E: device-limit lowered → kick newest ──────────────

def test_device_limit_lowered_kicks_newest_excess_only(app, kicked):
    """override_concurrent=1 with two live sessions → exactly the NEWER
    session is kicked; the oldest stays within the limit."""
    with app.app_context():
        _mk_sub("badr", override_concurrent=1)
        _mk_session("badr", "old-s", minutes_ago=10)
        _mk_session("badr", "new-s", minutes_ago=1)
        stats = _run(usernames=["badr"])
    assert stats["violations"] == 1 and stats["disconnected"] == 1
    assert [c["session_id"] for c in kicked] == ["new-s"]


# ───────────────────────── robustness ─────────────────────────────────────

def test_unreachable_nas_never_errors_the_run(app, monkeypatch):
    """PoD delivery failing (unreachable NAS / socket error) must not raise —
    counted as failed, the run completes."""
    with app.app_context():
        from app.radius.services import live_session_control as lsc

        def _boom(**_kw):
            raise OSError("NAS unreachable")

        monkeypatch.setattr(lsc, "disconnect_live", _boom)
        _mk_sub("mona", status="disabled")
        _mk_session("mona", "s5")
        stats = _run(usernames=["mona"])
    assert stats["violations"] == 1
    assert stats["failed"] == 1
    assert stats["disconnected"] == 0


# ───────────────────────── save-sites wiring ──────────────────────────────

def test_users_disable_invokes_hook_with_username_scope(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_reconciler as pr
        seen: list[dict] = []
        monkeypatch.setattr(
            pr, "reconcile_active_sessions_against_policy",
            lambda tid, **kw: seen.append({"tid": tid, **kw}))
        _mk_sub("khaled")
        from app.radius.services.users import get_users_service
        get_users_service().disable(actor="t", username="khaled")
    assert seen and seen[0]["usernames"] == ["khaled"]
    assert seen[0]["reason"] == "subscriber_disable"


def test_plans_update_invokes_hook_with_plan_scope(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_reconciler as pr
        seen: list[dict] = []
        monkeypatch.setattr(
            pr, "reconcile_active_sessions_against_policy",
            lambda tid, **kw: seen.append({"tid": tid, **kw}))
        plan = _mk_plan(name="سيُعدَّل")
        from dataclasses import replace
        from app.radius.services.plans import get_plans_service
        get_plans_service().update(actor="t",
                                   plan=replace(plan, name="عُدِّل"))
    assert seen and seen[0]["plan_id"] == plan.id
    assert seen[0]["reason"] == "plan_update"


def _mk_card(*, plan_id, username="polrec-card"):
    """A real card row (batch + card) so card-service save sites can act on it."""
    from app.radius.db.connection import transaction
    with transaction() as c:
        bid = c.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
            "created_at) VALUES (1,?,?,?,?)",
            (f"b-{username}", plan_id, 1, _now_iso())).lastrowid
        cid = c.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, "
            "plan_id, created_at) VALUES (1,?,?,?,?,?)",
            (bid, username, "pw", plan_id, _now_iso())).lastrowid
    return bid, cid


def test_card_revoke_invokes_hook_with_username_scope(app, monkeypatch):
    with app.app_context():
        from app.radius.services import policy_reconciler as pr
        seen: list[dict] = []
        monkeypatch.setattr(
            pr, "reconcile_active_sessions_against_policy",
            lambda tid, **kw: seen.append({"tid": tid, **kw}))
        plan = _mk_plan(name="خطة بطاقة")
        _bid, cid = _mk_card(plan_id=plan.id)
        from app.radius.services.cards import get_cards_service
        get_cards_service().revoke_card(actor="t", card_id=cid)
    assert seen and seen[0]["usernames"] == ["polrec-card"]
    assert seen[0]["reason"] == "card_revoke"


def test_batch_scope_checks_only_that_batchs_cards(app, kicked):
    """batch_id scope: revoked card of THIS batch is kicked; a live card of
    another batch is not even checked."""
    with app.app_context():
        from app.radius.db.connection import db
        plan = _mk_plan(name="خطة الحزم")
        bid1, _cid1 = _mk_card(plan_id=plan.id, username="batch1-card")
        _bid2, _cid2 = _mk_card(plan_id=plan.id, username="batch2-card")
        # بطاقة الحزمة الأولى أُلغيت (revoked → غير ممتثلة)؛ الثانية سليمة.
        db().execute("UPDATE cards SET revoked=1 WHERE username='batch1-card'")
        _mk_session("batch1-card", "s-b1")
        _mk_session("batch2-card", "s-b2")
        from app.radius.services.policy_reconciler import (
            reconcile_active_sessions_against_policy,
        )
        stats = reconcile_active_sessions_against_policy(
            1, batch_id=bid1, background=False, reason="test")
    assert stats["checked"] == 1
    assert [c["session_id"] for c in kicked] == ["s-b1"]


def test_access_block_save_invokes_hook_scoped_to_subscriber(app, monkeypatch):
    """Saving a subscriber-scoped access block re-checks that subscriber's
    sessions (scope-aware), not the whole tenant."""
    with app.app_context():
        from app.radius.services import policy_reconciler as pr
        seen: list[dict] = []
        monkeypatch.setattr(
            pr, "reconcile_active_sessions_against_policy",
            lambda tid, **kw: seen.append({"tid": tid, **kw}))
        from app.radius.services import access_control as ac
        ac.create_block_from_input(
            tenant_id=1, block_type="subscriber", target="blocked-user",
            reason="test", duration_mode="permanent")
    assert seen and seen[0]["usernames"] == ["blocked-user"]
    assert seen[0]["reason"] == "access_block:subscriber"


def test_save_survives_hook_failure(app, monkeypatch):
    """Enforcement must never break the save: even if the hook itself raises,
    disable() completes and the subscriber ends up disabled."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import policy_reconciler as pr

        def _boom(*a, **kw):
            raise RuntimeError("hook exploded")

        monkeypatch.setattr(
            pr, "reconcile_active_sessions_against_policy", _boom)
        _mk_sub("safe-user")
        from app.radius.services.users import get_users_service
        get_users_service().disable(actor="t", username="safe-user")
        row = db().execute(
            "SELECT status FROM subscribers WHERE username='safe-user'"
        ).fetchone()
    assert row["status"] == "disabled"
