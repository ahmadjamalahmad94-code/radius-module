"""R1 — Login-page template library.

Pure service-layer tests: catalogue contents, validator, render.
No Flask, no router.
"""
from __future__ import annotations

import pytest


def test_library_carries_the_full_catalogue():
    """R1 shipped 4 templates; R5 added the MikroTik-derived one;
    the «احترافية» family (gradient_pro/royal_night/emerald/
    aurora_store/swift_login) and the Fiber-Net-inspired «توهّج
    الألياف» (fiber_glow) followed. Adding a new template = update
    this set (the catalogue contract)."""
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_skins as sk
    slugs = {t.slug for t in ht.LIBRARY}
    expected = {
        "gradient_pro", "royal_night", "emerald", "aurora_store",
        "swift_login", "fiber_glow",
        "classic", "card", "dark", "minimal", "mikrotik",
    } | set(sk.SKIN_SLUGS)  # الجلود الجديدة (feat/hotspot-gallery-expansion)
    assert slugs == expected


def test_every_template_carries_required_routeros_placeholders():
    """RouterOS injects values for $(link-login-only), $(chap-id),
    $(chap-challenge), $(error). If any catalogue entry is missing
    one, the rendered login page won't accept credentials — pin
    the contract at this seam."""
    from app.radius.services import hotspot_templates as ht
    for tmpl in ht.LIBRARY:
        missing = ht.validate_routeros_placeholders(tmpl.html)
        assert not missing, (
            f"template {tmpl.slug!r} is missing placeholders: {missing}"
        )


def test_every_template_uses_arabic_rtl_lang():
    from app.radius.services import hotspot_templates as ht
    for tmpl in ht.LIBRARY:
        assert 'dir="rtl"' in tmpl.html, f"{tmpl.slug} must be RTL"
        assert 'lang="ar"' in tmpl.html, f"{tmpl.slug} must lang=ar"


def test_template_variables_have_defaults_and_patterns():
    from app.radius.services import hotspot_templates as ht
    seen_slugs = {v.slug for v in ht.TEMPLATE_VARIABLES}
    for required in (
        "TENANT_NAME", "TENANT_LOGO_URL",
        "WELCOME_TEXT", "ACCENT_COLOR", "BG_COLOR",
    ):
        assert required in seen_slugs, f"missing var: {required}"
    # Each variable's default must validate against its own
    # pattern — otherwise the no-input render path is broken.
    for v in ht.TEMPLATE_VARIABLES:
        assert v.pattern.match(v.default), (
            f"default {v.default!r} fails pattern for {v.slug}"
        )


def test_validate_vars_fills_defaults_for_missing():
    from app.radius.services.hotspot_templates import (
        validate_vars,
    )
    out = validate_vars({})
    assert out["TENANT_NAME"] == "Hoberadius WiFi"
    assert out["ACCENT_COLOR"] == "#2563EB"


def test_validate_vars_rejects_invalid_color():
    from app.radius.services.hotspot_templates import (
        validate_vars,
    )
    with pytest.raises(ValueError):
        validate_vars({"ACCENT_COLOR": "not-a-color"})


def test_validate_vars_rejects_invalid_logo_url():
    from app.radius.services.hotspot_templates import (
        validate_vars,
    )
    with pytest.raises(ValueError):
        validate_vars({"TENANT_LOGO_URL": 'javascript:alert("xss")'})


def test_validate_vars_rejects_html_in_welcome():
    """Operators can't ship `<script>` through the welcome text —
    that field renders verbatim into the login HTML."""
    from app.radius.services.hotspot_templates import (
        validate_vars,
    )
    with pytest.raises(ValueError):
        validate_vars({"WELCOME_TEXT": "<script>alert(1)</script>"})


def test_render_substitutes_hoberadius_vars():
    from app.radius.services import hotspot_templates as ht
    out = ht.render("classic", {
        "TENANT_NAME": "نادي الإنترنت",
        "ACCENT_COLOR": "#16A34A",
    })
    assert "نادي الإنترنت" in out
    assert "#16A34A" in out
    # Defaults filled in for the rest.
    assert "{{TENANT_LOGO_URL}}" not in out
    assert "{{WELCOME_TEXT}}" not in out


def test_render_leaves_routeros_placeholders_intact():
    """The router needs `$(link-login-only)` at request time —
    our renderer MUST NOT touch it. Only Hoberadius vars are
    substituted; `$(...)` stays for the router to fill."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("card", {})
    for p in ("$(link-login-only)", "$(chap-id)",
              "$(chap-challenge)", "$(error)"):
        assert p in out, f"render dropped {p}"


def test_render_unknown_slug_raises():
    from app.radius.services import hotspot_templates as ht
    with pytest.raises(ValueError):
        ht.render("nope", {})


def test_preview_strips_routeros_placeholders():
    """The designer iframe should NOT show literal `$(...)` text.
    The deploy path uses render(); preview() is for the iframe."""
    from app.radius.services import hotspot_templates as ht
    out = ht.preview("dark", {})
    for p in ("$(link-login-only)", "$(chap-id)",
              "$(chap-challenge)", "$(error)"):
        assert p not in out


def test_render_output_does_not_double_substitute():
    """If a malicious operator puts `{{TENANT_NAME}}` inside a
    field value, the substitution must NOT recurse. (str.replace
    + dict iteration order means this could theoretically loop if
    we ever switch to a regex engine.)"""
    from app.radius.services import hotspot_templates as ht
    # The variable validator rejects `{` chars in WELCOME_TEXT, so
    # the attack surface is closed at the validator. Sanity check
    # that attempt:
    with pytest.raises(ValueError):
        ht.render("classic", {"WELCOME_TEXT": "{{ACCENT_COLOR}}"})
