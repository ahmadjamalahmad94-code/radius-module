# -*- coding: utf-8 -*-
"""قالب «البوابة الحيّة» (live_portal) — Phase 2 / Wave 1.

تصميم فاخر مُفرَد في ملفّه الخاصّ. نتحقّق: مُسجَّل وقابل للرندر، يَحمل عناصره
الخاصّة (بطل المِقياس الحيّ)، عقود الدخول (CHAP/نموذج) سليمة، وموضوع في القسم
① «شبكة عامة» أوّلًا.
"""
import re


def test_registered_in_library_and_section():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert "live_portal" in ht.TEMPLATES_BY_SLUG
    t = ht.TEMPLATES_BY_SLUG["live_portal"]
    assert t.name_ar == "البوابة الحيّة"
    # في قسم «شبكة عامة» وأوّلًا.
    gen = [s for s in route._TEMPLATE_SECTIONS if s[0] == "general"][0]
    assert gen[3][0] == "live_portal"


def test_renders_with_bespoke_hero_and_motion():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("live_portal", {"TENANT_NAME": "نت",
                                      "ACCENT_COLOR": "#22D3EE",
                                      "MOTIF_ICON": "wifi"}, tenant_id=1)
    # عناصر البطل الخاصّة بهذا التصميم وحده.
    for marker in ("hr-live-portal", "lp-hero", "lp-gauge", "lp-eq",
                   "lp-ribbon", "البوابة نشطة"):
        assert marker in html, f"عنصر البطل مفقود: {marker}"
    # عُنصر المِقياس القديم المُكرَّر أُزيل (البطل يُغنيه) — نَفحص الـmarkup
    # لا السلسلة (اسم الصنف يَبقى في CSS الشِّل الأساس).
    assert '<div class="network-pulse-card">' not in html
    # يَحترم تقليل الحركة.
    assert "prefers-reduced-motion" in html


def test_login_contracts_intact():
    # عقود الدخول الإجباريّة (حتى لا يَنكسر تسجيل الدخول).
    from app.radius.services import hotspot_templates as ht
    html = ht.render("live_portal", {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)",
               "$(error)", "</body>"):
        assert ph in html, f"placeholder مفقود: {ph}"
    assert 'name="username"' in html and 'name="password"' in html
    # هيكل الشِّل المُثبَت (تبويبات CSS + شريط سفليّ).
    assert 'class="bottom-nav"' in html and "mobile-container" in html


def test_watermark_backmost_and_bar_safety_apply():
    # البَصمة في أدنى طبقة، وأمان الشريط السفلي يُحقَن (شريط موجود).
    from app.radius.services import hotspot_templates as ht
    html = ht.render("live_portal", {"MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html


def test_no_raw_variable_leak():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("live_portal", {"TENANT_NAME": "نت",
                                      "ACCENT_COLOR": "#22D3EE"}, tenant_id=1)
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"تسرّب متغيّر: {leaks}"
