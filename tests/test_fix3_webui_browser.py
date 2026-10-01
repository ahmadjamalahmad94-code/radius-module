"""fix wave 3 — stream «webui»: browser-level checks (real Chromium via Playwright,
the Flask app served through request interception — tests/webui_harness.py).

Every test here reproduces a finding of the final campaign with the page's own
JavaScript running:
  F01-F1  «العودة إلى النموذج» on the 403 page restores the typed data.
  F03-N1/N2  a stale edit page writes only what the operator changed.
  F05-H1/F08-H1  the batch-cards table shows its rows (select-all, CSV, actions).
  F08-M3  checkboxes never stretch the page (no horizontal scroll).
  F08-L   «1e9» in a money field is refused with a message (never «19»),
          Esc/× close the /mt/operations modal and the designer drawer,
          «نقل للسلّة» uses the unified confirm modal.
Run this file on its own (one pytest process per file).
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

import webui_harness as H  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = H.make_app()
    with a.app_context():
        yield a


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(app, browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="ar",
                              accept_downloads=True)
    pg = ctx.new_page()
    pg.proxy = H.BrowserProxy(app, pg)
    yield pg
    ctx.close()


def _row(username):
    return H.db().execute("SELECT * FROM subscribers WHERE username=?", (username,)).fetchone()


# ───────────────────────── F01-F1 ─────────────────────────

def test_forbidden_back_to_form_restores_typed_data(app, page):
    """The save of an open form is refused (403) — the button must put the typed
    values back into the form (it did nothing: tojson inside onclick="…")."""
    mgr = H.role_admin(("dashboard.view", "plans.view", "plans.create", "plans.edit"))
    page.proxy.login(mgr.username)
    page.goto(H.BASE + "/admin/radius/bandwidth/new")
    assert page.locator("form input[name=name]").count() >= 1
    # the role loses plans.create while the form is open → the save is refused
    # (round 6: a NEW profile is gated by plans.create, no longer plans.edit)
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name="r_noedit_" + mgr.username, display_name="R",
                                permissions=("dashboard.view", "plans.view"))
    admins_repo.update_admin(mgr.id, role_id=r.id)
    page.fill("form input[name=name]", "ملف-مكتوب-للاختبار")
    with page.expect_navigation():
        page.locator("form input[name=name]").first.evaluate(
            "e => e.form.requestSubmit ? e.form.requestSubmit() : e.form.submit()")
    assert page.locator("[data-testid=forbidden-back-to-form]").count() == 1
    data = page.evaluate("JSON.parse(document.getElementById('hr-refused-fields').textContent)")
    assert data.get("name") == ["ملف-مكتوب-للاختبار"]
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # the owner gives the permission back; «back to the form» must still
    # carry what was typed (the form page itself needs plans.create now).
    admins_repo.update_admin(mgr.id, role_id=mgr.role_id)
    with page.expect_navigation():
        page.click("[data-testid=forbidden-back-to-form]")
    page.wait_for_load_state("domcontentloaded")
    assert "/bandwidth/new" in page.url
    assert page.input_value("form input[name=name]") == "ملف-مكتوب-للاختبار"
    assert not errors


def test_forbidden_page_json_survives_quotes_and_markup(app):
    """A typed value with quotes / </script> never breaks out of the JSON block."""
    with app.test_request_context("/admin/radius/x", method="POST"):
        from flask import render_template
        html = render_template("radius/forbidden_403.html", denial_detail="",
                               refused_fields={"name": ['a"b</script><b>x']})
    assert "onclick=\"try{sessionStorage" not in html
    block = html.split('id="hr-refused-fields">', 1)[1].split("</script>", 1)[0]
    assert "</script" not in block and '"' in block      # JSON, «<» escaped


# ───────────────────────── F03-N1 / N2 ─────────────────────────

def _open_edit(page, username):
    page.goto(H.BASE + f"/admin/radius/users/{username}/edit")
    assert page.locator("#uf-form").count() == 1


def _save(page):
    with page.expect_navigation():
        page.locator("#uf-form textarea[name=remark]").evaluate(
            "e => e.form.requestSubmit ? e.form.requestSubmit() : e.form.submit()")


def test_stale_edit_page_keeps_every_field_changed_meanwhile(app, page):
    p_old, p_new = H.plan(name="باقة-قديمة"), H.plan(name="باقة-جديدة")
    u = H.subscriber(p_old, mobile="0599000001", custom_price=0.0)
    page.proxy.login("owner_root")
    _open_edit(page, u)
    # meanwhile (API / another operator): paid plan change, price, mobile, disable
    H.db().execute("UPDATE subscribers SET plan_id=?, custom_price=7.5, mobile='0599111222', "
                   "status='disabled' WHERE username=?", (p_new, u))
    page.fill("textarea[name=remark]", "ملاحظة فقط")
    _save(page)
    r = _row(u)
    assert r["remark"] == "ملاحظة فقط"
    assert r["plan_id"] == p_new
    assert float(r["custom_price"] or 0) == 7.5
    assert r["mobile"] == "0599111222"
    assert r["status"] == "disabled"          # a disabled subscriber is NOT re-enabled


def test_stale_edit_page_still_saves_what_the_operator_changed(app, page):
    p = H.plan()
    u = H.subscriber(p, mobile="0599000002")
    page.proxy.login("owner_root")
    _open_edit(page, u)
    H.db().execute("UPDATE subscribers SET status='disabled' WHERE username=?", (u,))
    page.fill("input[name=mobile]", "0599333444")
    _save(page)
    r = _row(u)
    assert r["mobile"] == "0599333444"         # the operator's change is written
    assert r["status"] == "disabled"           # the concurrent one survives


def test_stale_unlimited_checkbox_does_not_wipe_a_later_renewal(app, page):
    p = H.plan()
    u = H.subscriber(p, expire_at=None)
    page.proxy.login("owner_root")
    _open_edit(page, u)
    assert page.is_checked("input[name=no_expiry]")
    renewed = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)
    H.db().execute("UPDATE subscribers SET expire_at=? WHERE username=?",
                   (renewed.isoformat(sep=" "), u))
    page.fill("textarea[name=remark]", "ملاحظة")
    _save(page)
    assert _row(u)["expire_at"] is not None     # the renewal is kept (was NULL again)


def test_unlimited_checkbox_ticked_by_the_operator_still_clears(app, page):
    p = H.plan()
    u = H.subscriber(p)
    page.proxy.login("owner_root")
    _open_edit(page, u)
    page.check("input[name=no_expiry]")
    _save(page)
    assert _row(u)["expire_at"] is None


# ───────────────────────── F05-H1 / F08-H1 ─────────────────────────

def test_batch_cards_table_shows_rows_select_all_and_csv(app, page):
    p = H.plan()
    bid, _cids = H.card_batch(p, 7)
    page.proxy.login("owner_root")
    page.goto(H.BASE + f"/admin/radius/cards/batches/{bid}/cards")
    assert page.locator("table tbody tr[data-row]").count() == 7
    visible = page.evaluate(
        "[...document.querySelectorAll('table tbody tr[data-row]')]"
        ".filter(r => r.offsetParent !== null).length")
    assert visible == 7                          # was 0: start = 0 × ∞ = NaN
    page.check("[data-select-all]")
    assert page.evaluate(
        "[...document.querySelectorAll('[data-row-select]')].filter(c => c.checked).length") == 7
    assert page.locator("[data-selected-count]").first.inner_text().strip() == "7"
    with page.expect_download() as dl:
        page.click("[data-export-visible]")
    csv = pathlib.Path(dl.value.path()).read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(csv) == 1 + 7                    # header + every row
    # a per-card action control is visible on the first row
    assert page.evaluate(
        "(() => { const r = document.querySelector('table tbody tr[data-row]');"
        " return [...r.querySelectorAll('button, a')].some(b => b.offsetParent !== null); })()")


def test_shared_table_scripts_never_hide_every_row_on_a_bad_size(page):
    """Siblings: uds_table.js / dashboard_table.js with a bogus page size."""
    html = ("<html><body><div class='uds-table-wrap' data-uds-table data-uds-page-size='abc'><table>"
            "<thead><tr><th>a</th></tr></thead><tbody>"
            + "".join(f"<tr><td>{i}</td></tr>" for i in range(5)) + "</tbody></table></div>"
            "<table id='dt' data-dashboard-table data-page-size='-3'><tbody>"
            + "".join(f"<tr><td>{i}</td></tr>" for i in range(4)) + "</tbody></table></body></html>")
    page.set_content(html)
    page.add_script_tag(content=pathlib.Path("app/static/js/uds_table.js").read_text(encoding="utf-8"))
    page.add_script_tag(content=pathlib.Path("app/static/js/dashboard_table.js").read_text(encoding="utf-8"))
    shown = page.evaluate(
        "[...document.querySelectorAll('tbody tr')].filter(r => r.style.display !== 'none').length")
    assert shown > 0


# ───────────────────────── F08-M3 ─────────────────────────

@pytest.mark.parametrize("width", [390, 1366])
def test_campaigns_page_has_no_horizontal_scroll(app, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.proxy.login("owner_root")
    page.goto(H.BASE + "/admin/radius/communications/campaigns")
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 1, over
    widths = page.evaluate(
        "[...document.querySelectorAll('input[type=checkbox],input[type=radio]')]"
        ".map(e => e.getBoundingClientRect().width)")
    assert widths and max(widths) <= 40


# ───────────────────────── F08-L: numbers ─────────────────────────

def test_money_field_refuses_1e9_with_a_message_never_19(app, page):
    p = H.plan(price=30)
    u = H.subscriber(p)
    page.proxy.login("owner_root")
    page.goto(H.BASE + f"/admin/radius/users/{u}/finance")
    page.click("button[data-ff-open=payment]")
    fld = page.locator("[data-ff-form=payment] [name=amount]")
    fld.fill("")
    fld.type("1e9")
    assert fld.input_value() == "1e9"            # not silently stripped to «19»
    msg = fld.evaluate("e => e.validationMessage")
    assert msg and ("رقم" in msg)
    count = lambda: H.db().execute(  # noqa: E731
        "SELECT COUNT(*) FROM payment_transactions WHERE username=?", (u,)).fetchone()[0]
    before = count()
    fld.evaluate("e => e.form.requestSubmit()")
    page.wait_for_timeout(400)
    assert count() == before


def test_arabic_decimal_still_normalised(app, page):
    p = H.plan(price=30)
    u = H.subscriber(p)
    page.proxy.login("owner_root")
    page.goto(H.BASE + f"/admin/radius/users/{u}/finance")
    page.click("button[data-ff-open=payment]")
    fld = page.locator("[data-ff-form=payment] [name=amount]")
    fld.fill("")
    fld.type("٣٥٫٥")
    assert fld.input_value() == "35.5"
    assert fld.evaluate("e => e.validationMessage") == ""


# ───────────────────────── F08-L: modals / confirm ─────────────────────────

def test_esc_closes_mt_operations_emergency_modal(app, page):
    page.proxy.login("owner_root")
    page.goto(H.BASE + "/admin/radius/mt/operations")
    assert page.locator("[data-mt-emergency-reset]").count() == 1
    page.evaluate("document.querySelector('[data-mt-emergency-reset]').click()")
    assert page.locator("[data-mt-emergency-modal]").is_visible()
    page.keyboard.press("Escape")
    assert not page.locator("[data-mt-emergency-modal]").is_visible()


def test_designer_drawer_closes_with_x_and_esc(app, page):
    page.proxy.login("owner_root")
    page.goto(H.BASE + "/admin/radius/print-templates")
    page.click("[data-room-tab=design]")           # the design room opens the drawer
    assert page.evaluate("document.getElementById('designer').open") is True
    page.locator("[data-designer-close]").scroll_into_view_if_needed()
    assert page.locator("[data-designer-close]").is_visible()
    page.click("[data-designer-close]")
    assert page.evaluate("document.getElementById('designer').open") is False
    page.evaluate("document.getElementById('designer').open = true")
    page.keyboard.press("Escape")
    assert page.evaluate("document.getElementById('designer').open") is False


def test_batch_move_to_bin_uses_the_unified_confirm_modal(app, page):
    p = H.plan()
    bid, _ = H.card_batch(p, 2, package_name="حزمة-سلة")
    page.proxy.login("owner_root")
    page.goto(H.BASE + "/admin/radius/cards/batches")
    native = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    assert page.locator("[data-batch-archive-form] button[data-confirm]").count() >= 1
    page.evaluate("document.querySelector('[data-batch-archive-form] button[type=submit]').click()")
    assert page.locator("#cfmModal").is_visible()
    assert not native                           # no native confirm()
    page.click("#cfmCancel")
    assert H.db().execute("SELECT deleted_at FROM card_batches WHERE id=?", (bid,)).fetchone()[0] is None


def test_backups_full_archive_asks_first(app, page):
    page.proxy.login("owner_root")
    page.goto(H.BASE + "/admin/radius/backups")
    btn = page.locator("[data-bk-runall][data-url*='mode=full']")
    assert btn.get_attribute("data-confirm")
    btn.click()
    assert page.locator("#cfmModal").is_visible()
    assert not page.locator("#bk-prog.is-open").count()   # nothing started yet
    page.click("#cfmCancel")
