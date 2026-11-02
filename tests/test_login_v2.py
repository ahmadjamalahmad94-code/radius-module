"""صفحة دخول المدير — معيار HOBE v2 (حزمة المالك 01/11/2026).

يحرس: مبدّل اللغة يعمل قبل تسجيل الدخول (كان يحوّل لصفحة الدخول نفسها فلا تتغيّر
اللغة)، وسطر العائلة الحرفيّ + شعار HT، وبقاء آليّة الدخول (اسم مستخدم + كلمة مرور).
"""
from __future__ import annotations

import re

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app import create_app
    return create_app().test_client()


def test_language_switch_works_before_login(client):
    r = client.get("/admin/radius/set-locale?locale=en&next=/admin/radius/login")
    assert r.status_code == 302
    assert r.headers["location"].endswith("/admin/radius/login")
    html = client.get("/admin/radius/login").get_data(as_text=True)
    assert re.search(r'<html lang="en" dir="ltr">', html)
    assert "Welcome back" in html


def test_set_locale_rejects_foreign_next(client):
    r = client.get("/admin/radius/set-locale?locale=en&next=https://evil.example/x")
    assert r.status_code == 302
    assert "evil.example" not in r.headers["location"]


def test_login_page_v2_markup(client):
    html = client.get("/admin/radius/login").get_data(as_text=True)
    assert 'class="hs"' in html and "hs-rule" in html            # one navy canvas, rule for ≥900px
    assert "HOBE RADIUS" in html
    assert "img/login/ht-monogram.svg" in html
    plain = re.sub(r"<[^>]+>", "", html.split('class="hobe-family"', 1)[1].split("</footer>", 1)[0])
    assert re.sub(r"\s+", " ", plain).strip().endswith("من عائلة HOBE TECHNOLOGIES")
    assert "هذا التطبيق من مجموعة" not in html
    assert 'name="username"' in html and 'name="password"' in html
    assert "تذكرني" not in html and "نسيت كلمة المرور" not in html   # no fake rows
