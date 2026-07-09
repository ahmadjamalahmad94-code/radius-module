"""Abbreviated durations must use LATIN unit letters and stay LTR-safe in RTL.

Arabic single-letter unit abbreviations (hour/minute/day/second) get bidi-
scrambled next to Latin digits inside an RTL page ("1h 38m" flipping into an
unreadable order). The shared formatter in ``app.radius.core.duration_fmt``
fixes this by emitting Latin unit letters (d/h/m/s) which are strong-LTR —
exactly like the digits — so the token never reverses.
See MEMORY: rtl-speed-pair-bidi-isolation.
"""
import pathlib
import re

from app.radius.core.duration_fmt import fmt_hm_short, fmt_uptime_short

# Arabic / Hebrew / Arabic-presentation-forms RTL Unicode blocks. If a duration
# string holds a code point here it can flip when rendered in an RTL context.
_RTL_RANGE = re.compile("[֐-ࣿיִ-﷿ﹰ-﻿]")

# Legacy Arabic unit letters that used to scramble: س د ي ث ش
_ARABIC_UNITS = "سديثش"

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _assert_ltr_safe(text: str) -> None:
    """LTR-safe = no RTL-range characters, only Latin digits/letters and
    neutral separators, and none of the legacy Arabic unit letters."""
    assert not _RTL_RANGE.search(text), f"RTL character leaked into duration: {text!r}"
    assert re.fullmatch(r"[0-9a-zA-Z /:]+", text), f"unexpected char in {text!r}"
    for ch in _ARABIC_UNITS:
        assert ch not in text, f"Arabic unit letter still present in {text!r}"


class TestLatinUnitLetters:
    def test_hm_short_uses_latin_letters(self):
        assert fmt_hm_short(3600 + 5 * 60) == "1h 5m"
        assert fmt_hm_short(3600) == "1h"
        assert fmt_hm_short(30 * 60) == "30m"
        assert fmt_hm_short(0) == "0m"

    def test_uptime_short_uses_latin_letters(self):
        assert fmt_uptime_short(86400 + 3600) == "1d 1h 0m"
        assert fmt_uptime_short(3 * 3600 + 5 * 60) == "3h 5m"
        assert fmt_uptime_short(5 * 60) == "5m"

    def test_outputs_are_ltr_safe(self):
        for secs in (0, 59, 60, 90, 3599, 3600, 3660, 86399, 90000, 172800, 999999):
            _assert_ltr_safe(fmt_hm_short(secs))
            _assert_ltr_safe(fmt_uptime_short(secs))

    def test_no_arabic_unit_letters_over_wide_range(self):
        joined = "".join(
            fmt_hm_short(s) + fmt_uptime_short(s) for s in range(0, 200000, 137)
        )
        assert not _RTL_RANGE.search(joined)

    def test_route_formatters_alias_shared_helper(self):
        # The daily-usage columns import the shared helper as `_fmt_hm`, so a
        # single source of truth governs every daily-time cell.
        from app.radius.core import duration_fmt
        assert duration_fmt.fmt_hm_short(3660) == "1h 1m"


class TestTemplateMacrosLatinAndIsolated:
    """The Jinja duration macros must emit Latin letters wrapped in an LTR
    isolate (belt-and-suspenders on top of the Latin-letter fix)."""

    def _macro_region(self, rel: str, start_needle: str) -> str:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        idx = text.index(start_needle)
        end = text.index("endmacro", idx)
        return text[idx:end]

    def test_sessions_list_duration_macro(self):
        region = self._macro_region(
            "app/templates/radius/sessions_list.html", "{% macro fmt_duration(")
        assert 'bdi dir="ltr"' in region
        for ch in _ARABIC_UNITS:
            assert ch not in region, "Arabic unit still in fmt_duration"

    def test_portal_and_report_macros(self):
        for rel, needle in (
            ("app/templates/radius/rep_sessions.html", "{% macro fmt_dur("),
            ("app/templates/radius/users_profile.html", "{% macro fmt_dur("),
            ("app/templates/radius/portal_subscriber.html", "{% macro fmt_secs("),
        ):
            region = self._macro_region(rel, needle)
            assert 'bdi dir="ltr"' in region, f"{rel}: macro not LTR-isolated"
            for ch in _ARABIC_UNITS:
                assert ch not in region, f"{rel}: Arabic unit still present"
