"""«راوتر الباقة» (router_ids) — باقةٌ مقيّدة براوتر لا تُفتح إلّا منه.

طلب المالك (2026-10-29): قائمة منسدلة في الباقة نختار منها راوترًا من
الراوترات المضافة؛ حين يُختار لا تعمل البطاقات/المشتركون على الباقة إلّا عليه،
وحين لا يُختار شيء تعمل على كلّ الراوترات.

تَغطّي:
  • باقة بلا راوتر ⇒ الدخول من أيّ راوتر (السلوك القديم)
  • باقة براوتر ⇒ سماح منه، رفض router_not_allowed من راوترٍ آخر/مجهول/بلا NAS-IP
  • الرفض رفضُ سياسة (لا fail2ban) ولا يُسرّب صحّة كلمة المرور
  • «اختبار المصادقة» (simulate) بلا NAS-IP لا يُرفض
  • راوترٌ محذوف (soft delete) لا يُحتسَب
  • نموذج الويب: القائمة تحفظ/تمسح الراوتر، وحفظٌ بلا الحقل يُبقيه
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "plan_router.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield app


def _nas(name: str, address: str) -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
        "enabled, created_at) VALUES (1, ?, ?, 's', 'mikrotik', 1, ?)",
        (name, address, datetime.utcnow().isoformat()))
    return int(cur.lastrowid)


def _plan(router_ids=()) -> int:
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    p = plans_repo.upsert_plan(AccessPlan(
        id=None, name=f"P{len(router_ids)}-{'-'.join(map(str, router_ids))}",
        tenant_id=1, enabled=True, router_ids=tuple(router_ids)))
    return int(p.id)


def _sub(username: str, plan_id: int):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, username=username, password="pw1", tenant_id=1,
        status="enabled", plan_id=plan_id))


def _auth(username: str, nas_ip: str = "", password: str = "pw1",
          simulate: bool = False):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=password,
                                 tenant_id=1, nas_ip=nas_ip,
                                 simulate=simulate))


class TestPolicyEngine:

    def test_no_router_selected_opens_everywhere(self, app_ctx):
        _nas("R1", "10.0.0.1")
        _sub("u_all", _plan(()))
        assert _auth("u_all", "10.0.0.1").ok
        assert _auth("u_all", "10.9.9.9").ok
        assert _auth("u_all", "").ok

    def test_locked_plan_allows_its_router(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        _sub("u_lock", _plan((r1,)))
        assert _auth("u_lock", "10.0.0.1").ok

    def test_locked_plan_rejects_other_router(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        _nas("R2", "10.0.0.2")
        _sub("u_lock", _plan((r1,)))
        d = _auth("u_lock", "10.0.0.2")
        assert not d.ok
        assert d.reason == "router_not_allowed"

    def test_locked_plan_rejects_unknown_router_and_missing_nas_ip(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        _sub("u_lock", _plan((r1,)))
        assert _auth("u_lock", "10.7.7.7").reason == "router_not_allowed"
        assert _auth("u_lock", "").reason == "router_not_allowed"

    def test_simulate_without_nas_ip_is_not_rejected(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        _sub("u_lock", _plan((r1,)))
        assert _auth("u_lock", "", simulate=True).reason != "router_not_allowed"

    def test_wrong_password_still_reports_password(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        _nas("R2", "10.0.0.2")
        _sub("u_lock", _plan((r1,)))
        assert _auth("u_lock", "10.0.0.2", password="bad").reason == "password_wrong"

    def test_router_lock_is_policy_not_fail2ban(self, app_ctx):
        from app.radius.services.policy_engine import _FAIL2BAN_REASONS
        assert "router_not_allowed" not in _FAIL2BAN_REASONS

    def test_soft_deleted_router_does_not_count(self, app_ctx):
        from app.radius.db.connection import db
        r1 = _nas("R1", "10.0.0.1")
        _sub("u_lock", _plan((r1,)))
        db().execute("UPDATE nas_devices SET deleted_at = ? WHERE id = ?",
                     (datetime.utcnow().isoformat(), r1))
        assert _auth("u_lock", "10.0.0.1").reason == "router_not_allowed"

    def test_any_of_several_routers(self, app_ctx):
        r1 = _nas("R1", "10.0.0.1")
        r2 = _nas("R2", "10.0.0.2")
        _nas("R3", "10.0.0.3")
        _sub("u_two", _plan((r1, r2)))
        assert _auth("u_two", "10.0.0.2").ok
        assert _auth("u_two", "10.0.0.3").reason == "router_not_allowed"


class TestWebForm:

    def test_form_to_dto_reads_router(self, app_ctx):
        from app.radius.routes import plans as plans_routes
        r1 = _nas("R1", "10.0.0.1")
        with app_ctx.test_request_context(
                "/radius/plans", method="POST",
                data={"name": "X", "router_id": str(r1)}):
            assert plans_routes._form_to_dto().router_ids == (r1,)
        with app_ctx.test_request_context(
                "/radius/plans", method="POST",
                data={"name": "X", "router_id": ""}):
            assert plans_routes._form_to_dto().router_ids == ()

    def test_foreign_router_id_is_ignored(self, app_ctx):
        from app.radius.routes import plans as plans_routes
        with app_ctx.test_request_context(
                "/radius/plans", method="POST",
                data={"name": "X", "router_id": "9999"}):
            assert plans_routes._form_to_dto().router_ids == ()

    def test_merge_keeps_routers_when_field_absent_or_keep(self, app_ctx):
        from app.radius.db.repos import plans_repo
        from app.radius.routes import plans as plans_routes
        r1 = _nas("R1", "10.0.0.1")
        r2 = _nas("R2", "10.0.0.2")
        pid = _plan((r1, r2))
        existing = plans_repo.get_plan(1, pid)
        for data in ({"name": "X"}, {"name": "X", "router_id": "__keep__"}):
            with app_ctx.test_request_context(
                    f"/radius/plans/{pid}", method="POST", data=data):
                merged = plans_routes._merge_web_form(
                    existing, plans_routes._form_to_dto(plan_id=pid))
                assert merged.router_ids == (r1, r2)

    def test_merge_clears_routers_when_all_chosen(self, app_ctx):
        from app.radius.db.repos import plans_repo
        from app.radius.routes import plans as plans_routes
        r1 = _nas("R1", "10.0.0.1")
        pid = _plan((r1,))
        existing = plans_repo.get_plan(1, pid)
        with app_ctx.test_request_context(
                f"/radius/plans/{pid}", method="POST",
                data={"name": "X", "router_id": ""}):
            merged = plans_routes._merge_web_form(
                existing, plans_routes._form_to_dto(plan_id=pid))
            assert merged.router_ids == ()


# ── الصفحة + مسار FreeRADIUS الحقيقيّ (/api/v1/internal/auth) ──
@pytest.fixture
def web_app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "plan_router_web.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("FLASK_SECRET", "plan-router-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_pr")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-pr")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


def _login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_pr", "password": "owner-pass-pr"})
    assert res.status_code in {302, 303}, res.status_code


class TestPageAndRadiusPath:

    def test_new_plan_page_lists_routers(self, web_app):
        _nas("Tower-North", "10.0.0.1")
        c = web_app.test_client()
        _login(c)
        html = c.get("/admin/radius/plans/new").get_data(as_text=True)
        assert 'name="router_id"' in html
        assert "Tower-North (10.0.0.1)" in html

    def test_edit_page_preselects_router(self, web_app):
        r1 = _nas("Tower-North", "10.0.0.1")
        pid = _plan((r1,))
        c = web_app.test_client()
        _login(c)
        html = c.get(f"/admin/radius/plans/{pid}/edit").get_data(as_text=True)
        assert f'<option value="{r1}" selected>' in html

    def test_internal_auth_rejects_other_router(self, web_app):
        r1 = _nas("R1", "10.0.0.1")
        _nas("R2", "10.0.0.2")
        _sub("u_api", _plan((r1,)))
        c = web_app.test_client()

        def auth(nas_ip):
            return c.post("/api/v1/internal/auth", json={
                "User-Name": "u_api", "User-Password": "pw1",
                "Calling-Station-Id": "AA:BB:CC:00:00:09",
                "NAS-IP-Address": nas_ip, "NAS-Port-Type": "Wireless-802.11"})

        bad = auth("10.0.0.2")
        assert bad.status_code == 200
        body = bad.get_json()
        assert body.get("control:Auth-Type") == "Reject"
        assert "غير متاحة على هذه الشبكة" in body.get("reply:Reply-Message", "")
        good = auth("10.0.0.1").get_json()
        assert good.get("control:Auth-Type") != "Reject"
