# -*- coding: utf-8 -*-
"""قالب «الكلاي الناعم» (soft_clay) — Phase 2 / كافي شوب.

تصميم فاخر مُفرَد في ملفّه الخاصّ، بطلُه رسمة SVG مُضمَّنة لمشهد طينيّ لطيف
(فِنجان مبتسم + كرواسون). نتحقّق: مُسجَّل وقابل للرندر، يَحمل رسمته المُضمَّنة
offline-safe، عقود الدخول سليمة، وموضوع في القسم ② «كافي شوب».
"""
import re


def test_registered_in_library_and_section():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert "soft_clay" in ht.TEMPLATES_BY_SLUG
    t = ht.TEMPLATES_BY_SLUG["soft_clay"]
    assert t.name_ar == "الكلاي الناعم"
    cafe = [s for s in route._TEMPLATE_SECTIONS if s[0] == "cafe"][0]
    assert "soft_clay" in cafe[3]


def test_renders_with_embedded_svg_illustration():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("soft_clay", {"TENANT_NAME": "نت",
                                   "ACCENT_COLOR": "#E8927C",
                                   "MOTIF_ICON": "coffee"}, tenant_id=1)
    for marker in ("hr-soft-clay", "sc-hero", "sc-art", "sc-face",
                   "<svg", "url(#scMug)", "sc-steam"):
        assert marker in html, f"عنصر البطل مفقود: {marker}"
    assert '<div class="network-pulse-card">' not in html
    assert "prefers-reduced-motion" in html


def test_login_contracts_intact():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("soft_clay", {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)",
               "$(error)", "</body>"):
        assert ph in html, f"placeholder مفقود: {ph}"
    assert 'name="username"' in html and 'name="password"' in html
    assert 'class="bottom-nav"' in html and "mobile-container" in html


def test_watermark_backmost_and_bar_safety_apply():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("soft_clay", {"MOTIF_ICON": "coffee"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html


def test_no_raw_variable_leak():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("soft_clay", {"TENANT_NAME": "نت",
                                   "ACCENT_COLOR": "#E8927C"}, tenant_id=1)
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"تسرّب متغيّر: {leaks}"


def test_no_external_image_urls():
    # حتميّ: offline-safe — لا روابط صور خارجيّة (البوّابة بلا إنترنت قبل الدخول).
    from app.radius.services import hotspot_template_soft_clay as mod
    art = mod._SOFT_CLAY_HERO + mod._SOFT_CLAY_STYLE
    assert "http://" not in art.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in art
