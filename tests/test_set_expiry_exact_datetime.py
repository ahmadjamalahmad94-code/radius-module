"""تعيينُ **تاريخِ وساعةِ** انتهاء المشترك بالضبط — لا إضافةَ مدّةٍ فحسب.

🔴 طلبُ المالك: «بدي تمديد أو تعيين التاريخ للمشتركين — يكون تحديد تاريخ
وساعة الانتهاء بالظبط».

وكان في الشاشتين نقصان مختلفان:

* **نافذة «إضافة وقت»** تسأل «كم أُضيف؟» ولا تسأل «متى ينتهي؟». فمن أراد
  نهايةً بعينها حسب الفارقَ بيده، ثمّ أخطأ حين تراكَم تمديدٌ على تمديد.
* **نموذجُ المشترك** يأخذ يومًا/شهرًا/سنة ويفرض `23:59:59` — فلا ينتهي
  اشتراكٌ ظهرًا ولو أراد صاحبُه.

وتحتهما عطبٌ ثالثٌ أهدأ: اللحظةُ المختارة كانت تُكتب خامًا بوصفها UTC مع
أنّ المشغّل يفكّر بساعته هو. ففي لوحةٍ على +3 تصير «23:59» ⇒ `23:59 UTC`
= **02:59 من اليوم التالي**: يومٌ زائدٌ بثلاث ساعاتٍ لم يبعه أحد. والعكسُ
في العرض: نهايةٌ منتصفَ الليل تُعرض «21:00».

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta

import pytest

TPL_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "templates", "radius")
LIST_TPL = os.path.join(TPL_DIR, "users_list.html")
FORM_TPL = os.path.join(TPL_DIR, "users_form.html")
ROUTES = os.path.join(os.path.dirname(__file__), "..", "app", "radius", "routes", "users.py")


# ════════════════════════════════ التركيب ════════════════════════════════

@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "setexp.db")
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
        # دمشق = +3 ثابتة بلا توقيتٍ صيفيّ ⇒ نتائجُ الاختبار لا تتغيّر بالموسم.
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Damascus")
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _mk(username, expire_at, balance=0.0):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,"
        " currency,speed_down_kbps,speed_up_kbps,quota_total_mb,created_at,updated_at)"
        " VALUES(1,'باقة',1440,30,10,'ILS',2048,2048,0,datetime('now'),datetime('now'))")
    pid = int(cur.lastrowid)
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,user_type,"
        " expire_at,balance,created_at) VALUES(1,?,'pw',?,'enabled','user',?,?,datetime('now'))",
        (username, pid, expire_at.isoformat() + "Z", balance))


def _expire_of(username):
    from app.radius.db.helpers import parse_dt
    r = db().execute("SELECT expire_at FROM subscribers WHERE username=?", (username,)).fetchone()
    return parse_dt(r["expire_at"])


def _svc():
    from app.radius.services.users import get_users_service
    return get_users_service()


# ═════════════════════════ الخدمة: التعيينُ الدقيق ═════════════════════════

def test_set_expiry_writes_the_exact_instant(app_ctx):
    """🔴 جوهرُ الطلب: اللحظةُ المكتوبةُ هي المختارةُ نفسُها — بلا انزياح.

    لا مرساةَ ولا `max(now, …)` ولا تقريبَ إلى آخر اليوم: من اختار الثالثةَ
    والنصفَ يريدها الثالثةَ والنصف.
    """
    _mk("u1", datetime.utcnow() + timedelta(days=5))
    target = datetime(2026, 12, 31, 15, 30, 0)
    _svc().set_expiry(actor="t", username="u1", expire_at=target)
    assert _expire_of("u1").replace(microsecond=0) == target


def test_set_expiry_may_shorten_or_end_now(app_ctx):
    """التقصيرُ طلبٌ مشروع — «ينتهي اشتراكُه الليلةَ» قرارٌ إداريّ لا خطأ.

    التمديدُ يرفع النهايةَ أبدًا؛ أمّا التعيينُ فصريحٌ في الاتّجاهين، وإلّا
    لَما أمكن إنهاءُ اشتراكٍ إلّا بحذف الحساب.
    """
    _mk("u2", datetime.utcnow() + timedelta(days=30))
    target = datetime.utcnow().replace(microsecond=0) - timedelta(hours=2)
    _svc().set_expiry(actor="t", username="u2", expire_at=target)
    assert _expire_of("u2").replace(microsecond=0) == target


def test_set_expiry_does_not_stack_on_previous_extends(app_ctx):
    """تعيينان متتاليان ينتهيان إلى اللحظة نفسِها — لا تتراكم.

    هذا ما يميّزه عن `extend_time`: تكرارُ التمديد يدفع النهايةَ مرّةً بعد
    مرّة، وتكرارُ التعيين لا يزحزحها.
    """
    _mk("u3", datetime.utcnow() + timedelta(days=1))
    target = datetime(2026, 11, 5, 9, 0, 0)
    _svc().set_expiry(actor="t", username="u3", expire_at=target)
    _svc().set_expiry(actor="t", username="u3", expire_at=target)
    assert _expire_of("u3").replace(microsecond=0) == target


def test_set_expiry_rejects_a_missing_moment(app_ctx):
    """بلا لحظةٍ لا تعيين — ولا يُخترع «الآن» صامتًا."""
    from app.radius.core.errors import RadiusValidationError
    _mk("u4", datetime.utcnow() + timedelta(days=1))
    with pytest.raises(RadiusValidationError):
        _svc().set_expiry(actor="t", username="u4", expire_at=None)


def test_paid_set_expiry_debits_the_wallet(app_ctx):
    """المدفوعُ يخصم فعلًا — نفسُ عقدِ `extend_time` لأنّ الذيل مشترك."""
    _mk("u5", datetime.utcnow() + timedelta(days=1), balance=50.0)
    _svc().set_expiry(actor="t", username="u5",
                      expire_at=datetime.utcnow() + timedelta(days=10),
                      charge_mode="paid", amount=12.5)
    row = db().execute("SELECT balance FROM subscribers WHERE username='u5'").fetchone()
    assert float(row["balance"]) == pytest.approx(37.5)


def test_audit_records_set_expiry_with_before_and_after(app_ctx):
    """السجلُّ يقول «كان X ← صار Y» — وبفعلٍ باسمه لا مندسًّا في «تمديد»."""
    old = datetime(2026, 10, 1, 12, 0, 0)
    _mk("u6", old)
    _svc().set_expiry(actor="t", username="u6", expire_at=datetime(2026, 10, 9, 8, 0, 0))
    row = db().execute(
        "SELECT action FROM audit_log WHERE target_id='u6' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action"] == "set_expiry"


def test_extend_time_still_stacks(app_ctx):
    """حارسُ الانحدار: إعادةُ التركيب لم تُحوّل التمديدَ إلى تعيين."""
    base = datetime.utcnow() + timedelta(days=2)
    _mk("u7", base)
    _svc().extend_time(actor="t", username="u7", minutes=60)
    _svc().extend_time(actor="t", username="u7", minutes=60)
    delta = _expire_of("u7") - base
    assert 115 <= delta.total_seconds() / 60 <= 125


# ═══════════════════════ المحوّل: محلّيّ ⇄ UTC ═══════════════════════

def test_from_local_shifts_by_the_panel_timezone(app_ctx):
    """🔴 العطبُ الهادئ: «18:00» عند المشغّل ليست «18:00» في القاعدة.

    على +3 تُخزَّن 15:00. كتابتُها خامًا تعني أنّ الاشتراك يعيش ثلاثَ ساعاتٍ
    زائدة لم يدفع ثمنَها أحد.
    """
    from app.radius.core.system_config import from_local
    assert from_local("2026-09-01 18:00", tenant_id=1) == datetime(2026, 9, 1, 15, 0)


def test_from_local_accepts_the_browser_t_separator(app_ctx):
    """`datetime-local` يرسل `YYYY-MM-DDTHH:MM` — الشكلُ الذي يصل فعلًا."""
    from app.radius.core.system_config import from_local
    assert from_local("2026-09-01T06:30", tenant_id=1) == datetime(2026, 9, 1, 3, 30)


def test_from_local_round_trips_with_to_local(app_ctx):
    """ما يُعرض يُقرأ فيعود كما كان — وإلّا انزاح التاريخُ كلَّ حفظة.

    نموذجُ المشترك يعرض النهايةَ ثمّ يُعيد إرسالها كما هي عند أيّ حفظٍ روتينيّ؛
    فلو اختلّت الرحلةُ لَزحف التاريخُ ثلاثَ ساعاتٍ في كلّ مرّة.
    """
    from app.radius.core.system_config import from_local, to_local
    stored = datetime(2026, 7, 4, 21, 5)
    assert from_local(to_local(stored, tenant_id=1), tenant_id=1) == stored


def test_from_local_bare_date_takes_the_default_time(app_ctx):
    """تاريخٌ بلا ساعة + `23:59:59` = سلوكُ الحقلِ القديم محفوظًا."""
    from app.radius.core.system_config import from_local
    got = from_local("2026-09-01", tenant_id=1, default_time="23:59:59")
    assert got == datetime(2026, 9, 1, 20, 59, 59)


def test_from_local_empty_is_none_not_now(app_ctx):
    """فارغٌ = لا قرار. اختراعُ «الآن» هنا يُنهي اشتراكًا بحفظةٍ روتينيّة."""
    from app.radius.core.system_config import from_local
    assert from_local("", tenant_id=1) is None
    assert from_local("   ", tenant_id=1) is None
    assert from_local("كلامٌ ليس تاريخًا", tenant_id=1) is None


# ══════════════════════════ الواجهة والمسار ══════════════════════════

@pytest.fixture(scope="module")
def list_html() -> str:
    return io.open(LIST_TPL, encoding="utf-8").read()


@pytest.fixture(scope="module")
def form_html() -> str:
    return io.open(FORM_TPL, encoding="utf-8").read()


@pytest.fixture(scope="module")
def routes_src() -> str:
    return io.open(ROUTES, encoding="utf-8").read()


def _extend_modal(html: str) -> str:
    i = html.index('data-usq-modal="extend"')
    return html[i:i + 9000]


def test_modal_offers_an_exact_moment_field(list_html):
    """🔴 الانحدارُ بعينه: نافذةٌ بلا حقلِ لحظةٍ = «متى ينتهي؟» بلا جواب."""
    block = _extend_modal(list_html)
    assert 'data-usq-emode-seg="exact"' in block, "لا وضعَ للتعيين"
    assert 'type="datetime-local"' in block, "لا حقلَ لتاريخٍ وساعة"
    assert 'name="expire_at"' in block, "الحقلُ لا يُرسَل باسمٍ يعرفه المسار"


def test_exact_field_starts_disabled_so_duration_mode_is_unchanged(list_html):
    """الحقلُ المُعطَّل لا يدخل الطلب — وهو الفاصلُ بين المسلكين خادميًّا.

    لو أُرسل فارغًا لَما ضرّ (المسارُ يتجاهل الفارغ)، لكنّ التعطيلَ يجعل
    النيّةَ صريحةً في الطلب نفسِه.
    """
    block = _extend_modal(list_html)
    at = block[block.index("data-usq-exact-at"):]
    at = at[:at.index(">")]
    assert "disabled" in at


def test_price_in_exact_mode_uses_the_same_formula_as_the_server(list_html):
    """السعرُ يُشتقّ من الفارق عن **الأبعد بين نهايته والآن** — كالخادم.

    اختلافُ المعادلتين يعني رقمًا على الشاشة وآخرَ في الدفتر.
    """
    assert "function exactMinutes" in list_html
    fn = list_html[list_html.index("function exactMinutes"):]
    fn = fn[:fn.index("\n  }")]
    assert "activeRow?.dataset.expire" in fn, "لا يقرأ نهايته الحاليّة"
    assert "t > anchor" in fn, "لا يرسو على الأبعد بين النهاية والآن"


def test_row_expiry_is_local_not_raw_utc(list_html):
    """🔴 `data-expire` كان UTC خامًا — عليه يُبنى العرضُ وحسابُ السعر معًا."""
    assert "data-expire=\"{{ u.expire_at|dt_local(" in list_html
    assert "data-expire=\"{{ u.expire_at.strftime" not in list_html


def test_subscriber_form_has_an_hour_field(form_html):
    """🔴 الانحدار: منتقي يوم/شهر/سنة وحدَه ⇒ لا انتهاءَ إلّا آخرَ اليوم."""
    assert 'name="expire_time"' in form_html
    assert 'type="time"' in form_html


def test_form_shows_the_stored_expiry_in_local_time(form_html):
    """الأجزاءُ المعروضة تُقرأ بساعة المشغّل لا بـUTC."""
    assert "dt_local('%Y|%m|%d|%H:%M')" in form_html
    assert "sub.expire_at.day" not in form_html


def test_route_no_longer_hardcodes_end_of_day_as_utc(routes_src):
    """🔴 `datetime(y, m, d, 23, 59, 59)` كان يعني «23:59:59 UTC» — يومٌ زائد."""
    assert "datetime(_e_y, _e_m, _e_d, 23, 59, 59)" not in routes_src
    assert "from_local(f\"{_e_y:04d}-{_e_m:02d}-{_e_d:02d} {_e_t}\")" in routes_src


def test_route_sends_expire_at_down_the_set_expiry_path(routes_src):
    """وجودُ `expire_at` يحوّل المسار — ولا يُقرأ `minutes` عندها أصلًا."""
    assert "_svc.set_expiry(expire_at=_exp, **_kw)" in routes_src
    assert "def _form_expire_at()" in routes_src


def test_bulk_route_supports_setting_one_shared_deadline(routes_src):
    """نهايةٌ واحدةٌ لجميع المحدَّدين — «كلُّهم ينتهون آخرَ الشهر»."""
    i = routes_src.index("def users_extend_bulk()")
    block = routes_src[i:i + 4000]
    assert "expire_at = _form_expire_at()" in block
    assert "svc.set_expiry(" in block
