"""S1 — Q (programming) + R (login designer) pages are reachable
from the UI without the operator typing the URL.

Two navigation surfaces:
  - Per-router dashboard hero/quicknav strip
  - Operations Center fleet-row action column
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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
    u = f"s1_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="s1-pass", full_name="S1 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s1-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    """Names + addresses are unique-per-tenant; derive both from
    nas_id so a single test can seed multiple rows without
    colliding."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        name = f"s1-rtr-{nas_id}"
        addr = f"203.0.113.{(nas_id % 250) + 1}"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, name, addr, now),
            )


# ─── Dashboard quick-nav strip ────────────────────────────────


def test_dashboard_has_quicknav_strip(app, client):
    _seed(app, nas_id=42)
    _login(client)
    res = client.get("/admin/radius/mt/42/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-router-quicknav" in html


def test_dashboard_quicknav_no_longer_shows_program_card(app, client):
    """يونيو 2026 — طلب المالك: أُزيلت بطاقة «برمجة الشبكة» من شبكة
    خدمات لوحة الراوتر. الوظيفة تُغطّيها بطاقات الخدمات المستقلّة
    (الهوت سبوت/البرودباند/خدمات المنافذ). الـroute mt_program_form
    يبقى مُسجَّلًا لمن يصل لها مباشرةً — لكن سطح المستخدم لا يَعرضها."""
    _seed(app, nas_id=42)
    _login(client)
    html = client.get("/admin/radius/mt/42/dashboard").get_data(as_text=True)
    assert 'data-mt-router-link="program"' not in html
    assert "برمجة الشبكة" not in html


def test_dashboard_quicknav_links_to_login_designer(app, client):
    _seed(app, nas_id=42)
    _login(client)
    html = client.get("/admin/radius/mt/42/dashboard").get_data(as_text=True)
    assert 'data-mt-router-link="login-designer"' in html
    assert 'href="/admin/radius/mt/42/login-designer"' in html
    assert "مصمّم صفحة الدخول" in html


def test_dashboard_quicknav_links_to_fleet_diagnostics(app, client):
    """The fleet diagnostics page (the one with the repair scripts
    + connection-mode badges from O5) is per-tenant, not per-router,
    so the link doesn't carry nas_id."""
    _seed(app, nas_id=42)
    _login(client)
    html = client.get("/admin/radius/mt/42/dashboard").get_data(as_text=True)
    assert 'data-mt-router-link="diagnostics"' in html
    assert '/admin/radius/diagnostics' in html


def test_dashboard_quicknav_uses_real_router_id(app, client):
    """Regression: it'd be easy to hard-code nas_id=1 in the
    template. Verify the surviving cards carry the actual id.

    تحديث يونيو 2026: بطاقة «برمجة الشبكة» أُزيلت، لذا نتحقّق من
    بطاقتَين باقيتَين تحملان nas_id في الرابط: «مصمّم صفحة الدخول»
    و«النسخ الاحتياطية»."""
    _seed(app, nas_id=777)
    _login(client)
    html = client.get("/admin/radius/mt/777/dashboard").get_data(as_text=True)
    assert "/admin/radius/mt/777/login-designer" in html
    assert "/admin/radius/mt/777/backups" in html


# ─── Operations Center fleet rows ─────────────────────────────


def test_operations_row_drops_program_link(app, client):
    """قرار التصميم: صف العمليات يُبقي فقط (خدمات/تعديل/مصمم/الطاقة)؛
    «برمجة الشبكة» انتقلت إلى تبويب «خدماتي» بلوحة الراوتر، فلا تظهر
    كأيقونة في الصف بعد الآن. اللوحة نفسها ما زالت تربط للبرمجة."""
    _seed(app, nas_id=13)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert 'data-mt-row-link="program"' not in html
    # مصمّم صفحة الدخول يبقى أحد الإجراءات الأربعة المعتمدة.
    assert 'data-mt-row-link="login-designer"' in html


def test_operations_row_links_to_login_designer(app, client):
    _seed(app, nas_id=13)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert 'data-mt-row-link="login-designer"' in html
    assert "/admin/radius/mt/13/login-designer" in html


def test_operations_row_links_use_each_routers_id(app, client):
    """كل راوتر في الأسطول يجب أن يحصل على رابط مصمّمه الخاص — خطأ في
    القوالب قد يثبّت id واحدًا. (رابط «برمجة» أُزيل من الصف بقرار
    التصميم، فبقي مصمّم الدخول وحده مرجعًا لفحص تفرّد المعرّف.)"""
    _seed(app, nas_id=11)
    _seed(app, nas_id=22)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    for nas_id in (11, 22):
        assert f"/admin/radius/mt/{nas_id}/login-designer" in html
