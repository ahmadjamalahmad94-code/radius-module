"""MT107 — تغيير كلمة مرور بطاقة مسرَّبة، بلا إتلاف البطاقة.

البطاقة الأسبوعيّة/الشهريّة تبقى بيد الزبون أسابيع، فتُصوَّر كلمتها وتُتداول.
لم يكن أمام المشغّل إلّا تعطيلها وتوليد غيرها — والزبون قد دفع. الاختبار
يحرس ثلاثة عقود لا واحدًا:

  1. الكلمة تتغيّر في **الموضعَين**: جدول الكروت (ما يُطبَع) وحساب RADIUS
     (ما يُصادَق به). لو تغيّر أحدهما وحده لظنّ المشغّل أنّه أغلق الثغرة
     والدخول لا يزال بالقديمة — وهذا أسوأ من ألّا يفعل شيئًا.
  2. الجلسات القائمة **تُقطع**. المتسلّل متّصلٌ الآن وجلسته لا يُعاد
     التحقّق منها، فتغييرٌ بلا طردٍ لا يطرده.
  3. اسم الدخول ووقت البطاقة **لا يتغيّران** — وإلّا لصار العلاج إتلافًا.
"""

import pytest

from app.radius.core.errors import RadiusValidationError
from app.radius.services.cards import CardsService


class _Card:
    def __init__(self):
        self.id = 7
        self.username = "316240"
        self.password = "111111"
        self.batch_id = 3
        self.expire_at = "2026-09-01T00:00:00"


class _Batch:
    password_length = 8
    password_charset = "mixed"


class _Adapter:
    def __init__(self):
        self.radius_password = "111111"
        self.disconnected: list[str] = []

    def reset_password(self, username, new_password):
        self.radius_password = new_password

    def disconnect(self, username, **kw):
        self.disconnected.append(username)


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


@pytest.fixture()
def svc(monkeypatch):
    from app.radius.services import cards as cards_mod

    card, store = _Card(), {}

    def _set_card_password(tenant_id, card_id, password):
        if not (password or "").strip():
            return False
        store["password"] = password
        card.password = password
        return True

    monkeypatch.setattr(cards_mod.cards_repo, "get_card",
                        lambda t, cid: card if cid == card.id else None)
    monkeypatch.setattr(cards_mod.cards_repo, "get_batch",
                        lambda t, bid, **kw: _Batch())
    monkeypatch.setattr(cards_mod.cards_repo, "set_card_password",
                        _set_card_password)

    s = CardsService(_Adapter(), audit=_Audit())
    monkeypatch.setattr(type(s), "_store_tenant_id", lambda self: 1)
    s._test_card = card
    return s


def test_password_changes_in_both_places(svc):
    """جوهر العقد: البطاقة والشبكة معًا، لا إحداهما."""
    res = svc.change_card_password(actor="admin", card_id=7,
                                   new_password="NewPass9")
    assert res["password"] == "NewPass9"
    assert svc._test_card.password == "NewPass9"        # ما يُطبَع
    assert svc._adapter.radius_password == "NewPass9"   # ما يُصادَق به


def test_active_sessions_are_kicked(svc):
    """تغييرٌ بلا طرد يترك المتسلّل متّصلًا حتى تنتهي مهلته."""
    res = svc.change_card_password(actor="admin", card_id=7)
    assert res["kicked"] is True
    assert svc._adapter.disconnected == ["316240"]


def test_username_and_expiry_untouched(svc):
    """العلاج ليس إتلافًا: البطاقة نفسها ووقتها يبقيان."""
    before = (svc._test_card.username, svc._test_card.expire_at)
    svc.change_card_password(actor="admin", card_id=7)
    assert (svc._test_card.username, svc._test_card.expire_at) == before


def test_blank_password_is_generated_with_batch_shape(svc):
    """الكلمة المولَّدة تُطبع على بطاقةٍ بين أخواتها — فلتكن بنمط الحزمة."""
    res = svc.change_card_password(actor="admin", card_id=7)
    assert res["generated"] is True
    assert len(res["password"]) == _Batch.password_length
    assert res["password"].isalnum()


def test_generated_passwords_differ_between_calls(svc):
    """لو تكرّرت لصارت «تغييرًا» صوريًّا."""
    a = svc.change_card_password(actor="admin", card_id=7)["password"]
    b = svc.change_card_password(actor="admin", card_id=7)["password"]
    assert a != b


def test_password_with_space_is_rejected(svc):
    """المسافة لا تُرى عند الطباعة ثمّ يفشل الدخول بلا سبب ظاهر."""
    with pytest.raises(RadiusValidationError):
        svc.change_card_password(actor="admin", card_id=7,
                                 new_password="a b1234")


def test_missing_card_is_rejected(svc):
    with pytest.raises(RadiusValidationError):
        svc.change_card_password(actor="admin", card_id=999)


def test_audit_records_the_change_without_the_password(svc):
    """السجلّ يُقرأ من الواجهة — لا تُكتب فيه الكلمة."""
    svc.change_card_password(actor="admin", card_id=7, new_password="Secret77")
    rec = svc._audit.records[-1]
    assert rec["action"] == "card.change_password"
    assert "Secret77" not in str(rec)
    assert rec["payload"]["username"] == "316240"
    assert rec["payload"]["kicked"] is True


def test_kick_failure_does_not_undo_the_change(svc, monkeypatch):
    """الكلمة تغيّرت فعلًا في الموضعَين؛ فشل الطرد لا يُبطلها."""
    def _boom(username, **kw):
        raise RuntimeError("NAS unreachable")

    monkeypatch.setattr(svc._adapter, "disconnect", _boom)
    res = svc.change_card_password(actor="admin", card_id=7,
                                   new_password="Boom1234")
    assert res["kicked"] is False
    assert svc._adapter.radius_password == "Boom1234"
