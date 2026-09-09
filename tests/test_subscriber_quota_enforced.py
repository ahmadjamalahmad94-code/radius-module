"""كوتا المشترك تُنفَّذ فعلًا — الاستهلاكُ من `radacct` لا من عمودٍ ميّت.

🔴 المكتشَف 2026-09-09: `_is_quota_exhausted` كان يقيس بـ
`sub.used_bytes_in + sub.used_bytes_out`، وهما عمودان **لا يكتبهما أحدٌ**
**لمشتركٍ حقيقيّ**. فكانت النتيجةُ صفرًا دائمًا: سقفٌ مضبوطٌ في اللوحة لا
يُنفَّذ أبدًا، ومشتركٌ محدودٌ بخمسة جيجا يستهلك بلا حدّ.

وقرارُ المالك (2026-09-09): تُفعَّل كما صُمّمت — الاستهلاكُ الكاملُ يُحتسب.

ومسارُ البطاقات لا يُمَسّ: `_subscriber_from_card` يحقن استهلاكَ البطاقة
على الكائن، وعدّادٌ محمولٌ > 0 يُؤخذ كما هو قبل النظر في radacct.

شغّل هذا الملفّ وحدَه (عزلُ الاختبارات لكلّ ملف).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

MB = 1048576


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_quota_")
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


def _sub(**kw):
    from app.radius.core.types import Subscriber
    base = dict(id=1, username="bob", password="p", tenant_id=1,
                status="enabled", user_type="subscriber",
                combined_quota_mb=0, quota_limit_enabled=False,
                used_bytes_in=0, used_bytes_out=0)
    base.update(kw)
    return Subscriber(**base)


def _acct(app, username, inb, outb, tenant_id=1):
    from app.radius.db.connection import db
    from app.radius.db.repos import tenants_repo
    tenants_repo.ensure_default_tenant()
    db().execute(
        "INSERT OR IGNORE INTO subscribers(tenant_id,username,password,status,"
        "user_type,created_at) VALUES(?,?,'p','enabled','subscriber',"
        "datetime('now'))", (tenant_id, username))
    db().execute(
        "INSERT INTO radacct(tenant_id,username,acctsessionid,acctstarttime,"
        " acctsessiontime,acctinputoctets,acctoutputoctets)"
        " VALUES(?,?,?,datetime('now'),60,?,?)",
        (tenant_id, username, "s-%s-%d" % (username, inb + outb), inb, outb))
    db().commit()


# ───────────── جوهرُ الإصلاح ─────────────

def test_quota_now_fires_from_radacct(app):
    """السقفُ 100 ميجا والاستهلاكُ 150 ⇒ نفدت. كانت تردّ False دائمًا."""
    with app.app_context():
        from app.radius.services.policy_engine import _is_quota_exhausted
        _acct(app, "bob", 50 * MB, 100 * MB)
        assert _is_quota_exhausted(_sub(combined_quota_mb=100), None) is True


def test_under_cap_still_allowed(app):
    with app.app_context():
        from app.radius.services.policy_engine import _is_quota_exhausted
        _acct(app, "bob", 10 * MB, 20 * MB)
        assert _is_quota_exhausted(_sub(combined_quota_mb=100), None) is False


def test_no_cap_never_exhausts_and_reads_nothing(app):
    """بلا سقفٍ لا استعلامَ أصلًا — لا نُثقل مصادقةَ من لا كوتا له."""
    with app.app_context():
        from app.radius.services import policy_engine as pe
        _acct(app, "bob", 900 * MB, 900 * MB)
        calls = []
        orig = pe._subscriber_used_bytes
        pe._subscriber_used_bytes = lambda s: (calls.append(1), orig(s))[1]
        try:
            assert pe._is_quota_exhausted(_sub(combined_quota_mb=0), None) is False
        finally:
            pe._subscriber_used_bytes = orig
        assert calls == [], "استُدعي جامعُ الاستهلاك بلا سقف"


# ───────────── البطاقاتُ لا تُمَسّ ─────────────

def test_card_carried_counter_wins(app):
    """بطاقةٌ تحمل استهلاكَها المحسوب — لا يُستبدَل بـradacct."""
    with app.app_context():
        from app.radius.services.policy_engine import _subscriber_used_bytes
        _acct(app, "card1", 900 * MB, 900 * MB)
        s = _sub(username="card1", user_type="card",
                 used_bytes_in=3 * MB, used_bytes_out=7 * MB)
        assert _subscriber_used_bytes(s) == 10 * MB


# ───────────── العزلُ والتحصين ─────────────

def test_other_tenant_usage_is_not_counted(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.policy_engine import _subscriber_used_bytes
        _acct(app, "bob", 5 * MB, 5 * MB)
        db().execute("INSERT INTO tenants(name, slug, created_at)"
                     " VALUES('ثانية','t2',datetime('now'))")
        tid2 = int(db().execute("SELECT last_insert_rowid()").fetchone()[0])
        _acct(app, "bob", 900 * MB, 900 * MB, tenant_id=tid2)
        assert _subscriber_used_bytes(_sub()) == 10 * MB


def test_read_failure_falls_back_and_never_cuts(app, monkeypatch):
    """عطبُ قراءةٍ لا يقطع خدمةً: يردّ المحمول (صفرًا) فلا تنفد الكوتا."""
    with app.app_context():
        from app.radius.services import policy_engine as pe
        import app.radius.db.connection as conn

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(conn, "db", boom)
        assert pe._subscriber_used_bytes(_sub()) == 0
        assert pe._is_quota_exhausted(_sub(combined_quota_mb=1), None) is False
