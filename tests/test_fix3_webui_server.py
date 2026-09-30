"""fix wave 3 — stream «webui»: server-rendered checks (Flask test client).

  F08-M1  card checker status follows card.status (API) with the table labels.
  F08-L   /service-reports renders; every template reference resolves; the
          mark-all confirm attribute is not escaped; a CSRF failure is a real
          Arabic page (and JSON for AJAX); the live-search hint; temp speed 0/0
          refused; raw «api-token:N» / «unknown» / «GET · 200» / sound file
          names never shown; events paged beyond 500; Arabic plurals; generator
          limits = the server's; refused payment returns where it came from;
          payment hint shows the 1-year rule; quick-print shows package names;
          checker data attributes are valid JSON.
  F03-L   approvals page readable; loans filtered by date (web + API); success
          flash after a payment on the finance page.
Run this file on its own (one pytest process per file).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

import webui_harness as H


@pytest.fixture(scope="module")
def app():
    a = H.make_app()
    with a.app_context():
        yield a


@pytest.fixture
def owner(app):
    c = app.test_client()
    H.login_session(c, H.owner_id())
    return c


def _html(resp):
    return resp.get_data(as_text=True)


def _visible_text(html):
    return re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)


# ───────────────────────── F08-M1 card checker ─────────────────────────

def _card(bid):
    return H.db().execute("SELECT * FROM cards WHERE batch_id=? ORDER BY id LIMIT 1", (bid,)).fetchone()


def _pill(html):
    m = re.search(r'data-cc-field="status_pill">\s*<span[^>]*data-cc-status="(\w+)"[^>]*>'
                  r'<span class="dot"></span>([^<]+)</span>', html)
    assert m, "status pill not rendered"
    return m.group(1), m.group(2).strip()


def test_checker_status_follows_card_status_and_table_labels(app, owner):
    p = H.plan(days=1)
    bid, _ = H.card_batch(p, 3)
    c = _card(bid)
    html = _html(owner.get(f"/admin/radius/cards/checker?query={c['username']}"))
    assert _pill(html) == ("ready", "متاح")
    # exhausted by a deduction (API status «expired») — was shown «جاهزة»
    H.db().execute("UPDATE cards SET extra_seconds=? WHERE id=?", (-10 ** 9, c["id"]))
    html = _html(owner.get(f"/admin/radius/cards/checker?query={c['username']}"))
    assert _pill(html) == ("expired", "منتهي")
    # revoked = «موقوف» exactly like the batch table (was «ملغاة»)
    H.db().execute("UPDATE cards SET revoked=1 WHERE id=?", (c["id"],))
    html = _html(owner.get(f"/admin/radius/cards/checker?query={c['username']}"))
    assert _pill(html) == ("revoked", "موقوف")
    assert "ملغاة" not in _visible_text(html)


def test_checker_json_data_attributes_are_parseable(app):
    """Sibling of F01-F1: tojson inside a double-quoted attribute broke it."""
    src = open("app/templates/radius/cards_checker_v2.html", encoding="utf-8").read()
    for m in re.finditer(r'[a-z-]+="\{\{[^}]*tojson[^}]*\}\}"', src):
        assert "forceescape" in m.group(0), m.group(0)


# ───────────────────────── F08-L templates ─────────────────────────

def test_service_reports_renders(app, owner):
    r = owner.get("/admin/radius/service-reports")
    assert r.status_code == 200
    assert "تقارير الخدمات" in _html(r)


def test_every_literal_template_reference_exists(app):
    """extends / include / import / from of a literal name must resolve (the
    service-reports page extended a template that does not exist → 500)."""
    env = app.jinja_env
    names = set(env.list_templates(extensions=["html"]))
    rx = re.compile(r"""\{%-?\s*(?:extends|include|import|from)\s+["']([^"']+)["']""")
    missing = []
    for n in sorted(names):
        try:
            src = env.loader.get_source(env, n)[0]
        except Exception:  # noqa: BLE001
            continue
        for ref in rx.findall(src):
            if ref not in names:
                missing.append(f"{n} → {ref}")
    assert not missing, "\n".join(missing)


def test_mark_all_confirm_attribute_is_not_escaped(app, owner):
    html = _html(owner.get("/admin/radius/notifications"))
    assert 'data-confirm="تعليم كلّ الإشعارات كمقروءة؟' in html
    assert "=&#34;" not in html


@pytest.mark.parametrize("path", [
    "/admin/radius/notifications", "/admin/radius/cards/batches", "/admin/radius/users",
    "/admin/radius/finance-center", "/admin/radius/communications", "/admin/radius/reports/manager_events",
])
def test_no_escaped_attribute_or_markup_on_pages(app, owner, path):
    html = _html(owner.get(path))
    assert not re.search(r'\s[a-z-]+=&#34;', html), path
    assert not re.search(r"&lt;(i|span|a|button|bdi|div|b) ", html), path


# ───────────────────────── F08-L CSRF ─────────────────────────

def test_csrf_failure_is_an_arabic_page_that_keeps_the_typed_data(app):
    c = app.test_client()
    H.login_session(c, H.owner_id())
    c.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    r = c.post("/admin/radius/plans", data={"_csrf_token": "stale", "name": "باقة-كتبتها",
                                            "password": "sekret"},
               headers={"Referer": "http://localhost/admin/radius/plans/new"})
    assert r.status_code == 400
    html = _html(r)
    assert 'data-testid="csrf-error"' in html and "انتهت صلاحية الصفحة" in html
    assert 'lang="ar"' in html and 'dir="rtl"' in html
    fields = json.loads(html.split('id="hr-refused-fields">', 1)[1].split("</script>", 1)[0])
    assert fields == {"name": ["باقة-كتبتها"]}          # no password, no token
    assert 'href="/admin/radius/plans/new"' in html


@pytest.mark.parametrize("headers", [
    {"X-Requested-With": "XMLHttpRequest"}, {"Accept": "application/json"}, {"X-CSRFToken": "bad"},
])
def test_csrf_failure_is_json_for_ajax(app, headers):
    c = app.test_client()
    H.login_session(c, H.owner_id())
    c.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    r = c.post("/admin/radius/plans", data={"name": "x"}, headers=headers)
    assert r.status_code == 400
    body = r.get_json()
    assert body["status"] == "csrf_error" and "انتهت صلاحية" in body["error"]


# ───────────────────────── F08-L users list / temp speed ─────────────────────────

def test_users_live_search_says_enter_searches_everyone(app, owner):
    html = _html(owner.get("/admin/radius/users"))
    assert "data-users-search-hint" in html
    assert "اضغط Enter للبحث في كل المشتركين" in html


def test_temp_speed_zero_zero_is_refused_on_the_edit_form(app, owner):
    p = H.plan()
    u = H.subscriber(p)
    r = owner.post(f"/admin/radius/users/{u}", data={
        "username": u, "plan_id": str(p), "status": "enabled", "service_type": "hotspot",
        "temporary_speed": "1", "temporary_download_speed_kbps": "0",
        "temporary_upload_speed_kbps": "0", "temporary_speed_duration_minutes": "",
    })
    assert r.status_code == 422
    assert "0/0" in _html(r)
    row = H.db().execute("SELECT temporary_speed FROM subscribers WHERE username=?", (u,)).fetchone()
    assert not row["temporary_speed"]


def test_temp_speed_service_refuses_zero_zero(app):
    from app.radius.services.temp_speed import apply_temp_speed
    u = H.subscriber(H.plan())
    with pytest.raises(ValueError, match="0/0"):
        apply_temp_speed(tenant_id=1, actor="t", username=u, down_kbps=0, up_kbps=0,
                         duration_minutes=30)


# ───────────────────────── F08-L raw text ─────────────────────────

def _app_token(admin_id):
    from app.radius.db.repos import api_tokens_repo
    rec, _plain = api_tokens_repo.create_token(
        tenant_id=1, name=f"login:someone:{uuid4().hex[:6]}", scopes=["admin:full"],
        created_by=int(admin_id))
    return int(getattr(rec, "id", None) or rec["id"])


def test_actor_display_names(app):
    from app.radius.services.actor_names import actor_display, humanize_actor_refs
    mgr = H.role_admin(("dashboard.view",), full_name="سامر المدير")
    tid = _app_token(mgr.id)
    with app.test_request_context():
        assert actor_display(f"api-token:{tid}") == "تطبيق — سامر المدير"
        assert actor_display("unknown") == "غير معروف"
        assert actor_display("system") == "النظام"
        assert actor_display(str(mgr.id)) == "سامر المدير"
        assert "api-token" not in humanize_actor_refs(f"أضافه: api-token:{tid}")


def test_alert_bodies_never_carry_a_raw_token(app):
    from app.radius.services import admin_alerts
    tid = _app_token(H.owner_id())
    with app.test_request_context():
        txt = admin_alerts.render("subscriber_created", {"username": "x", "actor": f"api-token:{tid}"}) \
            if admin_alerts.get_spec("subscriber_created") else ""
        if not txt:
            key = next(k for k in admin_alerts._BY_KEY if "{actor}" in admin_alerts._BY_KEY[k].template)
            txt = admin_alerts.render(key, {"actor": f"api-token:{tid}"})
    assert "api-token" not in txt and "تطبيق —" in txt


def test_reports_show_readable_actor_and_hide_tech_codes(app, owner):
    tid = _app_token(H.owner_id())
    now = datetime.utcnow().isoformat(timespec="seconds")
    H.db().execute(
        "INSERT INTO audit_log(tenant_id, actor, action, target_type, target_id, created_at) "
        "VALUES(1,?,?,?,?,?)", (f"api-token:{tid}", "update", "user", "zz_actor", now))
    for path in ("/admin/radius/audit", "/admin/radius/reports/user_events"):
        text = _visible_text(_html(owner.get(path)))
        assert f"api-token:{tid}" not in text, path
        assert "تطبيق —" in text, path
    text = _visible_text(_html(owner.get("/admin/radius/reports/manager_events")))
    assert "GET · 200" not in text


def test_unknown_login_username_is_arabic(app, owner):
    H.db().execute(
        "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class) "
        "VALUES(1,'unknown','','Access-Reject',?, '')", (datetime.utcnow().isoformat(sep=" "),)) \
        if False else None
    src = open("app/templates/radius/rep_login_states_detail.html", encoding="utf-8").read()
    assert "_('غير معروف')" in src
    src = open("app/templates/radius/rep_mikrotik_actions.html", encoding="utf-8").read()
    assert "_('غير معروف')" in src


def test_sound_file_names_are_not_shown(app, owner):
    from app.radius.services import notification_sounds as snd
    wav = bytes.fromhex("524946462400000057415645666d74201000000001000100401f0000803e0000020010006461746100000000")
    ok, msg = snd.save_sound(1, snd.GLOBAL_KEY, wav, mime="audio/wav",
                             filename="davinci________.wav")
    assert ok, msg
    html = _html(owner.get("/admin/radius/notifications/sounds"))
    assert "davinci________.wav" not in _visible_text(html)
    assert "صوتٌ مخصّص" in _visible_text(html)


# ───────────────────────── F08-L events paging ─────────────────────────

def test_events_are_paged_beyond_500(app, owner):
    base = datetime.utcnow() - timedelta(days=1)
    for i in range(530):
        H.db().execute(
            "INSERT INTO audit_log(tenant_id, actor, action, target_type, target_id, created_at) "
            "VALUES(1,'owner_root','update','user',?,?)",
            (f"page_{i:04d}", (base + timedelta(seconds=i)).isoformat(timespec="seconds")))
    h1 = _html(owner.get("/admin/radius/reports/user_events?q=page_"))
    assert 'data-testid="list-pager"' in h1
    assert "page_0529" in h1 and "page_0000" not in h1
    h2 = _html(owner.get("/admin/radius/reports/user_events?q=page_&pn=2"))
    assert "page_0000" in h2 and "page_0529" not in h2
    assert "q=page_" in h2.split('data-testid="list-pager"', 1)[1]   # filters kept


# ───────────────────────── F08-L plurals ─────────────────────────

@pytest.mark.parametrize("n,expected", [
    (1, "1 بطاقة"), (2, "2 بطاقات"), (3, "3 بطاقات"), (10, "10 بطاقات"), (11, "11 بطاقةً"),
    (99, "99 بطاقةً"), (100, "100 بطاقة"), (103, "103 بطاقات"), (0, "0 بطاقة"),
])
def test_ar_count_cards(n, expected):
    from app.radius.core.ar_text import ar_count
    assert ar_count(n, "card") == expected


def test_ar_count_settings_and_karts():
    from app.radius.core.ar_text import ar_count
    assert ar_count(3, "setting") == "3 إعدادات"
    assert ar_count(12, "setting") == "12 إعدادًا"
    assert ar_count(3, "kart") == "3 كروت"


def test_settings_flash_uses_plural(app):
    from app.radius.core.ar_text import ar_count
    src = open("app/radius/routes/settings.py", encoding="utf-8").read()
    assert "إعدادًا.\"" not in src and "ar_count(len(changed), 'setting')" in src
    assert ar_count(3, "setting") == "3 إعدادات"


# ───────────────────────── F08-L generator limits ─────────────────────────

def test_generator_form_max_matches_server_limit(app, owner):
    from app.radius.services.cards import PASSWORD_LENGTH_MAX, USERNAME_LENGTH_MAX
    html = _html(owner.get("/admin/radius/cards/generate"))
    assert f'name="username_length" min="4" max="{USERNAME_LENGTH_MAX}"' in html
    assert f'name="password_length" min="1" max="{PASSWORD_LENGTH_MAX}"' in html
    assert "(4–16)" not in html


# ───────────────────────── F08-L payments ─────────────────────────

def test_refused_payment_returns_where_the_operator_came_from(app, owner):
    u = H.subscriber(H.plan(price=10))
    r = owner.post(f"/admin/radius/users/{u}/payments", data={"amount": "0"},
                   headers={"Referer": "http://localhost/admin/radius/users?q=" + u})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/radius/users?q=" + u)
    # an external Referer is never followed
    r = owner.post(f"/admin/radius/users/{u}/payments", data={"amount": "0"},
                   headers={"Referer": "http://evil.example/x"})
    assert r.headers["Location"].endswith(f"/admin/radius/users/{u}/finance")


def test_finance_page_payment_success_flash_survives_the_reload(app, owner):
    u = H.subscriber(H.plan(price=10))
    r = owner.post(f"/admin/radius/users/{u}/payments",
                   data={"amount": "5", "flash_on_reload": "1"},
                   headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.get_json()["ok"]
    page = _html(owner.get(f"/admin/radius/users/{u}/finance"))
    assert "flash-success" in page or "flash flash-success" in page
    # the finance form asks for it; the users-list modal does not
    assert 'name="flash_on_reload"' in page


def test_modal_payment_without_flag_leaves_no_stale_flash(app, owner):
    u = H.subscriber(H.plan(price=10))
    owner.post(f"/admin/radius/users/{u}/payments", data={"amount": "5"},
               headers={"X-Requested-With": "fetch"})
    with owner.session_transaction() as s:
        assert not s.get("_flashes")


def test_payment_hints_carry_the_one_year_rule(app, owner):
    from app.radius.core.numbers import EXTEND_TOO_LONG_AR
    u = H.subscriber(H.plan(price=10))
    for path in (f"/admin/radius/users/{u}/finance", "/admin/radius/users"):
        html = _html(owner.get(path))
        assert json.dumps(EXTEND_TOO_LONG_AR, ensure_ascii=False) in html \
            or json.dumps(EXTEND_TOO_LONG_AR) in html, path
        assert "is-over-cap" in html


# ───────────────────────── F08-L quick print ─────────────────────────

def test_quick_print_batch_picker_shows_package_names(app, owner):
    bid, _ = H.card_batch(H.plan(), 2, package_name="باقة-الطباعة-السريعة")
    html = _html(owner.get("/admin/radius/cards/print/quick"))
    opt = re.search(rf'<option value="{bid}"[^>]*>([^<]+)</option>', html)
    assert opt and "باقة-الطباعة-السريعة" in opt.group(1)


# ───────────────────────── F03-L ─────────────────────────

def test_approvals_page_shows_readable_values(app, owner):
    mgr = H.role_admin(("dashboard.view", "users.view", "users.loans"), full_name="منى المديرة")
    now = datetime.utcnow().isoformat(timespec="seconds")
    H.db().execute(
        "INSERT INTO manager_pending_approvals(tenant_id, admin_id, action_key, amount_minor, "
        "payload_json, summary, status, created_at) VALUES(1,?,?,?,?,?,?,?)",
        (mgr.id, "subscriber.loan", 15000, json.dumps({"currency": "ILS", "username": "x"}),
         "سلفة", "pending", now))
    html = _html(owner.get("/admin/radius/approvals"))
    text = _visible_text(html)
    assert "منى المديرة" in text and "منح سلفة" in text
    assert "150 ₪" in text                     # money filter, with the currency
    assert "subscriber.loan" not in text and f"#{mgr.id}" not in text.replace(f"#{mgr.id}\"", "")


def _loan(username, created_at):
    sid = H.db().execute("SELECT id FROM subscribers WHERE username=?", (username,)).fetchone()[0]
    cols = [r[1] for r in H.db().execute("PRAGMA table_info(loan_entries)").fetchall()]
    vals = {"tenant_id": 1, "subscriber_id": sid, "username": username, "amount": 5.0,
            "currency": "ILS", "status": "open", "created_at": created_at,
            "updated_at": created_at, "duration_minutes": 1440, "reason": "t",
            "starts_at": created_at, "ends_at": created_at,
            "created_by": "owner_root", "loan_type": "debt", "minutes": 1440}
    use = {k: v for k, v in vals.items() if k in cols}
    H.db().execute(f"INSERT INTO loan_entries({','.join(use)}) VALUES({','.join('?' * len(use))})",
                   list(use.values()))


def test_loans_date_filter_web_and_api(app, owner):
    u = H.subscriber(H.plan())
    _loan(u, "2030-01-05T10:00:00")
    _loan(u, "2030-03-05T10:00:00")
    from app.radius.services.accounting import AccountingService
    svc = AccountingService(1)
    got = svc.list_loans(date_from="2030-01-01", date_to="2030-01-31", limit=500)
    assert [x["created_at"][:10] for x in got if x.get("username") == u] == ["2030-01-05"]
    assert svc.loan_totals(date_from="2030-03-01", date_to="2030-03-31")["count"] == 1
    html = _html(owner.get("/admin/radius/finance-center?tab=loans_debts&date_from=2030-03-01&date_to=2030-03-31"))
    assert 'name="date_from" value="2030-03-01"' in html
    # API
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(tenant_id=1, name="t-" + uuid4().hex[:6],
                                               scopes=["admin:full"], created_by=H.owner_id())
    api = app.test_client()
    r = api.get("/api/v1/loans?date_from=2030-01-01&date_to=2030-01-31",
                headers={"Authorization": "Bearer " + plain})
    assert r.status_code == 200
    assert [i["username"] for i in r.get_json()["data"]["items"]].count(u) == 1
    r = api.get("/api/v1/loans?date_from=bad", headers={"Authorization": "Bearer " + plain})
    assert r.status_code == 422
