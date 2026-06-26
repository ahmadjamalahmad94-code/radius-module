# -*- coding: utf-8 -*-
"""قالب «النيون الداكن» (neon_dark) — Phase 2 / Wave 2.

تصميم فاخر مُفرَد، مُتميّز عن «البوابة الحيّة»: شبكة دوائر + نيون جيمر + HUD
تصنيف. نتحقّق: مُسجَّل، عناصره الخاصّة موجودة، عقود الدخول سليمة، البَصمة
خلفيّة + الشريط آمن، العلامة ديناميكيّة، وموضعه #2 في القسم ① «شبكة عامة».
"""
import re


def test_registered_and_second_in_general():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert "neon_dark" in ht.TEMPLATES_BY_SLUG
    assert ht.TEMPLATES_BY_SLUG["neon_dark"].name_ar == "النيون الداكن"
    gen = [s for s in route._TEMPLATE_SECTIONS if s[0] == "general"][0]
    assert gen[3][1] == "neon_dark", "يجب أن يكون البطاقة #2 في «شبكة عامة»"


def test_bespoke_hud_elements_present():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("neon_dark", {"TENANT_NAME": "نت", "ACCENT_COLOR": "#4ADE80",
                                    "MOTIF_ICON": "wifi"}, tenant_id=1)
    for marker in ("hr-neon-dark", "nd-hero", "nd-grade", "nd-power-bar",
                   "nd-hud", "nd-beam", "تصنيف الاتصال"):
        assert marker in html, f"عنصر البطل مفقود: {marker}"
    # مُتميّز عن «البوابة الحيّة»: لا عناصرها الخاصّة هنا.
    assert "lp-hero" not in html and "hr-live-portal" not in html
    # المِقياس القديم المُكرَّر أُزيل.
    assert '<div class="network-pulse-card">' not in html
    assert "prefers-reduced-motion" in html


def test_login_contracts_intact():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("neon_dark", {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)",
               "$(error)", "</body>"):
        assert ph in html, f"placeholder مفقود: {ph}"
    assert 'name="username"' in html and 'name="password"' in html
    assert 'class="bottom-nav"' in html and "mobile-container" in html


def test_watermark_backmost_bar_safety_dynamic_brand():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("neon_dark", {"TENANT_NAME": "شبكتي", "ACCENT_COLOR": "#4ADE80",
                                    "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html
    # علامة ديناميكيّة: لا اسم عيّنة مخبوز؛ الاسم المُمرَّر يَظهر.
    assert "شبكتي" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"تسرّب متغيّر: {leaks}"
