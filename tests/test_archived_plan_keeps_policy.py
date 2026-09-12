# -*- coding: utf-8 -*-
"""أرشفةُ باقةٍ لا تُسقط سرعةَ البطاقات الصادرة عليها.

واقعةُ «شركتي» ‏2026-09-12: ثلاثُ باقاتٍ أُرشفت في ‏2026-08-29 وبقيت ‏22
حزمةً (~‏16,700 بطاقة) تُشير إليها. `get_plan` يُخفي المحذوفَ ⇒ الباقةُ
None ⇒ لا `Mikrotik-Rate-Limit` في الردّ ⇒ الراوترُ يطبّق ملفَّه الافتراضيّ
بلا حدّ. الزبونُ رأى ‏30 ميجا والباقةُ تقول ‏7.5.

طبقتان: شبكةُ أمانٍ في المحرّك (الباقةُ المؤرشفةُ ما تزال العقد)، وحارسٌ
يرفض أرشفةَ باقةٍ عليها إصداراتٌ حيّة.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_arch_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "t-arch")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(app, *, username="32794735", down=7500, up=7500):
    """باقةٌ ‏7.5M وحزمةٌ عليها وبطاقةٌ مع مرآتها — كما عند شركتي."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "speed_down_kbps, speed_up_kbps, created_at) VALUES (1,?,?,?,?,?)",
                ("طلاب - محدث", "Hotspot", down, up, now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "package_name, count_from_first_connect, time_value, time_unit, "
                "source_type, created_at) VALUES (1,?,?,?,?,?,?,?,?,?)",
                ("b-" + username, pid, 1, "ساعتين", 1, 2, "hours",
                 "generated", now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, "
                "plan_id, used, created_at) VALUES (1,?,?,?,?,0,?)",
                (bid, username, "pw", pid, now))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, "
                "user_type, plan_id, card_batch_id, created_at) "
                "VALUES (1,?,?,?,?,?,?)",
                (username, "pw", "card", pid, bid, now))
        return pid, bid


def _archive(app, pid):
    with app.app_context():
        from app.radius.db.repos import plans_repo
        assert plans_repo.archive_plan(1, pid, actor="t", reason="اختبار")


def test_rate_limit_survives_plan_archive(app):
    pid, _ = _seed(app)
    with app.app_context():
        from app.radius.services.bandwidth_rate import effective_rate_limit
        assert effective_rate_limit(1, "32794735") == "7500k/7500k"
    _archive(app, pid)
    with app.app_context():
        from app.radius.services.bandwidth_rate import effective_rate_limit
        assert effective_rate_limit(1, "32794735") == "7500k/7500k", \
            "أرشفةُ الباقة أسقطت السرعةَ ⇒ الراوتر يطبّق «بلا حدّ»"


def test_accept_reply_still_carries_rate_limit_after_archive(app):
    """الحكمُ الحقيقيّ: ما يبنيه المحرّكُ للردّ، لا الكاسكيدُ وحدَه."""
    pid, _ = _seed(app)
    _archive(app, pid)
    with app.app_context():
        from app.radius.services import policy_engine as pe
        from app.radius.db.repos import subscribers_repo, plans_repo
        sub = subscribers_repo.get_subscriber(1, "32794735")
        plan = plans_repo.get_plan(1, sub.plan_id, include_deleted=True)
        assert plan is not None
        attrs = pe._build_accept_attrs(sub, plan)
        flat = " ".join("%s=%s" % kv for kv in attrs.items()) if isinstance(attrs, dict) else str(attrs)
        assert "7500k" in flat, "الردّ بلا Mikrotik-Rate-Limit: %s" % flat


def test_archive_refused_while_plan_in_use(app):
    """الحارس: باقةٌ عليها حزمةٌ وبطاقةٌ لا تُؤرشف عبر واجهة السلّة."""
    pid, _ = _seed(app)
    client = app.test_client()
    res = client.post("/api/v1/recycle-bin/plan/%d/archive" % pid,
                      json={"reason": "x"},
                      headers={"Authorization": "Bearer t-arch"})
    assert res.status_code == 409, res.get_data(as_text=True)
    body = res.get_json()
    assert "بطاقة" in (body.get("error", {}).get("message") or body.get("message") or str(body))
    with app.app_context():
        from app.radius.db.repos import plans_repo
        assert plans_repo.get_plan(1, pid) is not None, "أُرشفت رغم الرفض"


def test_archive_allowed_when_plan_unused(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "created_at) VALUES (1,?,?,?)",
                ("قديمة", "Hotspot", _dt.datetime.utcnow().isoformat())).lastrowid
    client = app.test_client()
    res = client.post("/api/v1/recycle-bin/plan/%d/archive" % pid,
                      json={"reason": "x"},
                      headers={"Authorization": "Bearer t-arch"})
    assert res.status_code == 200, res.get_data(as_text=True)
