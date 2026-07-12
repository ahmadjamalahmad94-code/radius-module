"""Subscribers list: «آخر تجديد» column shows the date of the latest renewal
(extend_time / change_plan) for each subscriber, sourced from audit_log.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "last_renewal.db")
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
    return flask_app


def _auth(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "lr_admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "lr-csrf"


def test_last_renewal_column_shows_latest_renewal_date(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.connection import db
        from app.radius.db.repos import audit_repo, subscribers_repo

        for name in ("renewed_user", "fresh_user"):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, tenant_id=1, username=name, password="pw1234",
                status="enabled", user_type="subscriber"))

        # two renewals for renewed_user — the column must show the LATEST
        audit_repo.record(tenant_id=1, actor="admin", action="extend_time",
                          target_type="user", target_id="renewed_user")
        audit_repo.record(tenant_id=1, actor="admin", action="change_plan",
                          target_type="user", target_id="renewed_user")
        db().execute("UPDATE audit_log SET created_at=? WHERE action='extend_time'",
                     ("2026-02-01T09:00:00Z",))
        db().execute("UPDATE audit_log SET created_at=? WHERE action='change_plan'",
                     ("2026-05-20T09:00:00Z",))
        db().commit()

    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/subscribers")

    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "آخر تجديد" in html                       # header present
    assert 'data-col="last_renewal"' in html          # column wired (th + td)
    assert "2026-05-20" in html                       # latest renewal (not 02-01)
    assert "2026-02-01" not in html                   # older one is NOT shown
