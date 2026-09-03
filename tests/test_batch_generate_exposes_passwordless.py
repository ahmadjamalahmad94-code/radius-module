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
import itertools
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


_plan_seq = itertools.count(1)


def _plan_id():
    # اسمٌ فريدٌ لكلّ نداء: `access_plans` تفرض تفرّدَ (المستأجر، الاسم)،
    # واختبارٌ يُنشئ حزمتين كان يسقط على ذلك لا على السلوك المقصود.
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,0,1,1.0,"
        "'ILS',2048,2048,0,datetime('now'),datetime('now'))",
        ("2ميغا-%d" % next(_plan_seq),))
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


# ── افتراضُ الشبكة: يُطبَّق عند الإنشاء لا عند الدخول ────────────────
#
# «كلُّ حزم سمير بدون باسوورد — مش هينفع كل مرّة مشكلة هيك» ⇒ افتراضٌ للشبكة.
# ثمّ: «ليش البطاقات ما يكونوا نظامين؟ مفتاحُ سوتش يعطّل قسمَ الباسوورد كلَّه»
# ⇒ فالقرارُ النهائيّ **للحزمة** في الاتّجاهين، والافتراضُ يُطبَّق لحظةَ
# إنشائها لا لحظةَ دخول الزبون. هكذا:
#   • تحمل كلُّ حزمةٍ نيّتَها مكتوبةً في صفّها،
#   • ولا يتبدّل سلوكُ بطاقاتٍ مطبوعةٍ لأنّ أحدًا غيّر إعدادًا عامًّا بعد بيعها،
#   • والحزمُ التي تُنشأ بلا نموذج (استيراد · عرض · متجر) ترث فلا تُنسى.

SETTING = "cards.login_without_password_default"


def _set_default(value):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, SETTING, value)


def _card(username, *, batch_flag):
    """بطاقةٌ في حزمةٍ علَمُها كما يُطلب — وتُعاد لنسأل عنها محرّكَ السياسة."""
    from app.radius.db.connection import db
    pid = _plan_id()
    cur = db().execute(
        "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id,"
        " count, login_without_password, created_at)"
        " VALUES(1,?,'\u062a\u062c\u0631\u0628\u0629',?,1,?,datetime('now'))",
        ("B-T-%s" % username, pid, int(batch_flag)))
    bid = int(cur.lastrowid)
    db().execute(
        "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id,"
        " created_at) VALUES(1,?,?,'secret',?,datetime('now'))",
        (bid, username, pid))
    db().commit()
    return username


def test_new_batch_inherits_the_network_default(app_ctx):
    """شبكةٌ تبيع «رقمًا فقط» ⇒ حزمةٌ تُولَّد بلا ذكرِ المفتاح ترثه."""
    _set_default("1")
    batch = _generate()
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT login_without_password FROM card_batches WHERE id = ?",
        (batch.id,)).fetchone()
    assert int(row[0]) == 1


def test_an_explicit_choice_beats_the_network_default(app_ctx):
    """🔑 نظامان: الحزمةُ تقول «بكلمة مرور» ولو كان افتراضُ الشبكة العكس."""
    _set_default("1")
    batch = _generate(login_without_password=False, password_length=6)
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT login_without_password FROM card_batches WHERE id = ?",
        (batch.id,)).fetchone()
    assert int(row[0]) == 0
    pwds = _passwords_of(batch.id)
    assert pwds and all(len(p) == 6 for p in pwds), pwds


def test_an_imported_batch_inherits_it_too(app_ctx):
    """الاستيرادُ بلا نموذجٍ يرث — وإلّا عاد اللغمُ من بابٍ ثانٍ."""
    from app.radius.services.cards import get_cards_service
    _set_default("1")
    pid = _plan_id()
    res = get_cards_service().import_batch(
        actor="admin", plan_id=pid, package_name="مستورَدة",
        cards=[{"username": "imp-0001", "password": "x"}])
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT login_without_password FROM card_batches WHERE id = ?",
        (res["batch"].id,)).fetchone()
    assert int(row[0]) == 1


def test_the_batch_flag_decides_at_login_in_both_directions(app_ctx):
    """عند الدخول لا يُستشار إعدادٌ عامّ — صفُّ الحزمة وحدَه يحكم.

    وإلّا تبدّل سلوكُ بطاقاتٍ مطبوعةٍ ومبيعةٍ لأنّ أحدًا غيّر إعدادًا بعدها.
    """
    from app.radius.services import policy_engine as pe
    _set_default("1")
    closed = _card("22222222", batch_flag=0)
    opened = _card("33333333", batch_flag=1)
    assert pe._login_without_password(1, closed) is False
    assert pe._login_without_password(1, opened) is True


def test_unknown_username_is_never_opened(app_ctx):
    """اسمٌ ليس بطاقةً ولا مشتركًا: لا بابَ يُفتح له."""
    from app.radius.services import policy_engine as pe
    _set_default("1")
    assert pe._login_without_password(1, "99999999") is False


def test_a_subscriber_is_untouched_by_the_cards_default(app_ctx):
    """المفتاحُ `cards.*` للبطاقات — لا يسري على المشتركين."""
    from app.radius.db.connection import db
    from app.radius.services import policy_engine as pe
    _set_default("1")
    db().execute(
        "INSERT INTO subscribers(tenant_id, username, password, status,"
        " created_at) VALUES(1,'sub-1','p','enabled',datetime('now'))")
    db().commit()
    assert pe._login_without_password(1, "sub-1") is False


def test_a_broken_setting_never_opens_the_door(monkeypatch):
    """تعذّرت قراءةُ الإعداد ⇒ نبقى على الافتراض المُغلَق."""
    from app.radius.services import cards as cs

    from app.radius.db.repos import tenants_repo
    monkeypatch.setattr(tenants_repo, "get_setting",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert cs.network_cards_passwordless(1) is False


def test_the_setting_is_offered_in_the_settings_page():
    """حارسٌ نصّيّ: المفتاحُ معروضٌ للمشغّل لا مدفونٌ في القاعدة."""
    assert SETTING in _read("app/templates/radius/settings_page.html")
    assert SETTING in _read("app/radius/routes/settings.py")


# ── والحزمةُ تنزل **بلا كلمات مرورٍ أصلًا** ──────────────────────────
#
# «هو عاملها بدون باسوورد لكن نازلة بباسووردات.» كلمةٌ لا تُطلب ولا تُقارَن
# ومع ذلك تُطبع وتُعرض في الجدول: تُربك البائعَ والزبون وتُوهم أنّ الدخول
# ناقص. لوحةُ adv التي نُحاكيها تُخزّن `Cleartext-Password := ''` صراحةً.


def _passwords_of(batch_id):
    from app.radius.db.connection import db
    return [r[0] for r in db().execute(
        "SELECT password FROM cards WHERE batch_id = ?", (batch_id,))]


def test_passwordless_batch_generates_empty_passwords(app_ctx):
    batch = _generate(login_without_password=True, password_length=6)
    pwds = _passwords_of(batch.id)
    assert pwds and all(p == "" for p in pwds), pwds


def test_the_batch_records_zero_password_length(app_ctx):
    """تُحفظ 0 على الحزمة فتبقى متّسقةً لو أُعيدت الطباعةُ لاحقًا."""
    from app.radius.db.connection import db
    batch = _generate(login_without_password=True, password_length=6)
    row = db().execute("SELECT password_length FROM card_batches WHERE id = ?",
                       (batch.id,)).fetchone()
    assert int(row[0]) == 0


def test_a_normal_batch_still_gets_real_passwords(app_ctx):
    """حزمةٌ عاديّة لا تتأثّر — الكلماتُ تُولَّد بالطول المطلوب."""
    batch = _generate(password_length=6)
    pwds = _passwords_of(batch.id)
    assert pwds and all(len(p) == 6 for p in pwds), pwds


def test_the_form_disables_the_password_section(app_ctx=None):
    """حارسٌ نصّيّ: الحقلان مُعلَّمان والسكربتُ يُعطّلهما."""
    src = _read(GEN)
    assert src.count("data-pw-field") >= 2
    assert "getElementById('lwp')" in src
