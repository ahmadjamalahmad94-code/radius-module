"""اختبارات تحسين قنوات الدفع — شعار القناة (logo) عبر الخدمة + نقطة
الزبون + لوحة المدير (رفع شعار، تعديل، عرض). يكمّل
test_store_finance/test_store_advanced_api دون تكرار.
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile

import pytest

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_chan_")
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


def _auth(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "admin"
        s["admin_name"] = "Admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "t"


# ───────────────────────── الخدمة ─────────────────────────


def test_service_logo_in_create_and_public(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        d = DepositRequestService(tenant_id=1)
        m = d.create_payment_method(
            method="jawaly_pay", label="جوال باي",
            account_number="771000111",
            logo_image_path="uploads/store/logo/a.png")
        assert m["logo_image_url"] == "/static/uploads/store/logo/a.png"
        pub = d.public_payment_methods()
        assert pub[0]["logo_image_url"] == "/static/uploads/store/logo/a.png"
        # تعديل الشعار
        d.update_payment_method(m["id"], logo_image_path="uploads/store/logo/b.png")
        assert d.get_payment_method(m["id"])["logo_image_url"].endswith("b.png")


def test_logo_optional_defaults_empty(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        d = DepositRequestService(tenant_id=1)
        m = d.create_payment_method(method="bank", label="بنك فلسطين")
        assert m["logo_image_url"] == ""


# ───────────────────────── نقطة الزبون ─────────────────────────


def test_store_payment_methods_endpoint_exposes_logo(app, client):
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        from app.radius.services.store_deposits import DepositRequestService
        CardUsersMarketplaceService(tenant_id=1).create_card_user(
            display_name="زبون قناة", mobile="0590000099", password="pw1234")
        DepositRequestService(tenant_id=1).create_payment_method(
            method="palpay", label="بال باي", account_number="0599",
            logo_image_path="uploads/store/logo/c.png")
    tok = client.post("/api/v1/store/login",
                      json={"mobile": "0590000099", "password": "pw1234"}
                      ).get_json()["data"]["token"]
    res = client.get("/api/v1/store/payment-methods",
                     headers={"Authorization": "Bearer " + tok})
    assert res.status_code == 200
    item = res.get_json()["data"]["items"][0]
    assert item["logo_image_url"] == "/static/uploads/store/logo/c.png"
    assert item["account_number"] == "0599"


# ───────────────────────── لوحة المدير ─────────────────────────


def test_admin_create_channel_with_logo_upload(app, client):
    _auth(client)
    res = client.post(
        "/admin/radius/store-support/payment-methods",
        data={
            "_csrf_token": "t", "method": "jawaly_pay",
            "label": "محفظة جوال باي", "account_number": "771222333",
            "logo_image": (io.BytesIO(_PNG), "logo.png"),
            "qr_image": (io.BytesIO(_PNG), "qr.png"),
        },
        content_type="multipart/form-data")
    assert res.status_code == 302
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        methods = DepositRequestService(tenant_id=1).list_payment_methods()
        assert len(methods) == 1
        assert methods[0]["logo_image_url"].startswith("/static/uploads/store/logo/")
        assert methods[0]["qr_image_url"].startswith("/static/uploads/store/qr/")


def test_admin_edit_channel_updates_fields(app, client):
    _auth(client)
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        m = DepositRequestService(tenant_id=1).create_payment_method(
            method="bank", label="بنك قديم", account_number="111")
        mid = m["id"]
    res = client.post(
        f"/admin/radius/store-support/payment-methods/{mid}",
        data={"_csrf_token": "t", "label": "بنك فلسطين",
              "account_number": "999888", "method": "bank"},
        content_type="multipart/form-data")
    assert res.status_code == 302
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        m2 = DepositRequestService(tenant_id=1).get_payment_method(mid)
        assert m2["label"] == "بنك فلسطين"
        assert m2["account_number"] == "999888"
