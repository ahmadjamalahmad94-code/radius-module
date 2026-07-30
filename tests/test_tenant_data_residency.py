"""MT121 — كلّ صفٍّ يهبط في جهته، والبطاقة تجد حسابها في شبكتها.

الحادثة (كُشفت بفحص البيانات الحيّة لا بالاختبارات): ٤٠٠ بطاقةٍ لشبكة
«البرق» كانت بطاقاتها في الجهة 8 بينما **حسابات مصادقتها** في الجهة 1
(مساحة المزوّد). الأثر ليس خرق عزلٍ فحسب: البطاقة لا تجد حسابها في شبكتها
⇒ **لا تُصادِق**. أربعمئة بطاقة سليمة غير مستعملة ولا ملغاة — ولا تعمل.

السبب كان `tenant_id` الافتراضيّة الصادقة (=1) تغلب في الخيوط الخلفيّة،
وقد عولج (MT31 ثمّ MT64: `g.tenant_id` يحكم في أيّ سياق). البيانات
المتضرّرة نُقلت. وهذه الاختبارات تحرس **الصنف** لا الحادثة:

  • أيّ DTO يُكتب في خيطٍ خلفيّ يأخذ جهة السياق لا افتراضيّته.
  • ولا يُقبل صفٌّ بجهةٍ لا وجود لها.
"""

import pytest

from app.radius.integration.sqlite_adapter import _effective_tid


class _Ctx:
    """سياق تطبيقٍ صغير يضبط g.tenant_id — يُحاكي الخيط الخلفيّ."""

    def __init__(self, app, tid):
        self.app, self.tid, self.ctx = app, tid, None

    def __enter__(self):
        from flask import g
        self.ctx = self.app.app_context()
        self.ctx.push()
        g.tenant_id = self.tid
        return self

    def __exit__(self, *a):
        self.ctx.pop()
        return False


@pytest.fixture()
def flask_app():
    from flask import Flask
    return Flask(__name__)


def test_context_tenant_beats_the_dto_default(flask_app):
    """جوهر العطب: DTO يحمل 1 افتراضًا، والسياق يقول 8 ⇒ الفوز للسياق."""
    with _Ctx(flask_app, 8):
        assert _effective_tid(1) == 8


def test_context_tenant_beats_any_dto_value(flask_app):
    """حتى قيمةٌ صريحة في الـDTO لا تتجاوز جهة السياق — وإلّا صار الـDTO
    بابًا للكتابة في جهةٍ أخرى."""
    with _Ctx(flask_app, 9):
        assert _effective_tid(8) == 9
        assert _effective_tid(None) == 9
        assert _effective_tid(0) == 9


def test_works_in_a_bare_app_context_not_only_a_request(flask_app):
    """الخيوط الخلفيّة تُنشئ سياق تطبيقٍ فقط بلا طلب — وهنا وقع العطب."""
    from flask import has_request_context
    with _Ctx(flask_app, 5):
        assert not has_request_context()
        assert _effective_tid(1) == 5


def test_guard_is_documented_where_it_lives():
    """حارسٌ نصّيّ: عودةُ `dto.tenant_id or _tid()` تُعيد العطب كلّه."""
    import inspect
    from app.radius.integration import sqlite_adapter

    src = inspect.getsource(sqlite_adapter._effective_tid)
    assert "has_app_context" in src, "الشرط عاد إلى سياق الطلب وحده"
    assert "g" in src


def test_card_sync_does_not_hardcode_a_tenant():
    """مزامن البطاقات يبني Subscriber بلا tenant_id — فيلزم أن يبقى
    اشتقاقُها من السياق، لا أن يُثبَّت رقمٌ في الكود."""
    import inspect
    from app.radius.services.cards import CardsService

    src = inspect.getsource(CardsService._sync_cards_to_radius)
    assert "tenant_id=1" not in src
    assert "DEFAULT_TENANT_ID" not in src
