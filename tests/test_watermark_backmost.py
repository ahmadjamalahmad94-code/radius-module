# -*- coding: utf-8 -*-
"""البَصمة في أدنى ترتيب الطَلاء — خَلف كل شيء بلا استثناء (طلب المالك).

  • الطبقة z-index:-1 (لا 0) فتَقع تحت كل المحتوى المُتدفّق — بما فيه أيّ
    جَدول (مواقيت/جلسات/متجر) وأيّ ودجت، لا فَوقها أبدًا.
  • ودجات المحتوى/الجداول مَرفوعة صَراحةً فَوق البَصمة (حِزام إضافيّ).
  • html شفّاف كي تَبقى البَصمة مَرئيّةً فَوق لون الصَفحة (canvas).
"""
import re

import pytest

from app.radius.services import hotspot_templates as ht


def _render(motif="coffee", slug="clean_card", **ov):
    safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    safe["MOTIF_ICON"] = motif
    safe.update(ov)
    return ht.render(slug, safe, tenant_id=1, with_autologin=False)


def test_watermark_lowest_zindex():
    html = _render()
    m = re.search(r"\.hr-vm-pat\{([^}]*)\}", html)
    assert m, "طبقة البَصمة مفقودة"
    body = m.group(1)
    assert "position:fixed" in body and "inset:0" in body
    assert "z-index:-1" in body, "البَصمة ليست في أدنى ترتيب الطَلاء"
    assert "z-index:0" not in body  # لم تَعُد عند 0 (كانت تَطفو فَوق الساكن)


def test_tables_and_widgets_lifted_above_watermark():
    html = _render()
    # جداول/نماذج/ودجات مَرفوعة فَوق البَصمة صَراحةً (لا استثناء).
    m = re.search(r"position:relative;z-index:1\}", html)
    assert m, "قاعدة رَفع المحتوى مفقودة"
    # السلسلة قبل قاعدة الرَفع تَشمل table و form وودجات المحتوى.
    lift = html[:html.index("{position:relative;z-index:1}")]
    for sel in ("table", "form", ".hr-pray", ".hr-board", ".hr-card",
                ".hr-sessions", ".hr-prelogin-extras"):
        assert sel in lift, f"المُحدِّد {sel} غير مَرفوع فَوق البَصمة"


def test_html_transparent_keeps_watermark_visible():
    # html شفّاف ⇒ خَلفيّة body تُمَرَّر للـcanvas، فتَبقى البَصمة (z-index:-1)
    # مَرئيّةً فَوقها وخَلف المحتوى (لا تَختفي خَلف لون الصَفحة).
    html = _render()
    assert "html{background:transparent}" in html


def test_disabled_watermark_no_layer():
    html = _render(MOTIF_WATERMARK_ENABLED="no")
    assert 'class="hr-vm-pat"' not in html
    assert "z-index:-1" not in html.split("</body>")[-1] if "</body>" in html else True


def test_companion_pages_watermark_backmost():
    # الصفحات المرافقة (status فيها صفوف IP/MAC) تَرث نفس الطبقة الخَلفيّة.
    from app.radius.services import hotspot_companion_pages as hcp
    comp = hcp.build_all_companions(
        {"TENANT_NAME": "x", "ACCENT_COLOR": "#2563EB", "MOTIF_ICON": "cafe"})
    for fn in ("status.html", "alogin.html"):
        assert "z-index:-1" in comp[fn], f"{fn}: البَصمة ليست خَلفيّة مُطلَقة"
