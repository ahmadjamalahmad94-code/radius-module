"""MT44 — تحقّق منطق عدّادات لوحة المزوّد (المال + «يحتاج انتباهك»).

اختبارٌ خالص لا يَمسّ القاعدة: يُمرّر شبكات وهميّة محكومة إلى
``_provider_money_and_attention`` ويَتأكّد أنّ كل عدّاد يَعكس الحالة
الصحيحة — إذ اللوحة الحيّة كانت كلّها أصفارًا فلا تُثبت شيئًا.

الحالات المُغطّاة:
  • الإيراد = المدفوعة السارية فقط (المنقضية تُحتسب متأخّرة لا إيرادًا).
  • «يستحقّ قريبًا» ساري لكن ≤٧ أيّام (لا يُخصَم من الإيراد).
  • تجربة تنتهي ≤٣ أيّام.
  • قارب الحدّ عند ٨٥٪+ (وسقف ≥٢ حتى لا يُنبّه سقف الأجهزة=١ أبدًا).
  • مفعّلة بلا مشترك.
"""
from datetime import datetime, timedelta

from app.radius.routes.tenants import _provider_money_and_attention


class _T:
    def __init__(self, tid, *, status="active", billing_mode="free", billing_amount=0,
                 paid_until=None, trial_ends_at=None, max_subscribers=200, max_nas=1,
                 currency="USD"):
        self.id = tid
        self.status = status
        self.billing_mode = billing_mode
        self.billing_amount = billing_amount
        self.paid_until = paid_until
        self.trial_ends_at = trial_ends_at
        self.max_subscribers = max_subscribers
        self.max_nas = max_nas
        self.currency = currency
        self.name = self.display_name = f"net{tid}"


def _fixture():
    now = datetime(2026, 7, 24)
    items = [
        _T(2, billing_mode="paid", billing_amount=100, paid_until=now + timedelta(days=20)),
        _T(3, billing_mode="paid", billing_amount=50, paid_until=now - timedelta(days=5)),
        _T(4, billing_mode="paid", billing_amount=30, paid_until=now + timedelta(days=3)),
        _T(5, status="trial", trial_ends_at=now + timedelta(days=2)),
        _T(6, status="active"),
    ]
    usage_subs = {2: 190, 3: 10, 4: 0, 5: 0, 6: 0}   # net2 = 95% → near_limit
    usage_nas = {2: 1, 3: 1, 4: 1, 5: 1, 6: 1}
    return _provider_money_and_attention(items, usage_subs, usage_nas, now, signups=[])


def test_money_counters():
    money, _ = _fixture()
    assert money["revenue"] == 130.0            # 100 + 30 (السارية)، لا 50 المنقضية
    assert money["overdue_amount"] == 50.0
    assert money["overdue_count"] == 1
    assert money["paid_count"] == 3


def test_attention_counters():
    _, att = _fixture()
    assert len(att["overdue"]) == 1
    assert len(att["due_soon"]) == 1            # net4 خلال ٣ أيّام (ساري)
    assert len(att["expiring"]) == 1            # net5 تجربة تنتهي
    assert len(att["near_limit"]) == 1          # net2 مشتركون ٩٥٪
    assert len(att["idle"]) == 2                # net4 و net6 مفعّلتان بلا مشترك


def test_nas_limit_never_alerts_at_cap_one():
    """سقف الأجهزة الافتراضيّ = ١؛ جهةٌ تستخدم راوترها الوحيد يجب ألّا
    تُنبّه (١٠٠٪ دائم لا حدثٌ يستحقّ انتباهًا)."""
    now = datetime(2026, 7, 24)
    items = [_T(9, max_nas=1)]
    _, att = _provider_money_and_attention(items, {9: 0}, {9: 1}, now, signups=[])
    assert not any(a["what"] == "الأجهزة" for a in att["near_limit"])
