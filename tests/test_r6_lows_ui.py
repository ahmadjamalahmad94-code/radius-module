"""r6-lows: آخرُ عيوبِ الواجهة منخفضةِ الشدّة من تقرير r6ui (§5).

1) أهدافُ لمسٍ ≥40px على المؤشّر الخشِن (أزرارُ أيقونة، رقائق، تبويبات، منزلقات، زرُّ إظهار كلمة المرور).
2) حقولٌ ≥16px على الجوّال (pay-demo، منتقي اللون في مصمّم الدخول وفي الطباعة السريعة).
3) العربيُّ داخل code/pre/hcode-meta يأخذ Cairo بعد monospace (لا خطَّ النظام).
4) Escape يُغلق نافذةَ «الكروت المباعة» ونافذةَ «استيراد المشتركين».
5) رمزُ الحزمة وتاريخُ+وقتُ الإنشاء والمدى (10–100) لا تلتفّ — مع بقاءِ التهريب.

قِيس بـPlaywright (Pixel 7 360 / iPhone 13 390): 269 → 9 أهدافٍ دون 40px (الباقي روابطُ داخل جمل).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _coarse_blocks(css):
    return "\n".join(m.group(0) for m in re.finditer(r"@media \(pointer: coarse\)\{.*?\n\}", css, re.S))


def test_touch_targets_listed_in_coarse_pointer_block():
    blocks = _coarse_blocks(_read("app/static/css/hub_v2.css"))
    for sel in (".an-test", ".rc-denom-remove", ".pr-star-btn", ".mtld-logo-clear", ".dt-controls button",
                ".rc-denom-quick button", ".nc-tab", ".hub-tabs > a", ".acs-segment-btn", ".df-mini",
                ".qk-orient button", ".fb-filters a", ".cdl-fchip", ".spdx-modeswitch__btn", ".cs-mode",
                ".gd-copy-btn", ".hbsel-trigger", ".hub-input", ".ctpl-input", ".field > input",
                "[data-guide-bound][role=button]", ".nc-title > a", ".pl-name-link", "h2 > .hr-entity-link"):
        assert sel in blocks, sel
    # توسيعُ منطقةِ لمسِ روابطِ العناوين بلا تحريكِ التخطيط: حشوةٌ + هامشٌ سالبٌ مساوٍ.
    assert re.search(r"\.nc-title > a,\.pl-name-link,\.swsvf-back\{ padding-block:10px;margin-block:-10px; \}", blocks)


def test_password_toggle_is_40px_on_portals():
    for rel in ("app/templates/radius/portal_card_login.html", "app/templates/radius/portal_distributor_login.html"):
        src = _read(rel)
        block = src[src.index(".pwd-toggle{"):][:400]
        assert "width:40px" in block and "height:40px" in block, rel
        assert "32px" not in block, rel


def test_sliders_are_40px_on_coarse_pointer():
    css = _read("app/static/css/ops_speed_control.css")
    m = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", css, re.S)
    assert m and ".spdx-slider--mini .spdx-slider__rail" in m.group(1) and "height: 40px" in m.group(1)
    quick = _read("app/templates/radius/cards_print_quick.html")
    assert "@media (pointer: coarse){ .qk-slider input[type=range]{height:40px} }" in quick


def test_small_inputs_raised_to_16px():
    assert re.search(r"\.fld input\{[^}]*font-size:16px", _read("app/templates/radius/pay_demo.html"))
    swatch = _read("app/static/css/mt_login_designer.css")
    m = re.search(r"^\.mtld-color-swatch \{[^}]*\}", swatch, re.M)
    assert m and "font-size: 16px" in m.group(0)
    assert "data-qk-surf-color style=\"padding:3px;cursor:pointer;font-size:16px\"" in _read("app/templates/radius/cards_print_quick.html")


def test_arabic_in_bare_monospace_falls_back_to_cairo():
    layout = _read("app/static/css/admin_layout.css")
    assert 'code,kbd,samp,pre{font-family:monospace,"Cairo"}' in layout
    unified = _read("app/static/css/unified_design.css")
    assert "var(--hub-font-mono,monospace);" not in unified
    assert "var(--hub-font-mono,ui-monospace,monospace);" not in unified
    assert 'var(--hub-font-mono,monospace),"Cairo"' in unified


def test_escape_closes_sales_and_import_modals():
    ov = _read("app/templates/radius/cards_overview.html")
    assert re.search(r'event\.key !== "Escape".*?\.co-sales-modal:target.*?window\.location\.hash = ""', ov, re.S)
    imp = _read("app/static/js/mt_import.js")
    assert re.search(r"e\.key !== 'Escape' \|\| !overlay \|\| overlay\.style\.display === 'none'\) return;\s*e\.preventDefault\(\);\s*close\(\);", imp)


def test_batch_hero_nowrap_spans_keep_escaping():
    """Markup ~ str يُهرِّب الجزءَ النصّيّ: رمزُ الحزمة/اسمُ المدير لا يحقنان HTML."""
    from app import create_app
    app = create_app()
    tpl = app.jinja_env.from_string(
        "{{ 'كروت الحزمة ' ~ ('<span style=\"white-space:nowrap\">⁦'|safe) ~ code ~ ('⁩</span>'|safe) }}")
    out = tpl.render(code="<b>B-1</b>")
    assert '<span style="white-space:nowrap">⁦&lt;b&gt;B-1&lt;/b&gt;⁩</span>' in out
    src = _read("app/templates/radius/cards_of_batch.html")
    assert "('<span style=\"white-space:nowrap\">'|safe) ~ (batch.created_at|dt_local" in src
    assert "<span style=\"white-space:nowrap\">(⁦10–100⁩).</span>" in src
