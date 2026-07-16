# -*- coding: utf-8 -*-
"""مدير فرعيّ + إنشاء مشترك: لا «طريق مسدود».

قبل الإصلاح: النموذج (GET users_new) يفتح بصلاحية users.create لكن الحفظ
(POST users_create) يُرفَض 403 لأنّه محكوم بمنحة «إنشاء مشترك» المنفصلة —
فيملأ المدير البيانات ثم يُرفَض. بعد الإصلاح: فتح النموذج محكوم بنفس المنحة
(gate_get) فيكون الحارس متّسقًا (يُمنع مبكرًا/يُخفى الزرّ)، ومنح المنحة يُتيح
الاثنين معًا. شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "smc.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo, admins_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return application


def _make_submanager(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.create_admin(username="submgr", password="x",
                                     full_name="مدير فرعي", is_super_admin=False)
        return a.id if hasattr(a, "id") else a


def _grant_create(app, admin_id):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as tx:
            tx.execute(
                "INSERT INTO manager_distributor_policies(tenant_id,entity_type,entity_id,"
                "permissions_json,created_at) VALUES(1,'manager',?,?,datetime('now'))",
                (admin_id, '{"can_create_subscriber": true}'))


def _client(app, admin_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=admin_id, is_super_admin=False, tenant_id=1, admin_name="m",
                 permissions=["users.view", "users.create"], _csrf_token="tok")
    return c


def test_without_grant_form_and_save_both_blocked_consistently(app):
    """لا طريق مسدود: النموذج والحفظ كلاهما 403 (لا يفتح ثم يُرفَض)."""
    aid = _make_submanager(app)
    c = _client(app, aid)
    assert c.get("/admin/radius/users/new").status_code == 403       # was 200 (the bug)
    r = c.post("/admin/radius/users",
               data={"username": "u1", "password": "p", "_csrf_token": "tok"})
    assert r.status_code == 403


def test_with_grant_form_opens_and_save_succeeds(app):
    aid = _make_submanager(app)
    _grant_create(app, aid)
    c = _client(app, aid)
    assert c.get("/admin/radius/users/new").status_code == 200
    r = c.post("/admin/radius/users",
               data={"username": "okuser", "password": "p", "_csrf_token": "tok"},
               follow_redirects=False)
    assert r.status_code in (302, 303)                                # created
    with app.app_context():
        from app.radius.db.connection import db
        assert db().execute(
            "SELECT COUNT(*) c FROM subscribers WHERE username='okuser'"
        ).fetchone()["c"] == 1


def test_action_registry_gates_users_new(app):
    """users_new صار فعلًا محكومًا بـ subscriber.create مع gate_get."""
    from app.radius.services import manager_grants as mg
    assert mg.endpoint_action("users_new") == "subscriber.create"
    assert mg.ACTION_REGISTRY["subscriber.create"].get("gate_get") is True


def test_super_admin_unaffected(app):
    aid = _make_submanager(app)
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=aid, is_super_admin=True, tenant_id=1, admin_name="m",
                 permissions=[], _csrf_token="tok")
    assert c.get("/admin/radius/users/new").status_code == 200
