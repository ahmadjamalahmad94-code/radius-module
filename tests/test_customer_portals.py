from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _marketplace_service():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService

    return CardUsersMarketplaceService


def _portal_service():
    from app.radius.services.customer_portals import CustomerPortalService

    return CustomerPortalService


def _portal_auth_error():
    from app.radius.services.customer_portals import PortalAuthError

    return PortalAuthError


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "customer_portals.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file

    @flask_app.before_request
    def _bind_customer_portal_test_db():
        os.environ["HOBERADIUS_DB_PATH"] = db_file
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(db_file)

    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _plan(name: str = "Portal Plan", *, loan_enabled: bool = True, max_loan_minutes: int = 2880) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            loan_enabled, max_loan_minutes, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 24 * 60, 30, 10.0, "JOD", 1 if loan_enabled else 0, max_loan_minutes),
    )
    return int(cur.lastrowid)


def _subscriber(username: str = "portal-user", *, expired: bool = False, plan_id: int | None = None) -> int:
    plan_id = plan_id or _plan(f"Portal Plan {username}")
    expire_at = "2020-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z"
    cur = db().execute(
        """
        INSERT INTO subscribers(
            tenant_id, username, password, plan_id, status, expire_at,
            balance, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (1, username, "portal-pass", plan_id, "enabled", expire_at, -3.5, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    return int(cur.lastrowid)


def _csrf(client, token: str = "portal-csrf") -> str:
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    return token


def _card_user_with_purchase() -> tuple[int, dict, dict]:
    plan_id = _plan("Card Portal Plan", loan_enabled=False)
    svc = _marketplace_service()(tenant_id=1)
    card_user = svc.create_card_user(display_name="Card Customer", mobile="0590000000", password="portal-pass")
    package = svc.create_package(name="Portal Package", plan_id=plan_id, price="2.00")
    svc.recharge_wallet(card_user_id=card_user["id"], amount="10.00", actor="test")
    purchase = svc.purchase_package(card_user_id=card_user["id"], package_id=package["id"], actor="test")
    card = db().execute(
        "SELECT * FROM cards WHERE tenant_id=1 AND id=?",
        (purchase["card_id"],),
    ).fetchone()
    return int(card_user["id"]), purchase, dict(card)


def test_subscriber_portal_access_is_self_scoped_and_expired_can_view(app):
    with app.app_context():
        subscriber_id = _subscriber(expired=True)
        other_id = _subscriber("other-user")
    with app.test_client() as client:
        token = _csrf(client)
        res = client.post(
            "/admin/radius/portal/subscriber/login",
            data={"_csrf_token": token, "username": "portal-user", "password": "portal-pass"},
            follow_redirects=True,
        )
        admin_probe = client.get("/admin/radius/users", follow_redirects=False)

    body = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "subscriber-portal-home" in body
    assert "منته" in body
    assert "other-user" not in body
    assert admin_probe.status_code in {302, 303}
    assert subscriber_id != other_id


def test_subscriber_auth_rejects_other_password(app):
    with app.app_context():
        _subscriber()
        with pytest.raises(_portal_auth_error()):
            _portal_service()(tenant_id=1).authenticate_subscriber(
                username="portal-user",
                password="wrong",
            )


def test_loan_request_auto_approves_when_plan_policy_allows(app):
    with app.app_context():
        subscriber_id = _subscriber()
        result = _portal_service()(tenant_id=1).submit_loan_request(
            subscriber_id=subscriber_id,
            requested_minutes=1440,
            reason="need one day",
        )
        loan = db().execute(
            "SELECT * FROM loan_entries WHERE tenant_id=1 AND subscriber_id=?",
            (subscriber_id,),
        ).fetchone()

    assert result["status"] == "auto_approved"
    assert result["result"]["applied_to_radius"] is False
    assert loan is not None
    assert loan["duration_minutes"] == 1440


def test_loan_request_requires_approval_when_plan_policy_blocks(app):
    with app.app_context():
        subscriber_id = _subscriber(plan_id=_plan("No Loan Plan", loan_enabled=False, max_loan_minutes=0))
        result = _portal_service()(tenant_id=1).submit_loan_request(
            subscriber_id=subscriber_id,
            requested_minutes=1440,
            reason="blocked",
        )
        ticket = db().execute(
            "SELECT * FROM tickets WHERE tenant_id=1 AND subscriber_id=? ORDER BY id DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()
        inbox = db().execute(
            "SELECT * FROM inbox_messages WHERE tenant_id=1 AND subscriber_id=? ORDER BY id DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()
        event = db().execute(
            "SELECT * FROM business_events WHERE tenant_id=1 AND target_type='subscriber' AND target_id=? ORDER BY id DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()

    assert result["status"] == "requires_approval"
    assert result["result"]["applied_to_radius"] is False
    assert result["result"]["ticket_id"] == ticket["id"]
    assert ticket["category"] == "service_request"
    assert ticket["status"] == "open"
    assert "طلب سلفة وقت" in ticket["subject"]
    assert inbox is not None
    assert "تم فتح تذكرة متابعة" in inbox["body"]
    assert event is not None
    assert "تم تسجيل طلب سلفة وقت" in event["message"]


def test_subscriber_support_request_opens_complaint_ticket_and_notification(app):
    with app.app_context():
        subscriber_id = _subscriber("support-user")
        result = _portal_service()(tenant_id=1).submit_renewal_request(
            subscriber_id=subscriber_id,
            reason="[شكوى] الخدمة بطيئة جدًا",
        )
        ticket = db().execute(
            "SELECT * FROM tickets WHERE tenant_id=1 AND subscriber_id=? ORDER BY id DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()
        request_row = db().execute(
            "SELECT * FROM customer_portal_requests WHERE tenant_id=1 AND id=?",
            (result["id"],),
        ).fetchone()

    assert result["request_type"] == "support"
    assert result["result"]["ticket_id"] == ticket["id"]
    assert ticket["category"] == "complaint"
    assert ticket["status"] == "open"
    assert "طلب دعم من بوابة المشترك" in ticket["subject"]
    assert request_row["request_type"] == "support"


def test_card_user_portal_marketplace_purchase_uses_existing_service(app):
    with app.app_context():
        card_user_id, _purchase, card = _card_user_with_purchase()
        svc = _marketplace_service()(tenant_id=1)
        package = svc.create_package(name="Second Package", plan_id=card["plan_id"], price="1.00")
        svc.recharge_wallet(card_user_id=card_user_id, amount="5.00", actor="test")
    with app.test_client() as client:
        token = _csrf(client)
        login = client.post(
            "/admin/radius/portal/card/login",
            data={"_csrf_token": token, "mobile": "0590000000", "password": "portal-pass"},
            follow_redirects=True,
        )
        purchase = client.post(
            "/admin/radius/portal/card/purchase",
            data={"_csrf_token": token, "package_id": package["id"]},
            follow_redirects=True,
        )
    purchased_card = db().execute(
        """
        SELECT c.username
        FROM card_user_purchases p
        JOIN cards c ON c.id = p.card_id
        WHERE p.tenant_id = 1 AND p.card_user_id = ?
        ORDER BY p.id DESC
        LIMIT 1
        """,
        (card_user_id,),
    ).fetchone()

    assert login.status_code == 200
    assert "محفظتي" in login.get_data(as_text=True)
    assert card["password"] not in login.get_data(as_text=True)
    assert purchase.status_code == 200
    assert purchased_card is not None
    assert purchased_card["username"] in purchase.get_data(as_text=True)


def test_portal_pages_do_not_render_admin_navigation_or_routes(app):
    with app.app_context():
        _subscriber()
    with app.test_client() as client:
        token = _csrf(client)
        res = client.post(
            "/admin/radius/portal/subscriber/login",
            data={"_csrf_token": token, "username": "portal-user", "password": "portal-pass"},
            follow_redirects=True,
        )

    body = res.get_data(as_text=True)
    assert "admin-sidebar" not in body
    assert "/admin/radius/users" not in body
    assert "بوابة المشترك" in body


# ─── ربط مفاتيح «صفحة المشترك» (portal.*) بالبوابة ───────────────


def _set_portal(key: str, value: str) -> None:
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, key, value)


def _login_subscriber(client):
    token = _csrf(client)
    return client.post(
        "/admin/radius/portal/subscriber/login",
        data={"_csrf_token": token, "username": "portal-user", "password": "portal-pass"},
        follow_redirects=True,
    )


def test_portal_flags_default_show_all_sections(app):
    with app.app_context():
        _subscriber()
    with app.test_client() as client:
        body = _login_subscriber(client).get_data(as_text=True)
    # افتراضيًا كل التبويبات/النماذج ظاهرة
    assert 'data-tab="usage"' in body
    assert 'data-tab="billing"' in body
    assert 'data-tab="requests"' in body
    assert "subscriber-loan-request-form" in body
    assert "subscriber-renewal-request-form" in body


def test_portal_flag_off_hides_usage_and_invoices_tabs(app):
    with app.app_context():
        _subscriber()
        _set_portal("portal.show_usage", "0")
        _set_portal("portal.show_invoices", "0")
    with app.test_client() as client:
        body = _login_subscriber(client).get_data(as_text=True)
    assert 'data-tab="usage"' not in body
    assert 'data-tab="billing"' not in body
    # تبويب الطلبات ما زال ظاهرًا (مفاتيحه مفعّلة)
    assert 'data-tab="requests"' in body


def test_portal_flag_off_hides_request_forms(app):
    with app.app_context():
        _subscriber()
        _set_portal("portal.allow_loan_request", "0")
        _set_portal("portal.allow_renewal_request", "0")
        _set_portal("portal.show_support", "0")
    with app.test_client() as client:
        body = _login_subscriber(client).get_data(as_text=True)
    assert "subscriber-loan-request-form" not in body
    assert "subscriber-renewal-request-form" not in body
    # كل النماذج مُعطّلة → تبويب الطلبات يختفي كاملًا
    assert 'data-tab="requests"' not in body


def test_loan_request_post_rejected_with_403_when_disabled(app):
    with app.app_context():
        _subscriber()
        _set_portal("portal.allow_loan_request", "0")
    with app.test_client() as client:
        _login_subscriber(client)
        token = _csrf(client)
        res = client.post(
            "/portal/subscriber/loan-request",
            data={"_csrf_token": token, "requested_minutes": "60", "reason": "x"},
        )
    assert res.status_code == 403
    assert "غير مُفعَّل" in res.get_data(as_text=True)


def test_support_post_rejected_with_403_when_support_disabled(app):
    with app.app_context():
        _subscriber()
        _set_portal("portal.show_support", "0")
        # التجديد مفعّل — للتأكّد أنّ الرفض خاصّ بقناة الدعم ([شكوى]).
        _set_portal("portal.allow_renewal_request", "1")
    with app.test_client() as client:
        _login_subscriber(client)
        token = _csrf(client)
        res = client.post(
            "/portal/subscriber/renewal-request",
            data={"_csrf_token": token, "reason": "[شكوى] بطء شديد"},
        )
    assert res.status_code == 403
    assert "الدعم" in res.get_data(as_text=True)
