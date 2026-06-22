"""qr_svg — رمز QR → SVG لرابط ربط تيليجرام العميق.

المولّد يلفّ ``segno`` (تبعيّة معلنة، MIT). نتحقّق أنّ qr_svg:
  • يُخرِج SVG صحيح الشكل.
  • يُصيّر مصفوفة segno بأمانة (بلا إفساد) — نُعيد بناء الشبكة من المستطيلات
    ونقارنها بمصفوفة segno المرجعيّة.
  • محدّدات الموضع (Finder) في الأركان الثلاثة سليمة.
  • يرجع لوحة بديلة (لا انهيار) عند غياب المولّد.

شغّل الملف وحده:  python -m pytest tests/test_qr_svg.py
"""
from __future__ import annotations

import re

import pytest

from app.radius.services import qr_svg as q

_DEEP = "https://t.me/HobeAlertsBot?start=K7M2QP9XAB"
_BOX, _QUIET = 6, 4


def _svg_to_grid(svg: str, n: int, box: int = _BOX, quiet: int = _QUIET):
    """يُعيد بناء شبكة الوحدات (0/1) من مستطيلات الـSVG الداكنة."""
    grid = [[0] * n for _ in range(n)]
    for x, y, w in re.findall(
            r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="\d+" fill', svg):
        x, y, w = int(x), int(y), int(w)
        c0, r = x // box - quiet, y // box - quiet
        for c in range(c0, c0 + w // box):
            grid[r][c] = 1
    return grid


def _segno_matrix(data: str):
    import segno
    return [[1 if c else 0 for c in row]
            for row in segno.make(data, error="m", micro=False).matrix]


def test_qr_available():
    assert q.qr_available() is True  # segno مثبّت في بيئة الاختبار


def test_output_shape():
    svg = q.qr_svg(_DEEP)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "viewBox" in svg and svg.count("<rect") > 10


@pytest.mark.parametrize("data", [
    "x",
    "https://t.me/a?start=1",
    _DEEP,
    "https://t.me/SomeLongerBotName?start=ZZ99XX00QQ",
])
def test_faithful_render_matches_segno(data):
    m = _segno_matrix(data)
    grid = _svg_to_grid(q.qr_svg(data), len(m))
    assert grid == m, "الـSVG لا يطابق مصفوفة QR المرجعيّة"


def test_finder_patterns_present():
    m = _segno_matrix(_DEEP)
    grid = _svg_to_grid(q.qr_svg(_DEEP), len(m))
    n = len(grid)
    for (br, bc) in [(0, 0), (0, n - 7), (n - 7, 0)]:
        assert grid[br][bc] and grid[br][bc + 6] and grid[br + 6][bc]
        assert not grid[br + 1][bc + 1]  # حلقة فاتحة
        assert grid[br + 3][bc + 3]      # قلب داكن


def test_distinct_data_distinct_qr():
    assert q.qr_svg("https://t.me/Bot?start=AAA") \
        != q.qr_svg("https://t.me/Bot?start=BBB")


def test_placeholder_fallback_no_crash(monkeypatch):
    # محاكاة غياب المولّد → لوحة بديلة، لا استثناء.
    monkeypatch.setattr(q, "_matrix", lambda data, ecc: None)
    svg = q.qr_svg(_DEEP)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
