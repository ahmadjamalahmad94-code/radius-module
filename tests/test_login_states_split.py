"""R12.3 — card vs subscriber login-states split into two dedicated pages.

«حالات دخول الهوت سبوت» used to mix card accounts (8-digit numeric usernames)
and subscriber/gateway accounts in one login-states list. They are now two
dedicated routes/pages, each linked from its own sidebar section:

  • /reports/login_states/cards        (rep_login_states_cards)        → cards only
  • /reports/login_states/subscribers  (rep_login_states_subscribers)  → subs only

Account kind is detected at the QUERY level inside login_events._collect_rows:
web logins by audit_log.target_type, and network auth by exact membership in
the `cards` table (username IN/NOT IN cards) — never by username format.
"""
from __future__ import annotations

import os

import pytest

CARD_USERS = ["80700008", "80700009", "80700010"]
SUB_USERS = ["ahmad_sub", "portal_lina", "gw_user_77"]


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "login_states_split.db")
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
        # FK off for these minimal inserts — detection reads only cards.username
        # + radpostauth fields, no parent batch/plan rows needed.
        conn.execute("PRAGMA foreign_keys = OFF")
        for u in CARD_USERS:
            conn.execute(
                "INSERT INTO cards (tenant_id, batch_id, username, password, "
                "plan_id, created_at) VALUES (1, 1, ?, 'x', 1, '2026-06-07 10:00:00')",
                (u,),
            )
        seeded = []
        for i, u in enumerate(CARD_USERS):
            seeded.append((u, "Access-Accept" if i % 2 == 0 else "Access-Reject",
                           f"2026-06-07 1{i}:00:00"))
        for i, u in enumerate(SUB_USERS):
            seeded.append((u, "Access-Accept" if i % 2 == 0 else "Access-Reject",
                           f"2026-06-07 1{i}:30:00"))
        for u, reply, when in seeded:
            conn.execute(
                "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
                "authdate, class, nas) VALUES (1, ?, '', ?, ?, '', '10.0.0.1')",
                (u, reply, when),
            )
        conn.commit()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "ls_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ls-csrf"


def test_cards_page_lists_only_cards(app):
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/cards")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    for u in CARD_USERS:
        assert f"<bdi>{u}</bdi>" in html, f"card {u} missing"
    for u in SUB_USERS:
        assert u not in html, f"subscriber {u} leaked onto cards page"
    assert "حالات دخول الكروت" in html


def test_subscribers_page_lists_only_subscribers(app):
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    for u in SUB_USERS:
        assert f"<bdi>{u}</bdi>" in html, f"subscriber {u} missing"
    for u in CARD_USERS:
        assert f"<bdi>{u}</bdi>" not in html, f"card {u} leaked onto subscribers page"
    assert "حالات دخول المشتركين" in html


def test_sidebar_links_under_correct_sections(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert "حالات البطاقات" in html
    assert "حالات دخول المشتركين/البوابة" in html
    assert "/reports/login_states/cards" in html
    assert "/reports/login_states/subscribers" in html
    # placement: subscribers section precedes cards section in the markup;
    # each link must fall inside its own section block.
    i_subs_sec = html.find('data-hb-section="subscribers"')
    i_cards_sec = html.find('data-hb-section="cards"')
    i_subs_link = html.find("حالات دخول المشتركين/البوابة")
    i_cards_link = html.find("حالات البطاقات")
    assert i_subs_sec < i_subs_link < i_cards_sec, "subs link not in المشتركون section"
    assert i_cards_sec < i_cards_link, "cards link not in البطاقات section"


def test_dedicated_routes_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/reports/login_states/cards" in rules
    assert "/admin/radius/reports/login_states/subscribers" in rules
