"""New-subscriber form UX:
 1) باقة الخدمة (plan) is mandatory on create — HTML `required` + server guard.
 2) المدير المسؤول (manager) defaults to the creating admin.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "form.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _admin_client(app_ctx):
    """Authed client whose current admin is a real active admin row; returns
    (client, admin_id)."""
    with app_ctx.app_context():
        from app.radius.db.repos import admins_repo
        admins = [a for a in admins_repo.list_admins()
                  if getattr(a, "status", "active") == "active"]
        aid = int(admins[0].id)          # bootstrap admin
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = aid
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c, aid


def test_new_form_plan_required_and_manager_defaults_to_creator(app_ctx):
    c, aid = _admin_client(app_ctx)
    r = c.get("/admin/radius/users/new")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # (1) plan select is required
    seg = html.split('name="plan_id"', 1)[1][:80]
    assert "required" in seg, "plan_id select must be required on the new form"

    # (2) manager defaults to the creating admin (its option is pre-selected)
    mgr = html.split('name="manager_id"', 1)[1].split("</select>", 1)[0]
    assert f'value="{aid}" selected' in mgr, "creating admin must be the default manager"


def test_new_form_plan_has_forcing_placeholder(app_ctx):
    """The plan select opens on a disabled, non-submittable placeholder so a
    `required` select forces the operator to pick a real plan."""
    c, _ = _admin_client(app_ctx)
    html = c.get("/admin/radius/users/new").get_data(as_text=True)
    seg = html.split('name="plan_id"', 1)[1].split("</select>", 1)[0]
    assert "اختر الباقة" in seg          # forcing placeholder text
    assert "disabled" in seg             # placeholder option is not selectable
