# -*- coding: utf-8 -*-
"""دفترُ المالك ⑤ — «بطاقةٌ تعمل بعد انتهاء مدّتها بيومٍ أو أكثر».

السيناريو الحقيقيّ (مقيسٌ على «حسن10»: ‏٦ بطاقاتٍ من ‏٢٠٣ · ‏٧٣ ساعةً
ممنوحةً خطأً): دخولٌ أوّلُ يمرّ **بلا** محرّك السياسة (مصادقةُ rlm_sql
محلّيًّا) — يخدم البطاقةَ ويُسجَّل في `radacct` لكنّ `first_used_at` يبقى
فارغًا. ثمّ بعد يومٍ يمرّ دخولٌ بالمحرّك فيختمها **من ذلك اليوم** ⇒ نافذةٌ
كاملةٌ ثانية.

📏 والبحثُ عن «جلسةٍ تجاوزت المدّة» لا يكشفه: الجلساتُ كلُّها داخل نوافذها،
والنافذةُ نفسُها هي المزوَّرة. السؤالُ الصحيح: *هل بدايةُ العدّ صادقة؟*

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
    tmp = tempfile.mkdtemp(prefix="hr_stamp_")
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


def _seed_card(app, *, username, hours=10, bypassed_login_at=None):
    """بطاقةُ «من أوّل اتّصال» بمدّة `hours`، لم تُختم بعد. وإن أُعطي
    `bypassed_login_at` كُتبت جلسةٌ في radacct بذلك الوقت — أثرُ دخولٍ
    فاتَ المحرّك — بصيغةِ FreeRADIUS (مسافة) لا ISO، كما في الإنتاج."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "created_at) VALUES (1,?,?,?)",
                ("10 ساعات", "Hotspot", now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "package_name, count_from_first_connect, time_value, time_unit, "
                "source_type, created_at) VALUES (1,?,?,?,?,?,?,?,?,?)",
                ("b-" + username, pid, 1, "عشر ساعات", 1, hours, "hours",
                 "generated", now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, "
                "plan_id, used, created_at) VALUES (1,?,?,?,?,0,?)",
                (bid, username, "pw", pid, now))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, "
                "user_type, created_at) VALUES (1,?,?,?,?)",
                (username, "pw", "card", now))
            if bypassed_login_at is not None:
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                    "username, acctstarttime, acctsessiontime) "
                    "VALUES (1,?,?,?,?,?)",
                    ("s-" + username, "u-" + username, username,
                     bypassed_login_at.strftime("%Y-%m-%d %H:%M:%S"), 3600))


def _engine_login(app, username):
    with app.app_context():
        from app.radius.services import policy_engine as pe
        req = pe.AuthRequest(tenant_id=1, username=username, password="pw",
                             calling_station_id="AA:BB:CC:DD:EE:01")
        pe._update_login_timestamps(req, source="card",
                                    now=_dt.datetime.utcnow())
        from app.radius.db.connection import db
        return dict(db().execute(
            "SELECT first_used_at, expire_at FROM cards WHERE username=?",
            (username,)).fetchone())


def _p(s):
    return _dt.datetime.fromisoformat(str(s).replace("Z", ""))


def test_stamp_takes_earliest_evidence_not_now(app):
    """دخولٌ فاتَ المحرّكَ قبل ‏26 ساعة، ثمّ أوّلُ دخولٍ بالمحرّك الآن:
    بدايةُ العدّ = قبل ‏26 ساعة، والنافذةُ (‏10 ساعات) **منتهيةٌ أصلًا** —
    لا نافذةٌ جديدةٌ تبدأ اليوم."""
    bypassed = _dt.datetime.utcnow() - _dt.timedelta(hours=26)
    _seed_card(app, username="c-late", hours=10, bypassed_login_at=bypassed)
    row = _engine_login(app, "c-late")
    stamped = _p(row["first_used_at"])
    assert abs((stamped - bypassed).total_seconds()) < 2, \
        "الختمُ يجب أن يكون من أوّل جلسةٍ مسجَّلة لا من الآن"
    exp = _p(row["expire_at"])
    assert abs((exp - (bypassed + _dt.timedelta(hours=10))).total_seconds()) < 2
    assert exp < _dt.datetime.utcnow(), "النافذةُ يجب أن تكون منتهيةً لا مُهداة"


def test_stamp_is_now_when_no_prior_accounting(app):
    """بطاقةٌ لم تُستعمل قطّ: الختمُ الآن كما كان — لا انحدارَ في المسار
    الطبيعيّ."""
    _seed_card(app, username="c-fresh", hours=10)
    before = _dt.datetime.utcnow()
    row = _engine_login(app, "c-fresh")
    stamped = _p(row["first_used_at"])
    assert abs((stamped - before).total_seconds()) < 5
    exp = _p(row["expire_at"])
    assert abs((exp - (stamped + _dt.timedelta(hours=10))).total_seconds()) < 2


def test_future_or_equal_accounting_does_not_move_stamp_forward(app):
    """جلسةٌ مسجَّلةٌ **بعد** الآن (ساعةُ راوترٍ متقدّمة) لا تُؤخّر الختم:
    الأقدمُ هو الآن."""
    ahead = _dt.datetime.utcnow() + _dt.timedelta(hours=5)
    _seed_card(app, username="c-ahead", hours=10, bypassed_login_at=ahead)
    before = _dt.datetime.utcnow()
    row = _engine_login(app, "c-ahead")
    stamped = _p(row["first_used_at"])
    assert abs((stamped - before).total_seconds()) < 5


def test_existing_stamp_is_never_rewritten(app):
    """بطاقةٌ مختومةٌ سابقًا لا يُعاد ختمُها ولو ظهرت محاسبةٌ أقدم — عقدُ
    COALESCE محفوظ (لا نُطيل ولا نُقصّر عمرًا خُتم). تصحيحُ الختوم القديمة
    شأنُ المصالِح لا مسارِ الدخول."""
    old_stamp = _dt.datetime.utcnow() - _dt.timedelta(hours=2)
    _seed_card(app, username="c-stamped", hours=10,
               bypassed_login_at=old_stamp - _dt.timedelta(days=3))
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE cards SET first_used_at=?, used=1 "
                      "WHERE username='c-stamped'", (old_stamp.isoformat() + "Z",))
    row = _engine_login(app, "c-stamped")
    assert abs((_p(row["first_used_at"]) - old_stamp).total_seconds()) < 2


def test_reconciler_uses_earliest_of_stamp_and_accounting(app):
    """المصالِحُ كان يثق بالختم متى وُجد. الآن يأخذ الأقدمَ بينه وبين
    المحاسبة، فيلتقط الختومَ المتأخّرةَ التاريخيّة."""
    late_stamp = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    real_first = _dt.datetime.utcnow() - _dt.timedelta(hours=30)
    _seed_card(app, username="c-hist", hours=10, bypassed_login_at=real_first)
    with app.app_context():
        from app.radius.db.connection import db, transaction
        with transaction() as c:
            c.execute("UPDATE cards SET first_used_at=?, used=1 "
                      "WHERE username='c-hist'", (late_stamp.isoformat() + "Z",))
        from app.radius.services import card_time_reconcile as rec
        first = rec._first_connection(db(), 1, "c-hist", late_stamp.isoformat())
    assert abs((first - real_first).total_seconds()) < 2
