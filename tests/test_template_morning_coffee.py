# -*- coding: utf-8 -*-
"""قالب «قهوة الصباح» (morning_coffee) — Phase 2 / كافي شوب.

تصميم فاخر مُفرَد في ملفّه الخاصّ. نتحقّق: مُسجَّل وقابل للرندر، يَحمل عناصره
الخاصّة (بطل الفِنجان + البخار)، عقود الدخول (CHAP/نموذج) سليمة، وموضوع في
القسم ② «كافي شوب» أوّلًا.
"""
import re


def test_registered_in_library_and_section():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert "morning_coffee" in ht.TEMPLATES_BY_SLUG
    t = ht.TEMPLATES_BY_SLUG["morning_coffee"]
    assert t.name_ar == "قهوة الصباح"
    # في قسم «كافي شوب» وأوّلًا.
    cafe = [s for s in route._TEMPLATE_SECTIONS if s[0] == "cafe"][0]
    assert cafe[3][0] == "morning_coffee"


def test_renders_with_bespoke_hero_and_motion():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("morning_coffee", {"TENANT_NAME": "نت",
                                        "ACCENT_COLOR": "#A8612F",
                                        "MOTIF_ICON": "coffee"}, tenant_id=1)
    # عناصر البطل الخاصّة بهذا التصميم وحده.
    for marker in ("hr-morning-coffee", "mc-hero", "mc-cup", "mc-steam",
                   "mc-art", "صباح الخير"):
        assert marker in html, f"عنصر البطل مفقود: {marker}"
    # عُنصر المِقياس القديم المُكرَّر أُزيل (البطل يُغنيه) — نَفحص الـmarkup
    # لا السلسلة (اسم الصنف يَبقى في CSS الشِّل الأساس).
    assert '<div class="network-pulse-card">' not in html
    # يَحترم تقليل الحركة.
    assert "prefers-reduced-motion" in html


def test_login_contracts_intact():
    # عقود الدخول الإجباريّة (حتى لا يَنكسر تسجيل الدخول).
    from app.radius.services import hotspot_templates as ht
    html = ht.render("morning_coffee", {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)",
               "$(error)", "</body>"):
        assert ph in html, f"placeholder مفقود: {ph}"
    assert 'name="username"' in html and 'name="password"' in html
    # هيكل الشِّل المُثبَت (تبويبات CSS + شريط سفليّ).
    assert 'class="bottom-nav"' in html and "mobile-container" in html


def test_watermark_backmost_and_bar_safety_apply():
    # البَصمة في أدنى طبقة، وأمان الشريط السفلي يُحقَن (شريط موجود).
    from app.radius.services import hotspot_templates as ht
    html = ht.render("morning_coffee", {"MOTIF_ICON": "coffee"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html


def test_no_raw_variable_leak():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("morning_coffee", {"TENANT_NAME": "نت",
                                        "ACCENT_COLOR": "#A8612F"}, tenant_id=1)
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"تسرّب متغيّر: {leaks}"
