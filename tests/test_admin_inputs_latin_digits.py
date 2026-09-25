# -*- coding: utf-8 -*-
"""Western digits in form inputs — the admin layout must ship the latinizer.

With <html lang="ar"> browsers render number/date/time input digits as
Arabic-Indic (٠١٢٣). The owner wants Western digits (1234) in ALL locales,
so the admin layout injects a small script that stamps lang="en" on those
inputs (initial DOM + a MutationObserver for dynamically-injected ones).
This guards the script's presence at source level and in a rendered page.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest

_LAYOUT = os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                       "admin", "_admin_layout.html")
# الناقل صار ملفًّا ثابتًا واحدًا تحمّله اللوحة والصفحات المستقلّة معًا.
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "app", "static",
                       "js", "latin_digits.js")
_STANDALONE = [
    "radius/login.html", "radius/portal_card.html", "radius/portal_card_login.html",
    "radius/portal_distributor_checker.html", "radius/portal_distributor_login.html",
    "radius/portal_subscriber.html", "radius/portal_subscriber_login.html",
    "radius/subscription_expired.html", "radius/_quick_embed_base.html",
]


def _script_src():
    with open(_SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def test_layout_ships_the_input_digit_latinizer():
    with open(_LAYOUT, encoding="utf-8") as fh:
        assert "js/latin_digits.js" in fh.read()
    src = _script_src()
    # The selector must cover ALL form controls (number + text + select +
    # textarea) — Cairo renders Hindi digits in any field under lang="ar".
    assert "'input,textarea,select'" in src
    # …stamp lang="en" on them…
    assert "setAttribute('lang', 'en')" in src
    # …and keep watching dynamically-injected nodes.
    assert "MutationObserver" in src


def test_unit_input_picker_forces_latin_digits_server_side():
    """The number+unit picker (speed/time/temp/quota fields) uses
    type="text" inputmode="decimal" — NOT type="number". Chrome localizes
    type=number display to the browser locale (Arabic → ٠١٢٣) and can ignore
    the element lang; a text field always shows the literal Latin value. This
    is deploy-robust and locale-proof — the default values (30 / 0) render
    Latin for every user regardless of their browser language."""
    path = os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                        "_partials", "unit_input.html")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'type="text" inputmode="decimal" lang="en" class="ui-value"' in src
    # the digit-localizing type=number must be gone from the picker
    assert 'type="number"' not in src.split('class="ui-value"')[0][-80:]


def test_layout_normalizes_existing_hindi_digits_everywhere():
    """Round 2 (owner: «الأرقام الموجودة ديفولت لسا هندية»): beyond widget
    rendering, actual ٠-٩/۰-۹ characters in DB-stored values and text nodes
    must be converted to 0-9 — on load, on dynamic injection, and live while
    typing (with caret preservation)."""
    src = _script_src()
    # digit-conversion core (Arabic-Indic + Extended Arabic-Indic ranges)
    assert "٠-٩۰-۹" in src
    assert "toLatin" in src
    # text-node walker + input/textarea value normalization
    assert "createTreeWalker" in src
    assert "normalizeValues" in src
    # live typing normalization, passwords excluded
    assert "addEventListener('input'" in src
    assert "password" in src
    # dynamic text changes watched too
    assert "characterData" in src


def test_latinizer_covers_browser_drawn_attributes():
    """«شبكة المحترف»: placeholder/title يرسمهما المتصفح لا DOM نصّيّ — فكان
    «مثل: دفعة شهر ٧» يبقى هنديًّا. تُطبَّع السمات وتُراقَب عند تغيّرها."""
    src = _script_src()
    for attr in ("'placeholder'", "'title'", "'aria-label'", "'data-hint'"):
        assert attr in src, attr
    assert "attributeFilter" in src


def test_standalone_pages_load_the_latinizer_too():
    """صفحاتٌ خارج _admin_layout (الدخول، البوّابات، صفحة الانتهاء) لا ترث
    الناقل — فيجب أن تحمّله صراحةً وإلّا عادت ٠١٢٣ فيها وحدَها."""
    base = os.path.join(os.path.dirname(__file__), "..", "app", "templates")
    missing = []
    for rel in _STANDALONE:
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            if "js/latin_digits.js" not in fh.read():
                missing.append(rel)
    assert not missing, missing


def test_no_arabic_digit_locales_left_in_templates():
    """JS must not format numbers with Arabic-Indic digit locales — those
    produced the «default» Hindi numbers (counters, totals) the owner saw."""
    base = os.path.join(os.path.dirname(__file__), "..", "app", "templates")
    offenders = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            if not f.endswith(".html"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8") as fh:
                s = fh.read()
            # "ar-EG" أو "ar" وحدَها في منسِّقات الأرقام/التواريخ ⇒ ٠١٢٣.
            # الصحيح "ar-u-nu-latn" (أسماء أشهرٍ عربيّة وأرقام 0-9).
            if "ar-EG" in s or re.search(
                    r"(DateTimeFormat|NumberFormat|toLocale(?:Date|Time)?String)"
                    r"\(\s*['\"]ar['\"]", s):
                offenders.append(os.path.relpath(path, base))
    assert not offenders, f"Arabic-Indic digit locales found: {offenders}"


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_lat_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_rendered_admin_page_contains_latinizer(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"lat_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="lat-pass",
                                 full_name="Latin Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "lat-pass"})
    assert res.status_code in {302, 303}
    res = client.get("/admin/radius/cards/checker")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "js/latin_digits.js" in body


def test_number_inputs_become_literal_text_fields():
    """🔴 Chrome يرسم type=number بأرقام لغة المتصفّح ويتجاهل lang — متصفّحٌ
    عربيّ رأى «٠» و«٦» (لقطة حيّة على client20). الناقل يحوّلها نصّيّة
    inputmode=decimal بعلامة data-hr-num، ويُبقي تحقّق min/max عند الإرسال."""
    src = _script_src()
    assert "el.type = 'text'" in src and "data-hr-num" in src
    assert "inputmode" in src
    assert "addEventListener('submit'" in src and "reportValidity" in src
    # أنماط type=number تبقى على الحقول المحوَّلة
    css = os.path.join(os.path.dirname(__file__), "..", "app", "static", "css",
                       "style_unification.css")
    with open(css, encoding="utf-8") as fh:
        assert '[data-hr-num]' in fh.read()


def test_time_month_week_fields_become_literal_text():
    """حقول الوقت/الشهر/الأسبوع يرسمها Chrome عربيّ «٠٢:٣٥ م» حتى مع lang=en:
    تصير نصّيّةً بنفس صيغة القيمة الأصليّة مع تحقّق الصيغة عند الإرسال، والتاريخ
    يُترك لـhub_date.js حيث يُحمَّل."""
    src = _script_src()
    for t in ("time:", "month:", "week:", "'datetime-local':"):
        assert t in src, t
    assert "data-hr-fmt" in src and "window.__hubDateInit" in src


def test_consumption_report_week_month_are_latin_selects():
    path = os.path.join(os.path.dirname(__file__), "..", "app", "templates", "radius",
                        "rep_subscriber_consumption.html")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'type="week"' not in src and 'type="month"' not in src
    assert '<select class="hub-input" name="spec_week"' in src
    assert '<select class="hub-input" name="spec_month"' in src
    from app.radius.routes.reports import _recent_month_options, _recent_week_options
    import re as _re
    weeks, months = _recent_week_options(), _recent_month_options()
    assert len(weeks) == 26 and len(months) == 24
    assert all(_re.fullmatch(r"\d{4}-W\d{2}", v) for v, _ in weeks)
    assert all(_re.fullmatch(r"\d{4}-\d{2}", v) for v, _ in months)
    assert not any(_re.search("[٠-٩]", lbl) for _, lbl in weeks + months)
