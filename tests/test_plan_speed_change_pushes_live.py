# -*- coding: utf-8 -*-
"""تعديل سرعة العرض يصل الجلسات الحيّة فورًا (المحترف 2026-10-03).

«عدّلت سرعة العرض ما تطبّقت ع الميكروتك»: حفظ العرض كان يُعيد فحص الأيام
والكوتا فقط، فتبقى الجلسات المتّصلة على السرعة القديمة حتى تعيد الاتّصال.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_planspeed_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed():
    from app.radius.db.connection import transaction
    now = _dt.datetime.utcnow().isoformat() + "Z"
    sp = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as c:
        c.execute("INSERT INTO access_plans (tenant_id, name, created_at, speed_down_kbps,"
                  " speed_up_kbps) VALUES (1,'1DAY',?,1600,1600)", (now,))
        pid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO access_plans (tenant_id, name, created_at) VALUES (1,'other',?)", (now,))
        other = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO card_batches (tenant_id, batch_code, plan_id, count, generated,"
                  " created_at) VALUES (1,'B',?,2,2,?)", (pid, now))
        bid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        for u, plan, sess, open_ in (("sub-on", pid, "s1", True), ("sub-off", pid, "s2", False),
                                     ("sub-other", other, "s3", True)):
            c.execute("INSERT INTO subscribers (tenant_id, username, password, user_type, status,"
                      " plan_id, created_at) VALUES (1,?,'p','subscriber','enabled',?,?)", (u, plan, now))
            c.execute("INSERT INTO radacct (tenant_id, username, acctsessionid, acctuniqueid,"
                      " acctstarttime, acctstoptime) VALUES (1,?,?,?,?,?)",
                      (u, sess, sess, sp, None if open_ else sp))
        c.execute("INSERT INTO cards (tenant_id, batch_id, username, password, plan_id, used,"
                  " revoked, created_at) VALUES (1,?,'725563','p',?,1,0,?)", (bid, pid, now))
        c.execute("INSERT INTO radacct (tenant_id, username, acctsessionid, acctuniqueid,"
                  " acctstarttime) VALUES (1,'725563','s4','s4',?)", (sp,))
    return pid


def test_speed_change_pushes_coa_to_online_users_of_that_plan_only(app, monkeypatch):
    with app.app_context():
        pid = _seed()
        from app.radius.services import bandwidth_apply, policy_reconciler
        # no background reconcile thread racing the fixture's module cleanup
        monkeypatch.setattr(policy_reconciler, "reconcile_active_sessions_against_policy",
                            lambda *a, **k: None)
        pushed = []
        monkeypatch.setattr(bandwidth_apply, "apply_users_effective",
                            lambda t, names, **k: pushed.append(sorted(names)) or {})
        from app.radius.services.plans import get_plans_service as _g
        svc = _g()
        plan = svc.get(pid) if hasattr(svc, "get") else None
        assert plan is not None
        import dataclasses
        names = bandwidth_apply.online_usernames_on_plan(1, pid)
        assert sorted(names) == ["725563", "sub-on"]
        monkeypatch.setattr(bandwidth_apply, "push_plan_speed_live",
                            lambda t, p, **k: pushed.append(("push", p)) or [])
        svc.update(actor="t", plan=dataclasses.replace(plan, speed_down_kbps=3000))
        assert ("push", pid) in pushed


def test_no_push_when_speed_unchanged(app, monkeypatch):
    with app.app_context():
        pid = _seed()
        from app.radius.services import bandwidth_apply, policy_reconciler
        # no background reconcile thread racing the fixture's module cleanup
        monkeypatch.setattr(policy_reconciler, "reconcile_active_sessions_against_policy",
                            lambda *a, **k: None)
        pushed = []
        monkeypatch.setattr(bandwidth_apply, "push_plan_speed_live",
                            lambda t, p, **k: pushed.append(p) or [])
        from app.radius.services.plans import get_plans_service as _g
        svc = _g()
        import dataclasses
        plan = svc.get(pid)
        svc.update(actor="t", plan=dataclasses.replace(plan, price=9))
        assert pushed == []


def test_push_runs_coa_for_online_users(app, monkeypatch):
    with app.app_context():
        pid = _seed()
        from app.radius.services import bandwidth_apply
        got = []
        monkeypatch.setattr(bandwidth_apply, "apply_users_effective",
                            lambda t, names, **k: got.append(sorted(names)) or {})
        bandwidth_apply.push_plan_speed_live(1, pid, background=False)
        assert got == [["725563", "sub-on"]]
