"""Speed-control «تطبيق فعليّ»: a chosen preset now actually changes speed.

Before this, the speed-control center only saved a preview policy and sent no
CoA — picking a preset changed nothing. Now applying a policy stores an active
per-tenant factor that feeds bandwidth_rate.effective_rate_limit (so new/CoA'd
sessions get the reduced/boosted rate), and «الوضع الطبيعي» (100%) clears it.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_speedapply_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_apply_active_factor_math(app):
    """المُحلِّل يضرب «Uk/Dk» بالمعامل، يحترم النطاق ويُبقي ذيل الburst."""
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services import bandwidth_rate as br
        S = lambda v: tenants_repo.set_setting(1, "speed.active_factors", v)

        # لا سياسة نشطة → بلا تغيير
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "1000k/2000k"
        # 70% عامّ
        S('{"multiplier":0.7,"overrides":{},"profile_ids":[]}')
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "700k/1400k"
        # تجاوز لكلّ باقة (50% لهذه الباقة)
        S('{"multiplier":1.0,"overrides":{"5":{"down":0.5,"up":0.5}},"profile_ids":[]}')
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "500k/1000k"
        # باقة خارج النطاق (profile_ids=[99]) → بلا تغيير
        S('{"multiplier":0.5,"overrides":{},"profile_ids":[99]}')
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "1000k/2000k"
        # ذيل الburst يبقى كما هو، والمعدّل الأساسيّ فقط يُضرب
        S('{"multiplier":0.5,"overrides":{},"profile_ids":[]}')
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k 4000k/8000k") \
            == "500k/1000k 4000k/8000k"
        # 100% = بلا تغيير
        S('{"multiplier":1.0,"overrides":{},"profile_ids":[]}')
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "1000k/2000k"
        # مسح → بلا تغيير
        S("")
        assert br._apply_active_speed_factor(1, 5, "1000k/2000k") == "1000k/2000k"


def test_apply_speed_policy_sets_and_clears_active_factor(app):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services.operations_speed_center import OperationsSpeedCenterService
        svc = OperationsSpeedCenterService(tenant_id=1)

        # تطبيق «الضغط» (0.7) → يُخزّن المعامل النشِط
        svc.apply_speed_policy(preset="pressure", multiplier=0.7, actor="tester")
        raw = tenants_repo.get_setting(1, "speed.active_factors", "")
        assert raw and '"multiplier": 0.7' in raw

        # تطبيق «الطبيعي» (1.0 بلا تخصيص) → يمسح المعامل
        res = svc.apply_speed_policy(preset="normal", multiplier=1.0, actor="tester")
        assert res["reset"] is True
        assert tenants_repo.get_setting(1, "speed.active_factors", "") == ""


def test_effective_rate_reflects_active_factor(app):
    """التكامل: مشترك على باقة → السرعة الفعليّة تنخفض بعد التطبيق وتعود بعد التصفير."""
    with app.app_context():
        from app.radius.db.repos import tenants_repo, plans_repo, subscribers_repo
        from app.radius.core.types import Subscriber, AccessPlan
        from app.radius.services import bandwidth_rate as br
        from app.radius.services.operations_speed_center import OperationsSpeedCenterService

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="P10", speed_down_kbps=2000, speed_up_kbps=1000))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="u1", password="x",
            user_type="subscriber", status="enabled", plan_id=plan.id))

        base = br.effective_rate_limit(1, "u1")
        assert base  # e.g. "1000k/2000k"

        svc = OperationsSpeedCenterService(tenant_id=1)
        svc.apply_speed_policy(preset="pressure", multiplier=0.7, actor="t")
        reduced = br.effective_rate_limit(1, "u1")
        # up/down each ~70% of base
        assert reduced == "700k/1400k", reduced

        svc.apply_speed_policy(preset="normal", multiplier=1.0, actor="t")
        assert br.effective_rate_limit(1, "u1") == base
