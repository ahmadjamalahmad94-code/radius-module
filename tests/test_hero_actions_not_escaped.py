# -*- coding: utf-8 -*-
"""Regression guard: every `actions_html=` hero/section builder must render as
REAL HTML markup, not escaped text.

Root cause it guards against: Jinja's `~`/`+` concatenation under autoescape
escapes raw-HTML *str* operands (`'<a>' ~ _("x")` -> `&lt;a&gt;x`), so the value
reaches `hub.megahero`'s `{{ actions_html|safe }}` already-escaped and shows as
literal `<a ...>` text. Fix: each top-level HTML string literal in the builder
carries `|safe` so concatenation keeps it as markup.

Renders each builder in isolation with autoescape ON (as Flask does) and a
lenient stub context, then asserts the output is a real tag, not `&lt;`."""
from __future__ import annotations

import glob
import os

from jinja2 import Environment, Undefined

ROOT = os.path.dirname(os.path.dirname(__file__))


class _Lenient(Undefined):
    """Truthy, chainable stub: page-context vars don't blow up the render and
    conditional builders take their truthy branch."""
    __slots__ = ()

    def __getattr__(self, n):
        return _Lenient()

    def __getitem__(self, n):
        return _Lenient()

    def __call__(self, *a, **k):
        return _Lenient()

    def __str__(self):
        return "stub"

    def __html__(self):
        return "stub"

    def __bool__(self):
        return True

    def __iter__(self):
        return iter([])

    def __add__(self, o):
        return "stub" + str(o)

    def __radd__(self, o):
        return str(o) + "stub"


def _value_span(text, start):
    i, n = start, len(text)
    depth = 0
    instr = None
    while i < n:
        c = text[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == instr:
                instr = None
            i += 1
            continue
        if c in ('"', "'"):
            instr = c
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    return n


def _env():
    env = Environment(autoescape=True, undefined=_Lenient)
    env.globals["url_for"] = lambda *a, **k: "/stub/url"
    env.globals["_"] = lambda s, **k: s
    env.globals["gettext"] = env.globals["_"]
    env.globals["pgettext"] = lambda c, s, **k: s
    return env


def test_hero_action_builders_render_real_markup():
    env = _env()
    key = "actions_html="
    failures = []
    checked = 0
    for path in glob.glob(os.path.join(ROOT, "app", "templates", "**", "*.html"),
                          recursive=True):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if rel.endswith("_partials/hub.html"):
            continue  # macro library: signature defaults, not builders
        text = open(path, encoding="utf-8").read()
        idx = 0
        while True:
            p = text.find(key, idx)
            if p == -1:
                break
            vstart = p + len(key)
            vend = _value_span(text, vstart)
            idx = vend
            expr = text[vstart:vend].strip().rstrip(",").strip()
            if not expr or expr[0] not in ("'", '"', "("):
                continue  # variable reference (e.g. _hero_actions)
            if expr in ('""', "''"):
                continue
            try:
                rendered = env.from_string("{{ (%s)|safe }}" % expr).render()
            except Exception as e:  # pragma: no cover - surfaced as failure
                failures.append("%s :: RENDER-ERR %s" % (rel, str(e)[:80]))
                continue
            checked += 1
            if "&lt;a" in rendered or "&lt;button" in rendered or "&lt;form" in rendered:
                failures.append("%s :: ESCAPED -> %s" % (rel, rendered[:80]))
            elif not any(t in rendered for t in ("<a ", "<button", "<form")):
                failures.append("%s :: NO TAG -> %r" % (rel, rendered[:80]))
    assert checked > 100, "expected many builders, found %d" % checked
    assert not failures, (
        "hero actions_html builders escaped / broken (use '...'|safe on each "
        "HTML fragment):\n" + "\n".join(failures[:40]))
