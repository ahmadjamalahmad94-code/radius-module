from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_payments_web_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    yield create_app()

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"payments_web_{uuid4().hex[:10]}"
    password = "payments-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Payments Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_payment_collection_web_routes_are_login_guarded(client):
    res = client.get("/admin/radius/payments/settings", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_settings_page_renders_and_saves(client):
    _web_login(client)
    page = client.get("/admin/radius/payments/settings")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "مركز تحصيل الدفعات" in html
    assert "رقم المحفظة لتوجيه الدفع فقط" in html
    assert "معاينة بدون تنفيذ" in html
    assert "Jawwal Pay (لاحقًا)" not in html
    assert "واجهة الربط الآلي (لاحقًا)" not in html
    assert "غير مفعل حاليًا" in html

    saved = client.post(
        "/admin/radius/payments/settings",
        data={
            "_csrf_token": _csrf(client, "/admin/radius/payments/settings"),
            "enabled": "1",
            "provider": "manual_wallet",
            "wallet_number": "0599000000",
            "wallet_owner_name": "Hobe Wallet",
            "currency": "ILS",
            "confirmation_mode": "manual",
            "allow_cards": "1",
            "allow_monthly_subscriptions": "1",
            "allow_distributor_payments": "1",
            "payment_request_ttl_minutes": "1440",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert "0599000000" in saved.get_data(as_text=True)


def test_requests_list_and_detail_render_without_paid_apply_buttons(client):
    _web_login(client)
    from app.radius.db.repos.payments_repo import PaymentRequestRepository

    request = PaymentRequestRepository().create(
        tenant_id=1,
        payer_type="subscriber",
        payer_id=99,
        purpose="card_purchase",
        amount=12,
        currency="ILS",
        provider="manual_wallet",
        receiver_wallet="0599000000",
    )

    page = client.get("/admin/radius/payments/requests")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert request["reference_code"] in html
    assert "طلبات الدفع" in html

    detail = client.get(f"/admin/radius/payments/requests/{request['id']}")
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert request["reference_code"] in detail_html
    assert "إثباتات الدفع" in detail_html
    assert "Record service apply" not in detail_html
    assert "Mark paid" not in detail_html


def test_payment_collection_reconciliation_page_renders(client):
    _web_login(client)
    response = client.get("/admin/radius/payments/reconciliation")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "مطابقة تحصيل الدفعات" in html
    assert "مدفوع بلا قيد مالي" in html
