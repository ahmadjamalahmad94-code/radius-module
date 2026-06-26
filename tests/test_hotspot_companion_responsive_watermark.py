# -*- coding: utf-8 -*-
"""تدقيق التجاوب + البصمة على صفحات الهوت سبوت المرافقة
(fix/hotspot-companion-responsive).

يُطبّق المالك على login.html: تجاوب جوّال + بصمة قِطاعيّة مُربّعة خَلف
بطاقة مُعتِمة. هذه الاختبارات تَضمن أن نفس السلوك يَمتدّ لكل الصفحات
المرافقة (alogin/status/logout/error/radvert) وأن الإصلاحات الثلاثة قائمة:

  1) كل صفحة مرافقة مرئيّة تَرث أمان التجاوب (hr-responsive-safety) + البصمة.
  2) البصمة بَلاطة SVG مُربّعة (background-size:220px 220px) — لا تَمَطّط رأسيّ.
  3) البطاقة مُعتِمة ومَرفوعة فَوق البصمة (z-index) — لا نُفوذ داخل البطاقة.
  ودون إعادة إدخال حلقة auto-resubmit في alogin.
"""
import re

import pytest

from app.radius.services import hotspot_companion_pages as hcp

VALUES = {
    "TENANT_NAME": "شبكة فايبر نت",
    "ACCENT_COLOR": "#2563EB",
    "MOTIF_ICON": "cafe",
}

# الصفحات المرافقة المرئيّة (التي تَمرّ على _doc) — rlogin/redirect بروتوكول
# إعادة توجيه صِرف بلا بطاقة فلا تُحقن فيها بصمة/تجاوب.
VISUAL = ("alogin.html", "status.html", "logout.html", "error.html",
          "radvert.html")


@pytest.fixture(scope="module")
def comp():
    return hcp.build_all_companions(VALUES, store_url="store.html")


# ─────────────────── 1) أمان التجاوب على كل صفحة مرئيّة ───────────────────

@pytest.mark.parametrize("fn", VISUAL)
def test_visual_companions_have_responsive_safety(comp, fn):
    html = comp[fn]
    assert 'id="hr-responsive-safety"' in html, f"{fn}: أمان التجاوب مَفقود"
    assert "max-width:600px" in html, f"{fn}: media query الجوّال مَفقود"
    # أهداف لَمس ≥44px + خَطّ 16px (منع تَكبير iOS).
    assert "min-height:44px" in html and "font-size:16px" in html


@pytest.mark.parametrize("fn", VISUAL)
def test_visual_companions_have_viewport_meta(comp, fn):
    assert 'name="viewport"' in comp[fn], f"{fn}: viewport meta مَفقود"


# ─────────────────── 2) البصمة بَلاطة مُربّعة (لا تَمَطّط) ───────────────────

@pytest.mark.parametrize("fn", VISUAL)
def test_visual_companions_have_square_watermark(comp, fn):
    html = comp[fn]
    assert 'class="hr-vm-pat"' in html, f"{fn}: طبقة البصمة مَفقودة"
    assert 'background-image:url("data:image/svg+xml,' in html, \
        f"{fn}: بَلاطة البصمة (background-image) مَفقودة"
    # حَجم خَلفيّة مُربّع صَريح = نِسبة 1:1 (هذا جَوهر إصلاح التَمَطّط الرأسيّ).
    assert "background-size:220px 220px" in html, f"{fn}: بَلاطة غير مُربّعة"
    assert "background-repeat:repeat" in html
    # لا بُنية <pattern>/<rect fill=url> القَديمة المَعرّضة للتَمَطّط.
    assert 'fill="url(#hr-pat)"' not in html


# ─────────────────── 3) بطاقة مُعتِمة مَرفوعة فَوق البصمة ───────────────────

@pytest.mark.parametrize("fn", VISUAL)
def test_card_lifted_above_watermark(comp, fn):
    html = comp[fn]
    # الطبقة في أدنى ترتيب الطَلاء (z-index:-1) خَلف كل شيء، والبطاقات
    # تُرفَع (z-index:1) فَوقها — طلب المالك «خَلف كل شيء بلا استثناء».
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert ".hr-card" in html and "position:relative;z-index:1" in html, \
        f"{fn}: البطاقة غير مَرفوعة فَوق البصمة"


def test_card_background_is_opaque():
    # البطاقة المُشتركة مُعتِمة (#ffffff) فلا تَنفُذ البصمة عبرها.
    from app.radius.services.hotspot_companion_pages import _shared_css, _theme
    css = _shared_css(_theme(VALUES))
    assert "--card:#ffffff" in css
    assert ".hr-card{background:var(--card)" in css


# ─────────────────── 4) alogin يَبقى آمنًا ضدّ الحلقة ───────────────────

def test_alogin_has_no_autoresubmit_loop(comp):
    html = comp["alogin.html"]
    # لا نَموذج يُرسِل الاعتماد تلقائيًّا إلى link-login-only (سبب الحلقة).
    assert "link-login-only" not in html, "alogin يُعيد إرسال الاعتماد (حلقة)"
    assert 'name="sendin"' not in html
    # المسار الناجح يُوجّه إلى link-orig (السلوك القياسيّ الصحيح).
    assert "link-orig" in html


@pytest.mark.parametrize("fn", VISUAL)
def test_no_native_alert_in_companion_js(comp, fn):
    # لا alert() أصليّ — التَغذية الراجعة (إن وُجدت) بأسلوب النظام.
    assert "alert(" not in comp[fn], f"{fn}: alert() أصليّ ممنوع"


# ─────────────────── 5) صفحات البروتوكول لا تُحقن ───────────────────

def test_protocol_pages_stay_minimal(comp):
    # rlogin/redirect صفحتا إعادة توجيه — بلا بصمة/تجاوب (لا بطاقة فيهما).
    for fn in ("rlogin.html", "redirect.html"):
        assert 'class="hr-vm-pat"' not in comp[fn], f"{fn}: بصمة في صفحة بروتوكول"
        assert 'id="hr-responsive-safety"' not in comp[fn]


# ─────────────────── 6) كل الصفحات تُبنى بلا انهيار ───────────────────

def test_build_all_companions_complete(comp):
    for fn in hcp.COMPANION_FILENAMES:
        assert fn in comp and comp[fn], f"{fn}: لم تُبنَ"
    # لا تَسرّب أقواس placeholder للوحة (سلامة RouterOS).
    for fn, html in comp.items():
        assert not re.findall(r'\{\{[A-Z_]+\}\}', html), f"{fn}: تَسرّب placeholder"
