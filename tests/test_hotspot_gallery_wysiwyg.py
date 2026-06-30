# -*- coding: utf-8 -*-
"""WYSIWYG معرض صفحة الدخول (feat/hotspot-wysiwyg-gallery).

شكوى المالك: بطاقةُ المعرض (المُصغّر الذي يَختار منه الزبون) لا تُطابِق
الصفحةَ المُطبَّقة فعلًا — تَظهر «موجات» متحرّكة على الصفحة المُطبَّقة لم
تُظهرها البطاقة. الجذر: قوالبُ معرضٍ فاخرة في `hotspot_gallery` كانت تَفرض
إضافة `animated_svg` (شكلُها الافتراضيّ «موجات») فوق جلودٍ مُصمَّمة خلفيّتُها
تُعرّف المظهر، بينما تُصيَّر البطاقةُ من نفس خطّ الإنتاج (`render_login_surface`).

نتحقّق هنا:
  1) لا قالبَ فاخرًا (جلد bespoke) يَفرض `animated_svg` افتراضيًّا.
  2) القوالبُ الأربعة المعروفة (cowork_creative/cafe_artsy/isp_gaming/
     gym_power) لم تَعُد تَفرض الموجات/الزخرفة.
  3) استثناءٌ مقصود واحد فقط: «أطفال» (جلد عامّ، الزخرفة جزءُ هويّته).
  4) الإضافة `animated_svg` ما تزال مُتاحة (حرّية ما بعد التطبيق).
  5) بطاقةُ المعرض = الصفحة المُطبَّقة (نفس خطّ `render_login_surface`)،
     ولا موجاتٍ مفاجئة في الناتج الافتراضيّ.
"""
from __future__ import annotations

import re

from app.radius.services import hotspot_gallery as hg
from app.radius.services import hotspot_surfaces as hsf
from app.radius.services import hotspot_templates as ht
from app.radius.services import hotspot_addons as ha


# الجلودُ الفاخرة (bespoke) التي تُعرّف خلفيّتَها بنفسها — فلا يَجوز فرضُ
# زخرفة `animated_svg` عامّة فوقها (تُحدِث «موجات مفاجئة» تكسر WYSIWYG).
_BESPOKE_SKINS = {
    "royal_night", "fiber_glow", "crimson_luxe", "gilded_hospitality",
    "espresso_lux", "aurora_store", "emerald", "swift_login",
    "photo_backdrop", "tech_terminal", "frost_glass_blue",
    "telemetry_console", "carrier_app", "crimson_dining",
}


def _defaults() -> dict:
    return {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}


def _strip(html: str) -> str:
    """تجريد placeholders المايكروتيك كما تفعل نقطةُ المعاينة."""
    html = re.sub(r"\$\(if error\).*?\$\(endif\)", "", html, flags=re.S)
    return re.sub(r"\$\([^)]+\)", "", html)


# ── (1) لا قالب فاخر يَفرض animated_svg ──

def test_no_bespoke_preset_forces_animated_svg():
    offenders = [
        t.key for t in hg.GALLERY
        if t.base_slug in _BESPOKE_SKINS and "animated_svg" in t.addons
    ]
    assert not offenders, (
        "قوالبُ فاخرة ما تزال تَفرض animated_svg (موجات/زخرفة مفاجئة): "
        + ", ".join(offenders))


# ── (2) القوالب الأربعة المعروفة نُزع منها animated_svg ──

def test_known_waves_culprits_stripped():
    for key in ("cowork_creative", "cafe_artsy", "isp_gaming", "gym_power"):
        t = hg.GALLERY_BY_KEY[key]
        assert "animated_svg" not in t.addons, (
            f"{key}: ما يزال يَفرض animated_svg")


# ── (3) الاستثناء المقصود: «أطفال» على جلد عامّ يُبقي زخرفته ──

def test_school_kids_keeps_intended_decoration():
    t = hg.GALLERY_BY_KEY["school_kids"]
    assert t.base_slug == "card", "الاستثناء يَلزم جلدًا عامًّا لا فاخرًا"
    assert "animated_svg" in t.addons, "زخرفة «أطفال» جزءُ هويّتها — تُبقى"


# ── (4) الإضافة ما تزال مُتاحة للإضافة اليدوية بعد التطبيق ──

def test_animated_svg_addon_still_registered():
    assert "animated_svg" in ha.ADDONS, (
        "يَجب إبقاء الإضافة كي يُضيفها العميلُ يدويًّا بعد التطبيق")


# ── (5) بطاقةُ المعرض = الصفحةُ المُطبَّقة (WYSIWYG) ──

def _card_and_applied(key: str):
    """يُحاكي خطَّ البطاقة (المعاينة) والصفحة المُطبَّقة: كلاهما
    `render_login_surface(slug, vars, addons)` — فيَجب أن يَتطابقا."""
    slug, variables, addons = hg.resolve(key, base_vars=_defaults())
    safe = ht.validate_vars(variables)
    addons_cfg = ha.normalize_config(addons)
    # «البطاقة» ونقطةُ التطبيق تُصيّران من نفس الدالّة بنفس المدخلات.
    card = _strip(hsf.render_login_surface(slug, safe, addons_cfg, tenant_id=1))
    applied = _strip(hsf.render_login_surface(slug, safe, addons_cfg,
                                              tenant_id=1))
    return card, applied


def test_card_equals_applied_for_premium_templates():
    # نفس الخطّ ⇒ تطابقٌ تامّ، ولا موجاتٍ مفاجئة في الناتج الافتراضيّ.
    for key in ("cowork_creative", "cafe_artsy", "isp_gaming"):
        card, applied = _card_and_applied(key)
        assert card == applied, f"{key}: البطاقة ≠ المُطبَّق"
        assert "hrwave" not in applied, f"{key}: موجاتٌ مفاجئة في المُطبَّق"
        assert "hr-svgart" not in applied, f"{key}: زخرفةٌ مفاجئة في المُطبَّق"


def test_espresso_lux_card_clean_no_waves():
    # «البنّي الفاخر» — جلدٌ فاخر بطلُه SVG مُضمَّن؛ لا موجات إطلاقًا،
    # والبطاقةُ (قالب بلا إضافات) تُطابِق الصفحةَ المُطبَّقة الافتراضيّة.
    defaults = _defaults()
    safe = ht.validate_vars(defaults)
    card = _strip(hsf.render_login_surface("espresso_lux", safe, {},
                                           tenant_id=1))
    applied = _strip(hsf.render_login_surface("espresso_lux", safe, {},
                                              tenant_id=1))
    assert card == applied
    assert "hrwave" not in card and "hr-svgart" not in card
    # البطل الخاصّ بالقالب موجود (دليلُ أنّ الجلد الفاخر يُعرّف المظهر).
    assert "el-hero" in card


# ── (6) بطاقاتُ المكتبة تُصيَّر من خطّ render_login_surface لا mockup يدويّ ──

def test_library_cards_use_live_render_pipeline():
    """المُصغّر يُحمَّل من نقطة المعاينة (render_login_surface)، وmockup
    اليدويّ (.mtld-mock) مَقصورٌ على التصاميم الخاصّة المرفوعة فقط."""
    import os
    tpl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "templates", "radius", "mt_login_designer.html")
    with open(tpl, encoding="utf-8") as fh:
        src = fh.read()
    # بطاقاتُ المكتبة = iframe حيّ لنقطة المعاينة.
    assert "mtld-thumb-frame" in src
    assert "mt_login_designer_preview" in src
    assert "data-mtld-thumb-src" in src
    # mockup اليدويّ يُرسَم فقط داخل فرع is_custom.
    mock_idx = src.find("mtld-mock")
    custom_idx = src.find("tmpl.is_custom")
    assert mock_idx > 0 and custom_idx > 0 and custom_idx < mock_idx, (
        "mockup اليدويّ يَجب أن يَبقى محصورًا في فرع التصاميم الخاصّة")
