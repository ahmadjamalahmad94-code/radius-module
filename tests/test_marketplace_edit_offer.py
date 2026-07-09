"""«تعديل العرض» — edit an existing card-marketplace offer.

Covers the edit flow surfaced on /admin/radius/card-marketplace, reusing the
marketplace offer model (card_marketplace_packages) + the new update_package
service + the store.package_add-gated route:
  * success (owner) edits the safe fields and persists,
  * status «الحالة» toggle (فعّال ↔ موقوف),
  * server-side validation rejects bad input (name / price / plan),
  * STRUCTURAL/identity fields (card credential format) stay LOCKED — the edit
    can never reshape them even if the form smuggles those params,
  * permission boundary: owner/granted-manager can edit; a view-only manager
    (store.view only) is blocked (403),
  * the «تعديل العرض» action shows for creators, hidden from view-only managers.
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
    db_file = os.path.join(tmp_path, "marketplace_edit_offer.db")
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


def _svc():
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1)


def _plan_id(name="Marketplace 8h", minutes=8 * 60) -> int:
    cur = db().execute(
        """INSERT INTO access_plans(
               tenant_id, name, duration_minutes, validity_days, price, currency,
               created_at, updated_at)
           VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, name, minutes, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _make_offer(app, **overrides):
    """Create a base offer and return (offer_dict, plan_id)."""
    with app.app_context():
        pid = _plan_id()
        kwargs = dict(
            name="عرض 8 ساعات",
            plan_id=pid,
            price="5.00",
            duration_minutes=8 * 60,
            speed_down_kbps=2048,
            speed_up_kbps=512,
            sale_mode="instant",
            password_charset="mixed",   # structural identity → must stay locked
            username_length=8,
            password_length=10,
        )
        kwargs.update(overrides)
        offer = _svc().create_package(**kwargs)
    return offer, pid


def _login(client, *, is_super=True, permissions=None, admin_id=1):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["is_super_admin"] = is_super
        s["tenant_id"] = 1
        s["permissions"] = list(permissions or [])
        s["_csrf_token"] = "mkt-csrf"


def _edit_url(pid):
    return f"/admin/radius/card-marketplace/packages/{pid}/edit"


def _edit_form(plan, **overrides):
    form = {
        "_csrf_token": "mkt-csrf",
        "name": "عرض مُعدّل",
        "plan_id": str(plan),
        "price": "7.50",
        "duration_minutes": str(4 * 60),
        "speed_down_kbps": "4096",
        "speed_up_kbps": "1024",
        "sale_mode": "instant",
        "active": "1",
    }
    form.update(overrides)
    return form


# ── success ───────────────────────────────────────────────────────────
def test_edit_offer_success_owner(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post(_edit_url(offer["id"]),
                 data=_edit_form(pid, name="عرض جديد الاسم", price="9.00",
                                 speed_up_kbps="2048", active="1"))
    assert res.status_code in (302, 303)
    with app.app_context():
        updated = _svc().get_package(offer["id"])
        assert updated["name"] == "عرض جديد الاسم"
        assert float(updated["price"]) == 9.0
        assert int(updated["speed_up_kbps"]) == 2048
        assert int(updated["speed_down_kbps"]) == 4096
        assert int(updated["active"]) == 1


def test_edit_offer_status_toggle_to_paused(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post(_edit_url(offer["id"]), data=_edit_form(pid, active="0"))
    assert res.status_code in (302, 303)
    with app.app_context():
        assert int(_svc().get_package(offer["id"])["active"]) == 0


# ── locked structural identity ────────────────────────────────────────
def test_edit_offer_leaves_card_structure_locked(app):
    """Editing an offer must NOT change its card credential format (charset +
    username/password lengths) even if those fields are smuggled into the POST."""
    _bind(app)
    offer, pid = _make_offer(app)
    with app.app_context():
        before = _svc().get_package(offer["id"])["card_format"]
    c = app.test_client()
    _login(c, is_super=True)
    # Smuggle structural params the edit path must ignore.
    res = c.post(_edit_url(offer["id"]),
                 data=_edit_form(pid, name="محاولة تغيير البنية",
                                 password_charset="digits",
                                 username_length="4", password_length="4"))
    assert res.status_code in (302, 303)
    with app.app_context():
        after = _svc().get_package(offer["id"])
        # safe field changed…
        assert after["name"] == "محاولة تغيير البنية"
        # …but the card structure is unchanged (locked)
        assert after["card_format"] == before
        assert after["card_format"]["password_charset"] == "mixed"
        assert int(after["card_format"]["username_length"]) == 8
        assert int(after["card_format"]["password_length"]) == 10


# ── validation ────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {"name": ""},          # missing name
    {"price": "0"},        # non-positive price
    {"plan_id": "99999"},  # non-existent base plan
])
def test_edit_offer_validation_rejects(app, bad):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=True)
    res = c.post(_edit_url(offer["id"]), data=_edit_form(pid, **bad))
    assert res.status_code in (302, 303)   # route catches + flashes, no change
    with app.app_context():
        after = _svc().get_package(offer["id"])
        assert after["name"] == "عرض 8 ساعات"     # unchanged
        assert float(after["price"]) == 5.0


# ── permission boundary ───────────────────────────────────────────────
def test_edit_offer_forbidden_for_view_only_manager(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=False, permissions=["store.view"], admin_id=2)
    res = c.post(_edit_url(offer["id"]), data=_edit_form(pid, name="لن يُحفظ"))
    assert res.status_code == 403
    with app.app_context():
        assert _svc().get_package(offer["id"])["name"] == "عرض 8 ساعات"


def test_edit_offer_allowed_for_granted_manager(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=False,
           permissions=["store.view", "store.package_add"], admin_id=3)
    res = c.post(_edit_url(offer["id"]), data=_edit_form(pid, name="عدّله المدير"))
    assert res.status_code in (302, 303)
    with app.app_context():
        assert _svc().get_package(offer["id"])["name"] == "عدّله المدير"


# ── UI entry point ────────────────────────────────────────────────────
def test_edit_action_visible_for_owner(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=True)
    body = c.get("/admin/radius/card-marketplace").get_data(as_text=True)
    assert "تعديل العرض" in body
    assert f'action="/admin/radius/card-marketplace/packages/{offer["id"]}/edit"' in body


def test_edit_action_hidden_for_view_only_manager(app):
    _bind(app)
    offer, pid = _make_offer(app)
    c = app.test_client()
    _login(c, is_super=False, permissions=["store.view"], admin_id=2)
    res = c.get("/admin/radius/card-marketplace")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "تعديل العرض" not in body
    assert "/edit" not in body
