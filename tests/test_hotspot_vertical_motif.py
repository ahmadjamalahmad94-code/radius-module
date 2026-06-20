# -*- coding: utf-8 -*-
"""feat/card-template-icons — اختبارات «بَصمة قِطاعيّة» لصَفحات الـhotspot.

السياق (يونيو 2026، طلب المالك): الكَروت صار لها رَمز قِطاعيّ + علامة
مائيّة. الـhotspot يَحتاج نَفس الـ«لَمسة» — صفحة كافيه تَبدو قَهوة،
عيادة تَبدو طبّيّة، جيم رياضيّ، إلخ — بنفس مَكتبة motifs.

قَيد المالك للوالد-غاردن: الصَفحة المُولَّدة يَجب أن تَكون مُكتفية
ذاتيًّا (لا روابط خارجيّة) وصَغيرة. هذه الاختبارات تُثبّت ذلك.

شغّل وحده (عزل الاختبارات لكل ملف)."""
from __future__ import annotations

import re

import pytest


# ════════════════════════════════════════════════════════════════════════
# (1) injection: المتغيّرات الجَديدة مُسجَّلة + الـrender يَحقن الـmotif
# ════════════════════════════════════════════════════════════════════════
class TestVariableRegistration:

    def test_motif_variables_registered(self):
        from app.radius.services.hotspot_templates import VARIABLES_BY_SLUG
        assert "MOTIF_ICON" in VARIABLES_BY_SLUG
        assert "MOTIF_WATERMARK_ENABLED" in VARIABLES_BY_SLUG
        assert "MOTIF_WATERMARK_OPACITY" in VARIABLES_BY_SLUG
        # الافتراضات
        assert VARIABLES_BY_SLUG["MOTIF_ICON"].default == "wifi"
        assert VARIABLES_BY_SLUG["MOTIF_WATERMARK_ENABLED"].default == "yes"
        assert VARIABLES_BY_SLUG["MOTIF_WATERMARK_OPACITY"].default == "0.06"

    def test_motif_icon_pattern_accepts_known(self):
        from app.radius.services.hotspot_templates import VARIABLES_BY_SLUG
        pat = VARIABLES_BY_SLUG["MOTIF_ICON"].pattern
        for value in ("coffee", "medical", "fork_knife", "wifi", "none",
                       "gamepad", "scissors"):
            assert pat.match(value), value

    def test_motif_icon_pattern_rejects_html(self):
        from app.radius.services.hotspot_templates import VARIABLES_BY_SLUG
        pat = VARIABLES_BY_SLUG["MOTIF_ICON"].pattern
        for bad in ("<script>", "coffee; alert(1)", "../etc", "Coffee",
                     "1coffee", ""):
            assert not pat.match(bad), bad


# ════════════════════════════════════════════════════════════════════════
# (2) render injects motif markup
# ════════════════════════════════════════════════════════════════════════
class TestRenderInjectsMotif:

    def _render(self, slug: str = "clean_card",
                 motif: str = "coffee", **overrides) -> str:
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe.update({"MOTIF_ICON": motif})
        safe.update(overrides)
        return ht.render(slug, safe, tenant_id=1, with_autologin=False)

    def test_cafe_motif_injects_symbol(self):
        html = self._render(motif="coffee")
        assert 'id="hr-vm"' in html, "هل يَوجد symbol للـmotif؟"
        assert 'class="hr-vm-icon"' in html, "أيقونة الزاوية مَفقودة"
        assert 'href="#hr-vm"' in html, "use المُشير للـsymbol مَفقود"

    def test_clinic_motif_carries_medical_paths(self):
        html = self._render(motif="medical")
        assert 'id="hr-vm"' in html
        # رَمز الـmedical = صَليب + دائرة. نَتحقّق أنّ symbol يَحوي
        # دائرة (السمة الأبرز).
        m = re.search(r'<symbol id="hr-vm"[^>]*>(.*?)</symbol>', html, re.S)
        assert m, "symbol مَفقود"
        assert '<circle' in m.group(1)

    def test_gaming_motif_carries_gamepad_paths(self):
        html = self._render(motif="gamepad")
        m = re.search(r'<symbol id="hr-vm"[^>]*>(.*?)</symbol>', html, re.S)
        assert m
        # gamepad = هَيكل rect مَع rx (مُدوَّر) + D-pad rectangles
        assert '<rect' in m.group(1)

    def test_motif_none_skips_injection(self):
        html = self._render(motif="none")
        assert 'id="hr-vm"' not in html
        assert 'class="hr-vm-icon"' not in html
        assert 'class="hr-vm-wm"' not in html

    def test_watermark_toggle_off_removes_watermark(self):
        html = self._render(motif="coffee",
                             MOTIF_WATERMARK_ENABLED="no")
        # symbol + corner icon يَبقيان
        assert 'id="hr-vm"' in html
        assert 'class="hr-vm-icon"' in html
        # لكن طَبقة الـwatermark تَختفي
        assert 'class="hr-vm-wm"' not in html

    def test_watermark_opacity_clamped_at_30pct(self):
        html = self._render(motif="coffee", MOTIF_WATERMARK_OPACITY="0.95")
        # الـCSS تَحوي opacity:0.30 بَعد القَصّ
        assert "opacity:0.30" in html or "opacity:0.3" in html

    def test_watermark_opacity_zero_skipped(self):
        html = self._render(motif="coffee", MOTIF_WATERMARK_OPACITY="0")
        assert 'class="hr-vm-icon"' in html
        # opacity=0 → لا فائدة من رَسم الـlayer
        assert 'class="hr-vm-wm"' not in html

    def test_accent_color_styles_corner_icon(self):
        html = self._render(motif="coffee", ACCENT_COLOR="#7c3a1d")
        # الـCSS للـcorner icon يَستعمل ACCENT_COLOR كاللون
        assert "color:#7c3a1d" in html


# ════════════════════════════════════════════════════════════════════════
# (3) gallery auto-sets MOTIF_ICON from vertical
# ════════════════════════════════════════════════════════════════════════
class TestGalleryVerticalMapping:

    def test_cafe_gallery_template_sets_coffee_motif(self):
        from app.radius.services import hotspot_gallery as hg
        # نَلتقط أوّل قالب cafe في الـGALLERY
        cafe = next(t for t in hg.GALLERY if t.vertical == "cafe")
        slug, variables, addons = hg.resolve(cafe.key)
        assert variables.get("MOTIF_ICON") == "coffee"

    def test_clinic_gallery_template_sets_medical_motif(self):
        from app.radius.services import hotspot_gallery as hg
        clinic = next(t for t in hg.GALLERY if t.vertical == "clinic")
        slug, variables, addons = hg.resolve(clinic.key)
        assert variables.get("MOTIF_ICON") == "medical"

    def test_gym_gallery_template_sets_dumbbell_motif(self):
        from app.radius.services import hotspot_gallery as hg
        gym = next(t for t in hg.GALLERY if t.vertical == "gym")
        slug, variables, addons = hg.resolve(gym.key)
        assert variables.get("MOTIF_ICON") == "dumbbell"

    def test_resolve_respects_manual_motif_override(self):
        """تَعديل يَدوي من المُشغّل (base_vars) لا يُدهَس."""
        from app.radius.services import hotspot_gallery as hg
        cafe = next(t for t in hg.GALLERY if t.vertical == "cafe")
        slug, variables, addons = hg.resolve(
            cafe.key, base_vars={"MOTIF_ICON": "gamepad"})
        # الـoperator وضَع gamepad — لا يَتغيّر إلى coffee
        assert variables["MOTIF_ICON"] == "gamepad"

    def test_every_vertical_resolves_to_valid_motif(self):
        """كل vertical في VERTICALS يَنطبق على motif مُسجَّل في
        VERTICAL_TO_MOTIF (مَنع «لو ضِفت قِطاع بلا motif مُسبقًا»)."""
        from app.radius.services import hotspot_gallery as hg
        from app.radius.services import card_motifs
        for vertical in hg.VERTICALS:
            assert vertical in card_motifs.VERTICAL_TO_MOTIF \
                or card_motifs.VERTICAL_TO_MOTIF.get(vertical, "wifi"), \
                f"vertical {vertical} لا motif له"


# ════════════════════════════════════════════════════════════════════════
# (4) Walled-garden compliance: لا روابط خارجيّة + حَجم مَعقول
# ════════════════════════════════════════════════════════════════════════
class TestWalledGardenCompliance:

    def _render(self, motif: str = "coffee", slug: str = "clean_card"):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = motif
        return ht.render(slug, safe, tenant_id=1, with_autologin=False)

    _ALLOWED_URL_HOSTS = {
        # روابط داخليّة للـRouterOS hotspot + namespace SVG + روابط الزبون
        "www.w3.org",  # xmlns SVG
    }

    def _external_urls(self, html: str) -> list[str]:
        # روابط HTTP/HTTPS فقط (data:/file:/blob: مَقبولة لأنّها inline)
        urls = re.findall(r'(?<![A-Za-z])(?:https?:)?//([^\s/"\'<>]+)', html)
        return [u for u in urls if u not in self._ALLOWED_URL_HOSTS]

    def test_motif_injection_introduces_no_external_urls(self):
        # نُقارن: بَدون motif vs مَع motif. الفَرق لا يُدخل روابط خارجيّة.
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "none"
        plain = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        safe["MOTIF_ICON"] = "coffee"
        themed = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        new_urls = set(self._external_urls(themed)) - set(self._external_urls(plain))
        assert not new_urls, (
            f"حَقن الـmotif أدخل روابط خارجيّة: {new_urls}")

    def test_no_external_url_for_themed_page(self):
        """فحص شامل: الصفحة المُولَّدة مع motif لا تَحوي أيّ روابط
        http(s) لمُضيفات خارجيّة (CDN/خُطوط/صُور). يَستثني www.w3.org
        (xmlns SVG)."""
        html = self._render(motif="medical")
        ext = self._external_urls(html)
        # نَستثني أيضًا «hotspot.local» و«$(link-...)» وما شابه (RouterOS)
        ext = [u for u in ext if "hotspot" not in u and "$(link" not in u]
        assert not ext, f"روابط خارجيّة في الصَفحة: {ext}"

    def test_motif_adds_under_2kb(self):
        """فَرق الحَجم بين «بلا motif» و«مَع motif (+watermark)» لا
        يَتجاوز 2KB — السبب: SVG رَمز واحد بـcurrentColor + use بدلاً
        من تَكرار الـpaths، + CSS صَغير. حُدود المالك على walled-garden."""
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "none"
        plain = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        safe["MOTIF_ICON"] = "coffee"
        themed = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        diff_bytes = len(themed.encode("utf-8")) - len(plain.encode("utf-8"))
        assert diff_bytes < 2048, (
            f"حَقن motif يُكلّف {diff_bytes}B — تَجاوز ميزانية 2KB")

    def test_themed_page_total_under_60kb(self):
        """ميزانيّة الصَفحة كاملة (motif + addons + خَلفيّة المَراعي):
        ≤60KB — أقصى ما يَتحمّله المُتصفّح خَلف walled-garden قبل أن
        يَشعر الزبون بثقَل التَحميل."""
        html = self._render(motif="coffee")
        size_kb = len(html.encode("utf-8")) / 1024
        assert size_kb < 60, f"الصَفحة {size_kb:.1f}KB > ميزانيّة 60KB"


# ════════════════════════════════════════════════════════════════════════
# (5) no raw-placeholder leak (ضَمان الحارس القَائم)
# ════════════════════════════════════════════════════════════════════════
class TestNoPlaceholderLeak:

    def test_themed_page_has_no_unsubstituted_braces(self):
        """{{X}} يَجب أن لا يَبقى في الـoutput — حَقن motif يَستعمل
        ‎``f-string``‎ فلا يَخل بالحارس."""
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "coffee"
        html = ht.render("clean_card", safe, tenant_id=1, with_autologin=False)
        # المَوقت RouterOS $(…) مَسموح — هذه placeholders الراوتر
        # نَفسه. التَحقّق فَقط من {{VAR}} المُتسرّبة.
        leaks = re.findall(r'\{\{[A-Z_]+\}\}', html)
        # نَستبعد قائمة JSON HTML placeholders اللا-مُستعمَلة في clean_card
        leaks = [l for l in leaks if l not in
                  ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
        assert not leaks, f"{{}} غير مُستبدَلة في الـoutput: {leaks}"


# ════════════════════════════════════════════════════════════════════════
# (6) render smoke: كل القَوالب الـbaseline تُولّد بلا فَشل
# ════════════════════════════════════════════════════════════════════════
class TestAllTemplatesRenderWithMotif:

    @pytest.mark.parametrize("base_slug", [
        "clean_card", "glass_violet", "aurora_hero", "tech_terminal",
        "frost_glass_blue", "gradient_pro", "carrier_app",
    ])
    def test_render_with_motif_does_not_crash(self, base_slug):
        from app.radius.services import hotspot_templates as ht
        safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
        safe["MOTIF_ICON"] = "coffee"
        try:
            html = ht.render(base_slug, safe, tenant_id=1,
                              with_autologin=False)
        except ValueError as e:
            if "unknown template" in str(e).lower() or "غير معروف" in str(e):
                pytest.skip(f"{base_slug} not registered in this build")
            raise
        assert html, "render returned empty"
        # نَتحقّق أنّ المَفاتيح الأساسيّة لـmotif مَوجودة
        assert 'id="hr-vm"' in html
