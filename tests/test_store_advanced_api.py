"""اختبارات نقاط المتجر المتقدّم عبر HTTP — إيداع/سحب/شات/محافظ
الاستلام، بمصادقة توكن الزبون (نفس مسار صفحة store.html).

تتحقّق أن النقاط تُنشئ طلبات فقط (لا تحرّك مالًا)، وأن الرفع والتصفية
والاستطلاع تعمل. الحركة المالية الفعلية مغطّاة في test_store_finance.
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile

import pytest

# PNG 1×1 صالح (بايتات سحرية صحيحة) لاختبار رفع صورة الوصل.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_adv_")
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
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _token(client, app, mobile="0590000055"):
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        CardUsersMarketplaceService(tenant_id=1).create_card_user(
            display_name="زبون متقدم", mobile=mobile, password="pw1234")
    res = client.post("/api/v1/store/login",
                      json={"mobile": mobile, "password": "pw1234"})
    return res.get_json()["data"]["token"], res.get_json()["data"]["card_user"]["id"]


def _auth(token):
    return {"Authorization": "Bearer " + token}


# ───────────────────────── محافظ الاستلام ─────────────────────────


def test_payment_methods_public_listing(app, client):
    token, _ = _token(client, app)
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        dep = DepositRequestService(tenant_id=1)
        m = dep.create_payment_method(method="jawaly_pay", label="جوالي باي",
                                      account_number="777999")
        inactive = dep.create_payment_method(method="bank", label="بنك")
        dep.update_payment_method(inactive["id"], active=0)
    res = client.get("/api/v1/store/payment-methods", headers=_auth(token))
    assert res.status_code == 200, res.get_json()
    items = res.get_json()["data"]["items"]
    assert len(items) == 1  # المعطّلة مخفية عن الزبون
    assert items[0]["account_number"] == "777999"
    assert items[0]["method_ar"] == "جوالي باي"


# ───────────────────────── الإيداع ─────────────────────────


def test_deposit_create_with_receipt_and_list(app, client):
    token, _ = _token(client, app)
    data = {
        "amount_claimed": "30.00", "method": "jawaly_pay",
        "payer_phone": "0590000055", "reference": "OP-7788",
        "payer_name": "زبون متقدم",
        "receipt": (io.BytesIO(_PNG), "receipt.png"),
    }
    res = client.post("/api/v1/store/deposits", headers=_auth(token),
                      data=data, content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    req = res.get_json()["data"]["request"]
    assert req["status"] == "pending"
    assert req["amount_claimed"] == "30.00"
    assert req["receipt_image_url"].startswith("/static/uploads/store/receipts/")
    # القائمة تُظهر الطلب للزبون
    lst = client.get("/api/v1/store/deposits", headers=_auth(token))
    assert lst.status_code == 200
    items = lst.get_json()["data"]["items"]
    assert len(items) == 1 and items[0]["id"] == req["id"]


def test_deposit_rejects_bad_image(app, client):
    token, _ = _token(client, app)
    data = {
        "amount_claimed": "10.00", "method": "bank",
        "receipt": (io.BytesIO(b"not-an-image"), "x.png"),
    }
    res = client.post("/api/v1/store/deposits", headers=_auth(token),
                      data=data, content_type="multipart/form-data")
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "upload_failed"


def test_deposit_does_not_credit_wallet(app, client):
    token, uid = _token(client, app)
    client.post("/api/v1/store/deposits", headers=_auth(token),
                data={"amount_claimed": "50.00", "method": "bank"},
                content_type="multipart/form-data")
    me = client.get("/api/v1/store/me", headers=_auth(token))
    assert me.get_json()["data"]["wallet"]["balance"] == "0.00"  # لا حركة


# ───────────────────────── السحب ─────────────────────────


def test_withdrawal_insufficient_balance_rejected(app, client):
    token, _ = _token(client, app)
    res = client.post("/api/v1/store/withdrawals", headers=_auth(token),
                      json={"amount": "10.00", "payee_name": "زبون",
                            "payee_account": "ACC9"})
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "withdrawal_failed"


def test_withdrawal_create_after_recharge_and_list(app, client):
    token, uid = _token(client, app)
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        CardUsersMarketplaceService(tenant_id=1).recharge_wallet(
            card_user_id=uid, amount="40.00", actor="qa")
    res = client.post("/api/v1/store/withdrawals", headers=_auth(token),
                      json={"amount": "25.00", "payee_name": "زبون",
                            "payee_account": "ACC9"})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["request"]["status"] == "pending"
    # لا خصم قبل تأكيد المدير
    me = client.get("/api/v1/store/me", headers=_auth(token))
    assert me.get_json()["data"]["wallet"]["balance"] == "40.00"
    lst = client.get("/api/v1/store/withdrawals", headers=_auth(token))
    assert len(lst.get_json()["data"]["items"]) == 1


# ───────────────────────── الشات ─────────────────────────


def test_chat_post_and_poll(app, client):
    token, _ = _token(client, app)
    post = client.post("/api/v1/store/chat", headers=_auth(token),
                       data={"body": "مرحبًا، عندي استفسار"},
                       content_type="multipart/form-data")
    assert post.status_code == 201, post.get_json()
    poll = client.get("/api/v1/store/chat", headers=_auth(token))
    assert poll.status_code == 200
    items = poll.get_json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["sender"] == "customer"
    assert items[0]["body"] == "مرحبًا، عندي استفسار"


def test_chat_rejects_empty_message(app, client):
    token, _ = _token(client, app)
    res = client.post("/api/v1/store/chat", headers=_auth(token),
                      data={"body": ""}, content_type="multipart/form-data")
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "chat_failed"
