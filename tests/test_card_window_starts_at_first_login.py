"""MT112 — عدّاد البطاقة يبدأ عند أوّل دخول، لا عند التوليد.

الحادثة: المالك ولّد ١٢٠٠ بطاقة «٤ ساعات» الساعة 19:10، فحُفظ على الحزمة
`expire_at = 23:10` — ساعة حائطٍ من لحظة التوليد. نظر بعد تسع دقائق فوجد
كلّ بطاقةٍ تقول «3 ساعة 51 دقيقة» وهي لم تُلمَس: البطاقة تموت في الدرج،
والمشتري يدفع ثمن وقتٍ استُهلك قبل أن يراه.

كان الكود يختم الساعة عند التوليد بوصفها «سقف أمان» لأنّ الختم عند أوّل
دخول لم يُبنَ قطّ (تعليقٌ صريح في المصدر). فبُني هنا:

  • التوليد يترك `expire_at` فارغة.
  • مسار المصادقة يختمها عند أوّل دخول = الآن + مدّة الحزمة.
  • العرض يُظهر «٤ ساعات — لم تبدأ» بدل عدٍّ تنازليّ لم يبدأ.

طلب المالك نصًّا: «ما ينقص الوقت نهائيًّا إلّا لمّا يسجّل دخول فقط».
"""

import pytest

from app.radius.services.policy_engine import _card_window_seconds


class _Row(dict):
    """صفٌّ يشبه sqlite3.Row في الوصول بالمفتاح."""


# ── تحويل مدّة الحزمة إلى ثوانٍ ───────────────────────────────────────
@pytest.mark.parametrize("row, expected", [
    ({"time_value": 4, "time_unit": "hours"}, 4 * 3600),
    ({"time_value": 6, "time_unit": "hours"}, 6 * 3600),
    ({"time_value": 30, "time_unit": "minutes"}, 1800),
    ({"time_value": 7, "time_unit": "days"}, 7 * 86400),
])
def test_window_from_batch_duration(row, expected):
    assert _card_window_seconds(_Row(row)) == expected


def test_falls_back_to_validity_after_first_login():
    assert _card_window_seconds(
        _Row({"validity_after_first_login_days": 3})) == 3 * 86400


def test_no_duration_means_no_window():
    """بطاقةٌ بلا مدّةٍ محدَّدة لا يجوز أن نخترع لها انتهاءً."""
    assert _card_window_seconds(_Row({"time_value": 0, "time_unit": ""})) == 0
    assert _card_window_seconds(_Row({})) == 0


def test_junk_values_do_not_raise():
    assert _card_window_seconds(_Row({"time_value": "س", "time_unit": "hours"})) == 0
    assert _card_window_seconds(_Row({"time_value": 4, "time_unit": "قرون"})) == 0


# ── التوليد لا يختم ساعةً ─────────────────────────────────────────────
def test_generation_no_longer_stamps_a_wall_clock():
    """حارسٌ نصّيّ: عودة `utcnow() + المدّة` هنا تُعيد قتل البطاقة في الدرج."""
    import inspect
    from app.radius.services import cards as cards_mod

    src = inspect.getsource(cards_mod.CardsService.generate_batch)
    i = src.find("elif time_value and time_unit")
    assert i > 0, "فرع مدّة البطاقة اختفى"
    branch = src[i:i + 1400]
    assert "timedelta(hours=time_value)" not in branch
    assert "timedelta(days=time_value)" not in branch
    assert "timedelta(minutes=time_value)" not in branch


def test_auth_path_stamps_the_window_on_first_use_only():
    """الختم مرّةً واحدة: إعادة الدخول لا تُمدّد العمر."""
    import inspect
    from app.radius.services import policy_engine

    src = inspect.getsource(policy_engine._update_login_timestamps)
    assert "was_first_card_use" in src
    assert "_card_window_seconds" in src
    assert "expire_at IS NULL" in src, "بلا هذا الشرط يُعاد الختم فيُمدَّد العمر"
    assert "UPDATE subscribers SET expire_at" in src, \
        "ختم الكرت وحده يترك الدخول مفتوحًا بعد الانتهاء"


# ── العرض ─────────────────────────────────────────────────────────────
def test_unused_card_shows_its_duration_not_a_countdown():
    from datetime import datetime
    from app.radius.routes.cards import _card_remaining_meta

    meta = _card_remaining_meta({"expire_at": None}, datetime.utcnow(),
                                4 * 3600)
    assert meta["state"] == "pending"
    assert "لم تبدأ" in meta["label"]
    assert "4" in meta["label"] or "٤" in meta["label"]


def test_used_card_still_counts_down():
    """الإصلاح لا يُطفئ العدّ — يؤجّله إلى أوّل دخول."""
    from datetime import datetime, timedelta
    from app.radius.routes.cards import _card_remaining_meta

    later = (datetime.utcnow() + timedelta(hours=2)).isoformat()
    meta = _card_remaining_meta({"expire_at": later}, datetime.utcnow(), 4 * 3600)
    assert meta["state"] == "active"
    assert 0 < meta["seconds"] <= 2 * 3600 + 5
