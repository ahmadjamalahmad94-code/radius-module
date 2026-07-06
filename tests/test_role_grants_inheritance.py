"""وراثة الأفعال والرؤية من الدور (2026-07).

المالك يَضبط «الأفعال المسموح بها» و«نطاق الرؤية» على **الدور** مرّة، فيَرثها
كلّ مدير من دوره تلقائيًّا — دون ضبط كلّ مدير على حِدة. التجاوز الفرديّ يَغلب،
والحدود الرقميّة تبقى فرديّة، وغياب أساس الدور = السلوك الحاليّ بلا تغيير.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rolegrants_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _mk_manager_with_role(role_granular: dict | None):
    from app.radius.db.repos import admins_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    role = admins_repo.create_role(name="store_ops", display_name="Store Ops",
                                   permissions=("store.view",))
    if role_granular is not None:
        admins_repo.set_role_granular(role.id, role_granular)
    admin = admins_repo.create_admin(username="mgr_role", password="p",
                                     full_name="Mgr", is_super_admin=False,
                                     role_id=role.id)
    return role, admin


def test_manager_inherits_action_from_role(app):
    with app.app_context():
        from app.radius.services import manager_grants as mg
        _role, admin = _mk_manager_with_role(
            {"action_grants": {"_actions": {"storeuser.create": True}}})
        # لا سياسة فرديّة للمدير — يَرث الفعل من دوره (افتراض الفعل False).
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is True


def test_manager_inherits_scope_flag_from_role(app):
    with app.app_context():
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        _role, admin = _mk_manager_with_role(
            {"flags": {"can_view_all_subscribers": True}})
        svc = ManagerDistributorOpsService(tenant_id=1)
        assert svc.has_permission(entity_type="manager", entity_id=admin.id,
                                  permission="can_view_all_subscribers") is True
        # علَم لم يُمنَح على الدور يبقى False
        assert svc.has_permission(entity_type="manager", entity_id=admin.id,
                                  permission="can_see_profit") is False


def test_manager_override_beats_role(app):
    with app.app_context():
        from app.radius.services import manager_grants as mg
        _role, admin = _mk_manager_with_role(
            {"action_grants": {"_actions": {"storeuser.create": True}}})
        # الدور يَمنح الفعل؛ تجاوز فرديّ صريح بالمنع يَغلب.
        mg.set_action_override(admin.id, "storeuser.create", False, tenant_id=1)
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is False


def test_empty_role_grants_no_change(app):
    """غياب أساس الدور = السلوك الحاليّ: الفعل يبقى على افتراضه (False)."""
    with app.app_context():
        from app.radius.services import manager_grants as mg
        _role, admin = _mk_manager_with_role(None)
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is False


def test_limits_are_not_inherited(app):
    """الحدود الرقميّة لا تُورَث من الدور — تبقى فرديّة."""
    with app.app_context():
        from app.radius.services import manager_grants as mg
        # حتى لو وُضع حدّ في أساس الدور، لا يَظهر في limits المدير.
        _role, admin = _mk_manager_with_role(
            {"limits": {"max_subscribers": 5}})
        row = mg._grants_row(admin.id, 1)
        assert (row.get("limits") or {}).get("max_subscribers") in (None, 0)


def test_manager_inherits_section_from_role(app):
    """وصول الأقسام يُضبَط على الدور ويَرثه المدير (subscribers→hidden)."""
    with app.app_context():
        from app.radius.services import manager_grants as mg
        _role, admin = _mk_manager_with_role(
            {"section_access": {"cards": "hidden"}})
        assert mg.get_section_access(admin.id, tenant_id=1).get("cards") == "hidden"


def test_reset_overrides_returns_to_role(app):
    """تصفير التجاوزات يُزيل تجاوز المدير فيَعود لوراثة دوره."""
    with app.app_context():
        from app.radius.services import manager_grants as mg
        _role, admin = _mk_manager_with_role(
            {"action_grants": {"_actions": {"storeuser.create": True}}})
        mg.set_action_override(admin.id, "storeuser.create", False, tenant_id=1)
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is False
        mg.reset_overrides_to_role(admin.id, tenant_id=1)
        # عاد لوراثة الدور (True)
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is True


# ─────────────────────────── HTTP: محرّر أساس الدور ───────────────────────────

def _super(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "tk"


def test_role_grants_page_renders(app):
    from app.radius.db.repos import admins_repo, tenants_repo
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        role = admins_repo.create_role(name="editor_role", display_name="Editor",
                                       permissions=("store.view",))
    c = app.test_client()
    _super(c)
    res = c.get(f"/admin/radius/roles/{role.id}/grants", follow_redirects=False)
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "نطاق الرؤية" in body and "الأفعال المسموح بها" in body


def test_role_grants_save_persists_and_manager_inherits(app):
    from app.radius.db.repos import admins_repo, tenants_repo
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        role = admins_repo.create_role(name="save_role", display_name="Saver",
                                       permissions=("store.view",))
        admin = admins_repo.create_admin(username="mgr_http", password="p",
                                         full_name="M", is_super_admin=False,
                                         role_id=role.id)
    c = app.test_client()
    _super(c)
    # احفظ على الدور: علَم رؤية + فعل متجر
    res = c.post(f"/admin/radius/roles/{role.id}/grants",
                 data={"_csrf_token": "tk",
                       "can_view_all_subscribers": "1",
                       "action_storeuser.create": "1",
                       "section_cards": "hidden"},
                 follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.services import manager_grants as mg
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        blob = admins_repo.get_role_granular(role.id)
        assert blob.get("flags", {}).get("can_view_all_subscribers") is True
        assert blob.get("section_access", {}).get("cards") == "hidden"
        # المدير يَرث الكلّ دون أي ضبط فرديّ
        assert mg.action_permitted(admin.id, "storeuser.create", tenant_id=1) is True
        assert mg.get_section_access(admin.id, tenant_id=1).get("cards") == "hidden"
        svc = ManagerDistributorOpsService(tenant_id=1)
        assert svc.has_permission(entity_type="manager", entity_id=admin.id,
                                  permission="can_view_all_subscribers") is True
