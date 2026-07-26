"""MT67 — الدولة والمنطقة الزمنية قائمتان منسدلتان لا نصًّا حرًّا.

كان التوقيت نصًّا: `Asia/amman` بحرفٍ صغير يُسقط كل التواريخ المحليّة
والجداول الزمنيّة للـUTC **صامتةً**. والدولة لم تكن تُسأَل، فتُنشأ شبكةٌ
يمنيّة بتوقيت عمّان سهوًا.
"""
from __future__ import annotations

from app.radius.services import geo_catalog as geo


def test_every_country_maps_to_a_timezone_in_the_options():
    """لا دولةَ توقيتُها خارج قائمة الاختيار — وإلّا لم يُحفَظ اختيارها."""
    opts = {tz for tz, _ in geo.timezone_options()}
    for code, name, tz in geo.COUNTRIES:
        assert tz, f"{code} بلا توقيت"
        assert tz in opts, f"توقيت {name} ({tz}) غائبٌ عن القائمة"


def test_country_codes_unique_and_normalised():
    codes = [c for c, _, _ in geo.COUNTRIES]
    assert len(codes) == len(set(codes)), "رمز دولة مكرّر"
    assert all(c.isupper() and len(c) == 2 for c in codes), "رموز ISO alpha-2 فقط"


def test_normalize_rejects_anything_outside_the_catalogue():
    """مُدخَلٌ حرّ لا يُخزَّن — القائمة هي مصدر الحقيقة."""
    assert geo.normalize_country("ye") == "YE"       # حالة الأحرف تُطبَّع
    assert geo.normalize_country(" jo ") == "JO"     # المسافات تُقصّ
    for bad in ("ZZ", "<script>", "", None, "YEM", 7):
        assert geo.normalize_country(bad) == ""      # لا يَنهار ولا يُخزّن


def test_timezone_for_country_covers_the_real_cases():
    assert geo.timezone_for_country("YE") == "Asia/Aden"
    assert geo.timezone_for_country("JO") == "Asia/Amman"
    assert geo.timezone_for_country("PS") == "Asia/Hebron"
    assert geo.timezone_for_country("ZZ") == ""


def test_saved_value_outside_catalogue_is_kept_at_the_top():
    """قاعدة توافق: ضبطٌ يدويّ قائم لا تَبتلعه القائمة عند الحفظ."""
    opts = geo.timezone_options("Pacific/Auckland")
    assert opts[0][0] == "Pacific/Auckland"
    assert "محفوظة" in opts[0][1]
    # قيمةٌ داخل الكتالوج لا تُكرَّر في الرأس
    tzs = [t for t, _ in geo.timezone_options("Asia/Amman")]
    assert tzs.count("Asia/Amman") == 1


def test_options_have_no_duplicates():
    tzs = [t for t, _ in geo.timezone_options()]
    assert len(tzs) == len(set(tzs))
    names = [n for _, n in geo.country_options()]
    assert len(names) == len(set(names)), "اسم دولة مكرّر يُربك القائمة"
