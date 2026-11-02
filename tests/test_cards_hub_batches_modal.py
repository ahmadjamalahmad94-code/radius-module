"""«حزم البطاقات» مركزًا واحدًا — طلب المالك 2026-11-02.

① إنشاءُ الحزمة نافذةٌ داخل صفحة الحزم بنفس نموذج /cards/generate كاملًا
   (لا الحزمةَ السريعة المختصرة)، و‎?new=1‎ يفتحها عند التحميل.
② البطاقاتُ الإلكترونيّة وسوقُ البطاقات مخفيّةٌ تمامًا إلّا لزبونٍ فُعِّلت له
   خدمةُ «سوق البطاقات» في عقد لوحة التراخيص (services/ecards_gate.py):
   لا قسمَ في القائمة، ولا خيارَ «إلكترونيّة» (الحزمُ مطبوعةٌ فقط)، والمساراتُ
   نفسُها 404 — لا زرٌّ مخفيٌّ يبقى رابطُه يعمل.

Fixture/auth pattern mirrors tests/test_card_gen_validity_and_403.py.
"""
from __future__ import annotations

import json
import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "cards_hub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "HubPlan", 60, 7, 5.0, "ILS"),
    )
    return int(cur.lastrowid)


def _login(client, *, admin_id: int = 1, is_super: bool = True, perms=()):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["permissions"] = list(perms)
        sess["_csrf_token"] = "off-csrf"


def _owner(app):
    with app.app_context():
        _plan_id()
    c = app.test_client()
    _login(c)
    return c


def _modal(html: str) -> str:
    # النموذجُ نفسُه مليءٌ بـ<section>، فالحدُّ نافذةُ التصدير التي تليه في الصفحة.
    start = html.index("data-quick-batch-modal")
    return html[start:html.index('id="batchExportModal"', start)]


# ── ① نافذة الإنشاء ─────────────────────────────────────────────────────

def test_batches_page_carries_the_full_generate_form(app):
    html = _owner(app).get("/admin/radius/cards/batches").get_data(as_text=True)
    modal = _modal(html)
    # النموذجُ الكامل بأقسامه الخمسة، يُرسَل عبر مسار التوليد التدريجيّ نفسِه
    for title in ("بيانات الحزمة", "توليد البطاقات", "الصلاحية والوقت",
                  "السرعة المجدولة", "سلوك انتهاء الكوتا"):
        assert title in modal, title
    assert 'action="/admin/radius/cards/generate"' in modal
    assert "data-progress-start-url" in modal
    assert 'name="request_key"' in modal
    assert "HubPlan" in modal
    # «إلغاء» داخل النافذة يُغلقها ولا يذهب لصفحةٍ أخرى
    assert "data-quick-batch-close>" in modal
    # الحزمةُ السريعة المختصرة ورابطُ «متقدم» إلى الصفحة المنفصلة ذهبا
    assert ">إضافة حزمة سريعة<" not in html
    assert "data-qun-length" not in html


def _create_modal_tag(html: str) -> str:
    start = html.index('<div class="quick-batch-modal quick-batch-modal--full"')
    return html[start:html.index(">", start)]


def test_new_query_opens_the_modal_on_load(app):
    c = _owner(app)
    plain = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert "data-open-on-load" not in _create_modal_tag(plain)
    opened = c.get("/admin/radius/cards/batches?new=1").get_data(as_text=True)
    assert "data-open-on-load" in _create_modal_tag(opened)


def test_standalone_generate_page_still_renders_the_same_form(app):
    html = _owner(app).get("/admin/radius/cards/generate").get_data(as_text=True)
    assert "توليد الآن" in html and "HubPlan" in html
    # في الصفحة المستقلّة «إلغاء» رابطٌ يعود إلى الحزم، لا زرُّ إغلاق نافذة
    assert 'href="/admin/radius/cards/batches">' in html


def test_sub_manager_sees_the_offer_form_in_the_modal(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo

        sub = admins_repo.create_admin(username="hubmgr", password="x12345678",
                                       full_name="M hub", is_super_admin=False)
    c = app.test_client()
    # يرى الحزم ولا يملك «إنشاء دفعة بطاقات» ⇒ يولّد من عروض المالك فقط
    _login(c, admin_id=int(sub.id), is_super=False, perms=["cards.view"])
    r = c.get("/admin/radius/cards/batches")
    assert r.status_code == 200
    modal = _modal(r.get_data(as_text=True))
    # عارضُ العروض (أو حالتُه الفارغة) — لا النموذجُ الكامل بأسعاره
    assert "لا عروض متاحة لك بعد" in modal or 'id="cg-mgr-form"' in modal
    assert 'name="price_per_card"' not in modal


# ── ② بوّابة البطاقات الإلكترونيّة ─────────────────────────────────────────

_GATED = (
    "/admin/radius/card-marketplace",
    "/admin/radius/card-users",
    "/admin/radius/cards/recharge",
    "/admin/radius/store-support",
    "/admin/radius/reports/card_store_events",
    "/portal/card/login",
)


@pytest.mark.ecards_closed
def test_closed_by_default_hides_everything(app):
    c = _owner(app)
    html = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert 'data-hb-section="electronic-cards"' not in html
    assert "rep_card_store_events" not in html and "card_store_events" not in html.split("<main", 1)[0]
    modal = _modal(html)
    assert 'value="electronic"' not in modal
    assert '<input type="hidden" name="batch_type" value="printed">' in modal
    landing = c.get("/").get_data(as_text=True)
    assert "/portal/card/login" not in landing
    for url in _GATED:
        assert c.get(url).status_code == 404, url


@pytest.mark.ecards_closed
def test_closed_api_answers_json_404(app):
    c = _owner(app)
    r = c.get("/api/v1/store/packages")
    assert r.status_code == 404
    assert (r.get_json() or {}).get("error") == "service_not_enabled"


@pytest.mark.ecards_closed
def test_closed_rejects_a_hand_crafted_electronic_batch(app):
    with app.app_context():
        plan_id = _plan_id()
    c = app.test_client()
    _login(c)
    r = c.post("/admin/radius/cards/generate", data={
        "_csrf_token": "off-csrf", "plan_id": str(plan_id), "count": "2",
        "batch_type": "electronic", "password_length": "6", "device_count": "1",
    })
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        row = db().execute("SELECT metadata FROM card_batches ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert json.loads(row["metadata"] or "{}").get("batch_type") == "printed"


def test_enabled_contract_shows_everything_again(app):
    # الـconftest يُحاكي زبونًا فُعِّل له «سوق البطاقات» افتراضًا.
    c = _owner(app)
    html = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert 'data-hb-section="electronic-cards"' in html
    assert 'value="electronic"' in _modal(html)
    for url in _GATED:
        assert c.get(url).status_code != 404, url


@pytest.mark.ecards_closed
def test_contract_reading_is_closed_unless_enabled_and_active(app, monkeypatch):
    from app.radius.services import ecards_gate

    cases = [
        (None, False),
        ({"enabled": True, "status": "active"}, True),
        ({"enabled": True, "status": "disabled"}, False),
        ({"enabled": False, "status": "active"}, False),
        ({"enabled": "yes", "status": "active"}, False),
        ({"enabled": False, "status": "suspended"}, False),
        ({"enabled": True, "status": "active", "hidden": True}, False),
    ]
    for svc, want in cases:
        monkeypatch.setattr(ecards_gate, "_service_from_snapshot", lambda tenant_id, s=svc: s)
        with app.test_request_context("/"):
            assert ecards_gate.ecards_enabled(1) is want, svc


# ── ③ فحص البطاقة نافذةً مدمجة ──────────────────────────────────────────

def test_batches_page_has_the_checker_modal(app):
    html = _owner(app).get("/admin/radius/cards/batches").get_data(as_text=True)
    assert "data-checker-open" in html
    assert 'data-checker-src="/admin/radius/cards/checker?embed=1"' in html
    c = app.test_client(); _login(c)
    opened = c.get("/admin/radius/cards/batches?check=1&query=abc").get_data(as_text=True)
    assert 'data-checker-query="abc"' in opened and "data-open-on-load" in opened


def test_checker_embed_drops_the_panel_frame(app):
    c = _owner(app)
    embedded = c.get("/admin/radius/cards/checker?embed=1").get_data(as_text=True)
    assert '<body class="hb-embed">' in embedded
    assert ".cc-hero-shell .uds-hero-top{ display:none; }" in embedded
    full = c.get("/admin/radius/cards/checker").get_data(as_text=True)
    assert '<body class="hb-embed">' not in full


def test_checker_operation_stays_embedded(app):
    c = _owner(app)
    r = c.post("/admin/radius/cards/checker", data={
        "_csrf_token": "off-csrf", "op": "disable", "query": "nobody-x", "username": "nobody-x",
        "embed": "1",
    })
    assert r.status_code in (302, 303)
    assert "embed=1" in r.headers["Location"]
    r2 = c.post("/admin/radius/cards/checker", data={
        "_csrf_token": "off-csrf", "op": "disable", "query": "nobody-x", "username": "nobody-x",
    })
    assert "embed=1" not in r2.headers.get("Location", "")


# ── ④ النظرة العامة أسفل الحزم ─────────────────────────────────────────

def test_batches_page_lazy_loads_the_overview(app):
    html = _owner(app).get("/admin/radius/cards/batches").get_data(as_text=True)
    assert 'data-overview-src="/admin/radius/cards/overview?fragment=1"' in html


def test_overview_fragment_is_bare_sections(app):
    c = _owner(app)
    frag = c.get("/admin/radius/cards/overview?fragment=1").get_data(as_text=True)
    assert 'id="co-sales-section"' in frag
    assert "<html" not in frag and 'class="topbar"' not in frag
    assert "آخر 5 دفعات" not in frag          # قائمةُ الحزم فوقها أصلًا
    full = c.get("/admin/radius/cards/overview").get_data(as_text=True)
    assert 'id="co-sales-section"' in full and "<html" in full


@pytest.mark.ecards_closed
def test_overview_hides_electronic_when_closed(app):
    frag = _owner(app).get("/admin/radius/cards/overview?fragment=1").get_data(as_text=True)
    assert "co-sales-summary-item--wallet" not in frag
    assert "co-stock-table-card--electronic" not in frag
    assert "co-sales-split-electronic" not in frag


def test_overview_shows_electronic_when_enabled(app):
    frag = _owner(app).get("/admin/radius/cards/overview?fragment=1").get_data(as_text=True)
    assert "co-sales-summary-item--wallet" in frag


# ── ⑤ القائمة الجانبيّة ────────────────────────────────────────────────

def _side(html: str) -> str:
    start = html.index('id="hb-side"')
    return html[start:html.index("</aside>", start)]


def test_sidebar_keeps_only_batches_for_cards(app):
    side = _side(_owner(app).get("/admin/radius/cards/batches").get_data(as_text=True))
    assert 'href="/admin/radius/cards/batches"' in side
    for gone in ('href="/admin/radius/cards/overview"', 'href="/admin/radius/cards/checker"',
                 'href="/admin/radius/cards/generate"'):
        assert gone not in side, gone


def test_sidebar_keeps_checker_for_a_verify_only_manager(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo

        sub = admins_repo.create_admin(username="verifyonly", password="x12345678",
                                       full_name="V only", is_super_admin=False)
    c = app.test_client()
    _login(c, admin_id=int(sub.id), is_super=False, perms=["cards.verify"])
    r = c.get("/admin/radius/cards/checker")
    assert r.status_code == 200
    side = _side(r.get_data(as_text=True))
    assert 'href="/admin/radius/cards/checker"' in side


# ── ⑥ لا زرَّ ولا حقلَ للإلكترونيّ في بقيّة اللوحة حين يُطفأ ──────────────

@pytest.mark.ecards_closed
def test_closed_hotspot_store_button_is_forced_off(app):
    from app.radius.services import hotspot_templates as ht

    with app.test_request_context("/"):
        assert ht.validate_vars({"STORE_ENABLED": "yes"})["STORE_ENABLED"] == "no"


def test_enabled_hotspot_store_button_is_kept(app):
    from app.radius.services import hotspot_templates as ht

    with app.test_request_context("/"):
        assert ht.validate_vars({"STORE_ENABLED": "yes"})["STORE_ENABLED"] == "yes"


@pytest.mark.ecards_closed
def test_closed_docs_center_hides_ecards_guides(app):
    c = _owner(app)
    index = c.get("/admin/radius/docs").get_data(as_text=True)
    assert "/admin/radius/docs/section/e-cards" not in index
    assert c.get("/admin/radius/docs/section/e-cards").status_code == 404
    for slug in ("card-marketplace", "card-users", "store-support", "mikrotik-store"):
        assert c.get(f"/admin/radius/docs/{slug}").status_code == 404, slug


@pytest.mark.ecards_closed
def test_closed_settings_hide_store_fields(app):
    html = _owner(app).get("/admin/radius/settings").get_data(as_text=True)
    assert "network.store_api_key" not in html
    assert 'name="network.radius_server_ip"' not in html


def test_enabled_settings_show_store_fields(app):
    html = _owner(app).get("/admin/radius/settings").get_data(as_text=True)
    assert "network.store_api_key" in html


@pytest.mark.ecards_closed
def test_closed_overview_never_names_printed_or_electronic(app):
    frag = _owner(app).get("/admin/radius/cards/overview?fragment=1").get_data(as_text=True)
    assert "co-sales-summary-item--print" not in frag
    assert "co-sales-split" not in frag
    assert "حزم مطبوعة" not in frag


# ── ⑦ جدول الجلسات في العرض البسيط للفاحص ──────────────────────────────

def test_checker_simple_view_has_a_sessions_table(app):
    with app.app_context():
        from app.radius.db.connection import db as live_db
        from app.radius.services.cards import get_cards_service

        pid = _plan_id()
        _batch, cards = get_cards_service().generate_batch(actor="t", plan_id=pid, count=1)
        user = cards[0].username
        live_db().execute(
            "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, nasipaddress, "
            "framedipaddress, acctstarttime, acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets) "
            "VALUES(1, ?, 's1', 'u1', '10.0.0.1', '10.9.9.9', datetime('now','-1 hour'), datetime('now'), "
            "3600, 1000, 2000)",
            (user,))
    c = app.test_client(); _login(c)
    html = c.get(f"/admin/radius/cards/checker?query={user}&embed=1").get_data(as_text=True)
    simple = html[html.index('class="cc-simple-only"'):]
    sec = simple[simple.index('data-cc-section="sessions_simple"'):]
    sec = sec[:sec.index("</section>")]
    assert "<table" in sec and "10.9.9.9" in sec
    # الجدولُ المختصر لا يحمل وسمَ ترقيم المتقدّم كي لا يتنازعا عليه
    assert "data-cc-sessions-region" not in sec
