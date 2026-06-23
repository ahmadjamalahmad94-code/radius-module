# -*- coding: utf-8 -*-
"""اكتمال التدويل (i18n) إلى 100% للغات en/fr/tr/es.

يثبّت أنّ:
  • كل لغة غير العربية لا تحوي أي msgstr فارغًا ولا أي مدخل fuzzy (تغطية 100%).
  • العناصر النائبة (placeholders) محفوظة بدقّة بين الـmsgid والترجمة.
  • صفحات مُغلَّفة فعليًّا تُعرَض باللغة الهدف عند تبديل اللغة (لا عربيّة متبقّية
    على النص المُغلَّف)، ومبدّل اللغة يسرد اللغات الخمس.
شغّل هذا الملف وحده (عزل الاختبارات لكل ملف)."""
from __future__ import annotations

import os
import re
import tempfile

import pytest

LOCALES = ("en", "fr", "tr", "es")
TOKEN_RE = re.compile(r'%\([^)]+\)[sd]|%[sd]|\{[^}]*\}')


def _po(locale: str):
    from babel.messages.pofile import read_po
    path = os.path.join("translations", locale, "LC_MESSAGES", "messages.po")
    with open(path, encoding="utf-8") as fh:
        return read_po(fh)


def _msgstr(m) -> str:
    s = m.string
    return "".join(s) if isinstance(s, (list, tuple)) else (s or "")


@pytest.mark.parametrize("locale", LOCALES)
def test_no_empty_or_fuzzy_msgstr(locale):
    """صفر msgstr فارغ + صفر fuzzy ⇒ تغطية 100%."""
    cat = _po(locale)
    empty = [m.id for m in cat if m.id and not _msgstr(m)]
    fuzzy = [m.id for m in cat if m.id and m.fuzzy]
    assert not empty, f"{locale}: {len(empty)} empty msgstr (e.g. {empty[:3]})"
    assert not fuzzy, f"{locale}: {len(fuzzy)} fuzzy entries (e.g. {fuzzy[:3]})"


@pytest.mark.parametrize("locale", LOCALES)
def test_placeholders_preserved(locale):
    """مجموعة الرموز النائبة في الترجمة تطابق المصدر تمامًا."""
    cat = _po(locale)
    bad = []
    for m in cat:
        if not m.id:
            continue
        mid = m.id if isinstance(m.id, str) else m.id[0]
        val = _msgstr(m)
        if not val:
            continue
        if sorted(TOKEN_RE.findall(mid)) != sorted(TOKEN_RE.findall(val)):
            bad.append(mid)
    assert not bad, f"{locale}: {len(bad)} placeholder mismatches (e.g. {bad[:3]})"


def test_supported_locales_has_five():
    from app.radius.i18n import SUPPORTED_LOCALES, LANGUAGES
    assert set(SUPPORTED_LOCALES) == {"ar", "en", "fr", "tr", "es"}
    assert all(k in LANGUAGES for k in SUPPORTED_LOCALES)


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "b.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        with transaction() as cx:
            cx.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
    return application


# (page url, per-locale expected translated marker) — all from wrapped strings.
_PAGE_MARKERS = {
    "/admin/radius/connected-stats": {
        "en": "Connected users statistics", "fr": "Statistiques",
        "tr": "istatistik", "es": "Estadísticas"},
    "/admin/radius/settings/system": {
        "en": "Save system settings", "fr": "Enregistrer les paramètres du système",
        "tr": "Sistem ayarlarını kaydet", "es": "Guardar configuración del sistema"},
    "/admin/radius/notifications": {
        "en": "Notification center", "fr": "Centre de notifications",
        "tr": "Bildirim merkezi", "es": "Centro de notificaciones"},
}


@pytest.mark.parametrize("locale", LOCALES)
def test_pages_render_in_target_language(app, locale):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t", locale=locale)
    for url, markers in _PAGE_MARKERS.items():
        r = c.get(url, follow_redirects=True)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        html = r.get_data(as_text=True)
        assert markers[locale] in html, f"{locale} {url}: missing marker {markers[locale]!r}"


def test_language_switcher_lists_all_five(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t", locale="en")
    html = c.get("/admin/radius/", follow_redirects=True).get_data(as_text=True)
    for name in ("English", "Français", "Türkçe", "Español", "العربية"):
        assert name in html, f"switcher missing {name}"


# ── docs guide prose translated (the «كيف تستخدمني» guides) ──
# marker = the translated "All guides" action button, present on every guide.
_DOCS_MARKER = {"en": "All guides", "fr": "Tous les guides",
                "tr": "Tüm kılavuzlar", "es": "Todas las guías"}
_DOCS_PAGES = ("/admin/radius/docs/anti-mac-clone", "/admin/radius/docs/add-router")


@pytest.mark.parametrize("locale", LOCALES)
def test_docs_guides_render_translated(app, locale):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t", locale=locale)
    for url in _DOCS_PAGES:
        r = c.get(url, follow_redirects=True)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        html = r.get_data(as_text=True)
        assert _DOCS_MARKER[locale] in html, f"{locale} {url}: docs prose not translated"


# ── JS toast strings translated via the {{ _()|tojson }} bridge in ipchange ──
_JS_TOAST = {  # source → per-locale expected (as decoded JS string)
    "تم توليد السكربت.": {"en": "Script generated.", "fr": "Script généré.",
                          "tr": "Betik oluşturuldu.", "es": "Script generado."},
    "تم النسخ.": {"en": "Copied.", "fr": "Copié.", "tr": "Kopyalandı.", "es": "Copiado."},
}


@pytest.mark.parametrize("locale", LOCALES)
def test_js_toast_strings_translated(app, locale):
    import json as _json
    import re as _re
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t", locale=locale)
    html = c.get("/admin/radius/ip-change", follow_redirects=True).get_data(as_text=True)
    # the bridge emits e.g.  script_generated: "....",  (tojson may \u-escape)
    for key in ("script_generated", "copied"):
        m = _re.search(key + r':\s*("(?:[^"\\]|\\.)*")', html)
        assert m, f"{locale}: _t.{key} bridge entry missing"
        rendered = _json.loads(m.group(1))   # decodes \uXXXX → real chars
        src = "تم توليد السكربت." if key == "script_generated" else "تم النسخ."
        assert rendered == _JS_TOAST[src][locale], \
            f"{locale}: _t.{key} = {rendered!r}, expected {_JS_TOAST[src][locale]!r}"


# ── comprehensive in-page JS strings (inline `{{ _()|tojson }}`) translated ──
# (page url, arabic source string wrapped in that page's <script>)
_JS_PAGE_STRINGS = {
    "/admin/radius/users": "قائمة المشتركين",   # export filename
    "/admin/radius/backups": "تعذّر الاتصال بالخادم.",
}


@pytest.mark.parametrize("locale", LOCALES)
def test_inpage_js_strings_translated(app, locale):
    """Inline `{{ _()|tojson }}` JS literals render as the locale's translation
    in the page source (tojson may \\u-escape non-ASCII, which JS decodes)."""
    import json as _json
    from babel.support import Translations
    tr = Translations.load("translations", [locale])
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t", locale=locale)
    for url, src in _JS_PAGE_STRINGS.items():
        html = c.get(url, follow_redirects=True).get_data(as_text=True)
        expected = tr.ugettext(src)
        enc = _json.dumps(expected)[1:-1]          # the \u-escaped JS form
        assert (expected in html) or (enc in html), \
            f"{locale} {url}: JS string {src!r} not rendered translated ({expected!r})"
