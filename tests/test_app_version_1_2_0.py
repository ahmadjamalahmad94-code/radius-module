# -*- coding: utf-8 -*-
"""مقارنةُ الإصدارات وعلامةُ «مثبَّت» في صفحة تحديثِ النظام.

كان يوقّت نفسَه على «1.2.0» فيفشل عند كلّ رفعٍ تالٍ. صار يُوكّد
منطقَ المقارنة نفسَه — وهو ما يقرّر: هل يرى الزبونُ تحديثًا متاحًا؟
"""
from app.radius.core import app_version


def test_is_newer_orders_releases():
    assert app_version.is_newer("1.2.0", "1.1.0") is True
    assert app_version.is_newer("1.10.0", "1.9.0") is True, \
        "المقارنةُ رقميّةٌ لا معجميّة — وإلّا بدت 1.10 أقدمَ من 1.9"
    assert app_version.is_newer("1.1.0", "1.2.0") is False
    assert app_version.is_newer("1.2.0", "1.2.0") is False


def test_customer_on_older_build_sees_current_as_newer():
    assert app_version.is_newer(app_version.APP_VERSION, "0.1.0") is True


def test_installed_marker_renders_running_version(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    from app import create_app

    app = create_app()
    rendered = app.jinja_env.from_string(
        "{{ _('محدّث — إصدار %(v)s', v=running_version()) }}"
    ).render()
    assert app_version.APP_VERSION in rendered
    assert "محدّث" in rendered
