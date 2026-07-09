"""«إنشاء عرض جديد» — create-offer entry point on the card marketplace page.

Covers the create flow surfaced on /admin/radius/card-marketplace, reusing the
existing marketplace offer model (card_marketplace_packages) + create_package
service + the store.package_add-gated route:
  * success (owner) creates an active offer that shows in the grid,
  * the «الحالة» status field can create a paused (موقوف) offer,
  * server-side validation rejects bad input (name / price / plan),
  * permission boundary: owner/granted-manager can create; a manager with only
    store.view (generate-only) is blocked (403),
  * the «إنشاء عرض جديد» button is shown to creators, hidden from view-only
    managers.
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


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations
    run_pending_migrations()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "marketplace_create_offer.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    flask_app.config["_DB_FILE"] = db_file
    return flask_app


def _bind(app):
    os.environ["HOBERADIUS_DB_PATH"] = app.config["_DB_FILE"]


def _plan_id() -> int:
    cur = db().execute(
        """INSERT INTO access_plans(
               tenant_id, name, duration_minutes, validity_days, price, currency,
               created_at, updated_at)
           VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, "Marketplace 8h", 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _login(client, *, is_super: bool = True, permissions=None, admin_id: int = 1):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = "mkt_admin"
        s["admin_name"] = "Market Admin"
        s["is_super_admin"] = is_super
        s["tenant_id"] = 1
        s["permissions"] = list(permissions or [])
        s["_csrf_token"] = "mkt-csrf"


def _valid_form(plan, **overrides):
    """Build a valid create-offer form; `overrides` win (incl. plan_id)."""
    form = {
        "_csrf_token": "mkt-csrf",
        "name": "8 ساعات / 2 ميجا",
        "plan_id": str(plan),
        "price": "5.00",
        "duration_minutes": str(8 * 60),
        "speed_down_kbps": "2048",
        "speed_up_kbps": "512",
        "sale_mode": "instant",
        "active": "1",
    }
    form.update(overrides)
    return form


def _packages(active_only=True):
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1).list_packages(active_only=active_only, limit=200)


def _pkg_count():
    return db().execute("SELECT COUNT(*) n FROM card_marketplace_packages").fetchone()["n"]


# ── success ───────────────────────────────────────────────────────────
def test_create_offer_success_owner(app):
    _bind(app)
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post("/admin/radius/card-marketplace/packages",
                 data=_valid_form(pid, name="عرض 8 ساعات"))
    assert res.status_code in (302, 303)   # redirects back to the marketplace
    with app.app_context():
        rows = _packages()
        assert any(p["name"] == "عرض 8 ساعات" for p in rows)
        offer = next(p for p in rows if p["name"] == "عرض 8 ساعات")
        assert int(offer["plan_id"]) == pid
        assert float(offer["price"]) == 5.0
        assert int(offer.get("active", 1)) == 1


def test_create_offer_paused_status(app):
    """The «الحالة» field can create a paused (موقوف) offer — active=0."""
    _bind(app)
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post("/admin/radius/card-marketplace/packages",
                 data=_valid_form(pid, name="عرض موقوف", active="0"))
    assert res.status_code in (302, 303)
    with app.app_context():
        row = db().execute(
            "SELECT active FROM card_marketplace_packages WHERE name=?",
            ("عرض موقوف",)).fetchone()
        assert row is not None and int(row["active"]) == 0
        # a paused offer is hidden from the active (buyer-facing) listing
        assert not any(p["name"] == "عرض موقوف" for p in _packages(active_only=True))


# ── validation ────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {"name": ""},              # missing name
    {"price": "0"},            # non-positive price
    {"plan_id": "0"},          # missing/invalid base plan
    {"plan_id": "99999"},      # non-existent base plan
])
def test_create_offer_validation_rejects(app, bad):
    _bind(app)
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post("/admin/radius/card-marketplace/packages",
                 data=_valid_form(pid, **bad))
    # route catches the service error and flashes → redirect, nothing created
    assert res.status_code in (302, 303)
    with app.app_context():
        assert _pkg_count() == 0


# ── permission boundary ───────────────────────────────────────────────
def test_create_offer_forbidden_for_view_only_manager(app):
    """A manager who can only VIEW/generate (store.view, no store.package_add)
    is blocked from creating an offer — the owner/manager boundary."""
    _bind(app)
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    _login(c, is_super=False, permissions=["store.view"], admin_id=2)
    res = c.post("/admin/radius/card-marketplace/packages",
                 data=_valid_form(pid))
    assert res.status_code == 403
    with app.app_context():
        assert _pkg_count() == 0


def test_create_offer_allowed_for_granted_manager(app):
    """A manager explicitly granted store.package_add can create offers."""
    _bind(app)
    with app.app_context():
        pid = _plan_id()
    c = app.test_client()
    _login(c, is_super=False,
           permissions=["store.view", "store.package_add"], admin_id=3)
    res = c.post("/admin/radius/card-marketplace/packages",
                 data=_valid_form(pid, name="عرض المدير"))
    assert res.status_code in (302, 303)
    with app.app_context():
        assert any(p["name"] == "عرض المدير" for p in _packages())


# ── UI entry point visibility ─────────────────────────────────────────
def test_create_button_visible_for_owner(app):
    _bind(app)
    c = app.test_client()
    _login(c, is_super=True)
    body = c.get("/admin/radius/card-marketplace").get_data(as_text=True)
    # visible create label (button + modal title) + the create form posting to
    # the existing route — one product/offer per the marketplace model.
    assert "إنشاء عرض جديد" in body
    assert 'action="/admin/radius/card-marketplace/packages"' in body


def test_create_button_hidden_for_view_only_manager(app):
    _bind(app)
    c = app.test_client()
    _login(c, is_super=False, permissions=["store.view"], admin_id=2)
    res = c.get("/admin/radius/card-marketplace")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # view-only manager sees the marketplace but NEITHER the create label NOR
    # the create form (the trigger button + modal are gated away). The JS still
    # references the selector, so assert on the visible label + the form action.
    assert "إنشاء عرض جديد" not in body
    assert 'action="/admin/radius/card-marketplace/packages"' not in body
