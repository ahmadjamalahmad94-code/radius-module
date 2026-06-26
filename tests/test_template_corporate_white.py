# -*- coding: utf-8 -*-
"""قالب «الأعمال الرسمي» (corporate_white) — Phase 2 / شركة.

تصميم فاخر مُفرَد في ملفّه الخاصّ، بطلُه رسمة SVG مُضمَّنة لمبنى مكاتب خطّيّ
(تفضيل المالك: الصور أحلى من الرموز). نتحقّق: مُسجَّل وقابل للرندر، يَحمل رسمته
المُضمَّنة offline-safe، عقود الدخول سليمة، وموضوع في القسم ④ «شركة».
"""
import re


def test_registered_in_library_and_section():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert "corporate_white" in ht.TEMPLATES_BY_SLUG
    t = ht.TEMPLATES_BY_SLUG["corporate_white"]
    assert t.name_ar == "الأبيض المؤسسي"
    company = [s for s in route._TEMPLATE_SECTIONS if s[0] == "company"][0]
    assert "corporate_white" in company[3]


def test_renders_with_embedded_svg_illustration():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("corporate_white", {"TENANT_NAME": "نت",
                                          "ACCENT_COLOR": "#111827",
                                          "MOTIF_ICON": "wifi"}, tenant_id=1)
    for marker in ("hr-corporate-white", "cw-hero", "cw-art", "cw-sun",
                   "<svg", "cw-stage", "حلول الأعمال"):
        assert marker in html, f"عنصر البطل مفقود: {marker}"
    assert '<div class="network-pulse-card">' not in html
    assert "prefers-reduced-motion" in html


def test_login_contracts_intact():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("corporate_white", {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)",
               "$(error)", "</body>"):
        assert ph in html, f"placeholder مفقود: {ph}"
    assert 'name="username"' in html and 'name="password"' in html
    assert 'class="bottom-nav"' in html and "mobile-container" in html


def test_watermark_backmost_and_bar_safety_apply():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("corporate_white", {"MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html


def test_no_raw_variable_leak():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("corporate_white", {"TENANT_NAME": "نت",
                                          "ACCENT_COLOR": "#111827"}, tenant_id=1)
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"تسرّب متغيّر: {leaks}"


def test_no_external_image_urls():
    # حتميّ: offline-safe — لا روابط صور خارجيّة (البوّابة بلا إنترنت قبل الدخول).
    from app.radius.services import hotspot_template_corporate_white as mod
    art = mod._CORPORATE_WHITE_HERO + mod._CORPORATE_WHITE_STYLE
    assert "http://" not in art.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in art
