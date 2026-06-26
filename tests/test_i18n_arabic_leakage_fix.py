# -*- coding: utf-8 -*-
"""Regression: no Arabic leakage in the surfaces fixed by the leakage audit.

Two classes of fix are covered:
  1. CATALOG gaps — the leftover single-char abbreviations (د/س) and the
     Arabic-Indic digits (٢٠٠، ١٠٠٠…) that stayed inside otherwise-translated
     strings now resolve to non-Arabic in en/fr/es/tr.
  2. HARDCODED titles — page/browser titles that were raw Arabic are now wrapped
     in _() and reuse existing translations.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import gettext
import os
import re

import pytest

AR = re.compile('[؀-ۿ]')
AR_DIGITS = re.compile('[٠-٩]')
LOCALES = ("en", "fr", "es", "tr")
TRDIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations")


def _t(lang):
    return gettext.translation("messages", TRDIR, [lang])


@pytest.mark.parametrize("lang", LOCALES)
def test_minute_hour_abbreviations_not_arabic(lang):
    t = _t(lang)
    assert t.gettext("د") != "د"        # minute abbrev now Latin
    assert t.gettext("س") != "س"        # hour abbrev now Latin
    assert not AR.search(t.gettext("د"))
    assert not AR.search(t.gettext("س"))


@pytest.mark.parametrize("lang", LOCALES)
def test_no_arabic_indic_digits_leak_in_translations(lang):
    """No translated string in a non-Arabic locale may contain Arabic-Indic
    digits (they used to leak inside docs strings)."""
    t = _t(lang)
    for src in (
        "مثال: باقة بحدّ ٢٠٠ متّصل تكفي لقاعدة فيها ١٠٠٠ مشترك، لأنّ نادرًا ما يتّصلون كلّهم معًا.",
        "الآن، وحدودها كما استلمها النظام من عقد التشغيل. بجانب كل فئة عدّاد مثل «٣ / ٥ مفعّلة».",
        "(حتى ٢٠٠٠ حرف) أو",
    ):
        out = t.gettext(src)
        assert out != src                      # actually translated
        assert not AR_DIGITS.search(out), (lang, out)


@pytest.mark.parametrize("lang", LOCALES)
def test_wrapped_page_titles_translate(lang):
    """A sample of the page titles that were hardcoded Arabic now translate (they
    reuse existing catalog entries)."""
    t = _t(lang)
    for src in ("التحكم بالدخول", "السجل المالي", "المدراء",
                "النسخ الاحتياطي", "التقارير المالية"):
        out = t.gettext(src)
        assert out != src and not AR.search(out), (lang, src, out)


def test_audited_templates_have_no_unwrapped_arabic_block_titles():
    """The covered admin templates must not carry a raw-Arabic block title/page_title
    anymore (it must go through _())."""
    import glob
    block_re = re.compile(
        r"\{%-?\s*block\s+(?:title|page_title)\s*-?%\}(.*?)\{%-?\s*endblock")
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "app", "templates", "radius")
    offenders = []
    # spot-check the files this pass wrapped
    for name in ("access_control.html", "accounting_ledger.html",
                 "accounting_reports.html", "admins_list.html",
                 "backups.html", "audit_list.html"):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8").read()
        for m in block_re.finditer(text):
            body = m.group(1)
            if AR.search(body) and "_(" not in body:
                offenders.append((name, body.strip()))
    assert not offenders, offenders
