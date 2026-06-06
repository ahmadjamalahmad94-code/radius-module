"""اختبارات لوحة دعم المتجر المتقدّم (للمدير).

تغطّي: فتح اللوحة (GET 200 + عنوان عربي)، تأكيد طلب شحن يضيف الرصيد،
تأكيد طلب سحب يخصم الرصيد، وإضافة قناة استلام تظهر في القائمة.

أسلوب الـfixture مأخوذ من test_store_registration.py (متغيرات بيئة +
إعادة تحميل وحدات app.*)، وجلسة المصادقة + توكن CSRF من
test_card_users_marketplace.py.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_support_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    with created.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "admin"
        s["admin_name"] = "Admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "t"


def _services():
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    from app.radius.services.store_deposits import DepositRequestService
    from app.radius.services.store_withdrawals import WithdrawalRequestService

    return (
        CardUsersMarketplaceService,
        DepositRequestService,
        WithdrawalRequestService,
    )


def _balance(app, card_user_id):
    Market, _, _ = _services()
    with app.app_context():
        wallet = Market(tenant_id=1)._wallet_for_card_user(card_user_id)
        return int(wallet["balance_minor"] or 0)


def _make_card_user(app, *, mobile="0590000001"):
    Market, _, _ = _services()
    with app.app_context():
        user = Market(tenant_id=1).create_card_user(
            display_name="زبون المتجر دعم", mobile=mobile,
        )
        return user["id"]


# ───────────────────────── العرض ─────────────────────────


def test_store_support_page_loads(app, client):
    _auth(client)
    res = client.get("/admin/radius/store-support")
    assert res.status_code == 200, res.get_data(as_text=True)[:500]
    html = res.get_data(as_text=True)
    assert "طلبات الشحن" in html
    assert "محافظ الاستلام" in html


# ───────────────────────── تأكيد الشحن يضيف الرصيد ─────────────────────────


def test_confirm_deposit_credits_wallet(app, client):
    _, Deposit, _ = _services()
    user_id = _make_card_user(app)
    with app.app_context():
        req = Deposit(tenant_id=1).create_request(
            card_user_id=user_id, amount_claimed="12.50",
            method="bank", payer_name="معطي", payer_phone="0590000009",
        )
        req_id = req["id"]

    assert _balance(app, user_id) == 0

    _auth(client)
    res = client.post(
        f"/admin/radius/store-support/deposits/{req_id}/confirm",
        data={"_csrf_token": "t", "note": "تم"},
    )
    assert res.status_code == 302
    assert _balance(app, user_id) == 1250


# ───────────────────────── تأكيد السحب يخصم الرصيد ─────────────────────────


def test_confirm_withdrawal_debits_wallet(app, client):
    Market, _, Withdraw = _services()
    user_id = _make_card_user(app, mobile="0590000002")
    with app.app_context():
        # اشحن المحفظة أولًا ثم أنشئ طلب سحب.
        Market(tenant_id=1).recharge_wallet(
            card_user_id=user_id, amount="20.00", actor="qa",
        )
        req = Withdraw(tenant_id=1).create_request(
            card_user_id=user_id, amount="8.00",
            payee_name="صاحب الحساب", payee_account="123456789",
        )
        req_id = req["id"]

    assert _balance(app, user_id) == 2000

    _auth(client)
    res = client.post(
        f"/admin/radius/store-support/withdrawals/{req_id}/confirm",
        data={"_csrf_token": "t", "note": ""},
    )
    assert res.status_code == 302
    assert _balance(app, user_id) == 1200


# ───────────────────────── إضافة قناة استلام ─────────────────────────


def test_create_payment_method_appears_in_list(app, client):
    _, Deposit, _ = _services()
    _auth(client)
    res = client.post(
        "/admin/radius/store-support/payment-methods",
        data={
            "_csrf_token": "t",
            "method": "jawaly_pay",
            "label": "محفظة جوالي باي الرئيسية",
            "account_name": "المدير",
            "account_number": "0599999999",
            "instructions": "حوّل ثم ارفع الوصل.",
            "sort_order": "1",
        },
    )
    assert res.status_code == 302
    with app.app_context():
        methods = Deposit(tenant_id=1).list_payment_methods()
    labels = [m["label"] for m in methods]
    assert "محفظة جوالي باي الرئيسية" in labels
    created = next(m for m in methods if m["label"] == "محفظة جوالي باي الرئيسية")
    assert created["method"] == "jawaly_pay"
    assert created["account_number"] == "0599999999"
