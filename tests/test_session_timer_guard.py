"""حارسُ عدّاد الجلسة — منطقُ التصنيف والتحليل.

CoA لا يُصلح جلسةً قائمة (مايكروتيك يردّ ``Unsupported-Extension``)، فالحارسُ
هو ما يكشف التفاوتَ بين ما يحمله الراوترُ وما نسمح به. واختبارُ منطقه لا
يحتاج راوترًا: التحليلُ والتصنيفُ دالّتان خالصتان.
"""
from __future__ import annotations

import pytest

from app.radius.services.session_timer_guard import (
    KIND_EXPIRED_LIVE, KIND_MISSING, KIND_OVER, classify, parse_duration,
)


# ── قراءةُ مُدد RouterOS ──────────────────────────────────────────────
@pytest.mark.parametrize("raw,secs", [
    ("7h23m33s", 7 * 3600 + 23 * 60 + 33),
    ("2h5s", 2 * 3600 + 5),          # الوحداتُ الصفريّة تُسقَط
    ("24w6d21h39m59s", 24 * 604800 + 6 * 86400 + 21 * 3600 + 39 * 60 + 59),
    ("45s", 45),
    ("1d", 86400),
])
def test_parse_duration_reads_routeros_format(raw, secs):
    assert parse_duration(raw) == secs


@pytest.mark.parametrize("raw", [None, "", "  ", "none", "0", "غير مفهوم"])
def test_parse_duration_unknown_is_zero(raw):
    """صفرٌ يعني «لا عدّاد» — ولا يجوز أن يُخطئ فيُقرأ عدّادًا وهميًّا."""
    assert parse_duration(raw) == 0


# ── التصنيف ──────────────────────────────────────────────────────────
def test_no_counter_is_missing():
    """🔴 أخطرُ حالة: الراوترُ بلا سقفٍ ⇒ لن يقطع الزبونَ أبدًا."""
    assert classify(0, 3600) == KIND_MISSING


def test_router_more_generous_than_us_is_over():
    """عدّادٌ أطولُ ممّا نسمح = وقتٌ مجّانيّ (يقع حين نُقصّر نافذةً بعد الدخول)."""
    assert classify(10 * 3600, 8 * 3600) == KIND_OVER


def test_expired_card_still_connected():
    assert classify(3600, -5) == KIND_EXPIRED_LIVE
    assert classify(0, 0) == KIND_EXPIRED_LIVE


def test_small_drift_is_not_a_finding():
    """الرزمُ تتأخّر والساعاتُ تنحرف — تفاوتٌ دقائقُ ليس خللًا."""
    assert classify(8 * 3600 + 120, 8 * 3600) is None


def test_router_stricter_than_us_is_not_a_finding():
    """عدّادٌ أقصرُ ممّا نسمح لا يضرّ: الزبونُ يُقطع مبكّرًا ثمّ يعود."""
    assert classify(3600, 8 * 3600) is None


def test_tolerance_boundary_is_respected():
    tol = 600
    assert classify(8 * 3600 + tol, 8 * 3600, tolerance_sec=tol) is None
    assert classify(8 * 3600 + tol + 1, 8 * 3600, tolerance_sec=tol) == KIND_OVER


def test_expiry_wins_over_missing_counter():
    """بطاقةٌ منتهيةٌ بلا عدّاد: السببُ الأهمّ هو الانتهاء لا غيابُ السقف."""
    assert classify(0, -100) == KIND_EXPIRED_LIVE


# ── بوّابةُ الطرد: `expired` وحدَه ────────────────────────────────────
#
# 🔴 قِيس على خادم سمير 2026-09-03: كلُّ الجلسات الحيّة كانت `missing` — لا
# عدّادَ على الراوتر — بسبب ثغرةِ `Session-Timeout` عند أوّل دخول. ولو كان
# الطردُ يشمل `missing` لقُطع في تلك الجولة أصحابُ بطاقاتٍ بقي لها ساعتان.


class _FakeClient:
    """راوترٌ وهميّ: يُعيد جلساتٍ حيّةً ويُسجّل ما طُرد."""

    removed: list = []

    def __init__(self, active):
        self._active = active

    def connect(self):
        return None

    def print_(self, _path):
        return list(self._active)

    def run(self, path, attrs=None):
        assert path == "/ip/hotspot/active/remove"
        type(self).removed.append((attrs or {}).get(".id"))

    def close(self):
        return None


def _audit_with(monkeypatch, active, expiries, *, enforce=True):
    """يُشغّل ``audit`` على راوترٍ واحدٍ وهميّ ويُعيد (التقرير، المطرودين)."""
    from datetime import datetime, timedelta

    from app.radius.services import session_timer_guard as g

    _FakeClient.removed = []
    monkeypatch.setattr(g, "MikrotikClient", lambda **k: _FakeClient(active),
                        raising=False)

    now = datetime.utcnow()

    class _DB:
        def execute(self, sql, args=()):
            class _C:
                def fetchall(self_inner):
                    # صفُّ راوترٍ واحد: (id, name, address, user, pass, port)
                    return [(1, "SAM", "10.50.0.2", "api", "x", 8728)]

                def fetchone(self_inner):
                    left = expiries.get(args[1])
                    if left is None:
                        return None
                    return ((now + timedelta(seconds=left)).isoformat() + "Z",)
            return _C()

    monkeypatch.setattr(g, "db", lambda: _DB(), raising=False)
    import app.radius.db.connection as _conn
    monkeypatch.setattr(_conn, "db", lambda: _DB(), raising=False)
    import app.radius.integration.mikrotik.client as _mtc
    monkeypatch.setattr(_mtc, "MikrotikClient",
                        lambda **k: _FakeClient(active), raising=False)

    rep = g.audit(1, enforce=enforce)
    return rep, list(_FakeClient.removed)


def test_enforce_kicks_the_expired_session(monkeypatch):
    """نافذةٌ انتهت والجلسةُ قائمة ⇒ تُقطع."""
    rep, removed = _audit_with(
        monkeypatch,
        [{"user": "60817080", ".id": "*1", "session-time-left": ""}],
        {"60817080": -1800})
    assert rep[KIND_EXPIRED_LIVE] == 1
    assert removed == ["*1"]
    assert rep["kicked"] == 1


def test_enforce_spares_a_valid_card_that_lacks_a_counter(monkeypatch):
    """`missing` عطبٌ عندنا — بطاقةٌ بقي لها ساعتان لا تُقطع بسببه."""
    rep, removed = _audit_with(
        monkeypatch,
        [{"user": "18759737", ".id": "*2", "session-time-left": ""}],
        {"18759737": 7200})
    assert rep[KIND_MISSING] == 1
    assert removed == []
    assert rep["kicked"] == 0


def test_enforce_spares_an_over_generous_counter(monkeypatch):
    """`over` ليس أوانَه: ينتهي عندنا قريبًا فيُلتقط `expired` لاحقًا."""
    rep, removed = _audit_with(
        monkeypatch,
        [{"user": "33253146", ".id": "*3", "session-time-left": "10h"}],
        {"33253146": 600})
    assert rep[KIND_OVER] == 1
    assert removed == []


def test_read_only_mode_never_kicks(monkeypatch):
    """بلا `enforce` لا يُلمَس شيءٌ مهما كان التفاوت."""
    rep, removed = _audit_with(
        monkeypatch,
        [{"user": "60817080", ".id": "*1", "session-time-left": ""}],
        {"60817080": -1800}, enforce=False)
    assert rep[KIND_EXPIRED_LIVE] == 1
    assert removed == []
    assert rep["kicked"] == 0
