# -*- coding: utf-8 -*-
"""الشريط السفلي الثابت لا يُغطّى أبدًا (fix/hotspot-bottombar-overlap).

الشريط (.bottom-nav: الرئيسية/الباقات/الموزعون/الدعم/معلومات) هو التَنقّل
الأساسيّ ويَجب أن يَعلو كلّ محتوى. كان رَفع .mobile-container إلى z-index:1
(من إصلاح البَصمة) يُنشئ سياق تَكديس يَحبس الشريط داخله فتَطفو فَوقه الأجزاء
المحقونة (إعلانات الشبكة). الإصلاح: استثناء حاويات التخطيط من الرَفع +
رَفع z-index الشريط + حَجز مساحة سُفلى.
"""
import re

import pytest

from app.radius.services import hotspot_templates as ht
from app.radius.services import hotspot_surfaces as hsf

PRO_SLUGS = ["gradient_pro", "royal_night", "emerald"]


def _render(slug, with_ann=True):
    safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    safe["MOTIF_ICON"] = "wifi"
    addons = {}
    if with_ann:
        addons = {"announcements": {"enabled": True, "config": {
            "title": "إعلانات الشبكة", "body": "صيانة الجمعة\nسرعات جديدة"}}}
    return hsf.render_login_surface(slug, ht.validate_vars(safe), addons,
                                   tenant_id=1)


# ── الحاويات الجذريّة لم تَعُد تُرفَع (كي لا تَحبس الشريط) ──

def test_layout_containers_not_lifted_into_stacking_context():
    html = _render("gradient_pro")
    # سلسلة الرَفع (z-index:1) يَجب ألّا تَشمل .mobile-container/main/.wrap.
    m = re.search(r"([^{};]*)\{position:relative;z-index:1\}", html)
    assert m, "قاعدة رَفع المحتوى مفقودة"
    lift = m.group(1)
    for wrap in (".mobile-container", "main", ".wrap"):
        assert wrap not in lift, f"{wrap} مَرفوع → يَحبس الشريط السفلي"


# ── أمان الشريط السفلي يُحقَن حين يوجد شريط ──

@pytest.mark.parametrize("slug", PRO_SLUGS)
def test_bottombar_safety_injected_for_pro(slug):
    html = _render(slug)
    assert 'class="bottom-nav"' in html, f"{slug}: لا شريط سفلي"
    assert "hr-bottombar-safety" in html, f"{slug}: أمان الشريط غير مُحقَن"
    # z-index قُصوى للشريط + حَجز مساحة سُفلى (safe-area).
    assert ".bottom-nav{z-index:2147483000!important}" in html
    assert "padding-bottom:calc(78px + env(safe-area-inset-bottom" in html


def test_bottombar_safety_not_injected_without_bar():
    # القوالب البسيطة المُوسَّطة بلا شريط لا تَتلقّى حَشوة سُفلى (لا إزاحة).
    html = _render("classic")
    assert 'class="bottom-nav"' not in html
    assert "hr-bottombar-safety" not in html


def test_watermark_stays_backmost():
    # تأكيد عدم تراجُع: البَصمة تَبقى في أدنى طبقة (z-index:-1).
    html = _render("gradient_pro")
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
