"""تبديلُ نمط الحساب على الحزمة يجب أن يَسري على بطاقاتها — في الاتّجاهين.

للحزمة نمطان:
  • «من أوّل اتّصال» (Mode B) — عدٌّ بساعة الحائط يبدأ عند أوّل دخول.
  • «محاسبة بالثانية» (Mode A) — رصيدُ وقتٍ يُستهلك **أثناء الاتّصال فقط**،
    ولا يجوز أن يُرافقه تاريخُ انتهاءٍ بالتقويم (يقتل البطاقةَ وهي في الدرج).

وكان التبديل يفشل من طرفين: لا يُطلق المطابقةَ أصلًا إن لم تُمسّ المدّة،
ويكتب — حين يُطلقها — ختمَ ساعةِ حائطٍ في الحالتين معًا.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mode_switch.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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
    return flask_app


def _plan_id():
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,'باقة',600,1,5.0,'ILS',4096,2048,0,"
        "datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


FIRST_USED = datetime(2026, 8, 25, 17, 0, 0)


def _batch_with_started_card(svc):
    """حزمةُ «١٠ ساعات · من أوّل اتّصال» وفيها بطاقةٌ بدأت واستُهلك ختمُها."""
    batch, cards = svc.generate_batch(
        actor="t", plan_id=_plan_id(), count=1, username_length=8,
        password_length=6, price_per_card=1.0,
        time_value=10, time_unit="hours", package_name="حسن10",
        count_from_first_connect=True, count_by_seconds=False)
    db().execute(
        "UPDATE cards SET first_used_at = ?, expire_at = ? WHERE batch_id = ?",
        (FIRST_USED.isoformat() + "Z",
         (FIRST_USED + timedelta(hours=10)).isoformat() + "Z", batch.id))
    return batch


def _expire(batch_id):
    r = db().execute("SELECT expire_at FROM cards WHERE batch_id = ?",
                     (batch_id,)).fetchone()
    return r["expire_at"]


def test_mode_only_change_still_realigns_cards(app):
    """🔴 تغييرُ النمط وحدَه (مسارُ الـAPI) كان لا يُطلق المطابقة إطلاقًا."""
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        batch = _batch_with_started_card(svc)
        # تغييرُ النمط وحدَه — بلا أيّ حقلِ مدّة (كما يفعل مسارُ الـAPI).
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "count_by_seconds": True, "count_from_first_connect": False,
        })
        assert svc.last_realign_summary()["started"] == 1, \
            "تغييرُ النمط تغييرٌ في المعنى — يجب أن يُطلق المطابقةَ مثلَ المدّة"


def test_switch_to_by_seconds_drops_the_wall_clock_stamp(app):
    """«بالثانية» رصيدُ استخدامٍ لا تاريخَ تقويم — فلا يبقى ختمُ 10 ساعات."""
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        batch = _batch_with_started_card(svc)
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "count_by_seconds": True, "count_from_first_connect": False,
            "time_value": 10, "time_unit": "hours",
            "validity_after_first_login_days": 30,
        })
        got = _expire(batch.id)
        wall = (FIRST_USED + timedelta(hours=10)).isoformat() + "Z"
        assert got != wall, ("البطاقة صارت «بالثانية» ومعها تاريخُ انتهاءٍ "
                             "محسوبٌ من مدّة الاستخدام — تموت في التقويم")
        # الصلاحيّة التقويميّة المعلنة (30 يومًا) هي السقف الصحيح لهذا النمط.
        assert got == (FIRST_USED + timedelta(days=30)).isoformat() + "Z", got


def test_switch_back_to_first_connect_restores_the_window(app):
    """والعكس: العودةُ إلى «من أوّل اتّصال» تُعيد ختمَ العشر ساعات."""
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        batch = _batch_with_started_card(svc)
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "count_by_seconds": True, "count_from_first_connect": False,
            "time_value": 10, "time_unit": "hours",
            "validity_after_first_login_days": 30})
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "count_by_seconds": False, "count_from_first_connect": True,
            "time_value": 10, "time_unit": "hours",
            "validity_after_first_login_days": 30})
        assert _expire(batch.id) == (FIRST_USED + timedelta(hours=10)).isoformat() + "Z"


def test_switch_reports_cards_it_kills_retroactively(app):
    """⚠️ التحويل يُعيد الحساب من أوّل دخولٍ — فمن مضت نافذتُه يموت بالحفظ.

    مقيسٌ على الإنتاج: 71 بطاقةً من 86 ماتت لحظةَ حفظ المشغّل و345.7 ساعةً
    ضاعت، بلا كلمةٍ واحدةٍ تُنذره. العدد يجب أن يصل إليه.
    """
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        batch, _ = svc.generate_batch(
            actor="t", plan_id=_plan_id(), count=1, username_length=8,
            password_length=6, price_per_card=1.0,
            time_value=10, time_unit="hours", package_name="بالثانية",
            count_from_first_connect=False, count_by_seconds=True)
        # بطاقةٌ دخلت أمسِ واستهلكت دقائقَ فقط — بلا نافذةٍ تقويميّة (نمط A).
        db().execute("UPDATE cards SET first_used_at = ?, expire_at = NULL "
                     "WHERE batch_id = ?",
                     ((datetime.utcnow() - timedelta(hours=30)).isoformat() + "Z",
                      batch.id))
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "count_by_seconds": False, "count_from_first_connect": True,
            "time_value": 10, "time_unit": "hours"})
        summary = svc.last_realign_summary()
        assert summary["started"] == 1
        assert summary["expired_now"] == 1, (
            "نافذتُها الجديدة (أوّل دخول + 10س) مضت قبل 20 ساعة — "
            "يجب أن يُبلَّغ المشغّل أنّها ماتت بحفظه")
