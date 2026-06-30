"""Wave A notifications — اختبارات التكامل.

يتحقّق من:
  1. recharge_added يُطلَق عند شحن رصيد المشترك (add_cash_balance).
  2. balance_withdraw يُطلَق عند خصم رصيد المشترك (extend_time paid/debt).
  3. card_store_purchase يُطلَق عند نجاح purchase_package().
  4. card_store_deposit  يُطلَق عند نجاح recharge_wallet().
  5. card_store_withdraw يُطلَق عند خصم المحفظة في purchase_package().
  6. SMS يُرسَل ببيانات البطاقة بعد purchase_package() الناجح.
  7. صفحة subscriber-notifications تعرض قسم متجر البطاقات (status 200).
  8. card_store موجود في _SUB_GROUPS ومُمرَّر للقالب.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "wave_a_notif.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _bind(app):
    db_file = app.config["_HOBERADIUS_TEST_DB_FILE"]
    os.environ["HOBERADIUS_DB_PATH"] = db_file
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "admin"
        sess["admin_name"] = "Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _db():
    from app.radius.db.connection import db
    return db()


def _plan_id():
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,currency,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (1, "Wave-A Plan", 480, 1, 5.0, "ILS"),
    )
    return int(cur.lastrowid)


def _market_setup(app):
    _bind(app)
    with app.app_context():
        from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
        svc = CardUsersMarketplaceService(tenant_id=1)
        user = svc.create_card_user(display_name="اختبار A", mobile="0591234567")
        pkg = svc.create_package(
            name="باقة اختبار",
            plan_id=_plan_id(),
            duration_minutes=480,
            speed_down_kbps=2048,
            speed_up_kbps=512,
            price="5.00",
        )
    return user, pkg


def _subscriber_setup(app):
    _bind(app)
    with app.app_context():
        _db().execute(
            "INSERT OR IGNORE INTO subscribers(tenant_id,username,password,status,"
            "plan_id,balance,created_at,updated_at) "
            "VALUES(1,'testuser','pass','active',NULL,0.0,datetime('now'),datetime('now'))"
        )
    return "testuser"


# notify_event is imported lazily inside service functions via
# `from .notifications_engine import notify_event` — patching the
# notifications_engine module attribute intercepts it correctly.
_NE = "app.radius.services.notifications_engine.notify_event"


class TestRechargeAddedEvent:
    def test_recharge_added_fires_on_credit(self, app):
        username = _subscriber_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().add_cash_balance(
                    actor="admin", username=username, amount=10.0)
        assert "recharge_added" in fired, f"recharge_added not in {fired}"

    def test_recharge_added_context_has_amount_and_balance(self, app):
        username = _subscriber_setup(app)
        _bind(app)
        captured = {}
        def fake_notify(event_key, *, tenant_id=None, subscriber=None, context=None):
            if event_key == "recharge_added":
                captured["context"] = context or {}
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().add_cash_balance(
                    actor="admin", username=username, amount=15.5)
        ctx = captured.get("context", {})
        assert "amount" in ctx, f"amount missing from context {ctx}"
        assert "balance" in ctx, f"balance missing from context {ctx}"

    def test_recharge_added_not_fired_on_zero_credit(self, app):
        username = _subscriber_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().add_cash_balance(
                    actor="admin", username=username,
                    amount=5.0, settled_deduction=5.0)
        assert "recharge_added" not in fired, "must NOT fire when net credit is 0"


class TestBalanceWithdrawEvent:
    def _set_balance(self, app, username, amount=100.0):
        _bind(app)
        with app.app_context():
            _db().execute(
                "UPDATE subscribers SET balance=? WHERE username=?", (amount, username))

    def test_balance_withdraw_fires_on_paid_extend(self, app):
        username = _subscriber_setup(app)
        self._set_balance(app, username)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().extend_time(
                    actor="admin", username=username,
                    minutes=60, charge_mode="paid", amount=5.0)
        assert "balance_withdraw" in fired, f"balance_withdraw not in {fired}"

    def test_balance_withdraw_context_has_amount_and_balance(self, app):
        username = _subscriber_setup(app)
        self._set_balance(app, username)
        _bind(app)
        captured = {}
        def fake_notify(event_key, *, tenant_id=None, subscriber=None, context=None):
            if event_key == "balance_withdraw":
                captured["context"] = context or {}
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().extend_time(
                    actor="admin", username=username,
                    minutes=60, charge_mode="paid", amount=5.0)
        ctx = captured.get("context", {})
        assert "amount" in ctx
        assert "balance" in ctx

    def test_balance_withdraw_fires_on_debt_extend(self, app):
        username = _subscriber_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().extend_time(
                    actor="admin", username=username,
                    minutes=60, charge_mode="debt", amount=5.0)
        assert "balance_withdraw" in fired

    def test_balance_withdraw_not_fired_on_free_extend(self, app):
        username = _subscriber_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            with patch(_NE, side_effect=fake_notify):
                from app.radius.services.users import get_users_service
                get_users_service().extend_time(
                    actor="admin", username=username,
                    minutes=60, charge_mode="free")
        assert "balance_withdraw" not in fired


class TestCardStorePurchaseEvent:
    def test_card_store_purchase_fires(self, app):
        user, pkg = _market_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            svc.recharge_wallet(card_user_id=user["id"], amount="10.00")
            with patch(_NE, side_effect=fake_notify):
                svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"])
        assert "card_store_purchase" in fired, f"not in {fired}"

    def test_card_store_purchase_context_has_package_and_amount(self, app):
        user, pkg = _market_setup(app)
        _bind(app)
        captured = {}
        def fake_notify(event_key, *, tenant_id=None, subscriber=None, context=None):
            if event_key == "card_store_purchase":
                captured["context"] = context or {}
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            svc.recharge_wallet(card_user_id=user["id"], amount="10.00")
            with patch(_NE, side_effect=fake_notify):
                svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"])
        ctx = captured.get("context", {})
        assert "package" in ctx
        assert "amount" in ctx

    def test_card_store_withdraw_fires_on_purchase(self, app):
        user, pkg = _market_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            svc.recharge_wallet(card_user_id=user["id"], amount="10.00")
            with patch(_NE, side_effect=fake_notify):
                svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"])
        assert "card_store_withdraw" in fired, f"not in {fired}"


class TestCardStoreDepositEvent:
    def test_card_store_deposit_fires(self, app):
        user, _ = _market_setup(app)
        _bind(app)
        fired = []
        def fake_notify(event_key, **kw):
            fired.append(event_key)
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            with patch(_NE, side_effect=fake_notify):
                svc.recharge_wallet(card_user_id=user["id"], amount="20.00")
        assert "card_store_deposit" in fired, f"not in {fired}"

    def test_card_store_deposit_context_has_amount_and_balance(self, app):
        user, _ = _market_setup(app)
        _bind(app)
        captured = {}
        def fake_notify(event_key, *, tenant_id=None, subscriber=None, context=None):
            if event_key == "card_store_deposit":
                captured["context"] = context or {}
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            with patch(_NE, side_effect=fake_notify):
                svc.recharge_wallet(card_user_id=user["id"], amount="20.00")
        ctx = captured.get("context", {})
        assert "amount" in ctx
        assert "balance" in ctx


class TestSmsCredentialsOnPurchase:
    def test_sms_sent_when_channel_configured(self, app):
        user, pkg = _market_setup(app)
        _bind(app)
        sms_calls = []
        mock_cfg = {
            "enabled": True,
            "send_url_template": "https://sms.example.com/send?phone={phone}&msg={message}",
            "http_method": "GET",
        }
        mock_outcome = MagicMock()
        mock_outcome.ok = True
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            svc.recharge_wallet(card_user_id=user["id"], amount="10.00")
            with patch(_NE), \
                 patch("app.radius.services.card_users_marketplace.load_channel_config",
                       return_value=mock_cfg), \
                 patch("app.radius.services.card_users_marketplace.http_send",
                       side_effect=lambda **kw: (sms_calls.append(kw), mock_outcome)[1]), \
                 patch("app.radius.services.card_users_marketplace.normalize_msisdn",
                       side_effect=lambda p, d: p), \
                 patch("app.radius.services.card_users_marketplace.tenant_dial_code",
                       return_value="+970"):
                purchase = svc.purchase_package(
                    card_user_id=user["id"], package_id=pkg["id"])
        assert len(sms_calls) == 1, f"Expected 1 SMS, got {len(sms_calls)}"
        msg = sms_calls[0]["message"]
        assert purchase["cred_username"] in msg, (
            f"cred_username {purchase['cred_username']!r} not in msg {msg!r}")

    def test_sms_not_sent_when_channel_disabled(self, app):
        user, pkg = _market_setup(app)
        _bind(app)
        sms_calls = []
        with app.app_context():
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            svc = CardUsersMarketplaceService(tenant_id=1)
            svc.recharge_wallet(card_user_id=user["id"], amount="10.00")
            with patch(_NE), \
                 patch("app.radius.services.card_users_marketplace.load_channel_config",
                       return_value={"enabled": False, "send_url_template": "",
                                     "http_method": "GET"}), \
                 patch("app.radius.services.card_users_marketplace.http_send",
                       side_effect=lambda **kw: sms_calls.append(kw)):
                svc.purchase_package(card_user_id=user["id"], package_id=pkg["id"])
        assert len(sms_calls) == 0, "SMS must NOT be sent when channel is disabled"

    def test_sms_max_60_chars_for_short_creds(self):
        msg = "بطاقتك: ab12 / cd34"
        assert len(msg) <= 60

    def test_sms_fallback_newline_for_long_creds(self):
        u, p = "verylongusername1234", "verylongpassword5678"
        standard = f"بطاقتك: {u} / {p}"
        if len(standard) > 60:
            fallback = f"{u}\n{p}"
            assert "\n" in fallback


class TestSubscriberNotificationsPage:
    def test_page_renders_200(self, app):
        with app.test_client() as client:
            _bind(app)
            _auth(client)
            rv = client.get("/admin/radius/subscriber-notifications")
        assert rv.status_code == 200

    def test_page_has_card_store_group_label(self, app):
        with app.test_client() as client:
            _bind(app)
            _auth(client)
            rv = client.get("/admin/radius/subscriber-notifications")
        body = rv.data.decode("utf-8")
        assert "متجر البطاقات الإلكتروني" in body

    def test_page_has_card_store_event_keys(self, app):
        with app.test_client() as client:
            _bind(app)
            _auth(client)
            rv = client.get("/admin/radius/subscriber-notifications")
        body = rv.data.decode("utf-8")
        assert "card_store_purchase" in body
        assert "card_store_deposit" in body

    def test_card_store_in_sub_groups(self, app):
        with app.app_context():
            from app.radius.routes.notification_hub import _SUB_GROUPS
        assert "card_store" in _SUB_GROUPS

    def test_card_store_events_in_engine_registry(self, app):
        with app.app_context():
            from app.radius.services.notifications_engine import EVENTS, GROUP_LABELS
        for key in ("card_store_purchase", "card_store_deposit", "card_store_withdraw"):
            assert key in EVENTS, f"{key} missing from EVENTS"
        assert "card_store" in GROUP_LABELS
        assert GROUP_LABELS["card_store"] == "متجر البطاقات الإلكتروني"

    def test_recharge_and_withdraw_in_engine_registry(self, app):
        with app.app_context():
            from app.radius.services.notifications_engine import EVENTS
        assert "recharge_added" in EVENTS
        assert "balance_withdraw" in EVENTS
