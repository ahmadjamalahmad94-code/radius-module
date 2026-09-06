"""مشتركٌ يدخل **باسمه وحدَه** — مفتاحٌ في نموذج الإنشاء والتعديل.

الطريقُ كان مقطوعًا في نصفه: عمودُ ``subscribers.login_without_password``
موجودٌ منذ الهجرة 171 وسياسةُ المصادقة تقرؤه فعلًا، لكن لا الـDTO يحمله ولا
المستودعُ يكتبه ولا النموذجُ يعرضه — فلا سبيلَ لتشغيله إلّا بتحرير قاعدة
البيانات باليد. هذه الاختبارات تُثبّت الطريقَ كاملًا:

  1. العلَمُ يدور ذهابًا وإيابًا عبر ``UsersService`` والمستودع.
  2. إطفاؤه **لا يمسح** الكلمةَ المخزَّنة، فإعادةُ تفعيله تُعيد الدخولَ بها.
  3. ``_form_dto`` يقرأ المفتاحَ من النموذج.
  4. النموذجُ يعرض المفتاحَ ولا يفرض `required` على الكلمة حين يُرفَع.

شغّل هذا الملف وحدَه (عزلُ الاختبارات لكلّ ملف).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_lwp_sub_")
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


def _mk(username, *, password, flag):
    from app.radius.core.types import Subscriber
    return Subscriber(id=None, username=username, password=password,
                      status="enabled", user_type="subscriber",
                      login_without_password=flag,
                      expire_at=datetime.utcnow() + timedelta(days=30))


# ───────────────── 1. دورةُ الحفظ والقراءة ─────────────────

def test_flag_round_trips_through_the_repo(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        subscribers_repo.upsert_subscriber(_mk("u-on", password="s3cret", flag=True))
        got = subscribers_repo.get_subscriber(1, "u-on")
        assert got.login_without_password is True


def test_flag_defaults_off(app):
    """الافتراضُ مُطفأ — إضافةُ الميزة لا تفتح حسابًا قائمًا."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        subscribers_repo.upsert_subscriber(_mk("u-off", password="s3cret", flag=False))
        assert subscribers_repo.get_subscriber(1, "u-off").login_without_password is False


def test_flag_survives_the_service_create_and_update(app):
    with app.app_context():
        from dataclasses import replace
        from app.radius.services.users import get_users_service
        svc = get_users_service()
        saved = svc.create(actor="t", sub=_mk("u-svc", password="pw1", flag=True))
        assert saved.login_without_password is True
        again = svc.update(actor="t", sub=replace(saved, login_without_password=False))
        assert again.login_without_password is False


# ───────────────── 2. الكلمةُ لا تُمسح ─────────────────

def test_turning_the_flag_on_keeps_the_stored_password(app):
    """🔑 المفتاحُ يُسكِت الفحصَ ولا يمسح السرّ — فإطفاؤه لاحقًا يُعيد الدخولَ
    بالكلمة نفسِها بلا إعادةِ تعيينٍ ولا مكالمةٍ مع الزبون."""
    with app.app_context():
        from dataclasses import replace
        from app.radius.services.users import get_users_service
        svc = get_users_service()
        saved = svc.create(actor="t", sub=_mk("u-keep", password="keepme", flag=False))
        on = svc.update(actor="t", sub=replace(saved, login_without_password=True))
        assert on.password == "keepme"
        # والحقلُ المعطَّل في النموذج لا يصل أصلًا ⇒ كلمةٌ فارغةٌ في الـDTO
        blank = svc.update(actor="t", sub=replace(on, password=""))
        assert blank.password == "keepme"


def test_auth_accepts_the_name_alone_only_while_flagged(app):
    """السلوكُ الفعليّ عند الرديوس — لا مجرّدَ عمودٍ محفوظ."""
    with app.app_context():
        from dataclasses import replace
        from app.radius.services.users import get_users_service
        from app.radius.services.policy_engine import AuthRequest, authorize
        svc = get_users_service()
        saved = svc.create(actor="t", sub=_mk("2050", password="", flag=True))

        def _auth():
            return authorize(AuthRequest(username="2050", tenant_id=1,
                                         nas_ip="10.0.0.1",
                                         chap_password="anything",
                                         chap_challenge="x"))
        assert _auth().ok
        svc.update(actor="t", sub=replace(saved, login_without_password=False))
        d = _auth()
        assert not d.ok and d.reason == "password_wrong"


# ───────────────── 3. النموذج يقرأ ويعرض ─────────────────

def test_form_dto_reads_the_toggle(app):
    with app.app_context():
        from app.radius.routes.users import _form_dto
        with app.test_request_context(
                "/", method="POST",
                data={"username": "u-form", "password": "",
                      "login_without_password": "1", "status": "enabled"}):
            assert _form_dto().login_without_password is True
        with app.test_request_context(
                "/", method="POST",
                data={"username": "u-form", "password": "pw", "status": "enabled"}):
            assert _form_dto().login_without_password is False


def _tpl() -> str:
    return io.open("app/templates/radius/users_form.html", encoding="utf-8").read()


def test_template_exposes_the_toggle():
    t = _tpl()
    assert 'name="login_without_password"' in t, "المفتاحُ غائبٌ عن نموذج المشترك"
    assert "id=\"uf-lwp\"" in t


def test_template_drops_required_when_flag_is_on():
    """حقلٌ مطلوبٌ ومعطَّلٌ معًا يمنع الحفظَ بلا رسالةٍ مفهومة."""
    t = _tpl()
    assert "{% if is_new and not sub.login_without_password %}required{% endif %}" in t


def test_template_ties_the_password_field_to_the_switch():
    t = _tpl()
    assert "data-uf-pw-field" in t
    assert "uf-lwp" in t
