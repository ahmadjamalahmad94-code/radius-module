"""M01 mobile-web touch-emulation regression tests — findings D1, D2, D4, D5, D7.

Real phone emulation with Playwright: Chromium + ``devices['Pixel 7']`` and
WebKit + ``devices['iPhone 13']`` (WebKit is installed on this host), using
real ``.tap()`` calls and no reload between steps, matching how the owner
actually hit these bugs on his phone (see
``C:/Projects/hr-stress-campaign/m01_report.md``).

D2 and D4 are pure front-end widget bugs (hub_date.js / hub_hint.js) and are
tested in isolation on a minimal static page — no Flask app needed, so they
also double as fast unit-ish checks of the touch/keyboard-resize guard that
hub_select.js pioneered and hub_date.js / unified_design.js / users_list.html
now share.

D1, D5 and D7 are wiring bugs in the real app (cards checker / subscribers
list), so they run through ``tests/webui_harness.py`` — the Flask app served
to a real browser page via request interception (no live server needed).

Run this file on its own (one pytest process per file, per FIX_BRIEF.md).
"""
from __future__ import annotations

import pathlib

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

import webui_harness as H  # noqa: E402

HUB_DATE_JS = pathlib.Path("app/static/js/hub_date.js").read_text(encoding="utf-8")
HUB_HINT_JS = pathlib.Path("app/static/js/hub_hint.js").read_text(encoding="utf-8")

DEVICES = [
    ("chromium", "Pixel 7"),
    ("webkit", "iPhone 13"),
]


# ───────────────────────── standalone widget harness (D2, D4) ─────────────

_VIEWPORT_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'

DATE_HTML = f"""
<!doctype html><html dir="rtl" lang="ar"><head><meta charset="utf-8">
{_VIEWPORT_META}</head>
<body style="margin:0;padding:20px;min-height:100vh">
<input type="datetime-local" id="dt" value="2026-09-27T12:00">
</body></html>
"""

HINT_HTML = f"""
<!doctype html><html dir="rtl" lang="ar"><head><meta charset="utf-8">
{_VIEWPORT_META}</head>
<body style="margin:0;padding:20px;min-height:100vh">
<button type="button" class="hub-hint" data-hint="نص المساعدة">؟</button>
</body></html>
"""


@pytest.fixture(params=DEVICES, ids=[d[1] for d in DEVICES])
def widget_page(request):
    engine, device_name = request.param
    with playwright_sync.sync_playwright() as p:
        browser_type = getattr(p, engine)
        browser = browser_type.launch()
        device = p.devices[device_name]
        ctx = browser.new_context(**device, locale="ar-SA")
        pg = ctx.new_page()
        yield pg
        ctx.close()
        browser.close()


def test_d2_hbdate_panel_survives_keyboard_resize(widget_page):
    """A soft keyboard opening resizes the *height* only — the picker must
    stay open (D2: it used to close on ANY resize/scroll)."""
    pg = widget_page
    pg.set_content(DATE_HTML)
    pg.add_script_tag(content=HUB_DATE_JS)
    pg.locator(".hbdate-trigger").tap()
    assert pg.locator(".hbdate-panel").is_visible()

    # Simulate the soft keyboard: innerHeight shrinks, innerWidth is unchanged
    # — hub_date.js only tracks width changes, matching hub_select.js's fix.
    pg.evaluate(
        "() => { Object.defineProperty(window, 'innerHeight', "
        "{configurable: true, value: Math.round(window.innerHeight * 0.6)}); "
        "window.dispatchEvent(new Event('resize')); }"
    )
    assert pg.locator(".hbdate-panel").is_visible(), (
        "hub_date panel closed on a height-only resize (keyboard) — D2 regressed"
    )

    # A scroll caused by the URL bar collapsing right after opening must not
    # close it either (grace window + coarse-pointer reposition-not-close).
    pg.evaluate("() => window.dispatchEvent(new Event('scroll'))")
    assert pg.locator(".hbdate-panel").is_visible(), (
        "hub_date panel closed on a post-open scroll (URL-bar collapse) — D2 regressed"
    )


def test_d2_hbdate_panel_closes_on_real_width_change(widget_page):
    """Sanity check: an actual width change (real rotation/resize) still
    closes the panel — the fix must not disable closing altogether."""
    pg = widget_page
    pg.set_content(DATE_HTML)
    pg.add_script_tag(content=HUB_DATE_JS)
    pg.locator(".hbdate-trigger").tap()
    assert pg.locator(".hbdate-panel").is_visible()
    pg.evaluate(
        "() => { Object.defineProperty(window, 'innerWidth', "
        "{configurable: true, value: window.innerWidth + 200}); "
        "window.dispatchEvent(new Event('resize')); }"
    )
    assert not pg.locator(".hbdate-panel").is_visible()


def test_d4_hint_shows_and_stays_visible_on_tap(widget_page):
    """One tap must show the tooltip and keep it visible (D2: touchstart→
    touchend synthesized mouseover→focusin (show) then click (hide) in the
    same tap, so nothing was ever seen)."""
    pg = widget_page
    pg.set_content(HINT_HTML)
    pg.add_script_tag(content=HUB_HINT_JS)
    pg.locator(".hub-hint").tap()
    assert pg.locator(".hub-hint-pop").count() == 1, (
        "hint tooltip did not stay visible after one tap — D4 regressed"
    )
    assert pg.locator(".hub-hint-pop").inner_text() == "نص المساعدة"


def test_d4_hint_closes_on_second_tap(widget_page):
    """A second, later tap on the same icon still closes it (not stuck open
    forever) — wait past the anti-flicker window first."""
    pg = widget_page
    pg.set_content(HINT_HTML)
    pg.add_script_tag(content=HUB_HINT_JS)
    pg.locator(".hub-hint").tap()
    assert pg.locator(".hub-hint-pop").count() == 1
    pg.wait_for_timeout(500)
    pg.locator(".hub-hint").tap()
    assert pg.locator(".hub-hint-pop").count() == 0


# ───────────────────────── real-app harness (D1, D5, D7) ──────────────────


@pytest.fixture(scope="module")
def app():
    a = H.make_app()
    with a.app_context():
        yield a


@pytest.fixture(params=DEVICES, ids=[d[1] for d in DEVICES])
def mobile_page(request, app):
    engine, device_name = request.param
    with playwright_sync.sync_playwright() as p:
        browser_type = getattr(p, engine)
        browser = browser_type.launch()
        device = p.devices[device_name]
        ctx = browser.new_context(**device, locale="ar-SA")
        pg = ctx.new_page()
        pg.proxy = H.BrowserProxy(app, pg)
        yield pg
        ctx.close()
        browser.close()


def _seed_card(app):
    with app.app_context():
        plan_id = H.plan(price=0, days=30)
        batch_id, card_ids = H.card_batch(plan_id, count=1, package_name="باقة تجربة")
        row = H.db().execute(
            "SELECT username FROM cards WHERE id = ?", (card_ids[0],)
        ).fetchone()
        return row["username"]


def test_d1_double_tap_time_apply_only_applies_once(app, mobile_page):
    """«تغيير الوقت» → تطبيق نقرتين سريعتين لا يُضيف الوقت مرّتين
    (M01 D1b: 5 دقائق كانت تصبح 10 على نقرتين سريعتين)."""
    username = _seed_card(app)
    pg = mobile_page
    pg.proxy.login("owner_root")
    pg.goto(H.BASE + f"/admin/radius/cards/checker?query={username}")
    pg.wait_for_load_state("domcontentloaded")

    posts = []
    pg.on("request", lambda req: posts.append(req)
          if (req.method == "POST" and "/cards/checker" in req.url) else None)

    pg.locator('[data-cc-op="set-time"]').tap()
    amount = pg.locator("#cc-time-amount-input")
    amount.click()
    amount.fill("")
    amount.type("5")
    pg.locator('[data-cc-unit-value="minutes"]').tap()

    apply_btn = pg.locator("#cc-time-apply")
    apply_btn.tap()
    apply_btn.tap()  # نقرة ثانية سريعة قبل انتقال الصفحة — يجب أن تُوقَف

    pg.wait_for_load_state("networkidle")
    assert len(posts) == 1, (
        f"expected exactly one set_time POST, got {len(posts)} — D1 regressed"
    )
    with app.app_context():
        row = H.db().execute(
            "SELECT extra_seconds FROM cards WHERE username = ?", (username,)
        ).fetchone()
    assert row["extra_seconds"] == 300, (
        f"extra_seconds={row['extra_seconds']!r}, expected 300 (5 min once) — D1 regressed"
    )


def test_d5_speed_apply_asks_for_confirmation_exactly_once(app, mobile_page):
    """«تغيير السرعة» → تطبيق يفتح تأكيدًا واحدًا فقط، لا تأكيدين متداخلين
    (M01 D5: data-confirm العامّ كان يعترض فوق تأكيد المودال الخاصّ)."""
    username = _seed_card(app)
    pg = mobile_page
    pg.proxy.login("owner_root")
    pg.goto(H.BASE + f"/admin/radius/cards/checker?query={username}")
    pg.wait_for_load_state("domcontentloaded")

    pg.locator('[data-cc-op="set-speed"]').tap()
    pg.locator("[data-down]").fill("2048")
    pg.locator("[data-up]").fill("1024")
    # المودال المُوحَّد العامّ (data-confirm) يجب ألّا يُفتح أبدًا هنا.
    assert pg.locator("#cfmModal").is_hidden()
    pg.locator("[data-cc-confirm]").tap()
    pg.wait_for_load_state("networkidle")
    assert pg.locator("#cfmModal").is_hidden(), (
        "the global confirm modal opened on top of the ccModal's own "
        "confirm flow — D5 regressed (double confirmation)"
    )


def test_d7_plan_picker_reset_dispatches_change_event(app, mobile_page):
    """فتح «تغيير العرض» لمشترك يملك نفس العرض الذي اختير قبل قليل لمشترك
    آخر يُصفّر الاختيار الحقيقيّ (Select قيمته الحقيقيّة فارغة) — ويجب أن
    تُطلَق change كي لا تبقى تسمية hub_select القديمة ظاهرة (M01 D7)."""
    with app.app_context():
        plan_a = H.plan(price=10.0, days=30, name="عرض أ")
        plan_b = H.plan(price=20.0, days=30, name="عرض ب")
        user_a = H.subscriber(plan_a, username="d7_a")
        user_b = H.subscriber(plan_b, username="d7_b")

    pg = mobile_page
    pg.proxy.login("owner_root")
    pg.goto(H.BASE + "/admin/radius/subscribers")
    pg.wait_for_load_state("domcontentloaded")

    def open_plan_modal(username):
        row = pg.locator(f'tr[data-username="{username}"]')
        row.locator("[data-urow-trigger]").nth(1).tap()  # «إجراءات إدارية»
        row.locator('[data-urow-open="plan"]').tap()

    # افتح لمشترك A واختر عرض B — يبقى select.value محفوظًا في نفس عنصر
    # الـDOM المشترك بعد الإغلاق (لا يُصفَّر تلقائيًا).
    open_plan_modal(user_a)
    select = pg.locator("[data-usq-plan-select]")
    # اختر عرض B صراحةً (نفس عرض B الحاليّ لاحقًا).
    options = select.locator("option").all_text_contents()
    b_index = next(i for i, t in enumerate(options) if "عرض ب" in t)
    select.select_option(index=b_index)
    pg.locator('[data-usq-modal="plan"] [data-usq-close]').first.tap()

    # الآن افتح لمشترك B — عرضه الحاليّ (B) هو نفسه المختار سلفًا، فيُعطَّل
    # ويُصفَّر القيمة. يجب أن يُطلَق change فتُحدَّث تسمية hub_select الفعليّة.
    open_plan_modal(user_b)
    real_value = select.input_value()
    assert real_value == "", (
        f"expected the plan select to reset to empty for its own current "
        f"plan, got {real_value!r}"
    )
    label_text = pg.locator('[data-usq-modal="plan"] .hbsel-label').first.inner_text()
    assert "عرض ب" not in label_text, (
        f"hub_select label still shows the stale plan name {label_text!r} "
        "while the real value is empty — D7 regressed"
    )
