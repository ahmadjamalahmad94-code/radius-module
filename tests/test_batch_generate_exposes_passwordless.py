"""خيارُ «الدخول بالرقم وحدَه» يجب أن يكون في صفحة **التوليد** لا التعديل فقط.

🔴 **سمير ٢٠٢٦-٠٩-٠٣.** ولّد المشغّلُ حزمةَ عشر دقائق ونيّتُه «بلا كلمة
مرور» — كما بقيّةُ حزمه — فوصلت الشكوى: «لمّا يحطّ البطاقة بيقلّه خطأ».
والسجلُّ يقولها حرفًا: ``Reject reason=password_wrong``.

السبب لم يكن في المصادقة: `login_without_password` كان مُعلَّقًا في
`cards_batch_edit.html` **وحدَه**، ولا أثرَ له في `cards_generate.html`. وخانةٌ
غائبةٌ عن النموذج تعني ``_form_bool`` تقرأ صفرًا دائمًا — فكلُّ حزمةٍ تُولَّد من
اللوحة تخرج وكلمةُ المرور مطلوبة، مهما كان قصدُ المشغّل. ولا رسالةَ خطأٍ عندنا
تشرح شيئًا؛ الشكوى تأتي من الزبون بعد يومين.

الدرس: **خيارٌ يوجَد في صفحة التعديل ولا يوجد في صفحة الإنشاء = عطبٌ صامت.**
"""
from __future__ import annotations

import io
import os

import pytest

GEN = "app/templates/radius/cards_generate.html"
EDIT = "app/templates/radius/cards_batch_edit.html"


def _read(path):
    return io.open(path, encoding="utf-8").read()


def test_generate_form_offers_the_passwordless_toggle():
    """حارسٌ نصّيّ: عودةُ الخيار إلى صفحة التعديل وحدَها تُعيد العطب."""
    src = _read(GEN)
    assert 'name="login_without_password"' in src, (
        "خيارُ الدخول بالرقم وحدَه غاب عن صفحة التوليد — وهو ما أسقط "
        "حزمةَ سمير: تُولَّد وكلمةُ المرور مطلوبةٌ رغمًا عن المشغّل")


def test_the_toggle_is_a_real_switch_not_a_hidden_default():
    """يُرسَل `1` عند التفعيل — لا قيمةً ثابتةً تُخزَّن دائمًا."""
    src = _read(GEN)
    i = src.find('name="login_without_password"')
    assert 'value="1"' in src[i:i + 400]


def test_create_and_edit_pages_agree_on_the_field_name():
    """اسمٌ واحدٌ في الصفحتين — وإلّا حُفظ في إحداهما وضاع في الأخرى."""
    assert 'name="login_without_password"' in _read(EDIT)


# ── والسلوكُ خلف الخيار: التوليدُ يحفظه فعلًا ───────────────────────


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "gen_lwp.db")
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


def _plan_id():
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,'2ميغا',0,1,1.0,"
        "'ILS',2048,2048,0,datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _generate(**kw):
    from app.radius.services.cards import get_cards_service
    batch, _cards = get_cards_service().generate_batch(
        actor="admin", plan_id=_plan_id(), count=2, package_name="عشر دقائق",
        time_value=10, time_unit="minutes", **kw)
    return batch


def test_generating_with_the_flag_on_persists_it(app_ctx):
    batch = _generate(login_without_password=True)
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT login_without_password FROM card_batches WHERE id = ?",
        (batch.id,)).fetchone()
    assert int(row[0]) == 1


def test_generating_without_it_stays_off(app_ctx):
    """الافتراضُ مُطفأ — تفعيلُه قرارُ صاحب الشبكة وحدَه."""
    batch = _generate()
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT login_without_password FROM card_batches WHERE id = ?",
        (batch.id,)).fetchone()
    assert int(row[0]) == 0
