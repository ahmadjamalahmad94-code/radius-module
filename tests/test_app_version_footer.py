# -*- coding: utf-8 -*-
"""التذييلُ يعرض الإصدارَ **الجاري** لا رقمًا مخبوءًا.

كان هذا الملفُّ يوقّت نفسَه على إصدارِ يومِه («1.1.0») فيفشل عند كلّ
رفعِ إصدارٍ بعدَه — إخفاقٌ لا يدلّ على عطبٍ بل على أنّ التوكيدَ تقادم.
فصار يُوكّد السلوكَ: أنّ ما يُعرَض هو ما يعمل فعلًا، وأنّ «0.1.0»
المخبوءةَ القديمةَ لم تعُد تظهر.
"""
import re

from app.radius.core import app_version


def test_app_version_is_a_real_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", app_version.APP_VERSION), \
        "APP_VERSION يجب أن يكون رقمَ إصدارٍ ثلاثيًّا"


def test_running_version_matches_baked_version(monkeypatch):
    """بلا تجاوزٍ من المشغّل: الجاري = المخبوز."""
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    assert app_version.running_version() == app_version.APP_VERSION


def test_operator_override_wins(monkeypatch):
    """`HOBERADIUS_VERSION` يتقدّم — به تُختبر ترقياتٌ قبل خبزِها."""
    monkeypatch.setenv("HOBERADIUS_VERSION", "9.9.9")
    assert app_version.running_version() == "9.9.9"


def test_footer_renders_running_version(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    from app import create_app

    app = create_app()
    rv = app.jinja_env.globals["running_version"]
    assert rv() == app_version.APP_VERSION
    rendered = app.jinja_env.from_string(
        "{{ _('الإصدار %(v)s', v=running_version()) }}"
    ).render()
    assert app_version.APP_VERSION in rendered
    # «0.1.0» كانت مخبوءةً في التذييل قبل ربطِه بالإصدار الجاري.
    assert "0.1.0" not in rendered
