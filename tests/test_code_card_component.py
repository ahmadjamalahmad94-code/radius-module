# -*- coding: utf-8 -*-
"""Reusable script/code-card component (_partials/code_card.html).

The «onboarding-script» treatment extracted into DRY macros + global CSS/JS,
applied across every panel page that shows a script the user copies.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_codecard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _render(app, body):
    from flask import render_template_string
    # a request context so the macros' _() (gettext) resolves
    with app.test_request_context("/"):
        return render_template_string(
            '{% import "_partials/code_card.html" as cc %}' + body)


def test_code_card_renders_toolbar_lines_and_verbatim(app):
    html = _render(app, "{{ cc.code_card('a=1\\nb=2\\n# note', id='x', title='T') }}")
    # dark card + toolbar (window dots, LTR + line-count badges) + copy button
    assert 'data-cc-card="x"' in html
    assert "hcode-dots" in html and "LTR" in html
    assert 'data-cc-copy="x"' in html
    # line-numbered code (one span per line) inside an LTR container
    assert 'data-cc-code="x"' in html and 'dir="ltr"' in html
    assert html.count('class="hcode-ln') == 3
    assert "is-cmt" in html              # the comment line styled
    # hidden verbatim source for byte-exact copy
    assert 'id="x-full"' in html and "a=1" in html


def test_code_card_sections_emit_hidden_bodies(app):
    secs = "[{'title':'S1','start_line':1,'body':'a=1'},{'title':'S2','start_line':2,'body':'b=2'}]"
    html = _render(app, "{{ cc.code_card('a=1\\nb=2', id='y', sections=" + secs + ") }}")
    assert 'id="y-sec-0"' in html and 'id="y-sec-1"' in html


def test_code_outline_chips_jump_and_copy(app):
    secs = "[{'title':'First','start_line':1},{'title':'Second','start_line':3}]"
    html = _render(app, "{{ cc.code_outline(" + secs + ", 5, target='z') }}")
    assert "hcode-outline" in html
    # jump targets the card id, with explicit start/end line ranges
    assert 'data-cc-jump="z"' in html
    assert 'data-cc-jump-start="1"' in html and 'data-cc-jump-end="2"' in html   # First → up to line before Second
    assert 'data-cc-jump-start="3"' in html and 'data-cc-jump-end="5"' in html   # Second → to the end
    # per-section copy
    assert 'data-cc-seccopy="z"' in html and 'data-cc-sec="0"' in html


def test_code_steps_and_callouts(app):
    html = _render(app, "{{ cc.code_steps(['one','two','three']) }}")
    assert "hcode-steps" in html and html.count("hcode-step-n") == 3
    warn = _render(app, "{{ cc.code_callout('careful', kind='warn', title='Heads up') }}")
    assert "hcode-callout--warn" in warn and "Heads up" in warn and "careful" in warn
    ok = _render(app, "{{ cc.code_callout('all good', kind='ok') }}")
    assert "hcode-callout--ok" in ok


def test_code_shell_for_dynamic_preview(app):
    html = _render(app, "{{ cc.code_shell('dyn1', title='Live', body='placeholder') }}")
    # chrome-only variant: a <pre id> the page JS fills + a live-copy button
    assert 'id="dyn1"' in html and "hcode-plain" in html
    assert 'data-cc-copy-live="dyn1"' in html
    assert "placeholder" in html


def test_component_assets_loaded_globally(app, client=None):
    """The CSS lives in the globally-loaded unified_design.css and the JS is
    wired once in the base layout — so any page can use the macro plug-and-play."""
    css = open("app/static/css/unified_design.css", encoding="utf-8").read()
    assert ".hcode-card" in css and ".hcode-ln::before" in css and ".hcode-callout" in css
    layout = open("app/templates/admin/_admin_layout.html", encoding="utf-8").read()
    assert "js/code_card.js" in layout
