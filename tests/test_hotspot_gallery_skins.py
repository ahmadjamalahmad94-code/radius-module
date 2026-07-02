# -*- coding: utf-8 -*-
"""ربط الجلود الجديدة بالمعرض: كل جلد له مدخل معرض على الأقل، الأعداد
اتسعت، وكل تركيبة تُحلّ وتُصيّر سطح login سليم المايكروتيك. شغّل وحده."""
from __future__ import annotations

import re

import pytest

from app.radius.services import hotspot_gallery as hg
from app.radius.services import hotspot_skins as sk
from app.radius.services import hotspot_surfaces as sf
from app.radius.services import hotspot_templates as ht


def _allowed():
    return set(ht.ROUTEROS_REQUIRED) | {
        "$(link-orig)", "$(link-orig-esc)", "$(username)", "$(mac-esc)",
        "$(if error)", "$(endif)"}


def test_gallery_grew():
    assert len(hg.GALLERY) >= 60
    assert len(hg.VERTICALS) == 17


def test_every_new_skin_has_gallery_entry():
    used = {t.base_slug for t in hg.GALLERY}
    for slug in sk.SKIN_SLUGS:
        assert slug in used, f"الجلد {slug} غير ممثّل في المعرض"


def test_new_skin_combos_resolve_and_render_valid(monkeypatch):
    from app.radius.services import hotspot_addons_content as c
    monkeypatch.setattr(c, "fetch_weather", lambda *a, **k: {"temp": 25, "code": 0})
    base = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    skin_set = set(sk.SKIN_SLUGS)
    checked = 0
    for t in hg.GALLERY:
        if t.base_slug not in skin_set:
            continue
        checked += 1
        slug, variables, addons = hg.resolve(t.key, base_vars=base)
        html = sf.render_login_surface(slug, variables, addons)
        for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
            assert tok in html, f"{t.key}: placeholder ناقص {tok}"
        unknown = set(re.findall(r"\$\([^)]*\)", html)) - _allowed()
        assert not unknown, f"{t.key}: placeholders خام مسرّبة {unknown}"
        assert "{{" not in html, f"{t.key}: متغيّر خام لم يُستبدل"
    # نقص واحدٌ بعد ترقية food_cobrand إلى قالب شِلّ (تركيبتاه food_resto/
    # food_cafe لم تَعُدا تُحسَبان ضمن الجلود، لكنّهما تُصيَّران بنجاح كقالب فاخر).
    assert checked >= 19, "عدد تركيبات الجلود الجديدة أقل من المتوقّع"


def test_carrier_combo_wires_new_addons():
    _slug, _vars, addons = hg.resolve("carrier_isp",
                                      base_vars={"TENANT_NAME": "X"})
    assert addons.get("tab_bar_nav", {}).get("enabled")
    assert addons.get("dealers_directory", {}).get("enabled")


def test_telemetry_combo_wires_connected_addons():
    _slug, _vars, addons = hg.resolve("telemetry_network", base_vars={})
    for k in ("mac_dashboard", "throughput_bars", "countdown_tile"):
        assert addons.get(k, {}).get("enabled"), f"{k} غير مفعّلة في التركيبة"
