"""«إدارة البيانات»: أيُّ حقولِ نموذجِ المشترك تظهر لصاحب هذه الشبكة.

ثلاثةُ عهودٍ تُثبَّت هنا:

  1. **الافتراضُ ظاهر.** شبكةٌ لم يلمس صاحبُها الصفحةَ ترى نموذجَها كما كان
     — لا يُخفى حقلٌ لأنّ ميزةً وُلدت.
  2. **الحقولُ الإلزاميّة لا مفتاحَ لها.** ليست في السجلّ أصلًا، فلا سبيلَ
     لإخفائها لا بالواجهة ولا بحقنِ مفتاحٍ في الـPOST.
  3. 🔑 **الإخفاءُ عرضٌ لا حذف.** الحقلُ المُطفأ يبقى في الصفحة وفي الـPOST؛
     يُخفى بالـCSS وحدَها. ولو حُذف من الصفحة لوصل فارغًا إلى `_form_dto`
     فكُتب فارغًا فوق ما في القاعدة — أي أنّ إخفاءً «نظيفًا» كان سيمحو
     مدينةَ المشترك وبريدَه عند أوّل حفظ. هذا الملفّ يمنع تلك الانزلاقة.

شغّل هذا الملفّ وحدَه (عزلُ الاختبارات لكلّ ملف).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dfvis_")
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


CSRF = "df-csrf-token"


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "df_admin"
        sess["admin_name"] = "DF Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = CSRF


# ───────────────── 1. الافتراضُ ظاهر ─────────────────

def test_everything_is_visible_by_default(app):
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        vis = sff.visibility(1)
        assert vis and all(vis.values())
        assert sff.hidden_keys(1) == ()
        assert sff.hidden_css(1) == ""


def test_registry_is_not_empty_and_keys_are_unique(app):
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        keys = sff.field_keys()
        assert len(keys) > 20
        assert len(set(keys)) == len(keys), "مفتاحٌ مكرَّرٌ في السجلّ"


# ───────────────── 2. الإلزاميّةُ بلا مفتاح ─────────────────

@pytest.mark.parametrize("core_key", [
    "username", "plan_id", "custom_speed", "temporary_speed",
    "name_first", "name_second", "mobile",
])
def test_core_fields_have_no_toggle(app, core_key):
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        assert core_key in sff.CORE
        assert core_key not in sff.field_keys()


def test_a_forged_post_cannot_hide_a_core_field(app):
    """حقنُ مفتاحٍ إلزاميٍّ في الـPOST لا يُخفيه — السجلُّ هو الحَكَم."""
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        # «visible» تحمل حقلًا واحدًا فقط ⇒ كلُّ ما في السجلّ يُطفأ،
        # لكنّ الإلزاميّة ليست فيه أصلًا فلا تتأثّر.
        sff.set_visibility(1, {"city"})
        css = sff.hidden_css(1)
        assert 'name="username"' not in css
        assert 'name="plan_id"' not in css
        assert 'name="mobile"' not in css


# ───────────────── 3. الحفظُ والأثر ─────────────────

def test_toggling_off_persists_and_produces_css(app):
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        keep = set(sff.field_keys()) - {"city", "email"}
        changed = sff.set_visibility(1, keep, by=1)
        assert changed == 2
        assert set(sff.hidden_keys(1)) == {"city", "email"}
        css = sff.hidden_css(1)
        assert 'name="city"' in css and 'name="email"' in css
        assert "display: none" in css


def test_turning_a_field_back_on_clears_it(app):
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        sff.set_visibility(1, set(sff.field_keys()) - {"remark"})
        assert sff.hidden_keys(1) == ("remark",)
        sff.set_visibility(1, set(sff.field_keys()))
        assert sff.hidden_keys(1) == ()
        assert sff.hidden_css(1) == ""


def test_settings_survive_a_fresh_read(app):
    """الإعداداتُ في قاعدة البيانات لا في الجلسة — تبقى بعد الخروج."""
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        from app.radius.db.repos import tenants_repo
        sff.set_visibility(1, set(sff.field_keys()) - {"national_id"})
        raw = tenants_repo.get_setting(
            1, sff.SETTING_PREFIX + "national_id", "1")
        assert str(raw) == "0"


# ───────────────── 4. المسار والصفحة ─────────────────

def test_page_renders_and_saves(app):
    with app.test_client() as client:
        _auth(client)
        assert client.get("/admin/radius/subscriber-fields").status_code == 200
        res = client.post("/admin/radius/subscriber-fields",
                          data={"visible": ["city", "email"],
                                "_csrf_token": CSRF},
                          follow_redirects=False)
        assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        hidden = set(sff.hidden_keys(1))
        assert "city" not in hidden and "email" not in hidden
        assert "remark" in hidden, "الغيابُ عن الـPOST يجب أن يُطفئ"


# ───────────────── 5. 🔑 إخفاءٌ لا حذف ─────────────────

def _form_tpl() -> str:
    return io.open("app/templates/radius/users_form.html",
                   encoding="utf-8").read()


def test_form_hides_by_css_not_by_dropping_inputs():
    """لو صار الإخفاءُ بحذفِ الحقلِ من الصفحة لمُحيت بياناتُ المشتركين."""
    t = _form_tpl()
    assert 'id="uf-field-visibility"' in t, "حقنُ أنماط الإخفاء غائب"
    assert "subscriber_field_css()" in t


@pytest.mark.parametrize("field_name", [
    "city", "district", "email", "national_id", "remark",
    "payment_method", "static_ip", "device_count", "pppoe_username",
])
def test_optional_inputs_stay_in_the_page_unconditionally(field_name):
    """كلُّ حقلٍ قابلٍ للإخفاء ما زال مُدخَلًا غيرَ مشروطٍ في القالب.

    فيصل في الـPOST بقيمته المحفوظة، ويعود كما هو إلى القاعدة."""
    t = _form_tpl()
    assert ('name="%s"' % field_name) in t


def test_hidden_css_targets_wrappers_not_inputs(app):
    """نُخفي غلافَ الحقل لا المُدخَلَ نفسَه — `display:none` على مُدخَلٍ
    لا يمنع إرسالَه، لكنّ إخفاءَ الغلاف يُخفي العنوانَ والشرحَ معه."""
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        sff.set_visibility(1, set(sff.field_keys()) - {"city"})
        css = sff.hidden_css(1)
        assert css.startswith(".uf-field:has(") or ".uf-field:has(" in css
        assert "disabled" not in css


def test_rendered_form_hides_the_field_yet_keeps_its_input(app):
    """البرهانُ الحيّ: الصفحةُ المرسومةُ تحمل قاعدةَ الإخفاء **و** المُدخَل.

    فالمشغّلُ لا يرى الحقل، والمتصفّحُ يُرسله مع الحفظ بقيمته المحفوظة —
    فلا تُمحى بيانات."""
    with app.app_context():
        from app.radius.services import subscriber_form_fields as sff
        sff.set_visibility(1, set(sff.field_keys()) - {"city", "remark"})
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/users/new")
        assert res.status_code == 200, res.status_code
        html = res.get_data(as_text=True)
        assert 'id="uf-field-visibility"' in html
        assert '.uf-field:has([name="city"])' in html
        assert 'name="city"' in html, "المُدخَل اختفى — هذا يمحو البيانات"
        assert 'name="remark"' in html
        # وحقلٌ لم يُطفأ لا تُكتب له قاعدةُ إخفاء
        assert '.uf-field:has([name="email"])' not in html
