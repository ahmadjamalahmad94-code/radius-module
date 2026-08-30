"""إضافةُ الوقت للمشترك تقبل **دقائق وساعات وأيّامًا** — لا أيّامًا فقط.

🔴 طلبُ المالك: «عند إضافة وقت يكون فقط أيام — ما بنقدر نضيف أقل من يوم،
وأحيانًا بنحتاج نضيف ساعة أو نص ساعة».

والمفارقة أنّ الخادمَ كان يقبل الدقائقَ من البداية (`users_extend` يقرأ
`minutes`)، وحسابُ السعر في الواجهة يوزّع بالتناسب (`minutes / plan_minutes`)،
بل والـJS كان يقرأ `data-usq-hours` — **لكنّ الحقلَ لم يوجد في القالب قطّ**.
فالقيدُ كان حقلًا ناقصًا لا منطقًا ناقصًا.

ولوحةُ الشحن (`recharge_panel.html`) فيها المحدِّدُ كاملًا منذ البداية —
فالنقصُ كان في شاشة المشتركين وحدَها، وهي الأكثرُ استعمالًا.
"""
from __future__ import annotations

import io
import os
import re

import pytest

TPL = os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                   "radius", "users_list.html")


@pytest.fixture(scope="module")
def html() -> str:
    return io.open(TPL, encoding="utf-8").read()


def _extend_modal(html: str) -> str:
    """كتلةُ نافذة «إضافة وقت» وحدَها — كي لا يلتقط الاختبارُ نافذةً أخرى."""
    i = html.index('data-usq-modal="extend"')
    return html[i:i + 6000]


def test_duration_value_and_unit_fields_exist(html):
    """🔴 الانحدارُ بعينه: حقلُ «عدد الأيام» وحدَه يمنع إضافةَ ساعة."""
    block = _extend_modal(html)
    assert "data-usq-dur-value" in block, "لا حقلَ لقيمة المدّة"
    assert "data-usq-dur-unit" in block, "لا محدِّدَ للوحدة"


def test_all_three_units_are_offered(html):
    """أيّام (1440) · ساعات (60) · دقائق (1) — بقيمها الصحيحة بالدقائق."""
    block = _extend_modal(html)
    sel = block[block.index("data-usq-dur-unit"):]
    sel = sel[:sel.index("</select>")]
    for value in ('value="1440"', 'value="60"', 'value="1"'):
        assert value in sel, f"الوحدة {value} غير معروضة"


def test_no_stale_days_only_field(html):
    """الحقلُ القديم أُزيل — وجودُه معه يعني مصدرَين للمدّة يتناقضان."""
    block = _extend_modal(html)
    assert "data-usq-days>" not in block
    assert "data-usq-hours>" not in block


def test_js_computes_minutes_from_value_times_unit(html):
    """المصدرُ واحدٌ لحساب السعر ولحقل الإرسال — فلا يفترقان."""
    assert "function readDurationMinutes" in html
    assert re.search(r"readDurationMinutes\s*\(", html)
    # الحقلُ المُرسَل يُملأ من الدالّة نفسها
    seg = html[html.index("function updateMinutes"):]
    seg = seg[:600]
    assert "readDurationMinutes" in seg


def test_price_calc_uses_the_same_source(html):
    """حسابُ السعر يقرأ المدّةَ من الدالّة لا من حقل الأيّام."""
    seg = html[html.index("function syncExtendAmount"):]
    seg = seg[:900]
    assert "readDurationMinutes" in seg
    assert "data-usq-days" not in seg


# ── الرسالة تُعرض كما يفكّر بها المشغّل ────────────────────────────────
@pytest.mark.parametrize("minutes,expect", [
    (30, "30 دقيقة"), (60, "ساعة"), (90, "ساعة و30 دقيقة"),
    (1440, "يوم"), (2880, "يومان"),
])
def test_duration_is_humanized_not_raw_minutes(minutes, expect):
    from app.radius.core.system_config import format_duration_days
    assert format_duration_days(minutes) == expect
