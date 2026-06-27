# -*- coding: utf-8 -*-
"""Regression for the final i18n leakage pass: the weekday homograph fix
(pgettext), the long-tail UI strings, and lone-% safety.

Run alone (per-file isolation)."""
from __future__ import annotations

import gettext
import os
import re

import pytest

AR = re.compile('[؀-ۿ]')
LOCALES = ("en", "fr", "es", "tr")
TRDIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations")


def _t(lang):
    return gettext.translation("messages", TRDIR, [lang])


@pytest.mark.parametrize("lang", LOCALES)
def test_weekday_homograph_distinct_from_time_units(lang):
    """pgettext('weekday', 'س') = Saturday abbrev; bare _('س') = hour; bare
    _('ث') = second. The single letters no longer collide."""
    t = _t(lang)
    sat = t.pgettext("weekday", "س")
    hour = t.gettext("س")
    sec = t.gettext("ث")
    assert not AR.search(sat) and sat not in ("", "س")
    assert not AR.search(hour)
    assert sat != hour                  # Saturday != hour
    # the full week resolves to 7 non-Arabic, distinct-ish abbreviations
    week = [t.pgettext("weekday", c) for c in "سحنثرخج"]
    assert all(not AR.search(w) and w for w in week)
    assert len(set(week)) == 7


@pytest.mark.parametrize("lang", LOCALES)
def test_long_tail_ui_strings_translate(lang):
    t = _t(lang)
    for src in ("توليد كروت", "جلسات الوصول البعيد", "كل الحسابات", "تراجع",
                "بحث…", "حسابات النفق", "إدارة العملاء", "المحافظ المالية",
                "تواصل عبر واتساب", "لا شيء"):
        out = t.gettext(src)
        assert out != src and not AR.search(out), (lang, src, out)


@pytest.mark.parametrize("lang", LOCALES + ("ar",))
def test_percent_strings_render_without_format_error(lang):
    """Strings containing % are stored %%-escaped so newstyle gettext's
    `rv % vars` does not raise 'incomplete format'."""
    flask_babel = pytest.importorskip("flask_babel")
    from app import create_app
    app = create_app()
    with app.test_request_context('/'):
        from flask import session, render_template_string
        session["locale"] = lang
        for expr in ('{{ _("نسبة الضريبة %%") }}', '{{ _("التحميل %%") }}',
                     '{{ _("نسبة مشاركة الربح %%") }}'):
            out = render_template_string(expr)
            assert "%%" not in out and out.endswith("%")
