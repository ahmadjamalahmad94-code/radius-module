"""R4 — QR auto-login URL builder + login-template JS injection.

We don't render a QR image in this commit (no qrcode lib in the
env). What we do ship — and pin — is the wiring:

  - `card_autologin_url()` builds the URL a printed QR would
    encode. URL-encodes the username + password so vouchers with
    "/", "?", or "%" don't break the query string.
  - `render()` now injects a small auto-login `<script>` into
    every catalogue template. The script reads ?u=...&p=...,
    fills the form, and submits. If those keys are missing the
    page falls back to manual login — no UX regression for
    operators who don't print QR cards.
"""
from __future__ import annotations

import pytest


def test_autologin_url_encodes_username_and_password():
    from app.radius.services.hotspot_templates import (
        card_autologin_url,
    )
    url = card_autologin_url(
        scheme="http", host="192.168.10.1",
        username="user/with?special", password="p@ss/word",
    )
    # The reserved characters in the credentials are percent-
    # encoded so they don't break the query string.
    assert "user%2Fwith%3Fspecial" in url
    assert "p%40ss%2Fword" in url
    # And the keys are the short u / p contract the JS reads.
    assert "?u=" in url
    assert "&p=" in url


def test_autologin_url_uses_explicit_keys():
    """Short keys keep the encoded QR small; tests pin them so
    the JS injector and URL builder don't drift apart."""
    from app.radius.services.hotspot_templates import (
        QR_AUTOLOGIN_USER_KEY, QR_AUTOLOGIN_PASS_KEY,
    )
    assert QR_AUTOLOGIN_USER_KEY == "u"
    assert QR_AUTOLOGIN_PASS_KEY == "p"


def test_render_injects_autologin_script_by_default():
    from app.radius.services import hotspot_templates as ht
    out = ht.render("classic", {})
    # The script tag is the contract — the captive portal page
    # MUST read ?u=...&p=... and submit the form.
    assert "<script>" in out
    assert 'qs.get("u")' in out
    assert 'qs.get("p")' in out
    # And it lands before </body>, not after.
    assert out.index("</script>") < out.index("</body>")


def test_render_with_autologin_false_skips_injection():
    from app.radius.services import hotspot_templates as ht
    raw = ht.render("classic", {}, with_autologin=False)
    assert 'qs.get("u")' not in raw


def test_autologin_script_in_every_catalogue_template():
    from app.radius.services import hotspot_templates as ht
    for tmpl in ht.LIBRARY:
        out = ht.render(tmpl.slug, {})
        assert 'qs.get("u")' in out, (
            f"template {tmpl.slug!r} lost its autologin injection"
        )
        # And RouterOS placeholders are still intact next to the
        # script — both contracts have to hold simultaneously.
        for p in ("$(link-login-only)", "$(chap-id)",
                  "$(chap-challenge)", "$(error)"):
            assert p in out, (
                f"template {tmpl.slug!r} lost {p} after R4 inject"
            )


def test_autologin_script_does_not_run_if_keys_missing():
    """JS contract: if u/p are not in location.search, the script
    must return early and leave the form alone. Read the actual
    JS body to pin it."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("dark", {})
    # The literal "if (!u || !p) return" is the fail-open path.
    assert "if (!u || !p) return" in out


def test_render_raises_when_template_has_no_body_tag():
    """A future template added without </body> would crash the
    injector — catch that at the seam instead of writing a broken
    file to the router."""
    from app.radius.services import hotspot_templates as ht
    bad = ht.LoginTemplate(
        slug="bad-r4", name_ar="x", description_ar="",
        html=("<html><form name=\"login\" action=\"$(link-login-only)\">"
              "<input name=\"username\"><input name=\"password\">"
              "<input name=\"chap-id\" value=\"$(chap-id)\">"
              "<input name=\"chap-challenge\" value=\"$(chap-challenge)\">"
              "$(if error)$(endif)"
              "</form></html>"),
    )
    ht.TEMPLATES_BY_SLUG[bad.slug] = bad
    try:
        with pytest.raises(ValueError):
            ht.render(bad.slug, {})
    finally:
        del ht.TEMPLATES_BY_SLUG[bad.slug]
