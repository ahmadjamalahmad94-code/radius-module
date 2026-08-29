"""حزمةٌ تُصادَق **برقم البطاقة وحدَه** — بلا كلمة مرور.

🔴 شبكاتٌ قائمةٌ تبيع هكذا فعلًا: لوحةُ adv تُخزّن ``Cleartext-Password := ''``
لكلّ بطاقات الحزمة، ورقمُ البطاقة هو السرُّ الوحيد. وشيفرتُنا كانت ترفضها
حتمًا (‏``if req.chap_password: if not sub.password: reject``) ومايكروتيك
هوت-سبوت يستعمل CHAP افتراضًا — فترحيلُ شبكةٍ كهذه كما هي يُنتج آلافَ
البطاقات التي لا تدخل، ولا عَرَضَ يُنذر إلّا شكوى الزبائن.

العلَمُ على **الحزمة** وافتراضُه مُطفأ ⇒ لا تتأثّر أيّ شبكةٍ أخرى.
شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

MAC = "AA:BB:CC:DD:EE:01"


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "lwp.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _seed(*, username, stored_password, without_password):
    """باقة + حزمة + بطاقة + مرآة المشترك — كما يُنتجها الترحيل."""
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        " price,currency,speed_down_kbps,speed_up_kbps,quota_total_mb,"
        " created_at,updated_at) VALUES(1,'4ميجا',180,1,0,'ILS',4096,4096,0,"
        "datetime('now'),datetime('now'))")
    plan_id = int(cur.lastrowid)
    cur = db().execute(
        "INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,"
        " generated,used,time_value,time_unit,count_from_first_connect,"
        " count_by_seconds,login_without_password,status,created_at)"
        " VALUES(1,'B-1','2026-10',?,1,1,0,3,'hours',1,0,?,'active',datetime('now'))",
        (plan_id, 1 if without_password else 0))
    batch_id = int(cur.lastrowid)
    exp = (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    db().execute(
        "INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,used,"
        " expire_at,created_at) VALUES(1,?,?,?,?,0,?,datetime('now'))",
        (batch_id, username, stored_password, plan_id, exp))
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,"
        " user_type,card_batch_id,expire_at,created_at)"
        " VALUES(1,?,?,?,'enabled','card',?,?,datetime('now'))",
        (username, stored_password, plan_id, batch_id, exp))
    return batch_id


def _auth(**kw):
    from app.radius.services.policy_engine import AuthRequest, authorize
    base = dict(username="00097106", tenant_id=1,
                calling_station_id=MAC, nas_ip="10.0.0.1")
    base.update(kw)
    return authorize(AuthRequest(**base))


def test_card_without_password_is_accepted_when_batch_allows(app_ctx):
    """🔴 الحالة التي وقعت: بطاقةٌ بلا كلمةٍ مخزَّنة وطلبٌ بـCHAP."""
    _seed(username="00097106", stored_password="", without_password=True)
    d = _auth(chap_password="anything", chap_challenge="x")
    assert d.ok, getattr(d, "reason", None)


def test_card_without_password_accepted_with_no_credentials_at_all(app_ctx):
    """طلبٌ بلا PAP ولا CHAP — الرقمُ وحدَه يكفي."""
    _seed(username="00097106", stored_password="", without_password=True)
    d = _auth()
    assert d.ok, getattr(d, "reason", None)


def test_same_card_is_rejected_when_flag_is_off(app_ctx):
    """الضابطة: بلا العلَم يبقى الرفضُ كما كان — لا تُفتَح شبكةٌ بالخطأ."""
    _seed(username="00097106", stored_password="", without_password=False)
    d = _auth(chap_password="anything", chap_challenge="x")
    assert not d.ok
    assert d.reason == "password_wrong"


def test_normal_batch_still_checks_the_password(app_ctx):
    """حزمةٌ عاديّةٌ بكلمةٍ مخزَّنة: الخطأُ يُرفض والصوابُ يُقبل."""
    _seed(username="00097106", stored_password="9634", without_password=False)
    assert not _auth(password="0000").ok
    assert _auth(password="9634").ok


def test_flag_defaults_off_for_a_plain_batch(app_ctx):
    """الافتراضُ مُطفأ — لا شبكةَ تتأثّر بوجود الميزة."""
    bid = _seed(username="00097106", stored_password="x", without_password=False)
    row = db().execute("SELECT login_without_password AS f FROM card_batches"
                       " WHERE id=?", (bid,)).fetchone()
    assert int(row["f"]) == 0
