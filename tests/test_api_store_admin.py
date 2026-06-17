"""feat/api-first-endpoints — store admin management JSON.

يعكس صفحة /admin/radius/store-support: لوحة الطلبات، CRUD قنوات الاستلام،
وشات الدعم (ردّ المدير + حالة الخيط). شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_storeadmin_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
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


def test_requires_auth(client):
    assert client.get("/api/v1/store/admin/support").status_code == 401


def test_support_dashboard_shape(client):
    res = client.get("/api/v1/store/admin/support", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert set(d) >= {"deposits", "withdrawals", "chat_threads",
                      "chat_unread_count", "payment_methods"}
    assert d["deposits"]["pending"] == [] and d["deposits"]["pending_count"] == 0
    assert d["withdrawals"]["resolved"] == []


def test_payment_method_crud(client):
    # create
    r = client.post("/api/v1/store/admin/payment-methods", headers=AUTH,
                    json={"method": "bank", "label": "بنك فلسطين",
                          "account_name": "متجري", "account_number": "12345",
                          "instructions": "حوّل ثم أرسل الوصل", "sort_order": 1})
    assert r.status_code == 201, r.get_json()
    mid = r.get_json()["data"]["payment_method"]["id"]
    # list
    lst = client.get("/api/v1/store/admin/payment-methods", headers=AUTH).get_json()
    assert any(m["id"] == mid for m in lst["data"]["payment_methods"])
    # update
    u = client.patch(f"/api/v1/store/admin/payment-methods/{mid}", headers=AUTH,
                     json={"label": "محدّث", "active": "0"})
    assert u.status_code == 200 and u.get_json()["data"]["payment_method"]["label"] == "محدّث"
    # delete
    dele = client.delete(f"/api/v1/store/admin/payment-methods/{mid}", headers=AUTH)
    assert dele.status_code == 200 and dele.get_json()["data"]["deleted"] is True
    lst2 = client.get("/api/v1/store/admin/payment-methods", headers=AUTH).get_json()
    assert not any(m["id"] == mid for m in lst2["data"]["payment_methods"])


def test_chat_reply_and_status(client):
    cu = 77
    # admin posts a reply (creates the thread)
    p = client.post(f"/api/v1/store/admin/chat/{cu}", headers=AUTH,
                    json={"body": "كيف أساعدك؟"})
    assert p.status_code == 201, p.get_json()
    assert p.get_json()["data"]["message"]["sender"] == "admin"
    # thread shows it
    t = client.get(f"/api/v1/store/admin/chat/{cu}", headers=AUTH)
    assert t.status_code == 200
    items = t.get_json()["data"]["thread"]["items"]
    assert any(m["body"] == "كيف أساعدك؟" for m in items)
    assert t.get_json()["data"]["status"] == "open"
    # set resolved
    s = client.post(f"/api/v1/store/admin/chat/{cu}/status", headers=AUTH,
                    json={"status": "resolved"})
    assert s.status_code == 200 and s.get_json()["data"]["status"] == "resolved"
    t2 = client.get(f"/api/v1/store/admin/chat/{cu}", headers=AUTH)
    assert t2.get_json()["data"]["status"] == "resolved"


def test_deposit_confirm_missing_422(client):
    r = client.post("/api/v1/store/admin/deposits/99999/confirm", headers=AUTH,
                    json={"note": "x"})
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "store_error"


def test_withdrawal_reject_missing_422(client):
    r = client.post("/api/v1/store/admin/withdrawals/99999/reject", headers=AUTH,
                    json={"note": "x"})
    assert r.status_code == 422
