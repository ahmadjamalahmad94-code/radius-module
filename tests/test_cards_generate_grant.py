"""MT111 — منح «إنشاء دفعة بطاقات» يجب أن يُطاع، لا أن يكون زينة.

صفحة المشغّل تَعرض صلاحيةً اسمها «إنشاء دفعة بطاقات» (`can_create_batch`)،
و`manager_grants` تُسجّلها باسم `cards.generate` على المسارَين. لكنّ المسار
كان يفحص `is_super_admin()` وحده — فيمنحها المالكُ لمديره العامّ ثمّ يُقال
له «هذه العملية مقصورة على المالك». مصدرا حقيقةٍ يتناقضان، والمالك يظنّ
النظام معطوبًا لأنّه وعده بشيءٍ لا يفعله.

الافتراض يبقى **مغلقًا**: مديرٌ بلا منحٍ لا يُولّد بالنموذج الكامل — يولّد
من العروض المسعَّرة فتُخصم الجملة من محفظته. الاختبار يحرس الطرفَين معًا،
لأنّ فتحًا زائدًا هنا يعني بطاقاتٍ تُولَّد بلا خصم.
"""

import pytest

from app.radius.routes import cards as cards_routes


@pytest.fixture()
def guard(monkeypatch):
    """يعزل `_can_generate_batches` عن الجلسة وقاعدة البيانات."""
    # rbac  = the derived RBAC action (cards.generate key / deny override / expiry)
    # granted = the owner's EXPLICIT «مسموح» (can_create_batch / _actions True)
    state = {"super": False, "me": 3, "granted": False, "rbac": True}

    monkeypatch.setattr(cards_routes, "is_super_admin", lambda: state["super"])
    monkeypatch.setattr(cards_routes, "current_admin_id", lambda: state["me"])
    monkeypatch.setattr(cards_routes, "_tid", lambda: 1)

    from app.radius.services import manager_grants
    monkeypatch.setattr(
        manager_grants, "action_permitted",
        lambda admin_id, key, *, tenant_id=1: (
            state["rbac"] and key == "cards.generate"),
    )
    monkeypatch.setattr(
        manager_grants, "explicitly_granted",
        lambda admin_id, key, *, tenant_id=1: (
            state["granted"] and key == "cards.generate"),
    )
    return state


def test_owner_may_always_generate(guard):
    guard["super"] = True
    assert cards_routes._can_generate_batches() is True


def test_manager_without_the_grant_is_denied(guard):
    """الافتراض مغلق — وإلّا وُلّدت بطاقاتٌ بلا خصمٍ من محفظة أحد."""
    guard["granted"] = False
    assert cards_routes._can_generate_batches() is False


def test_rbac_key_alone_is_not_the_grant(guard):
    """مفتاح RBAC «توليد بطاقات» يفتح الصفحة (عارض العروض المحاسَب) لكلّ «مدير
    عام»/«مشغّل» افتراضًا — لو كفى وحده لعاد النموذج الكامل المجّانيّ مفتوحًا
    للجميع (da238d4b + 531cb457). المنحُ الصريح شرطٌ."""
    guard["rbac"] = True
    guard["granted"] = False
    assert cards_routes._can_generate_batches() is False


def test_explicit_grant_still_obeys_a_deny_or_expiry(guard):
    """«ممنوع» صريح / انتهاء المنوحات / سقف الأب تأتي من action_permitted."""
    guard["rbac"] = False
    guard["granted"] = True
    assert cards_routes._can_generate_batches() is False


def test_manager_with_the_grant_is_allowed(guard):
    """جوهر العطب: المنح كان يُعرض ولا يُطاع."""
    guard["granted"] = True
    assert cards_routes._can_generate_batches() is True


def test_anonymous_is_denied(guard):
    guard["me"] = None
    guard["granted"] = True
    assert cards_routes._can_generate_batches() is False


def test_the_grant_key_matches_the_registry():
    """اسمٌ مختلفٌ حرفًا = منحٌ لا يُفعّل شيئًا، وصمتًا."""
    import inspect
    from app.radius.services.manager_grants import ACTION_REGISTRY

    assert "cards.generate" in ACTION_REGISTRY
    spec = ACTION_REGISTRY["cards.generate"]
    assert spec.get("flag") == "can_create_batch"
    assert "cards_generate" in spec["endpoints"]
    assert "cards_generate_progress_start" in spec["endpoints"]

    src = inspect.getsource(cards_routes._can_generate_batches)
    assert '"cards.generate"' in src


def test_both_generate_routes_share_one_guard():
    """المسار التدريجيّ كان يفحص السوبر وحده — فتحٌ في واجهةٍ وإغلاقٌ في
    أخرى يُنتج نموذجًا يظهر ثمّ ينهار عند الضغط."""
    import inspect
    src = inspect.getsource(cards_routes.cards_generate_progress_start)
    assert "_can_generate_batches()" in src
    assert "is_super_admin()" not in src
