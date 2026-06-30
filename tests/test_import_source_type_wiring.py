"""«نوع المصدر» is the single control driving import behaviour.

The redundant «بدون مزامنة خدمة المصادقة» toggle was removed; the radio choice
alone now drives the sync flag server-side:

  * external  → accounting/inventory ONLY — no real auth accounts, no sync;
  * imported  → real authenticatable accounts created/synced in the auth service.

Auth/fixture pattern mirrors test_import_perm_dryrun.py.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "import_source_type.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(
            username="owner_root", password="x12345678", full_name="Owner",
            is_super_admin=True,
        )
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "باقة", 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _login_owner(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["cards.view"]


def _import(client, *, plan_id, source_type, username):
    return client.post("/admin/radius/cards/batches/import", data={
        "_csrf_token": "off-csrf",
        "plan_id": str(plan_id),
        "source_type": source_type,
        "csv_text": f"username,password\n{username},pw1\n",
        "price_per_card": "2.00",
        "package_name": f"pkg_{username}",
    }, follow_redirects=False)


def _subscriber_exists(username: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM subscribers WHERE tenant_id=1 AND username=? AND deleted_at IS NULL",
        (username,),
    ).fetchone()
    return row is not None


def _card_exists(username: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM cards WHERE tenant_id=1 AND username=?", (username,),
    ).fetchone()
    return row is not None


# ═══ the «بدون مزامنة» toggle is gone; behaviour derives from the radio ═════
def test_external_source_creates_no_auth_account(app):
    with app.app_context():
        plan = _plan_id()
    with app.test_client() as client:
        _login_owner(client)
        res = _import(client, plan_id=plan, source_type="external", username="ext1")
    assert res.status_code in (302, 303)
    with app.app_context():
        # inventory card exists, but NO real auth account.
        assert _card_exists("ext1") is True
        assert _subscriber_exists("ext1") is False


def test_imported_source_creates_real_auth_account(app):
    with app.app_context():
        plan = _plan_id()
    with app.test_client() as client:
        _login_owner(client)
        res = _import(client, plan_id=plan, source_type="imported", username="imp1")
    assert res.status_code in (302, 303)
    with app.app_context():
        # imported into the auth service → a real, authenticatable account exists.
        assert _card_exists("imp1") is True
        assert _subscriber_exists("imp1") is True


def test_import_page_has_no_sync_toggle(app):
    with app.test_client() as client:
        _login_owner(client)
        html = client.get("/admin/radius/cards/batches/import").get_data(as_text=True)
    # the redundant toggle is removed; both source cards remain.
    assert 'name="sync_to_radius"' not in html
    assert "بدون مزامنة" not in html
    assert "ملف خارجي" in html and "داخل خدمة المصادقة" in html
