"""تمديدُ مشتركٍ **منتهٍ** يجب أن يمنحه المدّةَ من الآن — لا من نهايةٍ مضت.

🔴 مُشاهَدٌ حيًّا (2026-08-30، خادم «سمير»): مدّد المشغّل مشتركَين بأربع
ساعات، فسجّل النظام:
```
المشترك 2 → new_expire_at 2026-08-30T03:59  (والساعة 14:30 ⇒ مضت بـ10س)
المشترك 1 → new_expire_at 2026-08-29T19:59  (⇒ مضت بـ18س)
```
لأنّ الحساب كان `(expire_at or now) + delta` — ونهايةٌ ماضيةٌ + 4س تبقى ماضية.
والشاشةُ تقول «تم التمديد» والزبونُ لا يدخل: **كذبةٌ صامتة**.

وهو نفسُ الخطأ الذي أُصلح للبطاقات في `grant_card_time` — فالمرساةُ الصحيحة
`max(النهاية الحاليّة, الآن)`:
  • حيٌّ   → يُمدَّد من نهايته فلا يُسرق ما تبقّى له.
  • منتهٍ → يُمدَّد من الآن فينال المدّة كاملة.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "extend.db")
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


def _mk(username, expire_at):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,"
        " currency,speed_down_kbps,speed_up_kbps,quota_total_mb,created_at,updated_at)"
        " VALUES(1,'باقة',1440,30,0,'ILS',2048,2048,0,datetime('now'),datetime('now'))")
    pid = int(cur.lastrowid)
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,user_type,"
        " expire_at,created_at) VALUES(1,?,'pw',?,'enabled','user',?,datetime('now'))",
        (username, pid, expire_at.isoformat() + "Z"))


def _expire_of(username):
    from app.radius.db.helpers import parse_dt
    r = db().execute("SELECT expire_at FROM subscribers WHERE username=?", (username,)).fetchone()
    return parse_dt(r["expire_at"])


def _extend(username, minutes):
    from app.radius.services.users import get_users_service
    return get_users_service().extend_time(actor="t", username=username, minutes=minutes)


def test_expired_subscriber_gets_the_time_from_now(app_ctx):
    """🔴 الانحدارُ بعينه: منتهٍ منذ 18 ساعة + 4 ساعات ⇒ صالحٌ الآن."""
    now = datetime.utcnow()
    _mk("expired1", now - timedelta(hours=18))
    _extend("expired1", 240)
    exp = _expire_of("expired1")
    assert exp > now, "التمديدُ أنتج نهايةً في الماضي — المشترك ما زال منتهيًا"
    assert abs((exp - (now + timedelta(minutes=240))).total_seconds()) < 120


def test_live_subscriber_extends_from_its_own_end_not_from_now(app_ctx):
    """الحيُّ لا يُسرق منه: تمديدُه يُضاف إلى نهايته لا يُعيدها إلى الآن."""
    now = datetime.utcnow()
    end = now + timedelta(days=10)
    _mk("live1", end)
    _extend("live1", 240)
    exp = _expire_of("live1")
    assert abs((exp - (end + timedelta(minutes=240))).total_seconds()) < 120


def test_subscriber_without_expiry_starts_from_now(app_ctx):
    """بلا نهايةٍ مسبقة ⇒ المرساةُ الآن (السلوكُ السابق نفسُه)."""
    now = datetime.utcnow()
    _mk("noexp", now)          # نهاية = الآن تقريبًا
    db().execute("UPDATE subscribers SET expire_at = NULL WHERE username='noexp'")
    _extend("noexp", 60)
    exp = _expire_of("noexp")
    assert abs((exp - (now + timedelta(minutes=60))).total_seconds()) < 120


def test_barely_expired_is_not_shortchanged(app_ctx):
    """انتهى قبل دقيقةٍ واحدة: يجب أن ينال الستّين كاملةً من الآن."""
    now = datetime.utcnow()
    _mk("edge", now - timedelta(minutes=1))
    _extend("edge", 60)
    exp = _expire_of("edge")
    assert exp >= now + timedelta(minutes=59)
