# -*- coding: utf-8 -*-
"""حارس انحدار التدويل متعدّد اللغات (ar/en/fr/tr/es).

يضمن أن:
  • الموقع العربي الافتراضي سليم تمامًا (لا كسر، اتجاه rtl، نصوص ظاهرة).
  • اللغات الأربع تُرندر صفحات تمثيلية بنجاح (200) باتجاه ltr.
  • مبدّل اللغة يعرض اللغات الخمس.
  • الإنجليزية تُترجِم نصوص chrome الأساسية بلا تسريب عربي/إنجليزي.
  • صفحات بها '%' (مثل الإعدادات) لا تكسر الرندر بأي لغة.

شغّل هذا الملف وحده (عزل لكل ملف اختبار).
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")

from app import create_app  # noqa: E402

LOCALES = ("ar", "en", "fr", "tr", "es")
RTL = {"ar"}
# صفحات تمثيلية عبر القطاعات (تشمل الإعدادات لاحتوائها على '%').
PAGES = [
    "/admin/radius/",
    "/admin/radius/users",
    "/admin/radius/cards",
    "/admin/radius/settings",
    "/admin/radius/finance",
    "/admin/radius/communications",
    "/admin/radius/plans",
]
SWITCHER_NAMES = ["العربية", "English", "Français", "Türkçe", "Español"]


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config["WTF_CSRF_ENABLED"] = False
    return a


def _client(app, locale):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=True, tenant_id=1, permissions=["*"],
                 locale=locale)
    return c


def _html(app, locale, url="/admin/radius/"):
    r = _client(app, locale).get(url, follow_redirects=True)
    return r.status_code, r.get_data(as_text=True)


def _visible(html: str) -> str:
    """يزيل ما لا يراه المستخدم قبل فحص التسريب: تعليقات HTML وكتل
    style/script (تحوي تعليقات/سلاسل عربية مقصودة في الكود)."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html,
                  flags=re.DOTALL | re.IGNORECASE)
    return html


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("url", PAGES)
def test_pages_render_ok(app, locale, url):
    """كل صفحة تمثيلية تُرندر 200 بكل لغة (يشمل صفحات '%')."""
    code, _ = _html(app, locale, url)
    assert code == 200, f"{url} @ {locale} رجع {code}"


@pytest.mark.parametrize("locale", LOCALES)
def test_direction(app, locale):
    """اتجاه <html> صحيح: rtl للعربية، ltr لغيرها."""
    _, html = _html(app, locale)
    exp = "rtl" if locale in RTL else "ltr"
    assert f'dir="{exp}"' in html, f"{locale} يفتقد dir={exp}"


@pytest.mark.parametrize("locale", LOCALES)
def test_switcher_lists_all_five(app, locale):
    """مبدّل اللغة يعرض اللغات الخمس في كل لغة."""
    _, html = _html(app, locale)
    for name in SWITCHER_NAMES:
        assert name in html, f"المبدّل يفتقد «{name}» @ {locale}"


def test_arabic_default_intact(app):
    """حارس الانحدار: العربية الافتراضية تعرض نصوصها الأصلية سليمة."""
    code, html = _html(app, "ar")
    assert code == 200
    for s in ("المشتركون", "البطاقات", "تسجيل الخروج"):
        assert s in html, f"العربية تفتقد «{s}» — انحدار!"


def test_english_translates_chrome_no_leak(app):
    """الإنجليزية تترجم chrome الأساسي بلا تسريب باتجاهين (نصوص مرئية).

    أزواج مترجَمة معروفة: نتأكّد أن الترجمة تظهر بالإنجليزية، وأن أصلها
    العربي لا يظهر مرئيًّا بالإنجليزية، والعكس. (الفحص على النص المرئي فقط
    بعد إزالة التعليقات/style/script حيث العربية مقصودة في الكود.)"""
    pairs = [("المشتركون", "Subscribers"), ("البطاقات", "Cards"),
             ("تسجيل الخروج", "Sign out")]
    en = _visible(_html(app, "en")[1])
    ar = _visible(_html(app, "ar")[1])
    for ar_s, en_s in pairs:
        assert en_s in en, f"en يفتقد «{en_s}»"
        assert en_s not in ar, f"تسريب إنجليزي «{en_s}» في العربية"
        assert ar_s not in en, f"تسريب عربي «{ar_s}» في الإنجليزية"
