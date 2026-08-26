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
