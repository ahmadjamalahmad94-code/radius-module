"""بطاقةُ «عشر دقائق» لا تتوقّف — لأنّ أوّلَ دخولٍ يخرج بلا `Session-Timeout`.

**الحادثة (خادم سمير، ٢٠٢٦-٠٩-٠٣).** شكا المشغّل أنّ بطاقاتِ العشر دقائق لا
تنتهي. القياسُ على القاعدة الحيّة:

===========  ==========  ==========================================
البطاقة      المسموح      المستهلَك فعلًا
===========  ==========  ==========================================
60817080     ١٠ دقائق     ٦٣ دقيقة — وما زالت الجلسةُ مفتوحة
63938602     ١٠ دقائق     ٢٧٥ دقيقة في **جلسةٍ واحدة**
15117484     ١٠ دقائق     ٣٨ دقيقة
===========  ==========  ==========================================

وحارسُ عدّاد الجلسة كان يقولها صراحةً كلّ عشر دقائق:
``missing · راوتر=SAM · الراوتر=لا شيء`` — أي أنّ الهوتسبوت **لا يحمل عدّادًا
أصلًا**، فلا شيء يقطع.

**السبب.** `expire_at` للبطاقة تُختم في مسار أوّل الاستخدام — أي **بعد** أن
يكون الـAccess-Accept قد غادر. وعند أوّل دخول:

* لا `sub.expire_at` (لم تُختم بعد)،
* ولا `plan.session_timeout_sec` ولا `plan.duration_minutes` — فخطّةُ العميل
  عندنا سرعةٌ فقط (``2ميغا`` · ``4ميجا``) وكلُّ حقول المدّة فيها أصفار،

فيخرج الردُّ بلا `Session-Timeout` رأسًا. وسجلُّ المصادقة يُظهر الفرقَ عاريًا:
مشتركٌ عاديّ ⇒ ``reply_keys=[… 'Session-Timeout']``، وبطاقةٌ ⇒ بدونها.

**والمدّةُ لم تكن مجهولةً لحظتَها**: هي مكتوبةٌ على الحزمة سلفًا
(``time_value/time_unit``). فالعلاجُ أن نقرأها من الحزمة بدل انتظار الختم.
"""

from datetime import datetime, timedelta

import pytest

from app.radius.core.constants import USER_TYPE_CARD


class _Plan:
    """خطّةُ سرعةٍ صِرفة — كما هي عند العميل: كلُّ حقول المدّة أصفار."""

    def __init__(self, duration_minutes=0, session_timeout_sec=0):
        self.id = 2
        self.duration_minutes = duration_minutes
        self.session_timeout_sec = session_timeout_sec
        self.idle_timeout_sec = 0
        self.address_pool = ""
        self.tenant_id = 1
        self.bandwidth_id = None
        self.speed_down_kbps = 2048
        self.speed_up_kbps = 2048

    def __getattr__(self, name):
        return 0 if name.endswith(("_mb", "_kbps", "_sec", "_sessions",
                                   "_minutes", "_days", "_id")) else ""


class _Sub:
    def __init__(self, *, user_type=USER_TYPE_CARD, card_batch_id=5,
                 expire_at=None):
        self.tenant_id = 1
        self.username = "85187921"
        self.user_type = user_type
        self.card_batch_id = card_batch_id
        self.plan_id = 2
        self.expire_at = expire_at
        self.bandwidth_control_enabled = False
        self.download_speed_kbps = 0
        self.upload_speed_kbps = 0

    def __getattr__(self, name):
        return 0 if name.endswith(("_kbps", "_sec", "_mb", "_id")) else ""


@pytest.fixture()
def emit(monkeypatch):
    """يعزل بناءَ الردّ عن القاعدة، ويسمح بضبط نافذةِ الحزمة المُعادة."""
    from app.radius.services import policy_engine as pe

    monkeypatch.setattr(
        pe.operations_repo, "resolve_effective_bandwidth_schedule",
        lambda *a, **k: None)
    monkeypatch.setattr(pe, "_time_cap_remaining_seconds", lambda *a, **k: None)

    state = {"window": 0, "calls": []}

    def _fake_window(tenant_id, batch_id):
        state["calls"].append((tenant_id, batch_id))
        return state["window"]

    monkeypatch.setattr(pe, "_card_batch_window_seconds", _fake_window)

    def _run(sub, plan, *, window=0):
        state["window"] = window
        out = pe._build_accept_attrs(sub, plan)
        return int(out.get("Session-Timeout") or 0)

    _run.calls = state["calls"]
    return _run


# ── جوهرُ العطب ──────────────────────────────────────────────────────


def test_first_login_of_a_ten_minute_card_carries_the_window(emit):
    """أوّلُ دخولٍ لبطاقةِ عشر دقائق ⇒ ``Session-Timeout = 600``.

    قبل الإصلاح كانت النتيجةُ صفرًا — أي لا مفتاحَ في الردّ أصلًا.
    """
    seconds = emit(_Sub(expire_at=None), _Plan(), window=600)
    assert seconds == 600


def test_the_batch_is_consulted_with_the_right_keys(emit):
    """يُسأل عن حزمةِ **هذه** البطاقة تحت مستأجرها — لا عن غيرها."""
    emit(_Sub(expire_at=None, card_batch_id=7), _Plan(), window=600)
    assert emit.calls[-1] == (1, 7)


def test_old_card_without_explicit_user_type_is_covered_too(emit):
    """بطاقاتٌ قديمةٌ بلا `user_type` صريح — الانتماءُ لحزمةٍ يكفي."""
    seconds = emit(_Sub(user_type="", expire_at=None), _Plan(), window=600)
    assert seconds == 600


# ── ما يجب ألّا يتغيّر ───────────────────────────────────────────────


def test_stamped_card_still_uses_its_remaining_window(emit):
    """بعد الختم، الباقي من `expire_at` هو الحاكم — لا نافذةُ الحزمة كاملةً.

    وإلّا مُنح مَن عاد بعد ثمانِ دقائق عشرًا جديدةً في كلّ دخول.
    """
    sub = _Sub(expire_at=datetime.utcnow() + timedelta(seconds=120))
    seconds = emit(sub, _Plan(), window=600)
    assert 60 <= seconds <= 130
    assert not emit.calls, "لا يُسأل عن الحزمة وللبطاقة ختمٌ قائم"


def test_a_real_subscriber_gets_no_invented_cap(emit):
    """مشتركٌ عاديٌّ بلا تاريخِ انتهاء لا نخترع له سقفَ جلسة."""
    seconds = emit(_Sub(user_type="subscriber", card_batch_id=None,
                        expire_at=None), _Plan())
    assert seconds == 0
    assert not emit.calls


def test_batch_without_a_window_invents_nothing(emit):
    """حزمةٌ بلا مدّةٍ محدَّدة ⇒ لا سقف. لا نخترع انتهاءً لبطاقةٍ مفتوحة."""
    seconds = emit(_Sub(expire_at=None), _Plan(), window=0)
    assert seconds == 0


def test_explicit_operator_cap_still_wins_when_shorter(emit):
    """سقفٌ كتبه المشغّل عمدًا أقصرُ من النافذة ⇒ يحكم هو."""
    seconds = emit(_Sub(expire_at=None), _Plan(session_timeout_sec=300),
                   window=600)
    assert seconds == 300


# ── قارئُ الحزمة نفسُه ───────────────────────────────────────────────


def test_window_reader_returns_zero_without_a_batch():
    """بلا رقمِ حزمةٍ لا استعلامَ ولا اختراع."""
    from app.radius.services import policy_engine as pe
    assert pe._card_batch_window_seconds(1, None) == 0
    assert pe._card_batch_window_seconds(1, 0) == 0


def test_window_reader_survives_a_broken_database(monkeypatch):
    """قاعدةٌ لا تُقرأ لا تُسقط الـAccept — صفرٌ وسطرُ تحذير."""
    from app.radius.services import policy_engine as pe

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(pe, "db", _boom)
    assert pe._card_batch_window_seconds(1, 5) == 0


def test_window_reader_maps_units(monkeypatch):
    """يقرأ ما كتبه المشغّل: ``10 minutes`` ⇒ ٦٠٠ ثانية."""
    from app.radius.services import policy_engine as pe

    row = {"time_value": 10, "time_unit": "minutes",
           "validity_after_first_login_days": 0,
           "count_by_seconds": 0, "count_from_first_connect": 1}

    class _DB:
        def execute(self, *a, **k):
            class _C:
                def fetchone(self_inner):
                    return row
            return _C()

    monkeypatch.setattr(pe, "db", lambda: _DB())
    assert pe._card_batch_window_seconds(1, 5) == 600
