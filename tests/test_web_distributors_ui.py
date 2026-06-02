"""Web UI smoke tests for distributor management screens."""
from __future__ import annotations

import secrets

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_plans(
                    id, tenant_id, name, code, plan_type, service_type,
                    duration_minutes, validity_days, speed_down_kbps,
                    speed_up_kbps, price, currency, enabled, created_at
                )
                VALUES(1,1,'Web UI Test Plan','WEBUI','time','Hotspot',
                       1440,1,4000,2000,1,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from uuid import uuid4

    from app.radius.db.repos import admins_repo

    username = f"dist_web_{uuid4().hex[:10]}"
    password = "dist-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Distributor Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    res = client.get("/admin/radius/distributors/new")
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _auth_headers(client) -> dict:
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _batch(client) -> dict:
    prefix = "distui" + secrets.token_hex(4)
    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": prefix},
        headers=_auth_headers(client),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["batch"]


def test_distributors_web_routes_are_login_guarded(client):
    res = client.get("/admin/radius/distributors", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_distributors_web_form_uses_choice_controls_not_json_fields(client):
    _web_login(client)
    res = client.get("/admin/radius/distributors/new")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'type="checkbox" name="permissions"' in html
    assert 'type="hidden" name="scope_json"' in html
    assert "نطاق البيانات (JSON)" not in html
    assert "hub-textarea mono" not in html
    assert "cards.read, cards.sell" not in html
    assert "اختر الصلاحيات من القائمة" in html


def test_distributors_web_create_detail_assign_and_settle(client):
    _web_login(client)
    token = _csrf(client)
    batch = _batch(client)
    name = "dist_ui_" + secrets.token_hex(5)

    created = client.post(
        "/admin/radius/distributors",
        data={
            "_csrf_token": token,
            "name": name,
            "display_name": "موزع اختبار",
            "phone": "0590000000",
            "email": "dist@example.test",
            "status": "active",
            "permissions": "cards.read, cards.sell",
            "scope_json": '{"card_batches":"assigned"}',
            "balance": "0",
            "credit_limit": "100",
            "debt_balance": "0",
            "notes": "web ui smoke",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    detail_url = created.headers["Location"]

    detail = client.get(detail_url)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert name in html
    assert "الحزم المربوطة" in html
    assert "cards.read" in html

    distributor_id = int(detail_url.rstrip("/").split("/")[-1])
    assigned = client.post(
        f"/admin/radius/distributors/{distributor_id}/assign-batch",
        data={"_csrf_token": token, "batch_id": batch["id"], "notes": "test assignment"},
        follow_redirects=True,
    )
    assert assigned.status_code == 200
    assigned_html = assigned.get_data(as_text=True)
    assert batch["batch_code"] in assigned_html
    assert "test assignment" in assigned_html

    settled = client.post(
        f"/admin/radius/distributors/{distributor_id}/settle",
        data={"_csrf_token": token, "amount": "10", "direction": "debit", "notes": "manual debt"},
        follow_redirects=True,
    )
    assert settled.status_code == 200
    assert "تم تسجيل حركة الموزع" in settled.get_data(as_text=True)
