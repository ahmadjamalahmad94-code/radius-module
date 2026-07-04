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
    # After 5-way split: title is «حالات دخول البطاقات» (RADIUS لمست-source=network).
    assert "حالات دخول البطاقات" in html


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
    """After the 5-way restore, the login_states links live under
    «التقارير → الدخول والأمان» (consolidated), not scattered under
    Subscribers / Cards sections. Both URLs must be present and reachable
    from the sidebar; the surrounding group is data-hb-subgroup="reports-auth".
    """
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    # Both URLs must be on the page somewhere.
    assert "/reports/login_states/cards" in html
    assert "/reports/login_states/subscribers" in html
    # New consolidated labels under Reports → الدخول والأمان.
    assert "حالات دخول البطاقات" in html
    assert "حالات دخول المشتركين" in html
    # Placement: both URLs fall inside the reports-auth subgroup block.
    auth_sec = html.find('data-hb-subgroup="reports-auth"')
    i_cards_url = html.find("/reports/login_states/cards")
    i_subs_url = html.find("/reports/login_states/subscribers")
    assert auth_sec != -1, "reports-auth subgroup not found"
    assert auth_sec < i_cards_url, "cards link not under reports-auth subgroup"
    assert auth_sec < i_subs_url, "subscribers link not under reports-auth subgroup"


def test_dedicated_routes_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/reports/login_states/cards" in rules
    assert "/admin/radius/reports/login_states/subscribers" in rules


def test_reason_renders_in_its_own_column(app):
    # طلب المالك (يوليو 2026): «بدي السبب لحاله بعمود» — السبب المعرّب في
    # عمود «السبب» المستقلّ، لا ملحقًا بشارة «فشل» داخل عمود «النتيجة».
    with app.app_context():
        from app.radius.db.connection import db
        conn = db()
        conn.execute(
            "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
            "authdate, class, nas) VALUES (1, 'ahmad_sub', '', 'Access-Reject', "
            "'2026-06-07 15:00:00', 'out_of_schedule', '10.0.0.1')",
        )
        conn.commit()
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    # ترويسة العمود المستقلّ موجودة.
    assert "<th>السبب</th>" in html
    # السبب المعرّب يُصيَّر في خليّته الخاصّة (بداية <td>)، والخام في title.
    import re
    assert re.search(
        r"<td>\s*<span class=\"rep-reason\" title=\"out_of_schedule\">"
        r"خارج وقت السماح</span>", html), "السبب ليس في عموده المستقلّ"
    # ولم يعد ملتصقًا بالشارة داخل خليّة «النتيجة» (لا rep-reason بعد pill
    # في نفس الخليّة قبل إغلاقها).
    assert not re.search(r"فشل[^<]*</span>\s*<span class=\"rep-reason\"", html)


def test_bare_reject_is_labeled_wrong_password(app):
    # رفض شبكة بلا class (نواة FreeRADIUS رفضت كلمة المرور) — يُسمّى
    # «كلمة المرور غير صحيحة» في عمود السبب بدل شرطة، والرقاقة تبقى ظاهرة.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 10:00:00")
    with app.app_context():
        from app.radius.db.connection import db
        conn = db()
        conn.execute(
            "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
            "authdate, class, nas) VALUES (1, 'ahmad_sub', 'badpw123', "
            "'Access-Reject', ?, '', '10.0.0.1')", (today,))
        conn.commit()
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    assert "كلمة المرور غير صحيحة" in html
    # الرفض بسبب كلمة المرور ⇒ رقاقة المحاولة تبقى للمدير الرئيسي.
    assert "badpw123" in html


def test_non_password_reject_hides_attempted_pw_chip(app):
    # «الحساب معطَّل» + «كلمة المرور المُحاوَلة (خاطئة)» تناقُض — الرقاقة
    # تُحجب حين يكون سبب الرفض غير متعلّق بكلمة المرور (طلب المالك).
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 11:00:00")
    with app.app_context():
        from app.radius.db.connection import db
        conn = db()
        conn.execute(
            "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
            "authdate, class, nas) VALUES (1, 'ahmad_sub', 'secret791994', "
            "'Access-Reject', ?, 'disabled', '10.0.0.1')", (today,))
        conn.commit()
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    assert "الحساب معطَّل" in html
    # نصّ المحاولة لا يظهر إطلاقًا لرفضٍ سببه التعطيل.
    assert "secret791994" not in html


def test_attempted_pw_renders_inside_reason_cell_not_result(app):
    # طلب المالك الثاني: الرقاقة لا تُلصق بشارة «فشل» — تظهر قوسًا بعد
    # السبب في عموده: «كلمة المرور غير صحيحة (7777)».
    from datetime import datetime, timezone
    import re
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 12:00:00")
    with app.app_context():
        from app.radius.db.connection import db
        conn = db()
        conn.execute(
            "INSERT INTO radpostauth (tenant_id, username, pass, reply, "
            "authdate, class, nas) VALUES (1, 'ahmad_sub', '7777', "
            "'Access-Reject', ?, '', '10.0.0.1')", (today,))
        conn.commit()
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/login_states/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    # القيمة قوسٌ ملاصق للسبب داخل خليّة «السبب».
    assert re.search(
        r"كلمة المرور غير صحيحة</span>\s*<span class=\"rep-pw rep-pw--shown\""
        r"[^>]*>\(<bdi class=\"rep-pw-val\">7777</bdi>\)</span>", html), \
        "القيمة ليست قوسًا بعد السبب في عموده"
    # ولا رقاقة كلمة مرور ملاصقة لشارة «فشل» في عمود النتيجة.
    assert not re.search(r"فشل[^<]*</span>\s*<span class=\"rep-pw", html)
