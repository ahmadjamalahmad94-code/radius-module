# -*- coding: utf-8 -*-
"""اختبار تصغير مصغّرات معرض مصمّم صفحة الدخول (fix/designer-thumb-compact).

المالك طَلب مصغّرات أكثر إحكامًا: ارتفاع −30٪، عَرض −20٪، ومُحتوى داخليّ
مُكثّف — بلا تَمطيط (الإطار يُصغَّر والمُحتوى يُكثَّف). نتحقّق على مستوى
المصدر من القيم الجديدة في النظامَين (library picker + P4 gallery) والمُكوّن
المُشترك .mtld-mock.
"""
import os
import re

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "static", "css", "mt_login_designer.css")
TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "templates", "radius", "mt_login_designer.html")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _block(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"كتلة {selector} غير موجودة"
    return m.group(1)


# ── library picker (.mtld-gallery / .mtld-thumb) ──

def test_library_grid_narrower():
    css = _read(CSS)
    # العَرض −20٪: 180→144px في minmax.
    assert "minmax(144px, 1fr)" in css


def test_library_thumb_shorter_aspect():
    css = _read(CSS)
    blk = _block(css, ".mtld-thumb")
    # نِسبة أبعاد أقصر (6/7) بَدل 3/4 → ارتفاع أقلّ.
    assert "aspect-ratio: 6 / 7" in blk
    assert "3 / 4" not in blk


def test_live_preview_scaled_down():
    css = _read(CSS)
    # مقياس المعاينة الحيّة صغّر (.52 → .42) لتكثيف المُحتوى.
    assert "--mtld-thumb-scale: .42" in css
    assert "--mtld-thumb-scale: .52" not in css


# ── المُكوّن المُشترك .mtld-mock مُكثّف ──

def test_mock_content_condensed():
    css = _read(CSS)
    logo = _block(css, ".mtld-mock-logo")
    assert "width: 32px" in logo and "height: 32px" in logo  # كان 44px
    mock = _block(css, ".mtld-mock")
    assert "padding: 12px 9px 9px" in mock  # كان 20px 14px 14px
    line = _block(css, ".mtld-mock-line")
    assert "height: 6px" in line  # كان 9px
    btn = _block(css, ".mtld-mock-btn")
    assert "height: 9px" in btn  # كان 13px


# ── المعرض الموحّد بالتبويبات (دَمج معرض P4 القديم) ──

def test_unified_gallery_uses_compact_grid():
    # معرض «قوالب جاهزة حسب نوع المنشأة» القديم (.mtld-vgrid/.mtld-vthumb)
    # دُمج في المعرض الموحّد الذي يَستعمل .mtld-gallery المُصغّرة نفسها.
    tpl = _read(TPL)
    assert "mtld-vgrid" not in tpl and "mtld-vthumb" not in tpl  # أُزيل القديم
    assert "mtld-gtabs" in tpl and "mtld-gsec" in tpl            # الموحّد
    css = _read(CSS)
    assert "minmax(144px, 1fr)" in css     # شبكة .mtld-gallery المُصغّرة


def test_aspect_not_distorted_uniform_scale():
    # التصغير عبر scale() مُنتظم (لا scaleX/scaleY مُختلفان) فلا تَمطيط.
    tpl = _read(TPL)
    assert "scaleX" not in tpl and "scaleY" not in tpl
    css = _read(CSS)
    assert "scaleX" not in css and "scaleY" not in css
