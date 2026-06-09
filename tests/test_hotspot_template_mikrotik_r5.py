"""R5 — MikroTik official template + QR↔CHAP compatibility.

This file pins two contracts at once:

1. The new `mikrotik` template carries every RouterOS + R4 marker
   the runtime depends on, with NO external file references —
   /css, /img, /md5.js are all inlined.

2. The R4 autologin script still works when injected into a CHAP
   template like this one. The KEY change in R4 (introduced
   alongside R5) is using `requestSubmit()` instead of `submit()`
   so the `onSubmit="return doLogin()"` handler fires and the
   password is hashed before it lands at the wire. Without that,
   a CHAP-only router would reject the autologin attempt and the
   QR card would feel broken even though everything looks right.

We don't run a real browser here — there's no JS engine in the
test env. Instead the tests build the *deterministic* contract:
specific tokens that must appear in specific positions in the
rendered HTML, in the right order. If a future commit breaks the
order or drops a token, these tests catch it.
"""
from __future__ import annotations

import re

import pytest


# ─── Catalogue presence ───────────────────────────────────────


def test_mikrotik_template_is_in_library():
    from app.radius.services import hotspot_templates as ht
    by_slug = {t.slug: t for t in ht.LIBRARY}
    assert "mikrotik" in by_slug
    tmpl = by_slug["mikrotik"]
    assert tmpl.name_ar == "MikroTik الرسمي"
    assert "CHAP" in tmpl.description_ar


def test_mikrotik_template_is_resolved_by_render():
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {})
    assert "<!DOCTYPE html>" in out
    assert "MikroTik" in out or "{{TENANT_NAME}}" not in out


# ─── No external file references ──────────────────────────────


def test_mikrotik_template_has_no_external_file_references():
    """R3 deploys login.html only — any <link href="/css/...">,
    <img src="/img/...">, or <script src="/md5.js"> would 404 on
    the router. Pin that nothing leaks back into the template."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    forbidden = [
        '/css/style.css',
        '/css/',
        'src="/md5.js"',
        '/md5.js"',
        'src="md5.js"',
        'src="/img/',
        'src="img/',
        'href="/css/',
        'href="css/',
    ]
    for needle in forbidden:
        assert needle not in raw, (
            f"mikrotik template still has external reference: {needle}"
        )


def test_mikrotik_template_inlines_md5_implementation():
    """If we accidentally drop the inlined MD5 code, CHAP would
    silently break — the page would render fine but doLogin()
    would ReferenceError on hexMD5."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    # The core functions of the Paul Johnston MD5 implementation.
    assert "function safe_add" in raw
    assert "function coreMD5" in raw
    assert "function hexMD5" in raw


def test_mikrotik_template_inlines_icons_as_svg():
    """The user/password icons live inside <svg> elements, not as
    <img src="img/...">. Two SVG tags expected (user + password)."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    # At least 2 inline SVG tags — one per input icon.
    svg_count = raw.count('<svg')
    assert svg_count >= 2


# ─── RouterOS contract ────────────────────────────────────────


def test_mikrotik_carries_required_routeros_placeholders():
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    for p in (
        "$(link-login-only)",
        "$(chap-id)",
        "$(chap-challenge)",
        "$(error)",
    ):
        assert p in raw, f"mikrotik template missing {p}"


def test_mikrotik_keeps_chap_conditional_blocks():
    """The original MikroTik template gates the CHAP transformer
    behind `$(if chap-id)...$(endif)` so PAP-only routers don't
    pay the script-execution cost. Pin that pattern."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    assert "$(if chap-id)" in raw
    assert "$(endif)" in raw


def test_mikrotik_carries_required_chap_fields():
    """The CHAP transformer needs three structural pieces in the
    rendered page, in addition to the $(chap-id)/$(chap-challenge)
    placeholders the router fills:

      1. A hidden `sendin` form with `username` + `password` inputs
         (these receive the hashed credentials).
      2. The hidden `dst` + `popup` carry-over fields, so the
         post-auth redirect still works after CHAP.
      3. The doLogin() function that writes into sendin and submits.

    Pin all three so a future refactor that, say, renames the
    hidden form can't silently break CHAP without a failing test.
    """
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html

    # (1) Hidden sendin form with username + password slots.
    assert re.search(
        r'<form\s+name="sendin"[^>]*action="\$\(link-login-only\)"',
        raw,
    ), "missing hidden sendin form"
    sendin_block = raw[raw.index('name="sendin"'):]
    # The two slots the hash lands in.
    assert re.search(r'<input[^>]*\bname="username"',
                     sendin_block[:600])
    assert re.search(r'<input[^>]*\bname="password"',
                     sendin_block[:600])

    # (2) Carry-over fields so the post-auth redirect chain works.
    assert 'name="dst"' in sendin_block[:600]
    assert "$(link-orig)" in sendin_block[:600]
    assert 'name="popup"' in sendin_block[:600]

    # (3) The CHAP transformer function itself.
    assert "function doLogin" in raw
    assert "document.sendin.username.value" in raw
    assert "document.sendin.password.value" in raw
    assert "document.sendin.submit()" in raw


def test_mikrotik_uses_chap_onsubmit_handler():
    """`onSubmit="return doLogin()"` is what triggers MD5 hashing
    before submission. Without it the form posts a raw password."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    # Whitespace-tolerant regex — Jinja or future formatting changes
    # shouldn't trip this.
    assert re.search(
        r'onSubmit\s*=\s*"return\s+doLogin\(\)"', raw,
    ), "mikrotik form must wire onSubmit -> doLogin"


def test_mikrotik_template_is_rtl_arabic():
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    assert 'dir="rtl"' in raw
    assert 'lang="ar"' in raw
    # Arabic placeholders the operator sees.
    assert "اسم المستخدم" in raw
    assert "كلمة المرور" in raw
    assert "دخول" in raw


# ─── R4 autologin contract ────────────────────────────────────


def test_mikrotik_carries_form_name_login_and_required_inputs():
    """The R4 autologin script looks up `document.forms["login"]`
    and reads `f.username` / `f.password`. If any of those names
    drift, autologin silently breaks. Pin the contract."""
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    assert re.search(r'<form\s[^>]*name="login"', raw)
    assert re.search(r'<input[^>]*\bname="username"', raw)
    assert re.search(r'<input[^>]*\bname="password"', raw)


def test_mikrotik_has_closing_body_for_autologin_injection():
    from app.radius.services import hotspot_templates as ht
    raw = ht.TEMPLATES_BY_SLUG["mikrotik"].html
    assert "</body>" in raw


def test_mikrotik_render_injects_autologin_script():
    """Render through the same path the designer uses + verify
    the autologin script lands in the final HTML, BEFORE </body>
    and AFTER the CHAP transformer script."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {})
    # Autologin contract markers
    assert 'qsGet("u")' in out
    assert 'qsGet("p")' in out
    assert "document.forms[\"login\"]" in out
    # Injection point is just before </body>
    assert out.index("</script>") < out.index("</body>")


def test_mikrotik_autologin_uses_requestSubmit_not_raw_submit():
    """KEY R5 fix: with CHAP, calling f.submit() bypasses the
    form's onsubmit handler, so the raw password (not the
    md5-hashed CHAP response) lands at the wire. requestSubmit()
    fires onsubmit — that's what makes QR auto-login work on
    CHAP-enabled hotspots.

    The test reads the rendered HTML and pins the substring;
    a regression that drops back to plain submit() is caught
    here at the seam.
    """
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {})
    assert "requestSubmit" in out, (
        "autologin must use requestSubmit() so CHAP onsubmit "
        "fires when a QR auto-fills credentials"
    )
    # And there's a click() fallback for older browsers.
    assert "btn.click()" in out


def test_mikrotik_autologin_script_is_after_chap_setup():
    """Script order matters: the autologin <script> must come
    AFTER the CHAP <script> (the one that defines hexMD5 + doLogin)
    so doLogin is in scope when onsubmit fires. If the order
    inverts, requestSubmit triggers an onsubmit that references
    an undefined doLogin."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {})
    chap_pos = out.find("function doLogin")
    auto_pos = out.find('qsGet("u")')
    assert chap_pos != -1 and auto_pos != -1
    assert chap_pos < auto_pos, (
        "autologin script must be injected AFTER the CHAP "
        "transformer so doLogin() is in scope when "
        "requestSubmit() fires onsubmit"
    )


# ─── End-to-end QR auto-login contract ────────────────────────


def test_qr_url_keys_match_what_autologin_reads():
    """The URL builder + the injected JS must agree on key names.
    A drift here = printed QR cards stop working without any
    visible error."""
    from app.radius.services import hotspot_templates as ht
    from urllib.parse import urlparse, parse_qs
    url = ht.card_autologin_url(
        scheme="http", host="192.168.10.1",
        username="testuser", password="testpass",
    )
    params = parse_qs(urlparse(url).query)
    # The keys the URL carries.
    assert "u" in params and params["u"] == ["testuser"]
    assert "p" in params and params["p"] == ["testpass"]
    # And the rendered template reads exactly those keys.
    out = ht.render("mikrotik", {})
    assert 'qsGet("u")' in out
    assert 'qsGet("p")' in out


def test_qr_autologin_simulated_url_contract(monkeypatch):
    """Simulate what happens when a QR scan opens
    http://<gateway>/?u=testuser&p=testpass on the mikrotik
    template, end-to-end via the contract:

      1. URL builder produces /?u=testuser&p=testpass
      2. Render injects the autologin <script>
      3. Script reads u/p via manual location.search parser (ES5)
      4. Script writes to the form's username + password fields
      5. Script triggers requestSubmit()
      6. onSubmit handler fires doLogin() (defined by the CHAP block)
      7. doLogin reads document.login.password (now = "testpass"),
         hashes it, posts hidden sendin form

    We can't run JS here, so the test pins each link in the chain
    via string contracts. If any link breaks, the whole flow does.
    """
    from app.radius.services import hotspot_templates as ht
    # (1) URL builder
    url = ht.card_autologin_url(
        scheme="http", host="10.0.0.1",
        username="testuser", password="testpass",
    )
    assert "?u=testuser&p=testpass" in url

    # (2) + (3) Render produces a script that reads u/p via the
    # ES5 manual parser (captive-portal browsers often lack the
    # modern URL parser API — see hotspot_templates._AUTOLOGIN_JS).
    out = ht.render("mikrotik", {})
    assert "function qsGet(name)" in out
    assert 'qsGet("u")' in out
    assert 'qsGet("p")' in out
    # Hard sanity: the script must NOT use the modern URL parser
    # — that's the regression the ES5 rewrite was meant to fix.
    assert "URLSearchParams" not in out

    # (4) Script writes to form.username / form.password
    # Pin the exact field-access pattern so a rename of the
    # autologin internals doesn't silently drop the write side.
    assert "ui.value = u" in out
    assert "pi.value = p" in out
    # And the lookup goes via document.forms["login"].
    assert "document.forms[\"login\"]" in out

    # (5) Script triggers requestSubmit (NOT raw .submit()).
    # f.submit() bypasses onsubmit -> CHAP would be skipped.
    assert "f.requestSubmit()" in out

    # (6) onSubmit -> doLogin is wired on the visible login form.
    assert re.search(r'onSubmit\s*=\s*"return\s+doLogin\(\)"', out)

    # (7) doLogin posts the hidden sendin form with the MD5-hashed
    # password (chap-id + password + chap-challenge).
    assert "document.sendin.password.value=hexMD5(" in out
    assert "'$(chap-id)'" in out
    assert "'$(chap-challenge)'" in out
    assert "document.sendin.submit()" in out


def test_rendering_through_designer_preview_path_still_carries_qr_contract():
    """The designer iframe (R2 preview) strips RouterOS $(...)
    tokens to show a WYSIWYG view, but the deploy path uses
    render() which keeps everything. Make sure render() — the
    path R3 uses — preserves the full QR + CHAP contract."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {})
    # Required QR-pipeline markers all present. The script reads
    # u/p via a manual qsGet() parser (ES5-only — captive-portal
    # browsers often choke on URLSearchParams).
    for marker in (
        "function qsGet(name)",
        'qsGet("u")',
        'qsGet("p")',
        'document.forms["login"]',
        "f.requestSubmit",
    ):
        assert marker in out, f"render dropped {marker}"
    # Required CHAP markers all present.
    for marker in (
        "function doLogin",
        "$(chap-id)",
        "$(chap-challenge)",
        "hexMD5",
        "document.sendin",
    ):
        assert marker in out, f"render dropped {marker}"


# ─── Resilience to designer-style variations ──────────────────


def test_qr_contract_survives_variable_substitution():
    """Operator-supplied values flow through `{{TENANT_NAME}}`
    etc. — verify those substitutions don't accidentally touch
    the QR/CHAP plumbing.
    """
    from app.radius.services import hotspot_templates as ht
    custom = {
        "TENANT_NAME": "نادي WiFi",
        "ACCENT_COLOR": "#16A34A",
        "BG_COLOR": "#0F172A",
        "TENANT_LOGO_URL": "/img/club.png",
        "WELCOME_TEXT": "أهلاً بكم — يلزم تسجيل دخول",
    }
    out = ht.render("mikrotik", custom)
    # Custom values landed.
    assert "نادي WiFi" in out
    assert "#16A34A" in out
    # And QR/CHAP plumbing still intact.
    assert "f.requestSubmit" in out
    assert "function doLogin" in out
    assert 'qsGet("u")' in out


def test_qr_contract_survives_disabling_autologin_injection():
    """`render(..., with_autologin=False)` is the opt-out path —
    even there, the CHAP plumbing must remain unaffected. The
    autologin script disappears, but the form contract holds."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {}, with_autologin=False)
    # Autologin removed.
    assert 'qsGet("u")' not in out
    # CHAP still there.
    assert "function doLogin" in out
    assert "$(chap-id)" in out
    # Form contract intact.
    assert re.search(r'<form\s[^>]*name="login"', out)
