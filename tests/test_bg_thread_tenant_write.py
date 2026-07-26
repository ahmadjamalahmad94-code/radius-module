"""MT64 — الكتابة من خيطٍ خلفيّ تهبط في شبكة مُنشئها لا في جهة المزوّد.

علّةٌ إنتاجيّة: توليد حزمة كروت تدريجيًّا يعمل في ``threading.Thread``
داخل ``app.app_context()`` ويضبط ``g.tenant_id`` صراحةً. كان
``_effective_tid`` يشترط ``has_request_context()`` — وهو غائبٌ في الخيط —
فتغلب قيمة الـDTO الافتراضيّة (``DEFAULT_TENANT_ID`` = 1 وهي **صادقة**):
  • الحزمة و٢٠٠ بطاقة تُكتب في **جهة المزوّد** (اختراق عزل)،
  • ثمّ يُبحَث عنها بجهة الشبكة فترجع ``None`` ⇒
    ``'NoneType' object has no attribute 'plan_id'``.

يَقفل هذا الاختبار السلوكَ في **الحالتين**: خيطٌ بسياق تطبيق، وخارج أيّ
سياق (حيث يجب أن تُحترَم قيمة الـDTO لأنّ العمّال يمرّرونها صراحةً).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import pytest

from app.radius.core.tenant import DEFAULT_TENANT_ID
from app.radius.integration.sqlite_adapter import _effective_tid as adapter_tid
from app.radius.stores.cards_store import _effective_tid as cards_tid

_TENANT = 7          # شبكةٌ ليست جهة المزوّد
_IMPLS = (("cards_store", cards_tid), ("sqlite_adapter", adapter_tid))


@pytest.fixture()
def app():
    """تطبيقٌ معزول بقاعدةٍ مؤقّتة (نفس نمط بقيّة الاختبارات)."""
    d = tempfile.mkdtemp(prefix="mt64-")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(d, "t.db")
    sys.path.insert(0, os.getcwd())
    from app import create_app
    yield create_app()


def test_thread_with_app_context_honours_resolved_tenant(app):
    """🔴 جوهر العلّة: خيطٌ يضبط g.tenant_id ⇒ الكتابة في تلك الشبكة."""
    results: dict[str, int] = {}
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            from flask import g
            with app.app_context():
                g.tenant_id = _TENANT
                for name, fn in _IMPLS:
                    # الـDTO يحمل الافتراضيّة الصادقة (1) — كما يأتي من النماذج
                    results[name] = fn(DEFAULT_TENANT_ID)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=20)

    assert not errors, f"الخيط رفع استثناءً: {errors}"
    for name, _ in _IMPLS:
        assert results.get(name) == _TENANT, (
            f"{name}: كُتب في الجهة {results.get(name)} بدل {_TENANT} — "
            "اختراق عزل: بيانات الشبكة تهبط في جهة المزوّد")


def test_request_context_still_wins(app):
    """لا انحدار: داخل طلبٍ حيّ الجهة المحلولة تَحكم كما كانت."""
    from flask import g
    with app.test_request_context("/"):
        g.tenant_id = _TENANT
        for name, fn in _IMPLS:
            assert fn(DEFAULT_TENANT_ID) == _TENANT, name
            assert fn(99) == _TENANT, f"{name}: الطلب يجب أن يَغلب الـDTO"


def test_outside_any_context_dto_is_respected():
    """خارج أيّ سياق: العمّال يمرّرون الجهة صراحةً فتُحترَم قيمتها."""
    for name, fn in _IMPLS:
        assert fn(_TENANT) == _TENANT, name
        assert fn(None) == DEFAULT_TENANT_ID, f"{name}: بلا قيمة ⇒ الافتراضيّة"


def test_app_context_without_resolved_tenant_falls_back_to_dto(app):
    """سياق تطبيقٍ بلا g.tenant_id (عامل خلفيّ) ⇒ قيمة الـDTO تُحترَم."""
    with app.app_context():
        for name, fn in _IMPLS:
            assert fn(_TENANT) == _TENANT, name
