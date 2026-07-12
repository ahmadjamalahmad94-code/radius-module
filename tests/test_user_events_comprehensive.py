"""سجل الأحداث (/reports/user_events) must surface EVERY subscriber action —
add time (extend_time), edit (update), and «فتح سرعة» (temporary_speed.apply,
logged under target_type='subscriber') — while NOT leaking subscriber login /
portal noise (also target_type='subscriber').
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "user_events.db")
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
        s["admin_user"] = "ev_admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "ev-csrf"


def _rec(action, target_type, target_id):
    from app.radius.db.repos import audit_repo
    audit_repo.record(tenant_id=1, actor="admin", action=action,
                      target_type=target_type, target_id=target_id)


def test_user_events_shows_all_subscriber_actions_but_not_login_noise(app):
    with app.app_context():
        _rec("create", "user", "ev_create")
        _rec("extend_time", "user", "ev_extend")          # was EXCLUDED
        _rec("update", "user", "ev_update")               # was EXCLUDED
        _rec("subscriber.cash_balance_add", "user", "ev_cash")
        _rec("subscriber.quota_topup", "user", "ev_quota")
        _rec("disable", "user", "ev_disable")
        _rec("temporary_speed.apply", "subscriber", "ev_speed")   # was INVISIBLE
        _rec("login", "subscriber", "ev_login_noise")     # must stay OUT

    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/user_events")

    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # every subscriber admin action now appears
    for tid in ("ev_create", "ev_extend", "ev_update", "ev_cash",
                "ev_quota", "ev_disable", "ev_speed"):
        assert tid in html, f"missing subscriber action target {tid}"
    # owner-friendly labels for the newly surfaced ones
    assert "فتح سرعة" in html                 # temporary_speed.apply
    assert "إضافة وقت" in html                 # extend_time
    # subscriber login/portal noise (target_type='subscriber', non-speed) stays out
    assert "ev_login_noise" not in html
