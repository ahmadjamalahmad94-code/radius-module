"""MT118 — النظام لا يَعرض خيارًا ثمّ يرفضه.

الحادثة: المالك فتح «شبكة جديدة»، اختار **«العرض المجاني»** من القائمة التي
عرضها النظام عليه، فردّ عليه: «plan_tier غير معروف: free» — ولم تُنشأ
الشبكة. وكان على وشك إنشاء أوّل حسابٍ حقيقيّ.

مصدرا حقيقةٍ لمفهومٍ واحد:
  • النموذج يبني خياراته من `tier_config` — الفئات التي يُديرها المالك من
    اللوحة (`free` و`cafes` و`starter` عنده فعلًا).
  • التحقّق يقارن بـ`TIER_LIMITS` الثابتة في الكود
    (`starter`/`pro`/`enterprise`).

المصدر الحقّ هو ما يُدار من اللوحة. والثابتة تبقى ارتدادًا لو تعذّرت
قراءته، فلا ينكسر إنشاء الشبكات على عطبٍ في ملفّ الإعدادات.
"""

import pytest

from app.radius.services import tenants as tenants_svc


@pytest.fixture()
def tiers(monkeypatch):
    """يُشكّل الفئات الديناميكيّة كما يراها النموذج."""
    state = {"rows": []}
    from app.radius.services import tier_config
    monkeypatch.setattr(tier_config, "get_tiers", lambda: state["rows"])
    return state


def test_dynamic_tier_is_accepted(tiers):
    """جوهر العطب: `free` معروضةٌ في النموذج فيجب أن تُقبل."""
    tiers["rows"] = [{"key": "free"}, {"key": "cafes"}, {"key": "starter"}]
    assert "free" in tenants_svc._known_tier_keys()
    assert "cafes" in tenants_svc._known_tier_keys()


def test_unknown_tier_is_still_rejected(tiers):
    """الإصلاح لا يفتح الباب لأيّ نصّ — ما ليس في اللوحة يُرفض."""
    tiers["rows"] = [{"key": "free"}]
    assert "enterprise" not in tenants_svc._known_tier_keys()
    assert "'; DROP TABLE" not in tenants_svc._known_tier_keys()


def test_falls_back_to_constants_when_config_unreadable(tiers, monkeypatch):
    """عطبٌ في الإعدادات لا يجوز أن يمنع إنشاء الشبكات كلّها."""
    from app.radius.core.tenant import TIER_LIMITS
    from app.radius.services import tier_config

    def _boom():
        raise RuntimeError("ملفّ الفئات تالف")

    monkeypatch.setattr(tier_config, "get_tiers", _boom)
    assert tenants_svc._known_tier_keys() == set(TIER_LIMITS)


def test_empty_config_falls_back_too(tiers):
    """قائمةٌ فارغة تعني «لا فئات» — لا نقبل شيئًا بلا مرجع."""
    from app.radius.core.tenant import TIER_LIMITS
    tiers["rows"] = []
    assert tenants_svc._known_tier_keys() == set(TIER_LIMITS)


def test_blank_keys_are_not_valid_tiers(tiers):
    tiers["rows"] = [{"key": ""}, {"key": "  "}, {"key": "free"}]
    assert tenants_svc._known_tier_keys() == {"free"}


def test_both_create_and_update_use_the_same_source():
    """حارسٌ نصّيّ: بقاء `TIER_LIMITS` في أحد الموضعَين يُعيد العطب لنصف
    الحالات — يُنشئ المالك الشبكة ثمّ يعجز عن تعديل فئتها."""
    import inspect
    src = inspect.getsource(tenants_svc)
    assert src.count("_known_tier_keys()") >= 3, "موضعٌ ما زال يقارن بالثابتة"
    assert "plan_tier not in TIER_LIMITS" not in src


def test_rejection_message_names_the_value():
    """«غير معروف» بلا القيمة لا تُخبر المالك ماذا يُصلح."""
    import inspect
    src = inspect.getsource(tenants_svc.TenantsService.update)
    assert "{changes['plan_tier']}" in src or "plan_tier غير معروف: " in src
