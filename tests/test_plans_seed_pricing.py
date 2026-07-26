"""MT62 — الباقات: البذرة تحمل التسعير، وصفحة المنصّة تعرضه.

يَقفل علّةً وقعت على الإنتاج: ``_seed()`` كانت تُرجع صفوفًا خامًّا بلا
حقول تسعير، وهي مسار السقوط حين لا إعدادات محفوظة (حال أيّ نسخةٍ جديدة)
— فاختفى قسم الأسعار من صفحة المنصّة كلّيًّا رغم أنّ الكود «يعمل».
"""
from __future__ import annotations

import pytest

from app.radius.services import tier_config as tc


_PRICE_KEYS = ("concurrent", "price_monthly", "currency", "is_free",
               "trial_days", "highlight", "visible", "note", "discounts")


def test_seed_rows_carry_every_pricing_field():
    """كل صفٍّ في البذرة يحمل حقول التسعير — لا صفوفَ خامّة."""
    seed = tc._seed()
    assert seed, "البذرة لا يجوز أن تكون فارغة"
    for row in seed:
        for k in _PRICE_KEYS:
            assert k in row, f"البذرة تفتقد {k} في الباقة {row.get('key')!r}"
        for k in ("max_subscribers", "max_nas", "api_rpm"):
            assert row.get(k, 0) >= 1, f"حدٌّ غير صالح في {row.get('key')!r}"


def test_seed_has_visible_priced_plans():
    """البذرة تعرض باقاتٍ على صفحة المنصّة — وإلّا ظهرت الصفحة بلا أسعار."""
    visible = [r for r in tc._seed() if r.get("visible")]
    assert len(visible) >= 2, "لا باقات ظاهرة في البذرة"
    assert any(r.get("is_free") for r in visible), "لا باقة تجريبيّة مجانيّة"
    assert any((not r["is_free"]) and r["price_monthly"] > 0 for r in visible), \
        "لا باقة مدفوعة بسعرٍ فعليّ"


def test_totals_are_computed_from_monthly_not_stored():
    """إجماليّ المدّة = الشهريّ × الأشهر − الخصم (مقرَّبًا)، فلا تناقض."""
    plan = {"price_monthly": 17.0, "concurrent": 100,
            "discounts": [{"months": 12, "percent": 20}]}
    assert tc.period_total(plan, 12, 20) == 163      # 204 − 20% = 163.2 → 163
    assert tc.period_total(plan, 3, 10) == 46        # 51 − 10% = 45.9 → 46
    assert tc.unit_price(plan) == 0.17
    rows = tc.plan_rows(plan)
    assert rows and rows[0]["total"] == 163


def test_cleaner_never_drops_fields_on_garbage_input():
    """مُدخَلٌ فاسد لا يُنتج صفًّا ناقصًا — الحقول كاملةٌ بقيمٍ آمنة."""
    out = tc._clean_tier({"label": "", "max_subscribers": "abc",
                          "price_monthly": -5, "concurrent": "x",
                          "discounts": [{"months": "3", "percent": 999}]}, set())
    for k in _PRICE_KEYS + ("key", "label", "icon", "max_subscribers"):
        assert k in out
    assert out["price_monthly"] >= 0
    assert out["discounts"][0]["percent"] <= 90


@pytest.mark.parametrize("key", ["starter", "unknown-key"])
def test_limits_for_always_returns_usable_limits(key):
    """حدود الشبكات لا تنكسر بمفتاحٍ غريب (باقة محذوفة)."""
    limits = tc.limits_for(key)
    assert set(limits) == {"max_subscribers", "max_nas", "api_rpm"}
    assert all(v >= 1 for v in limits.values())
