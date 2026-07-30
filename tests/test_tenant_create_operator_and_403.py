"""MT119/MT120 — شبكةٌ بلا مدير، ورفضٌ يكذب في سببه.

**MT119**: أنشأ المالك شبكة «Paradise Net» بعد أن ملأ اسم مدير الشبكة
وكلمته، فوُلدت الشبكة **بلا مدير** ولم يستطع الزبون الدخول. السبب ليس أنّ
المسار يتجاهل الحقول — بل أنّ حقول قسم «مدير الشبكة» مثبَّتة على
``value=""`` بينما بقيّة الحقول تُعاد تعبئتها. فأيّ خطأ تحقّقٍ يُعيد رسم
النموذج يمسح هذا القسم **وحده** بصمت. وقد أصابه خطأ ``plan_tier غير
معروف: free`` فعلًا، فأعاد الإرسال والحقول فارغة وهو لا يدري.

**MT120**: مديرُ شبكةٍ فتح رابط شبكةٍ أخرى فرأى «ليس لديك صلاحية الوصول».
والرسالة تكذب في سببها: المشكلة ليست نقصَ صلاحيةٍ في شبكته بل أنّه في
شبكةٍ **ليست شبكته** — فيطلب صلاحيةً لن تُصلح شيئًا. ومعها كان الاستطلاع
يضرب 403 كلّ ثوانٍ بلا نهاية.
"""

import re
from pathlib import Path

import pytest

import app as app_pkg
from app.radius.routes import tenants as tenants_routes


TPL = Path(app_pkg.__file__).resolve().parent / "templates"


# ── MT119 — حقول المدير تنجو من خطأ التحقّق ──────────────────────────
@pytest.mark.parametrize("field", [
    "operator_username", "operator_password", "operator_full_name", "trial_days",
])
def test_operator_fields_are_repopulated(field):
    """قيمةٌ مثبَّتة على "" تمحو ما كتبه المشغّل عند أوّل خطأ تحقّق."""
    html = (TPL / "radius" / "tenants_form.html").read_text(encoding="utf-8")
    i = html.find(f'name="{field}"')
    assert i > 0, field
    window = html[i:i + 260]
    assert "request.form.get" in window, f"{field} لا يُعاد تعبئته"
    assert 'value=""' not in window, f"{field} ما زال مثبَّتًا على فارغ"


def test_route_still_seeds_the_operator():
    """الحارس النصّيّ: المسار يُنادي بذر المدير حين يصله الاسم."""
    import inspect
    src = inspect.getsource(tenants_routes.tenants_create)
    assert "create_trial" in src
    assert "operator_username" in src


def test_network_without_admin_warns_loudly():
    """الصمت هنا يُكتشف بعد أيّام — والزبون لا يستطيع الدخول أصلًا."""
    import inspect
    src = inspect.getsource(tenants_routes.tenants_create)
    assert "بلا مدير" in src, "لا تحذير عند إنشاء شبكةٍ بلا مدير"


# ── MT120 — الرفض يقول سببه الحقيقيّ ─────────────────────────────────
def test_membership_guard_tags_the_reason():
    import inspect
    from app.radius.middleware import tenant_resolver as tr

    src = inspect.getsource(tr)
    i = src.find("_enforce_slug_membership")
    assert i > 0
    body = src[i:i + 2200]
    assert "forbidden_reason" in body
    assert "forbidden_own_slug" in body, "بلا شبكة المستخدم لا يمكن عرض طريق"


def test_403_handler_distinguishes_the_two_cases():
    import inspect
    from app.radius.routes import blueprint

    src = inspect.getsource(blueprint)
    i = src.find("_friendly_forbidden")
    assert i > 0
    body = src[i:i + 1400]
    assert "wrong_tenant" in body
    assert "asked_slug" in body and "own_slug" in body


def test_403_template_names_both_networks_and_offers_a_way_out():
    html = (TPL / "radius" / "forbidden_403.html").read_text(encoding="utf-8")
    assert "wrong_tenant" in html
    assert "تخصّ شبكةً أخرى" in html
    assert "اذهب إلى شبكتك" in html
    assert "auth_logout" in html, "لا طريق لتبديل الحساب"


def test_json_callers_get_the_reason_too():
    """النوافذ المنبثقة تقرأ JSON لا HTML — فتظهر لها نفس الرسالة المضلِّلة."""
    import inspect
    from app.radius.routes import blueprint

    src = inspect.getsource(blueprint)
    i = src.find("_friendly_forbidden")
    body = src[i:i + 1400]
    assert '"reason": "wrong_tenant"' in body


# ── MT120 — الاستطلاع يتوقّف على بابٍ مغلق ───────────────────────────
def test_polling_stops_on_401_403():
    """403 كلّ ثوانٍ = ضربةٌ دائمة على الخادم وسطرٌ لا يُصلح شيئًا."""
    js = (Path(app_pkg.__file__).resolve().parent / "static" / "js"
          / "notif_live.js").read_text(encoding="utf-8")
    assert "stopPolling" in js
    assert re.search(r"status\s*===\s*401", js)
    assert re.search(r"status\s*===\s*403", js)
    assert "__hrPollTimers" in js, "المؤقّتات غير قابلة للإيقاف فعلًا"
