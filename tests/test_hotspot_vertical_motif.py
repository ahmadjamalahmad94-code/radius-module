# -*- coding: utf-8 -*-
"""feat/card-template-icons — اختبارات «خَلفيّة نَمطيّة قِطاعيّة» لصَفحات
الـhotspot.

تَنقيح المالك (يونيو 2026): الإصدارات السابقة (single watermark + corner
icon) لم تَنطبق على الرؤية. المَطلوب: **نَمط SVG قابل للتَكرار** من motifs
خَطّيّة دَقيقة قِطاعيّة (cafe = كوب ذَهاب + فُنجان + حُبوب + مَلعقة + سُكّر
+ ورقة + إبريق) يَملأ خَلفيّة الصَفحة كاملةً، بشَفافيّة هَامِسة.

قَيد المالك للوالد-غاردن: الصَفحة مُكتفية ذاتيًّا (لا روابط خارجيّة) +
صَغيرة. SVG ‎<pattern>‎ + ‎<use>‎ يَفي بالغَرض — تَعريف واحد + تَكرار
عبر الـCSS، صَغير + inline.

شغّل وحده."""
from __future__ import annotations

import re

import pytest


# ════════════════════════════════════════════════════════════════════════
# (1) المَتغيّرات + الـpatterns الافتراضيّة
# ════════════════════════════════════════════════════════════════════════
class TestVariableRegistration:

    def test_motif_variables_registered(self):
        from app.radius.services.hotspot_templates import VARIABLES_BY_SLUG
        assert "MOTIF_ICON" in VARIABLES_BY_SLUG
        assert "MOTIF_BRAND_ICON_ENABLED" in VARIABLES_BY_SLUG
        assert "MOTIF_WATERMARK_ENABLED" in VARIABLES_BY_SLUG
        assert "MOTIF_WATERMARK_OPACITY" in VARIABLES_BY_SLUG
        # الافتراضات (يونيو 2026، نَمط مُتَكَرّر):
        assert VARIABLES_BY_SLUG["MOTIF_ICON"].default == "wifi"
        assert VARIABLES_BY_SLUG["MOTIF_BRAND_ICON_ENABLED"].default == "no"
        assert VARIABLES_BY_SLUG["MOTIF_WATERMARK_ENABLED"].default == "yes"
        # الرِحلة: 0.04 → 0.06 → 0.15 → 0.30 (تَنقيح المالك يونيو 2026:
        # «خَلّيه واضح، 30٪»). النَمط مَرئيّ كامل كَخَلفيّة، الـlogin
        # form opaque فلا تَتأثّر القَراءة. clamp رُفع إلى 0.40.
        assert VARIABLES_BY_SLUG["MOTIF_WATERMARK_OPACITY"].default == "0.30"


# ════════════════════════════════════════════════════════════════════════
# (2) render injects SVG <pattern>
# ════════════════════════════════════════════════════════════════════════
class TestRenderInjectsPattern:

    def _render(self, slug: str = "clean_card",
                 motif: str = "coffee", **overrides) -> str:
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe.update({"MOTIF_ICON": motif})
        safe.update(overrides)
        return ht.render(slug, safe, tenant_id=1, with_autologin=False)

    def test_cafe_injects_pattern_element(self):
        # البَصمة صارت طبقة خَلفيّة CSS ببَلاطة SVG مُربّعة (background-image)
        # بَدل ‎<svg pattern>‎ inline الذي كان يَتَمَطّط رأسيًّا على الجوّال.
        html = self._render(motif="coffee")
        assert 'class="hr-vm-pat"' in html, "pattern container مَفقود"
        assert "background-image:url(\"data:image/svg+xml," in html, \
            "بَلاطة البَصمة كَـbackground-image مَفقودة"
        # حَجم خَلفيّة مُربّع صَريح يَضمن نِسبة 1:1 (لا تَمَطّط رأسيّ).
        assert "background-size:220px 220px" in html, "حَجم بَلاطة مُربّع مَفقود"
        assert "background-repeat:repeat" in html
        # لا بُنية ‎<pattern>/<rect fill=url>‎ المَعرّضة للتَمَطّط.
        assert 'fill="url(#hr-pat)"' not in html

    def test_clinic_injects_pattern_with_clinic_set(self):
        # بَلاطتا القِطاعَين (قَهوة/طِبّ) تَختلفان في الـdata URI المُضمَّن.
        cafe_html = self._render(motif="coffee")
        clinic_html = self._render(motif="medical")
        rx = re.compile(r'\.hr-vm-pat\{[^}]*background-image:url\("([^"]+)"\)')
        m1 = rx.search(cafe_html)
        m2 = rx.search(clinic_html)
        assert m1 and m2, "تَعريف البَصمة كَـbackground-image مَفقود"
        assert m1.group(1) != m2.group(1), "بَلاطتا القِطاعَين مُتطابقتان"

    def test_motif_none_skips_injection(self):
        html = self._render(motif="none")
        assert 'class="hr-vm-pat"' not in html
        assert "<pattern" not in html

    def test_corner_icon_off_by_default(self):
        html = self._render(motif="coffee")
        assert 'class="hr-vm-icon"' not in html

    def test_corner_icon_opts_in(self):
        html = self._render(motif="coffee",
                             MOTIF_BRAND_ICON_ENABLED="yes")
        assert 'class="hr-vm-icon"' in html
        assert 'class="hr-vm-pat"' in html

    def test_watermark_disabled_removes_pattern(self):
        html = self._render(motif="coffee",
                             MOTIF_WATERMARK_ENABLED="no")
        assert 'class="hr-vm-pat"' not in html
        assert "<pattern" not in html

    def test_watermark_disabled_with_icon_keeps_icon(self):
        html = self._render(motif="coffee",
                             MOTIF_WATERMARK_ENABLED="no",
                             MOTIF_BRAND_ICON_ENABLED="yes")
        assert 'class="hr-vm-pat"' not in html
        assert 'class="hr-vm-icon"' in html

    def test_both_off_skips_injection(self):
        html = self._render(motif="coffee",
                             MOTIF_WATERMARK_ENABLED="no",
                             MOTIF_BRAND_ICON_ENABLED="no")
        assert 'class="hr-vm-pat"' not in html
        assert 'class="hr-vm-icon"' not in html

    def test_opacity_clamped_at_40pct(self):
        """clamp رُفع 0.30 → 0.40 (يونيو 2026)."""
        html = self._render(motif="coffee", MOTIF_WATERMARK_OPACITY="0.95")
        assert "opacity:0.40" in html or "opacity:0.4" in html

    def test_opacity_zero_skips_pattern(self):
        html = self._render(motif="coffee", MOTIF_WATERMARK_OPACITY="0")
        assert 'class="hr-vm-pat"' not in html

    def test_accent_color_tints_pattern(self):
        # لون التمييز يُخبَز حَرفيًّا في بَلاطة الـSVG (currentColor لا يُورَّث
        # في background-image) — يَظهر مُرمَّزًا (‎#‎ → ‎%23‎) داخل الـdata URI.
        html = self._render(motif="coffee", ACCENT_COLOR="#7c3a1d")
        rx = re.compile(r'\.hr-vm-pat\{[^}]*background-image:url\("([^"]+)"\)')
        m = rx.search(html)
        assert m, "طبقة البَصمة مَفقودة"
        assert "%237c3a1d" in m.group(1), "لون التمييز غير مَخبوز في البَلاطة"

    def test_default_render_uses_30pct(self):
        """تَنقيح المالك يونيو 2026: 30٪ يَجعل النَمط واضحًا كَخَلفيّة
        قِطاعيّة دون مُنازَعَة لِنَموذج الدخول."""
        html = self._render(motif="coffee")
        assert "opacity:0.30" in html or "opacity:0.3" in html


# ════════════════════════════════════════════════════════════════════════
# (3) gallery auto-sets MOTIF_ICON من vertical
# ════════════════════════════════════════════════════════════════════════
class TestGalleryVerticalMapping:

    def test_cafe_gallery_sets_coffee_motif(self):
        from app.radius.services import hotspot_gallery as hg
        cafe = next(t for t in hg.GALLERY if t.vertical == "cafe")
        _slug, variables, _addons = hg.resolve(cafe.key)
        assert variables.get("MOTIF_ICON") == "coffee"

    def test_clinic_gallery_sets_medical_motif(self):
        from app.radius.services import hotspot_gallery as hg
        clinic = next(t for t in hg.GALLERY if t.vertical == "clinic")
        _slug, variables, _addons = hg.resolve(clinic.key)
        assert variables.get("MOTIF_ICON") == "medical"

    def test_every_vertical_resolves_to_pattern_set(self):
        from app.radius.services import hotspot_gallery as hg
        from app.radius.services import card_motif_patterns as cmp
        from app.radius.services import card_motifs
        for t in hg.GALLERY:
            motif = card_motifs.VERTICAL_TO_MOTIF.get(t.vertical, "wifi")
            if motif in cmp.VERTICAL_SETS:
                continue
            assert t.vertical in cmp.VERTICAL_SETS or "generic" in cmp.VERTICAL_SETS


# ════════════════════════════════════════════════════════════════════════
# (4) Walled-garden + size budget
# ════════════════════════════════════════════════════════════════════════
class TestWalledGardenCompliance:

    def _render(self, motif: str = "coffee", slug: str = "clean_card"):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = motif
        return ht.render(slug, safe, tenant_id=1, with_autologin=False)

    _ALLOWED_URL_HOSTS = {"www.w3.org"}

    def _external_urls(self, html: str) -> list[str]:
        urls = re.findall(r'(?<![A-Za-z])(?:https?:)?//([^\s/"\'<>]+)', html)
        return [u for u in urls if u not in self._ALLOWED_URL_HOSTS]

    def test_no_external_urls_introduced(self):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "none"
        plain = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        safe["MOTIF_ICON"] = "coffee"
        themed = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        new = set(self._external_urls(themed)) - set(self._external_urls(plain))
        assert not new, f"الـpattern أدخل URLs خارجيّة: {new}"

    def test_no_external_urls_in_themed_page(self):
        html = self._render(motif="medical")
        ext = self._external_urls(html)
        ext = [u for u in ext if "hotspot" not in u and "$(link" not in u]
        assert not ext, f"URLs خارجيّة: {ext}"

    def test_pattern_adds_under_8kb(self):
        """مَيزانيّة معقولة لتَنوّع بَصري — تَعريف واحد + تَكرار."""
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "none"
        plain = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        safe["MOTIF_ICON"] = "coffee"
        themed = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        diff = len(themed.encode("utf-8")) - len(plain.encode("utf-8"))
        assert diff < 8192, f"الـpattern يُكلّف {diff}B > 8KB"

    def test_themed_page_total_under_70kb(self):
        html = self._render(motif="coffee")
        kb = len(html.encode("utf-8")) / 1024
        assert kb < 70, f"الصَفحة {kb:.1f}KB > 70KB"

    def test_pattern_definition_is_compact(self):
        from app.radius.services import card_motif_patterns as cmp
        for v in cmp.list_verticals():
            pat = cmp.build_pattern_svg(v)
            assert len(pat) < 6144, f"{v}: tile {len(pat)}B > 6KB"


# ════════════════════════════════════════════════════════════════════════
# (5) no raw placeholder leak
# ════════════════════════════════════════════════════════════════════════
class TestNoPlaceholderLeak:

    def test_themed_page_no_braces(self):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "coffee"
        html = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        leaks = re.findall(r'\{\{[A-Z_]+\}\}', html)
        leaks = [l for l in leaks if l not in
                  ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
        assert not leaks


# ════════════════════════════════════════════════════════════════════════
# (6) render smoke — كل القَوالب الـbaseline
# ════════════════════════════════════════════════════════════════════════
class TestAllTemplatesRenderWithPattern:

    @pytest.mark.parametrize("base_slug", [
        "clean_card", "glass_violet", "aurora_hero", "tech_terminal",
        "frost_glass_blue", "gradient_pro", "carrier_app",
    ])
    def test_render_with_pattern_does_not_crash(self, base_slug):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "coffee"
        try:
            html = ht.render(base_slug, safe, tenant_id=1,
                              with_autologin=False)
        except ValueError as e:
            if "unknown template" in str(e).lower() or "غير معروف" in str(e):
                pytest.skip(f"{base_slug} not registered")
            raise
        assert "<pattern" in html or 'class="hr-vm-pat"' in html
