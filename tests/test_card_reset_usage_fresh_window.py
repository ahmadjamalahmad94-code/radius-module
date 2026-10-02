# -*- coding: utf-8 -*-
"""«تصفير الاستخدام» يعطي البطاقة نافذةً جديدة فعلًا.

فادي نت 2026-10-02: «لما أصفّر بطاقة بتفتح ١٠ دقايق وبعدها بقلّه خلص الوقت».
السبب: أوّل دخولٍ بعد التصفير خُتم من **أقدم جلسةٍ قبل التصفير** في
`radacct` (أمس) ⇒ وُلدت النافذة منتهية؛ ومرآةُ `subscribers` احتفظت بـ
`expire_at` القديم. البطاقة 164032: صُفِّرت 14:13 وعاد ختمها إلى أمس 20:19.
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
    tmp = tempfile.mkdtemp(prefix="hr_cardreset_")
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


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat() + "Z"


def _seed(app) -> int:
    from app.radius.db.connection import transaction
    now = _dt.datetime.utcnow()
    yday = now - _dt.timedelta(hours=20)
    with transaction() as c:
        c.execute("INSERT INTO access_plans (tenant_id, name, created_at) "
                  "VALUES (1, 'p', ?)", (_iso(now),))
        plan_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO card_batches (tenant_id, batch_code, plan_id, count,"
                  " generated, created_at, time_value, time_unit,"
                  " count_from_first_connect) VALUES (1,'B-R',?,1,1,?,4,'hours',1)",
                  (plan_id, _iso(now)))
        batch_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO cards (tenant_id, batch_id, username, password, plan_id,"
                  " used, revoked, first_used_at, expire_at, created_at)"
                  " VALUES (1,?,'164032','p',?,1,0,?,?,?)",
                  (batch_id, plan_id, _iso(yday),
                   _iso(yday + _dt.timedelta(hours=4)), _iso(yday)))
        card_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO subscribers (tenant_id, username, password, user_type,"
                  " status, expire_at, first_login_at, created_at)"
                  " VALUES (1,'164032','p','card','enabled',?,?,?)",
                  (_iso(yday + _dt.timedelta(hours=4)), _iso(yday), _iso(yday)))
        # جلسةٌ قديمة (قبل التصفير) بصيغة FreeRADIUS «مسافة»
        c.execute("INSERT INTO radacct (tenant_id, username, acctsessionid,"
                  " acctuniqueid, acctstarttime, acctstoptime, acctsessiontime)"
                  " VALUES (1,'164032','s1','u1',?,?,2281)",
                  (yday.strftime("%Y-%m-%d %H:%M:%S"),
                   (yday + _dt.timedelta(minutes=38)).strftime("%Y-%m-%d %H:%M:%S")))
    return card_id


def _row(sql, *a):
    from app.radius.db.connection import db
    return dict(db().execute(sql, a).fetchone())


def test_reset_then_first_login_opens_a_full_fresh_window(app):
    with app.app_context():
        card_id = _seed(app)
        from app.radius.db.repos.cards_repo import reset_card_usage
        assert reset_card_usage(1, card_id)
        mirror = _row("SELECT expire_at FROM subscribers WHERE username='164032'")
        assert not mirror["expire_at"], "المرآة احتفظت بانتهاءٍ قديم ⇒ البطاقة المصفَّرة منتهية"

        from app.radius.services.policy_engine import _do_update_login_timestamps
        now = _dt.datetime.utcnow()
        req = SimpleNamespace(tenant_id=1, username="164032",
                              calling_station_id="AA:BB:CC:DD:EE:FF")
        _do_update_login_timestamps(req, source="card", now=now)

        card = _row("SELECT first_used_at, expire_at FROM cards WHERE id=?", card_id)
        first = _dt.datetime.fromisoformat(card["first_used_at"].rstrip("Z"))
        assert first >= now - _dt.timedelta(minutes=1), (
            "الختم عاد إلى جلسةٍ قبل التصفير", card["first_used_at"])
        exp = _dt.datetime.fromisoformat(card["expire_at"].rstrip("Z"))
        assert exp > now + _dt.timedelta(hours=3, minutes=50)
        mexp = _row("SELECT expire_at FROM subscribers WHERE username='164032'")["expire_at"]
        assert mexp == card["expire_at"]


def test_reset_card_time_reconcile_ignores_pre_reset_sessions(app):
    with app.app_context():
        card_id = _seed(app)
        from app.radius.db.connection import db
        from app.radius.db.repos.cards_repo import reset_card_usage
        from app.radius.services.card_time_reconcile import _first_connection
        reset_card_usage(1, card_id)
        assert _first_connection(db(), 1, "164032", None) is None


def test_count_by_seconds_usage_ignores_pre_reset_sessions(app):
    with app.app_context():
        card_id = _seed(app)
        from app.radius.db.repos.cards_repo import reset_card_usage
        from app.radius.services.card_batch_flags import _accounted_seconds
        assert _accounted_seconds(1, "164032") == 2281
        reset_card_usage(1, card_id)
        assert _accounted_seconds(1, "164032") == 0
