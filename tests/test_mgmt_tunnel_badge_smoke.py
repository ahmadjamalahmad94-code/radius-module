"""Smoke render for the derived management-tunnel badge on mt_operations."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mgmt_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "X" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "1.2.3.4:51820")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"mg_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="mg-pass", full_name="MG Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "mg-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id: int, name: str, check: str = "",
              enabled: bool = True) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     ros_version, last_check_status, last_check_at,
                     created_at)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, 'hr-test', 'pw', '7.14', ?, ?, ?)""",
                (nas_id, name, f"10.10.0.{nas_id}", int(enabled),
                 check, now, now),
            )


def test_badge_renders_reachable(app, client):
    # صفّ معطّل (لا يُستطلَع حيًّا) ⇒ تُشتقّ الشارة من فحص الوصول.
    _seed_nas(app, nas_id=21, name="rt-up", check="reachable", enabled=False)
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-mgmt-state=\"active\"" in html
    assert "نفق فعّال" in html
    # العمود الميّت لم يعد يُعرض كنصّ مبهم
    assert "لا نفق إدارة مُعدّ" not in html


def test_badge_renders_down(app, client):
    # صفّ معطّل + فحص منتهي المهلة ⇒ «النفق متوقف» (لا استطلاع حيّ).
    _seed_nas(app, nas_id=22, name="rt-down", check="timeout", enabled=False)
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-mgmt-state=\"down\"" in html
    assert "النفق متوقف" in html


def test_enabled_row_defers_to_live_poll(app, client):
    """حارس الانحدار للتناقض: راوتر مفعّل بفحص TCP قديم منتهٍ يجب ألا
    يُرسَم «النفق متوقف» خادميًّا — يؤجَّل للاستطلاع الحيّ («جارٍ الفحص…»)
    كي لا يتناقض مع عمود «الحالة» الحيّ."""
    _seed_nas(app, nas_id=23, name="rt-live", check="timeout", enabled=True)
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-mgmt-state=\"checking\"" in html
    assert "جارٍ الفحص…" in html
    # لا تأكيد «متوقف» خادميًّا للصف الحيّ.
    assert "data-mt-mgmt-state=\"down\"" not in html
