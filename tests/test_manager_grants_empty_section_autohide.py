"""Auto-hide of «empty» sections (Stage 5).

A section the manager can neither view (no reachable view endpoint / RBAC perm)
nor act in (no granted action) nor edit (no granted field) is auto-hidden:
absent from the sidebar AND 403 on its URL — even without the owner setting it
«مخفي». A single capability (a reachable view endpoint, a role perm, a flag
action, or a field grant) reveals it.

Note (non-breaking): sections whose endpoints are OPEN to any admin (e.g.
subscribers' /users list, cards' offer-use) are always reachable, so they are
NOT auto-hidden — this preserves the manager's default card-sales workflow.
Auto-hide bites the fully perm-gated sections (reports, finance, …). The owner
can still force ANY section «مخفي» explicitly (Stage 1).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_autohide.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _login(client, *, admin_id, is_super, perms=()):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ helper unit ════════════════════════════════════════════════════════════
def test_fully_gated_empty_section_is_hidden(app):
    from app.radius.services import manager_grants as mg

    with app.app_context():
        m = _mgr("m_calc")
        # reports is fully perm-gated → empty for a no-perm manager → hidden
        assert mg.effective_section_hidden(m, "reports", tenant_id=1, perms=[]) is True
        # holding the view perm reveals it
        assert mg.effective_section_hidden(m, "reports", tenant_id=1,
                                           perms=["reports.view"]) is False


def test_open_section_not_autohidden(app):
    from app.radius.services import manager_grants as mg

    with app.app_context():
        m = _mgr("m_open")
        # subscribers has open endpoints (/users) → reachable → NOT auto-hidden
        assert mg.effective_section_hidden(m, "subscribers", tenant_id=1, perms=[]) is False


def test_capability_via_flag_reveals_gated_section(app):
    from app.radius.services import manager_grants as mg
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

    with app.app_context():
        m = _mgr("m_flag")
        # distributors is fully gated (reports.finance); grant the flag → reveal
        assert mg.effective_section_hidden(m, "distributors", tenant_id=1, perms=[]) is True
        ManagerDistributorOpsService(tenant_id=1).set_policy(
            entity_type="manager", entity_id=m,
            permissions={"can_manage_distributors": True})
    # fresh request/context (real flow redirects after save) → fresh grants cache
    with app.app_context():
        assert mg.effective_section_hidden(m, "distributors", tenant_id=1, perms=[]) is False


# ═══ server 403 on empty section URL ════════════════════════════════════════
def test_empty_gated_section_403_on_url(app):
    with app.app_context():
        m = _mgr("m_403")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=[])   # no capability in reports
        assert c.get("/admin/radius/reports").status_code == 403


def test_capability_reveals_url(app):
    with app.app_context():
        m = _mgr("m_ok")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=["reports.view"])
        assert c.get("/admin/radius/reports").status_code == 200


# ═══ sidebar absence / presence ═════════════════════════════════════════════
def test_empty_gated_section_absent_from_sidebar(app):
    with app.app_context():
        m = _mgr("m_side")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=[])
        html = c.get("/admin/radius/").get_data(as_text=True)
    assert 'data-hb-section="reports"' not in html


def test_gated_section_present_when_capable(app):
    with app.app_context():
        m = _mgr("m_side2")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=["reports.view"])
        html = c.get("/admin/radius/").get_data(as_text=True)
    assert 'data-hb-section="reports"' in html


# ═══ owner/super unaffected ═════════════════════════════════════════════════
def test_super_sees_everything(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=[])
        assert c.get("/admin/radius/reports").status_code == 200
        html = c.get("/admin/radius/").get_data(as_text=True)
    assert 'data-hb-section="reports"' in html
