# -*- coding: utf-8 -*-
"""Regression for the follow-up i18n leakage pass: the previously-hardcoded
admin page titles (and a few attribute strings) are now wrapped in _() and
translate to non-Arabic in en/fr/es/tr; no raw-Arabic block title remains in the
covered templates.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import gettext
import os
import re
import glob

import pytest

AR = re.compile('[؀-ۿ]')
LOCALES = ("en", "fr", "es", "tr")
ROOT = os.path.dirname(os.path.dirname(__file__))
TRDIR = os.path.join(ROOT, "translations")
SKIP = ("_sidebar.html", "settings_page.html")   # still possibly hot
BLOCK = re.compile(r"\{%-?\s*block\s+(?:title|page_title)\s*-?%\}(.*?)\{%-?\s*endblock")

# a representative sample of the titles this pass added translations for
SAMPLE = [
    "مركز عمليات البطاقة", "أسطول الراوترات", "اتصالات الراوترات",
    "العروض والخطط", "نسخ احتياطية", "مركز سياسات الشبكة",
    "معالج إعداد HobeRadius", "ملف المشترك", "تشخيص اتصال الراوترات",
    "الأنفاق — Hobe Hub", "مركز المخاطر — هوب ريديوس", "تعديل جهاز",
    "إضافة جهاز", "حدث",
]


def _t(lang):
    return gettext.translation("messages", TRDIR, [lang])


@pytest.mark.parametrize("lang", LOCALES)
def test_new_titles_translate_to_non_arabic(lang):
    t = _t(lang)
    for src in SAMPLE:
        out = t.gettext(src)
        assert out != src, (lang, src)            # actually translated
        assert not AR.search(out), (lang, src, out)


def test_no_unwrapped_arabic_block_titles_in_covered_templates():
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "app", "templates", "**", "*.html"),
                          recursive=True):
        rel = os.path.normpath(path).replace(os.sep, "/")
        if any(s in rel for s in SKIP):
            continue
        text = open(path, encoding="utf-8").read()
        for m in BLOCK.finditer(text):
            body = m.group(1)
            if AR.search(body) and "_(" not in body:
                offenders.append((rel, body.strip()[:60]))
    assert not offenders, offenders


@pytest.mark.parametrize("lang", LOCALES)
def test_close_aria_label_translates(lang):
    # a sampled attribute string wrapped this pass
    assert not AR.search(_t(lang).gettext("إغلاق"))
