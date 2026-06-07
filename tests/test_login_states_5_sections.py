"""R12.5 — حالات تسجيل الدخول: خمسة أقسام مفروزة بدقة.

Hub page shows 5 section cards; each dedicated page is filtered to
EXACTLY one kind (no cross-contamination). Sidebar links appear under
the correct section.

Five kinds:
  subscriber_net    — RADIUS subscriber   (/login_states/subscribers)
  card_net          — RADIUS card         (/login_states/cards)
  subscriber_portal — portal subscriber   (/login_states/sub_portal)
  card_store        — store/portal card   (/login_states/card_store)
  admin             — admin panel         (/login_states/admin)

Account-kind detection: SQL-level in login_events._collect_rows:
  network rows → radpostauth filtered by cards table membership;
  web rows     → audit_log filtered by target_type + payload.source.
"""
from __future__ import annotations

import json
import os

import pytest

CARD_USERS = ["80700008", "80700009"]
SUB_USERS  = ["ahmad_sub", "portal_lina"]
ADMIN_USER = "superadmin"
PORTAL_SUB = "portal_ali"
STORE_CARD = "0599123456"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "ls5.db")
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
        from app.radius.db.connection import db
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        conn = db()
        conn.execute("PRAGMA foreign_keys = OFF")

        # ── بذر الكروت في جدول cards (للتمييز SQL-level) ──
        for u in CARD_USERS + [STORE_CARD]:
            conn.execute(
                "INSERT INTO cards (tenant_id, batch_id, username, password, "
                "plan_id, created_at) VALUES (1,1,?,'-',1,'2026-06-07 10:00:00')",
                (u,),
            )

        # ── القسم 1: RADIUS مشتركون ──
        for i, u in enumerate(SUB_USERS):
            conn.execute(
                "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
                "authdate, class, nas) VALUES (1,?,'',%s,'2026-06-07 11:0%s:00','','10.0.0.1')"
                % ("'Access-Accept'" if i % 2 == 0 else "'Access-Reject'", i),
                (u,),
            )

        # ── القسم 2: RADIUS بطاقات ──
        for i, u in enumerate(CARD_USERS):
            conn.execute(
                "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
                "authdate, class, nas) VALUES (1,?,'',%s,'2026-06-07 12:0%s:00','','10.0.0.2')"
                % ("'Access-Accept'" if i % 2 == 0 else "'Access-Reject'", i),
                (u,),
            )

        # ── القسم 3: بوابة المشتركين (audit_log، بلا payload.source) ──
        conn.execute(
            "INSERT INTO audit_log (tenant_id,actor,action,target_type,target_id,"
            "payload_json,result_status,error_message,severity,created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (PORTAL_SUB, "auth_login", "subscriber", PORTAL_SUB,
             json.dumps({"kind": "login_event", "actor_type": "subscriber", "reason": ""}),
             "success", "", "info", "2026-06-07 13:00:00"),
        )
        conn.execute(
            "INSERT INTO audit_log (tenant_id,actor,action,target_type,target_id,"
            "payload_json,result_status,error_message,severity,created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (PORTAL_SUB, "auth_login_failed", "subscriber", PORTAL_SUB,
             json.dumps({"kind": "login_event", "actor_type": "subscriber", "reason": "bad_password"}),
             "failed", "bad_password", "warning", "2026-06-07 13:05:00"),
        )

        # ── القسم 4: متجر البطاقات (audit_log، payload.source="store") ──
        conn.execute(
            "INSERT INTO audit_log (tenant_id,actor,action,target_type,target_id,"
            "payload_json,result_status,error_message,severity,created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (STORE_CARD, "auth_login", "card", STORE_CARD,
             json.dumps({"kind": "login_event", "actor_type": "card", "reason": "", "source": "store"}),
             "success", "", "info", "2026-06-07 14:00:00"),
        )
        conn.execute(
            "INSERT INTO audit_log (tenant_id,actor,action,target_type,target_id,"
            "payload_json,result_status,error_message,severity,created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (STORE_CARD, "auth_login_failed", "card", STORE_CARD,
             json.dumps({"kind": "login_event", "actor_type": "card", "reason": "bad_password", "source": "store"}),
             "failed", "bad_password", "warning", "2026-06-07 14:05:00"),
        )

        # ── القسم 5: دخول المدراء (audit_log، target_type="admin") ──
        conn.execute(
            "INSERT INTO audit_log (tenant_id,actor,action,target_type,target_id,"
            "payload_json,result_status,error_message,severity,created_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (ADMIN_USER, "auth_login", "admin", ADMIN_USER,
             json.dumps({"kind": "login_event", "actor_type": "admin", "reason": ""}),
             "success", "", "info", "2026-06-07 15:00:00"),
        )

        conn.commit()

    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"]      = 1
        sess["admin_user"]    = "ls_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"]     = 1
        sess["_csrf_token"]   = "ls5-csrf"


# ─────────────────── اختبارات الصفحات الخمس ───────────────────

def test_routes_registered(app):
    """كل الراوتات الخمسة الجديدة مسجّلة في url_map."""
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    for ep in (
        "radius.rep_login_states_subscribers",
        "radius.rep_login_states_cards",
        "radius.rep_login_states_sub_portal",
        "radius.rep_login_states_card_store",
        "radius.rep_login_states_admin",
    ):
        assert ep in rules, f"endpoint missing: {ep}"


def test_hub_shows_5_cards(app):
    """صفحة hub تعرض بطاقات الأقسام الخمسة."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for kind in ("subscriber_net", "card_net", "subscriber_portal", "card_store", "admin"):
        assert f'data-testid="login-states-card-{kind}"' in html, \
            f"hub missing card for kind={kind}"


def test_subscriber_radius_page_isolation(app):
    """صفحة RADIUS المشتركين: SUB_USERS فقط — لا CARD_USERS ولا PORTAL_SUB ولا STORE_CARD."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for u in SUB_USERS:
        assert f"<bdi>{u}</bdi>" in html, f"missing sub RADIUS user: {u}"
    for u in CARD_USERS + [STORE_CARD]:
        assert f"<bdi>{u}</bdi>" not in html, f"card user leaked to sub-RADIUS page: {u}"
    assert PORTAL_SUB not in html, "portal sub leaked to RADIUS page"
    assert ADMIN_USER not in html, "admin leaked to sub-RADIUS page"


def test_card_radius_page_isolation(app):
    """صفحة RADIUS البطاقات: CARD_USERS فقط — لا SUB_USERS ولا STORE_CARD (بوابة)."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/cards")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for u in CARD_USERS:
        assert f"<bdi>{u}</bdi>" in html, f"missing card RADIUS user: {u}"
    for u in SUB_USERS:
        assert f"<bdi>{u}</bdi>" not in html, f"sub user leaked to card-RADIUS page: {u}"
    # STORE_CARD is also in cards table but has NO radpostauth row → absent
    assert PORTAL_SUB not in html, "portal sub leaked"
    assert ADMIN_USER not in html, "admin leaked"


def test_subscriber_portal_page_isolation(app):
    """صفحة بوابة المشتركين: PORTAL_SUB فقط — لا SUB_USERS RADIUS ولا STORE_CARD."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/sub_portal")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert f"<bdi>{PORTAL_SUB}</bdi>" in html, "portal sub user missing"
    for u in SUB_USERS:
        assert f"<bdi>{u}</bdi>" not in html, f"RADIUS sub user leaked to portal page: {u}"
    for u in CARD_USERS + [STORE_CARD]:
        assert f"<bdi>{u}</bdi>" not in html, f"card user leaked to sub-portal page: {u}"
    assert ADMIN_USER not in html, "admin leaked"


def test_card_store_page_isolation(app):
    """صفحة متجر البطاقات: STORE_CARD فقط — لا CARD_USERS RADIUS ولا PORTAL_SUB."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/card_store")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert f"<bdi>{STORE_CARD}</bdi>" in html, "store card user missing"
    for u in CARD_USERS:
        assert f"<bdi>{u}</bdi>" not in html, f"RADIUS card user leaked to store page: {u}"
    for u in SUB_USERS:
        assert f"<bdi>{u}</bdi>" not in html, f"sub user leaked to store page: {u}"
    assert ADMIN_USER not in html, "admin leaked"


def test_admin_page_isolation(app):
    """صفحة المدراء: ADMIN_USER فقط — لا أحد آخر."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/admin")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert f"<bdi>{ADMIN_USER}</bdi>" in html, "admin user missing"
    for u in SUB_USERS + CARD_USERS + [STORE_CARD, PORTAL_SUB]:
        assert f"<bdi>{u}</bdi>" not in html, f"non-admin user leaked to admin page: {u}"


# ─────────────────── اختبار الشريط الجانبي ───────────────────

def test_sidebar_links_under_correct_sections(app):
    """الروابط الخمسة تظهر تحت أقسامها الصحيحة."""
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)

    # الروابط موجودة
    assert "/reports/login_states/subscribers" in html, "subs RADIUS link missing"
    assert "/reports/login_states/cards"       in html, "cards RADIUS link missing"
    assert "/reports/login_states/sub_portal"  in html, "sub_portal link missing"
    assert "/reports/login_states/card_store"  in html, "card_store link missing"
    assert "/reports/login_states/admin"       in html, "admin link missing"

    # ترتيب: المشتركون → البطاقات → الإدارة
    i_subs_sec  = html.find('data-hb-section="subscribers"')
    i_cards_sec = html.find('data-hb-section="cards"')
    i_admin_sec = html.find('data-hb-section="administration"')

    i_subs_link   = html.find("/reports/login_states/subscribers")
    i_portal_link = html.find("/reports/login_states/sub_portal")
    i_cards_link  = html.find("/reports/login_states/cards")
    i_store_link  = html.find("/reports/login_states/card_store")
    i_admin_link  = html.find("/reports/login_states/admin")

    assert i_subs_sec   < i_subs_link   < i_cards_sec,  "subs link not in المشتركون"
    assert i_subs_sec   < i_portal_link < i_cards_sec,  "portal link not in المشتركون"
    assert i_cards_sec  < i_cards_link  < i_admin_sec,  "cards link not in البطاقات"
    assert i_cards_sec  < i_store_link  < i_admin_sec,  "store link not in البطاقات"
    assert i_admin_sec  < i_admin_link,                 "admin link not in الإدارة"


# ─────────────────── backward compat ───────────────────

def test_backward_compat_actor_redirect(app):
    """?actor=subscriber|card|admin يُعاد توجيهه للصفحة المخصّصة."""
    with app.test_client() as client:
        _auth(client)
        for actor, expected_path in (
            ("subscriber", "/reports/login_states/subscribers"),
            ("card",       "/reports/login_states/cards"),
            ("admin",      "/reports/login_states/admin"),
        ):
            res = client.get(f"/admin/radius/reports/login_states?actor={actor}")
            assert res.status_code in (301, 302), f"no redirect for actor={actor}"
            assert expected_path in res.headers.get("Location", ""), \
                f"wrong redirect for actor={actor}"
