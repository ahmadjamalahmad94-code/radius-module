"""fix/radius-sidebar-accordion — السايدبار أكورديون (backlog #11).

يحمّل ملف sidebar_v2.js الحقيقي على DOM مصغّر يطابق محدّدات السايدبار ويتحقّق
أن فتح أي قسم رئيسي/عائلة فرعية يُغلق الأشقّاء المفتوحين على نفس المستوى.
يستخدم Playwright (مُتاح في البيئة)؛ يُتخطّى إن غاب. شغّل الملف وحده.
"""
from __future__ import annotations

import pathlib

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

JS = pathlib.Path("app/static/js/sidebar_v2.js").read_text(encoding="utf-8")

HTML = """
<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="hb-side" class="hb-side">
  <nav class="hb-side-nav">
    <div class="hb-side-section is-open has-active" data-hb-section="a">
      <button type="button" data-hb-section-toggle>A</button>
      <div class="body">a-items</div>
    </div>
    <div class="hb-side-section" data-hb-section="b">
      <button type="button" data-hb-section-toggle>B</button>
      <div class="body">b-items</div>
    </div>
    <div class="hb-side-section" data-hb-section="network">
      <button type="button" data-hb-section-toggle>NET</button>
      <div class="body">
        <div class="hb-side-subgroup" data-hb-subgroup="network-routers">
          <button type="button" data-hb-subgroup-toggle>routers</button>
        </div>
        <div class="hb-side-subgroup" data-hb-subgroup="network-speed">
          <button type="button" data-hb-subgroup-toggle>speed</button>
        </div>
      </div>
    </div>
  </nav>
</div>
</body></html>
"""


def _open_sections(page):
    return page.eval_on_selector_all(
        ".hb-side-section.is-open", "els => els.map(e => e.getAttribute('data-hb-section'))")


def _open_subgroups(page):
    return page.eval_on_selector_all(
        ".hb-side-subgroup.is-open", "els => els.map(e => e.getAttribute('data-hb-subgroup'))")


@pytest.fixture
def page():
    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch()
        # عرض > 900 كي لا يُعدّ الوضع موبايل.
        ctx = browser.new_context(viewport={"width": 1200, "height": 900})
        pg = ctx.new_page()
        pg.set_content(HTML)
        pg.add_script_tag(content=JS)  # init() يعمل فورًا (readyState=complete)
        yield pg
        ctx.close()
        browser.close()


def test_active_section_open_on_load(page):
    assert _open_sections(page) == ["a"]  # القسم النشط يبقى مفتوحًا


def test_opening_section_closes_others(page):
    page.click('[data-hb-section="b"] [data-hb-section-toggle]')
    assert _open_sections(page) == ["b"]            # A أُغلق، B فقط مفتوح
    page.click('[data-hb-section="network"] [data-hb-section-toggle]')
    assert _open_sections(page) == ["network"]       # B أُغلق، network فقط


def test_clicking_open_section_collapses_it(page):
    page.click('[data-hb-section="b"] [data-hb-section-toggle]')
    assert _open_sections(page) == ["b"]
    page.click('[data-hb-section="b"] [data-hb-section-toggle]')  # نقرة ثانية = طيّ
    assert _open_sections(page) == []


def test_subgroup_accordion(page):
    # افتح قسم الشبكة أولًا ثم العوائل الفرعية بداخله
    page.click('[data-hb-section="network"] [data-hb-section-toggle]')
    page.click('[data-hb-subgroup="network-routers"] [data-hb-subgroup-toggle]')
    assert _open_subgroups(page) == ["network-routers"]
    page.click('[data-hb-subgroup="network-speed"] [data-hb-subgroup-toggle]')
    assert _open_subgroups(page) == ["network-speed"]  # routers أُغلق، speed فقط


def test_subgroup_toggle_off(page):
    page.click('[data-hb-section="network"] [data-hb-section-toggle]')
    page.click('[data-hb-subgroup="network-routers"] [data-hb-subgroup-toggle]')
    page.click('[data-hb-subgroup="network-routers"] [data-hb-subgroup-toggle]')
    assert _open_subgroups(page) == []
