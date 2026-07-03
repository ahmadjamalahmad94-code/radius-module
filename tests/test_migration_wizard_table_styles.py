"""Migration wizard tables use the unified hub-table design-system component.

Owner report: the migration section's tables (recent-jobs, sample preview,
plan/reconcile result, errors) did NOT follow the panel's table rules and the
column headers were misaligned with their cell data. Fix: all four tables now
use .hub-table / .hub-table-wrap (which centers both th and td so they line up)
and the custom .mig-t table CSS is gone.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "styles.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield app
    reset_for_tests(None)


def _login_owner(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"own_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="pw123456",
                                 full_name="Owner", is_super_admin=True)
    client.post("/admin/radius/login",
                data={"username": u, "password": "pw123456"})
    return client


def _seed_job(app):
    with app.app_context():
        from app.radius.db.repos import migration_jobs_repo
        migration_jobs_repo.create_job(
            tenant_id=1, token=f"t_{uuid4().hex[:8]}", filename="dump.sql.gz",
            fmt="sql_dump", file_path="/tmp/x", size_bytes=1,
            analysis={}, created_by="owner", status="committed")


def test_migrate_page_uses_hub_table_component(app):
    _seed_job(app)                     # makes the server-rendered jobs table appear
    client = _login_owner(app)
    resp = client.get("/admin/radius/migrate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Unified component present (server jobs table + JS builders for sample /
    # plan / errors tables all emit hub-table markup).
    assert "hub-table-wrap" in html
    assert "hub-table" in html


def test_old_custom_table_styles_removed(app):
    _seed_job(app)
    client = _login_owner(app)
    html = client.get("/admin/radius/migrate").get_data(as_text=True)
    # The bespoke .mig-t table CSS + its class usages are gone (they bypassed
    # the design system and caused the header/cell misalignment).
    assert "table.mig-t{" not in html
    assert 'class="mig-t"' not in html
    assert 'class="mig-t mig-jobs"' not in html
    assert 'class="mig-tablewrap"' not in html


def test_jobs_date_cell_no_longer_conflicts_alignment(app):
    # The date cell used dir=ltr + text-align:start while its header was RTL
    # start-aligned → opposite sides. It now rides the centered hub-table cell.
    _seed_job(app)
    client = _login_owner(app)
    html = client.get("/admin/radius/migrate").get_data(as_text=True)
    assert 'dir="ltr" style="text-align:start"' not in html
    assert '<td class="mono">' in html      # date cell centered via hub-table
