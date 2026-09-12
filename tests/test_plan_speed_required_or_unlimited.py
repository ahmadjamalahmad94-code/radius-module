# -*- coding: utf-8 -*-
"""«كيف يقبل النظامُ باقةً بلا سرعة؟» — لم يعُد.

الصفرُ الصامت كان يعني «مفتوح» على الراوتر (ردٌّ بلا Mikrotik-Rate-Limit ⇒
ملفُّ الهوت سبوت الافتراضيّ). عند «شركتي» باقةُ «Default service» ‏0/0 عليها
‏210 بطاقات — مفتوحةٌ بلا أن يعلم أحد.

الآن: الصفرُ يُرفض عند الحفظ ما لم تُعلَّم الباقةُ «بلا حدّ» صراحةً، وكلُّ
ردٍّ يخرج بلا سرعةٍ لباقةٍ غيرِ معلَّمة يُسجَّل ERROR بعلامة HR-NO-RATE.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_spd0_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _plan(**kw):
    from app.radius.core.types import AccessPlan
    base = dict(id=None, tenant_id=1, name="p", code="P1", plan_type="time",
                service_type="Hotspot", speed_down_kbps=4000, speed_up_kbps=2000)
    base.update(kw)
    return AccessPlan(**base)


def test_zero_speed_is_refused(app):
    with app.app_context():
        from app.radius.services.plans import _validate
        from app.radius.core.errors import RadiusValidationError
        for down, up in ((0, 2000), (4000, 0), (0, 0)):
            with pytest.raises(RadiusValidationError) as ei:
                _validate(_plan(speed_down_kbps=down, speed_up_kbps=up))
            assert "بلا حدّ" in str(ei.value)


def test_zero_speed_allowed_when_explicitly_unlimited(app):
    with app.app_context():
        from app.radius.services.plans import _validate
        _validate(_plan(speed_down_kbps=0, speed_up_kbps=0, speed_unlimited=True))


def test_flag_round_trips_through_repo(app):
    with app.app_context():
        from app.radius.db.repos import plans_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        saved = plans_repo.upsert_plan(_plan(name="مفتوحة", speed_down_kbps=0,
                                             speed_up_kbps=0, speed_unlimited=True))
        again = plans_repo.get_plan(1, saved.id)
        assert again.speed_unlimited is True


def test_rateless_accept_is_logged_loudly(app, caplog):
    """باقةٌ بصفرٍ (قديمةٌ، غيرُ معلَّمة) ⇒ الردُّ بلا سرعة ⇒ HR-NO-RATE."""
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import policy_engine as pe
        sub = Subscriber(id=1, username="u0", password="p", tenant_id=1, plan_id=7)
        plan = _plan(id=7, speed_down_kbps=0, speed_up_kbps=0)
        with caplog.at_level(logging.ERROR):
            out = pe._build_accept_attrs(sub, plan)
        assert not out.get("Mikrotik-Rate-Limit")
        assert any("HR-NO-RATE" in r.getMessage() for r in caplog.records)


def test_explicitly_unlimited_plan_is_silent(app, caplog):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import policy_engine as pe
        sub = Subscriber(id=1, username="u1", password="p", tenant_id=1, plan_id=8)
        plan = _plan(id=8, speed_down_kbps=0, speed_up_kbps=0, speed_unlimited=True)
        with caplog.at_level(logging.ERROR):
            pe._build_accept_attrs(sub, plan)
        assert not any("HR-NO-RATE" in r.getMessage() for r in caplog.records)


def test_normal_plan_is_silent(app, caplog):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services import policy_engine as pe
        sub = Subscriber(id=1, username="u2", password="p", tenant_id=1, plan_id=9)
        with caplog.at_level(logging.ERROR):
            out = pe._build_accept_attrs(sub, _plan(id=9))
        assert out.get("Mikrotik-Rate-Limit") == "2000k/4000k"
        assert not any("HR-NO-RATE" in r.getMessage() for r in caplog.records)
