"""بطاقةُ «٢٤ ساعة استخدام» تصل الراوتر بلا `Session-Timeout`.

**الحادثة (خالد نت، ٢٠٢٦-١١-٠٣).** صفحةُ الهوتسبوت النشطة: «Session Time
Left» فارغٌ لكلّ البطاقات. البطاقة 051668 من حزمة «بالثانية» (Mode A):
``count_by_seconds=1`` و``time_value=24 hours``، وباقتُها المستورَدة من ADV
مدّتُها صفر (الوقتُ على الحزمة لا على الباقة).

**السبب.** رصيدُ الاستخدام لا يُقرأ إلّا تحت ``plan.duration_minutes``، فباقةٌ
مدّتُها صفر تتخطّاه كلّه؛ ونمطُ «بالثانية» لا نافذةَ تقويم له ⇒ لا مفتاحَ في
الردّ أصلًا ⇒ الراوتر لا يعرض الباقي ولا يقطع عند النفاد (المُصادِق يرفض فقط
الدخولَ التالي ``card_time_exhausted``).

**العلاج.** المتبقّي = الأساس (من الحزمة) + المنحة − المستهلَك، لنمط
«بالثانية» وحده؛ نمطُ النافذة يبقى على مساره.
"""

import pytest

from app.radius.core.constants import USER_TYPE_CARD


class _Plan:
    """باقةٌ مستورَدة: سرعةٌ فقط، كلُّ حقول المدّة أصفار."""

    def __init__(self, duration_minutes=0, session_timeout_sec=0):
        self.id = 14
        self.duration_minutes = duration_minutes
        self.session_timeout_sec = session_timeout_sec
        self.idle_timeout_sec = 0
        self.address_pool = ""
        self.tenant_id = 1
        self.validity_days = 24        # رقمٌ مستورَد لا معنى له هنا
        self.speed_down_kbps = 1024
        self.speed_up_kbps = 1024

    def __getattr__(self, name):
        return 0 if name.endswith(("_mb", "_kbps", "_sec", "_sessions",
                                   "_minutes", "_days", "_id")) else ""


class _Sub:
    def __init__(self, *, expire_at=None):
        self.tenant_id = 1
        self.username = "051668"
        self.user_type = USER_TYPE_CARD
        self.card_batch_id = 113
        self.plan_id = 14
        self.expire_at = expire_at
        self.bandwidth_control_enabled = False
        self.download_speed_kbps = 0
        self.upload_speed_kbps = 0

    def __getattr__(self, name):
        return 0 if name.endswith(("_kbps", "_sec", "_mb", "_id")) else ""


class _Cur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Db:
    def __init__(self, row):
        self.row = row

    def execute(self, *a, **k):
        return _Cur(self.row)


@pytest.fixture()
def emit(monkeypatch):
    from app.radius.services import device_limit
    from app.radius.services import policy_engine as pe

    monkeypatch.setattr(pe.operations_repo, "resolve_effective_bandwidth_schedule",
                        lambda *a, **k: None)
    monkeypatch.setattr(pe, "_time_cap_remaining_seconds", lambda *a, **k: None)
    monkeypatch.setattr(pe, "_card_batch_window_seconds", lambda *a, **k: 0)
    monkeypatch.setattr(device_limit, "card_reset_since", lambda *a, **k: None)
    state = {"used": 0}
    monkeypatch.setattr(pe, "_accounted_session_seconds",
                        lambda *a, **k: state["used"])

    def _run(*, by_seconds=1, from_first=0, used=0, extra=0, value=24,
             unit="hours", plan=None, sub=None):
        state["used"] = used
        row = {"extra_seconds": extra, "batch_id": 113,
               "count_by_seconds": by_seconds,
               "count_from_first_connect": from_first,
               "time_value": value, "time_unit": unit,
               "validity_after_first_login_days": 0}
        monkeypatch.setattr(pe, "db", lambda: _Db(row))
        out = pe._build_accept_attrs(sub or _Sub(), plan or _Plan())
        return int(out.get("Session-Timeout") or 0)

    return _run


def test_usage_card_with_zero_plan_duration_gets_the_remaining_time(emit):
    """051668: ٢٤ ساعة، استُهلك ١٥ ساعة ⇒ الباقي ٩ ساعات يصل الراوتر."""
    assert emit(used=15 * 3600) == 9 * 3600


def test_unused_usage_card_gets_its_whole_budget(emit):
    assert emit(used=0) == 24 * 3600


def test_operator_grant_counts_toward_the_remaining_time(emit):
    assert emit(used=24 * 3600, extra=1800) == 1800


def test_exhausted_card_never_reads_unlimited(emit):
    """صفرٌ عند الراوتر = «بلا حدّ»؛ النافد يُرسَل ثانيةً واحدة."""
    assert emit(used=30 * 3600) == 1


def test_window_card_with_zero_plan_duration_is_left_to_its_own_path(emit):
    """بطاقةُ نافذة (لا «بالثانية»): لا تُحسب من الاستخدام، ولا تُخترع لها
    مدّةٌ من ``validity_days`` المستورَد."""
    assert emit(by_seconds=0, used=15 * 3600) == 0


def test_explicit_plan_session_timeout_still_rules(emit):
    assert emit(used=15 * 3600, plan=_Plan(session_timeout_sec=600)) == 600


# ── 204: صلاحيّةٌ بالدقائق لنمط «بالثانية» (٦ س استخدام / ٨ س صلاحيّة) ──

def test_by_seconds_window_in_minutes_beats_days():
    from app.radius.services.policy_engine import _card_window_seconds
    row = {"count_by_seconds": 1, "count_from_first_connect": 0,
           "time_value": 6, "time_unit": "hours",
           "validity_after_first_login_days": 1,
           "validity_after_first_login_minutes": 480}
    assert _card_window_seconds(row) == 8 * 3600


def test_by_seconds_without_minutes_keeps_days():
    from app.radius.services.policy_engine import _card_window_seconds
    row = {"count_by_seconds": 1, "count_from_first_connect": 0,
           "time_value": 6, "time_unit": "hours",
           "validity_after_first_login_days": 30}
    assert _card_window_seconds(row) == 30 * 86400


def test_first_login_gets_the_shorter_of_usage_and_window(emit, monkeypatch):
    """أوّل دخول (لا expire_at بعد): ٦ س رصيد، نافذة ٨ س ⇒ ٦ س؛ واستُهلك ٣ س
    ⇒ ٣ س؛ والنافذةُ أقصر من الرصيد ⇒ النافذة."""
    from app.radius.services import policy_engine as pe
    monkeypatch.setattr(pe, "_card_session_window_seconds", lambda *a, **k: 8 * 3600)
    assert emit(value=6, used=0) == 6 * 3600
    assert emit(value=6, used=3 * 3600) == 3 * 3600
    monkeypatch.setattr(pe, "_card_session_window_seconds", lambda *a, **k: 2 * 3600)
    assert emit(value=6, used=0) == 2 * 3600


def test_started_card_cut_at_its_stamp_even_with_usage_left(emit):
    """بعد الختم: استُهلكت ساعةٌ والختم بعد ٣٠ دقيقة ⇒ ٣٠ دقيقة لا ٥ ساعات."""
    from datetime import datetime, timedelta
    sub = _Sub(expire_at=datetime.utcnow() + timedelta(minutes=30))
    got = emit(value=6, used=3600, sub=sub)
    assert 29 * 60 <= got <= 30 * 60
