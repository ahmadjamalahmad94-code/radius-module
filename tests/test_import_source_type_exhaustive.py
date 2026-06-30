"""EXHAUSTIVE coverage — «نوع المصدر» is the single control; both paths differ.

  * the «بدون مزامنة» toggle is gone; the radio drives the sync flag;
  * external → accounting/inventory ONLY (card row, NO real auth account);
  * imported → a real authenticatable account is created/synced;
  * proven at BOTH the route level (full radio→flag→behaviour wiring) and the
    service level.
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
    db_file = os.path.join(tmp_path, "src_type_exhaustive.db")
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
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
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
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["cards.view"]


def _import(client, *, plan_id, source_type, username):
    return client.post("/admin/radius/cards/batches/import", data={
        "_csrf_token": "off-csrf", "plan_id": str(plan_id), "source_type": source_type,
        "csv_text": f"username,password\n{username},pw1\n", "price_per_card": "2.00",
        "package_name": f"pkg_{username}"}, follow_redirects=False)


def _subscriber_exists(username: str) -> bool:
    return db().execute(
        "SELECT 1 FROM subscribers WHERE tenant_id=1 AND username=? AND deleted_at IS NULL",
        (username,)).fetchone() is not None


def _card_exists(username: str) -> bool:
    return db().execute("SELECT 1 FROM cards WHERE tenant_id=1 AND username=?",
                        (username,)).fetchone() is not None


def _batch_source(username: str):
    return db().execute(
        "SELECT b.source_type st FROM card_batches b JOIN cards c ON c.batch_id=b.id "
        "WHERE c.username=?", (username,)).fetchone()["st"]


# ═══ route level — radio choice genuinely drives behaviour ══════════════════
def test_external_route_no_auth_account(app):
    with app.app_context():
        plan = _plan_id()
    with app.test_client() as c:
        _login_owner(c)
        assert _import(c, plan_id=plan, source_type="external", username="ext1").status_code in (302, 303)
    with app.app_context():
        assert _card_exists("ext1") is True              # inventory card
        assert _subscriber_exists("ext1") is False       # but NO real account
        assert _batch_source("ext1") == "external"


def test_imported_route_creates_auth_account(app):
    with app.app_context():
        plan = _plan_id()
    with app.test_client() as c:
        _login_owner(c)
        assert _import(c, plan_id=plan, source_type="imported", username="imp1").status_code in (302, 303)
    with app.app_context():
        assert _card_exists("imp1") is True
        assert _subscriber_exists("imp1") is True        # real authenticatable account
        assert _batch_source("imp1") == "imported"


# ═══ service level — flag really gates account creation ═════════════════════
def test_service_external_skips_sync(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        r = get_cards_service().import_batch(
            actor="t", plan_id=plan, source_type="external", sync_to_radius=True,
            cards=[{"username": "sx", "password": "p"}])
        # external is forced accounting-only even if a caller asks to sync.
        assert r["radius_sync_enabled"] is False
        assert r["radius_synced_count"] == 0
        assert _subscriber_exists("sx") is False


def test_service_imported_with_sync_creates_account(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        r = get_cards_service().import_batch(
            actor="t", plan_id=plan, source_type="imported", sync_to_radius=True,
            cards=[{"username": "sy", "password": "p"}])
        assert r["radius_sync_enabled"] is True
        assert r["radius_synced_count"] == 1
        assert _subscriber_exists("sy") is True


# ═══ UI — toggle gone, both option cards present + non-contradictory ════════
def test_page_has_no_sync_toggle_but_both_options(app):
    with app.test_client() as c:
        _login_owner(c)
        html = c.get("/admin/radius/cards/batches/import").get_data(as_text=True)
    assert 'name="sync_to_radius"' not in html
    assert "بدون مزامنة" not in html
    assert "ملف خارجي" in html                            # external card
    assert "داخل خدمة المصادقة" in html                   # imported card
    # the external card no longer claims it creates accounts (non-contradictory).
    assert "للحساب فقط" in html
