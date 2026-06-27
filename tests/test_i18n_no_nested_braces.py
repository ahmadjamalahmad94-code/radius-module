# -*- coding: utf-8 -*-
"""Regression guard: no `{{ _(...) }}` may sit INSIDE a string literal that is
itself inside a Jinja {{ }} / {% %} expression — there the braces don't evaluate
and render as literal text to users (a regression from the i18n wrapping passes).
The correct form inside such strings is  ' ~ _("...") ~ '  (concatenation).

Run alone (per-file isolation)."""
from __future__ import annotations

import glob
import os

BS = chr(92)
ROOT = os.path.dirname(os.path.dirname(__file__))


def _nested_hits(text):
    """Indices of every `{{ _` that appears inside a quoted string within a
    Jinja expression."""
    hits = []
    i, n = 0, len(text)
    in_expr = False
    close = None
    in_str = None
    while i < n:
        two = text[i:i + 2]
        if not in_expr:
            if two == '{{' or two == '{%':
                in_expr = True
                close = '}}' if two == '{{' else '%}'
                in_str = None
                i += 2
                continue
            i += 1
            continue
        c = text[i]
        if in_str:
            if c == BS:
                i += 2
                continue
            if c == in_str:
                in_str = None
                i += 1
                continue
            if text[i:i + 4] == '{{ _' or text[i:i + 3] == '{{_':
                hits.append(i)
                i += 2
                continue
            i += 1
            continue
        if c == '"' or c == "'":
            in_str = c
            i += 1
            continue
        if text[i:i + 2] == close:
            in_expr = False
            i += 2
            continue
        i += 1
    return hits


def test_no_translation_calls_nested_in_string_literals():
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "app", "templates", "**", "*.html"),
                          recursive=True):
        text = open(path, encoding="utf-8").read()
        for idx in _nested_hits(text):
            line = text.count("\n", 0, idx) + 1
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            offenders.append("%s:%d :: %s" % (rel, line, text[idx:idx + 40]))
    assert not offenders, (
        "{{ _() }} nested in a string literal (renders as raw braces); use "
        "' ~ _(\"...\") ~ ':\n" + "\n".join(offenders[:40]))
