"""MT114 — مدّة الخطّة ليست سقفَ جلسة: المشترك يرى صلاحيّته الحقيقيّة.

الحادثة: مشتركٌ صالحٌ حتى 2028 (نحو ٧٠٠ يوم) على خطّة «10-M» التي كتب
المشغّل مدّتها `duration_minutes = 43200` (= ٣٠ يومًا بالضبط). محرّك
السياسة كان يقرأ `duration_minutes` سقفًا للجلسة، فيُرسل
`Session-Timeout = 2,592,000`. وصفحة status في المايكروتيك تعرض هذه القيمة
تحت «الباقي من الصلاحية» — فقرأ الزبون «٢٩ يوم» واشتراكه سنتان، وطالب
بالفرق. الخلل في المعنى لا في الحساب: `duration_minutes` ميزانيّةُ وقتٍ
لبطاقةٍ زمنيّة، ومدّةُ خطّةٍ لاشتراك — وخلطُهما يكذب على الزبون.
"""

from datetime import datetime, timedelta

import pytest

from app.radius.core.constants import USER_TYPE_CARD


class _Plan:
    def __init__(self, duration_minutes=0, session_timeout_sec=0):
        self.id = 17
        self.duration_minutes = duration_minutes
        self.session_timeout_sec = session_timeout_sec
        self.idle_timeout_sec = 0
        self.address_pool = ""
        self.tenant_id = 1
        self.bandwidth_id = None
        self.speed_down_kbps = 0
        self.speed_up_kbps = 0

    def __getattr__(self, name):
        return 0 if name.endswith(("_mb", "_kbps", "_sec", "_sessions",
                                   "_minutes", "_days", "_id")) else ""


class _Sub:
    def __init__(self, *, user_type="subscriber", card_batch_id=None,
                 days_left=700):
        self.tenant_id = 1
        self.username = "0597626111"
        self.user_type = user_type
        self.card_batch_id = card_batch_id
        self.plan_id = 17
        self.expire_at = datetime.utcnow() + timedelta(days=days_left)
        self.bandwidth_control_enabled = False
        self.download_speed_kbps = 0
        self.upload_speed_kbps = 0

    def __getattr__(self, name):
        # أيّ حقلٍ آخر في Subscriber لا يخصّ Session-Timeout: صفرٌ/فراغ آمن.
        # هكذا لا يتعلّق الاختبار بشكل البنية كلّها، بل بالسلوك المقصود.
        return 0 if name.endswith(("_kbps", "_sec", "_mb", "_id")) else ""


@pytest.fixture()
def emit(monkeypatch):
    """يُعزل بناء attrs عن القاعدة ويُعيد Session-Timeout بالثواني."""
    from app.radius.services import policy_engine as pe

    monkeypatch.setattr(
        pe.operations_repo, "resolve_effective_bandwidth_schedule",
        lambda *a, **k: None)
    monkeypatch.setattr(pe, "_time_cap_remaining_seconds",
                        lambda *a, **k: None)

    def _run(sub, plan):
        out = pe._build_accept_attrs(sub, plan)
        return int(out.get("Session-Timeout") or 0)

    return _run


def test_monthly_plan_period_no_longer_caps_the_session(emit):
    """جوهر العطب: ٤٣٢٠٠ دقيقة مدّةُ خطّةٍ لا سقفُ جلسة."""
    seconds = emit(_Sub(days_left=700), _Plan(duration_minutes=43200))
    assert seconds > 600 * 86400, f"ما زال يُقصّ إلى {seconds/86400:.0f} يوم"


def test_card_time_budget_is_still_enforced(emit):
    """بطاقة «٤ ساعات» يجب أن تبقى أربع ساعات — الإصلاح لا يفتحها."""
    seconds = emit(
        _Sub(user_type=USER_TYPE_CARD, card_batch_id=3, days_left=700),
        _Plan(duration_minutes=240))
    assert seconds == 240 * 60


def test_card_without_explicit_user_type_is_still_a_card(emit):
    """بطاقات قديمة بلا user_type صريح — الانتماء لحزمة يكفي."""
    seconds = emit(_Sub(card_batch_id=3, days_left=700),
                   _Plan(duration_minutes=240))
    assert seconds == 240 * 60


def test_explicit_session_cap_still_wins(emit):
    """سقفٌ كتبه المشغّل عمدًا (session_timeout_sec) يبقى حاكمًا."""
    seconds = emit(_Sub(days_left=700),
                   _Plan(duration_minutes=43200, session_timeout_sec=3600))
    assert seconds == 3600


def test_never_exceeds_the_remaining_subscription(emit):
    """لا تُمنَح جلسةٌ أطول من الاشتراك الباقي."""
    seconds = emit(_Sub(days_left=2), _Plan(session_timeout_sec=30 * 86400))
    assert seconds <= 2 * 86400 + 60


def test_source_documents_the_two_meanings():
    """حارسٌ نصّيّ: عودة الخلط تُعيد الكذب على الزبون."""
    import inspect
    from app.radius.services import policy_engine as pe

    src = inspect.getsource(pe)
    i = src.find("is_time_budget")
    assert i > 0, "تمييز البطاقة عن المشترك اختفى"
    window = src[i:i + 400]
    assert "duration_minutes" in window
    assert "is_time_budget" in window
