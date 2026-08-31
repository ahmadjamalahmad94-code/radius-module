"""المادّية لا تُنشئ نسخةً ثانيةً لجلسةٍ سجّلها فري-رديوس سلفًا.

`mt_reconciler._materialize_nas` كان يبحث عن صفٍّ **مفتوح** وعلى **نفس عنوان
NAS** وحدَه. فإن أُغلق الصفُّ الحقيقيُّ خطأً (عاصفةُ إغلاقٍ على قراءةٍ ناقصة)،
أو قيّده فري-رديوس بالعنوان العامّ ونحن نستطلع عنوانَ النفق — لا يجده فيكتب
نسخةً ثانية. والنسختان تُجمعان خامًا في «الوقت المستخدم» وفي
`policy_engine._accounted_seconds`، فينفخ العرضُ وتُظلَم بطاقةٌ تُحاسَب بالثانية.
مقيسٌ على إنتاج: 260.7 ساعةً منفوخةً (3.6٪) على 182 حسابًا.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtsync_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


USER = "3151498120"
MAC = "56:6C:F0:51:BB:49"


def _insert_real(conn, *, start, stop, nas, secs, sid="806005b7"):
    conn.execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctstoptime, callingstationid, acctsessiontime) "
        "VALUES(1,?,?,?,?,?,?,?,?)",
        (sid, "u-" + sid, USER, nas, start, stop, MAC, secs),
    )


def _live_row(uptime_sec):
    return [{"username": USER, "mac": MAC, "uptime_sec": uptime_sec,
             "bytes_in": 0, "bytes_out": 0, "framed_ip": "10.19.6.7"}]


def _count_mtsync(db):
    return db().execute(
        "SELECT COUNT(*) c FROM radacct WHERE acctsessionid LIKE 'mtsync-%'"
    ).fetchone()["c"]


def test_closed_real_row_is_not_duplicated(app):
    """🔴 الحالة التي وقعت فعلًا: أُغلق الصفُّ الحقيقيُّ خطأً والجلسةُ حيّة."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            _insert_real(conn, nas="10.50.0.3", secs=17488,
                         start=(now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                         stop=(now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"))
        out = _materialize_nas(1, "10.50.0.3", _live_row(5 * 3600))
        assert out["inserted"] == 0
        assert _count_mtsync(db) == 0


def test_real_row_under_the_other_nas_address_is_not_duplicated(app):
    """فري-رديوس قيّدها بالعنوان العامّ ونحن نستطلع عنوانَ النفق."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            _insert_real(conn, nas="213.244.94.51", secs=3321, stop=None,
                         start=(now - timedelta(hours=1)).isoformat() + "Z")
        out = _materialize_nas(1, "10.50.0.2", _live_row(3600))
        assert out["inserted"] == 0
        assert _count_mtsync(db) == 0


def test_genuinely_new_session_is_still_materialized(app):
    """الحارس لا يخنق وظيفته: جلسةُ كوكي لم يرها الرديوس تُكتب كما كانت."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.workers.mt_reconciler import _materialize_nas
        out = _materialize_nas(1, "10.50.0.2", _live_row(600))
        assert out["inserted"] == 1
        assert _count_mtsync(db) == 1


def test_old_finished_session_does_not_block_a_new_one(app):
    """صفٌّ حقيقيٌّ انتهى **قبل** بداية الجلسة الجديدة لا يمنع مادّيتها."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            _insert_real(conn, nas="10.50.0.2", secs=1800,
                         start=(now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
                         stop=(now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"))
        out = _materialize_nas(1, "10.50.0.2", _live_row(600))
        assert out["inserted"] == 1
        assert _count_mtsync(db) == 1


def test_other_users_row_does_not_shield(app):
    """التغطية تُقاس لنفس المستخدم — لا لجارٍ يشترك في اللحظة نفسها."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            conn.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                "nasipaddress, acctstarttime, acctstoptime, callingstationid, acctsessiontime) "
                "VALUES(1,'other','u-other','9999999','10.50.0.2',?,NULL,?,3600)",
                ((now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "AA:BB:CC:DD:EE:FF"))
        out = _materialize_nas(1, "10.50.0.2", _live_row(3600))
        assert out["inserted"] == 1
        assert _count_mtsync(db) == 1


def test_newborn_session_under_the_other_nas_is_not_duplicated(app):
    """🔴 الثغرةُ التي أفلتت من الإصلاح الأوّل: جلسةٌ عمرُها ثانية.

    وقعت حيًّا على خادم سمير (2026-08-31) بعد بناء نفق SSTP. فري-رديوس
    يسجّل بعنوان النفق ونحن نستطلع بالعنوان العامّ فلا يتطابقان — وهذه
    الحالةُ مُغطّاةٌ سلفًا للجلسة القائمة. لكنّ الحارسَ كان يشترط تداخلًا
    ≥60ث، والمُصالِحُ يستطلع بعد ثانيةٍ من الولادة فالتداخلُ **صفر**:
    مرّت النسخة. الحصيلة في يومٍ واحد: 110 صفوفٍ مكرَّرة و136.6 ساعةً
    منفوخةً على 44 حسابًا — بطاقةُ ثلاثِ ساعاتٍ تُعرض 8:27.

    الصفُّ الحقيقيُّ **مفتوحٌ** وبدايتُه تساوي البدايةَ المحسوبة ⇒ هو هذه
    الجلسة، ولا يحتاج تداخلًا كي يُثبِت ذلك.
    """
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            _insert_real(conn, nas="212.14.245.7", secs=1, stop=None,
                         start=(now - timedelta(seconds=1)).isoformat() + "Z")
        out = _materialize_nas(1, "10.50.0.2", _live_row(1))
        assert out["inserted"] == 0
        assert _count_mtsync(db) == 0


def test_stale_open_row_does_not_shield_a_genuinely_new_session(app):
    """حارسُ الإفراط: «مفتوحٌ» وحدَه لا يكفي — البدايةُ يجب أن تتقارب.

    صفٌّ مفتوحٌ منذ ستِّ ساعاتٍ (زومبي لم يصله Acct-Stop) بينما الراوتر
    يقول إنّ الجلسة عمرُها عشرُ ثوانٍ: جلستان مختلفتان. لو غطّى المفتوحُ
    كلَّ شيءٍ بلا قيدٍ زمنيّ لَاختفت جلساتٌ حقيقيّةٌ من /online.
    """
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.mt_reconciler import _materialize_nas
        now = datetime.utcnow()
        with transaction() as conn:
            _insert_real(conn, nas="212.14.245.7", secs=21600, stop=None,
                         start=(now - timedelta(hours=6)).isoformat() + "Z")
        out = _materialize_nas(1, "10.50.0.2", _live_row(10))
        assert out["inserted"] == 1
