"""صفحة التحكم اليدوي بالسرعة تعكس السياسة المطبَّقة بعد إعادة التحميل.

بلاغ فرح نت (client20) 31/10: بعد تطبيق 75% وتحديث الصفحة رجعت شرائط الباقات
لـ100% مع أنّ السرعة الفعليّة مطبَّقة 75% — كان التحميل يضبط النموذج بالنسبة
العامّة فقط ولا يحرّك شرائط الباقات، ويتجاهل تخصيص كل باقة والوضع المنفصل.
"""
from __future__ import annotations

import html
import json
import re
from uuid import uuid4

import pytest

MANUAL_URL = "/admin/radius/operations/speed-control/manual"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.db.repos import plans_repo

        a = plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1, name="RA",
                                              speed_down_kbps=4000, speed_up_kbps=2000))
        b = plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1, name="RB",
                                              speed_down_kbps=8000, speed_up_kbps=4000))
        app.config["_PLAN_IDS"] = (a.id, b.id)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(app, client) -> None:
    with app.app_context():
        from app.radius.db.repos import admins_repo

        u = f"spd_rs_{uuid4().hex[:10]}"
        admins_repo.create_admin(
            username=u, password="spd-pass", full_name="Speed Owner", is_super_admin=True,
            role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    r = client.post("/admin/radius/login",
                    data={"username": u, "password": "spd-pass"}, follow_redirects=False)
    assert r.status_code in {302, 303}


def _set_active(app, value: dict) -> None:
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "speed.active_factors", json.dumps(value))


def _active_attr(body: str) -> dict:
    m = re.search(r"data-active='([^']*)'", body)
    assert m, "data-active attribute missing"
    return json.loads(html.unescape(m.group(1)))


def test_apply_records_mode_and_custom_label(app):
    with app.app_context():
        from app.radius.services.operations_speed_center import OperationsSpeedCenterService
        a, _b = app.config["_PLAN_IDS"]
        svc = OperationsSpeedCenterService(tenant_id=1)
        svc.apply_speed_policy(preset="normal", multiplier=0.4, profile_ids=[a],
                               overrides={a: {"down": 0.4, "up": 0.6}},
                               mode="separate", actor="t")
        act = svc.active_policy()
        assert act["mode"] == "separate"
        assert act["label"] != "الوضع الطبيعي"   # 40% ليست «الوضع الطبيعي»
        assert act["overrides"][str(a)] == {"down": 0.4, "up": 0.6}


def test_manual_page_embeds_active_state(app, client):
    a, b = app.config["_PLAN_IDS"]
    _set_active(app, {"multiplier": 0.75, "mode": "unified", "profile_ids": [a, b],
                      "overrides": {str(a): {"down": 0.75, "up": 0.75},
                                    str(b): {"down": 0.75, "up": 0.75}}})
    _login_super(app, client)
    r = client.get(MANUAL_URL)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'data-active-pct="75"' in body
    act = _active_attr(body)
    assert act["mode"] == "unified"
    assert act["overrides"][str(a)]["down"] == 0.75


def test_manual_page_without_policy_still_renders(app, client):
    _login_super(app, client)
    r = client.get(MANUAL_URL)
    assert r.status_code == 200
    assert _active_attr(r.get_data(as_text=True)) == {"mode": None, "overrides": {}, "profile_ids": []}


def _render_in_browser(client, url: str):
    """Load the page in headless Chromium, serving every request through the
    Flask test client (keeps the login cookie)."""
    pw = pytest.importorskip("playwright.sync_api")
    p = pw.sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        p.stop()
        pytest.skip(f"chromium unavailable: {exc}")

    def handler(route):
        path = re.sub(r"^https?://[^/]+", "", route.request.url)
        resp = client.get(path)
        route.fulfill(status=resp.status_code, body=resp.get_data(),
                      headers={"content-type": resp.headers.get("content-type", "text/html")})

    page = browser.new_page()
    page.route("**/*", handler)
    page.goto("http://testserver" + url)
    page.wait_for_selector("[data-profile]")
    return p, browser, page


def _row(page, plan_id, sel):
    return page.eval_on_selector(f'[data-profile][data-id="{plan_id}"] {sel}', "e => e.value")


def test_browser_unified_rows_show_applied_pct(app, client):
    a, b = app.config["_PLAN_IDS"]
    _set_active(app, {"multiplier": 0.75, "mode": "unified", "profile_ids": [a, b],
                      "overrides": {str(a): {"down": 0.75, "up": 0.75},
                                    str(b): {"down": 0.75, "up": 0.75}}})
    _login_super(app, client)
    p, browser, page = _render_in_browser(client, MANUAL_URL)
    try:
        assert page.eval_on_selector("[data-global-uni]", "e => e.value") == "75"
        assert _row(page, a, "[data-row-uni]") == "75"      # كان يبقى 100
        assert _row(page, b, "[data-row-uni]") == "75"
        eff = page.eval_on_selector(f'[data-profile][data-id="{a}"] [data-eff-down]', "e => e.textContent")
        assert eff.strip() == "3.00"                           # 4000k × 75%
    finally:
        browser.close()
        p.stop()


def test_browser_separate_per_plan_and_disabled_restored(app, client):
    a, b = app.config["_PLAN_IDS"]
    # باقة a: تحميل 40% رفع 60%؛ باقة b غير متحكَّم بها
    _set_active(app, {"multiplier": 0.4, "mode": "separate", "profile_ids": [a],
                      "overrides": {str(a): {"down": 0.4, "up": 0.6}}})
    _login_super(app, client)
    p, browser, page = _render_in_browser(client, MANUAL_URL)
    try:
        assert page.eval_on_selector('[data-view="separate"]', "e => e.hidden") is False
        assert _row(page, a, "[data-row-down]") == "40"
        assert _row(page, a, "[data-row-up]") == "60"
        assert page.eval_on_selector(f'[data-profile][data-id="{b}"] [data-enable]', "e => e.checked") is False
        assert page.eval_on_selector(f'[data-profile][data-id="{a}"] [data-enable]', "e => e.checked") is True
    finally:
        browser.close()
        p.stop()
