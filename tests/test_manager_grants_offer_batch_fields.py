"""Granular per-manager control on OFFER and BATCH edit (Stage 3).

Offer/batch edit is owner-only by default. The owner may open it to a specific
manager (action grant «edit») and then restrict WHICH fields that manager can
change (field grants). Ungranted fields are ignored SERVER-SIDE; card-structure
fields stay locked regardless [[batch-edit-owner-only-structural-lock]]; owner
bypasses. Default (no grant) => manager still gets 403 (non-regressive).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_grants_offer_batch.db")
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
                                 full_name="Owner", is_super_admin=True)  # min-id owner
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ──────────────────────────────────────────────────────────────
def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name=f"M {username}", is_super_admin=False)
    return int(adm.id)


def _plan(name="P") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _offer(plan_id: int):
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService(tenant_id=1).create_offer(
        name="Orig Offer", duration_minutes=60, wholesale=5, selling=10,
        plan_id=plan_id, created_by="test",
    )


def _batch(plan_id: int, *, count: int = 4):
    from app.radius.services.cards import get_cards_service

    batch, _ = get_cards_service().generate_batch(
        actor="test", plan_id=plan_id, count=count, username_length=8,
        password_length=6, password_charset="digits",
        password_generation_type="medium", price_per_card=2.0,
        time_value=1, time_unit="days", package_name="Orig Pkg",
    )
    return batch


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["cards.view", "cards.edit_batch", "cards.generate",
                               "reports.finance"]


def _grant_edit(mgr: int, entity: str, allow=True):
    from app.radius.services import manager_grants as mg

    mg.set_action_grants(mgr, entity, {"edit": True} if allow else None, tenant_id=1)


def _grant_fields(mgr: int, entity: str, fields):
    from app.radius.services import manager_grants as mg

    mg.set_field_grants(mgr, entity, fields, tenant_id=1)


def _col(bid, col):
    return db().execute(f"SELECT {col} FROM card_batches WHERE id=?", (bid,)).fetchone()[col]


# ═══ registry ═══════════════════════════════════════════════════════════════
def test_registry_has_offer_and_batch(app):
    from app.radius.services import manager_grants as mg

    assert "name" in mg.field_keys("offer") and "price" in mg.field_keys("offer")
    assert "name" in mg.field_keys("batch") and "accounting" in mg.field_keys("batch")


# ═══ OFFER ══════════════════════════════════════════════════════════════════
def test_offer_edit_denied_without_action_grant(app):
    with app.app_context():
        mgr = _sub_admin("m_off_no"); p = _plan(); off = _offer(p)
        oid = int(off["id"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(f"/admin/radius/cards/offers/{oid}/edit",
                          data={"_csrf_token": "off-csrf", "name": "Hacked"})
        assert res.status_code == 403


def test_offer_edit_granted_name_only(app):
    with app.app_context():
        mgr = _sub_admin("m_off_ok"); p1 = _plan("P1"); p2 = _plan("P2")
        off = _offer(p1); oid = int(off["id"])
        orig_selling = int(off["selling_minor"])
        _grant_edit(mgr, "offer"); _grant_fields(mgr, "offer", ["name"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(f"/admin/radius/cards/offers/{oid}/edit",
                          data={"_csrf_token": "off-csrf", "name": "New Offer",
                                "duration_minutes": "120", "selling": "99",
                                "wholesale": "50", "plan_id": str(p2),
                                "currency": "JOD"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        off2 = CardOffersService(tenant_id=1).get_offer(oid)
        assert off2["name"] == "New Offer"                 # granted
        assert int(off2["selling_minor"]) == orig_selling  # price NOT granted → kept
        assert int(off2["plan_id"]) == p1                  # plan NOT granted → kept
        assert int(off2["duration_minutes"]) == 60         # duration NOT granted → kept


def test_offer_owner_can_change_all(app):
    with app.app_context():
        p1 = _plan("P1"); p2 = _plan("P2"); off = _offer(p1); oid = int(off["id"])
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(f"/admin/radius/cards/offers/{oid}/edit",
                          data={"_csrf_token": "off-csrf", "name": "Owner New",
                                "duration_minutes": "120", "selling": "99",
                                "wholesale": "50", "plan_id": str(p2), "currency": "JOD"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        off2 = CardOffersService(tenant_id=1).get_offer(oid)
        assert off2["name"] == "Owner New"
        assert int(off2["plan_id"]) == p2
        assert int(off2["duration_minutes"]) == 120


# ═══ BATCH ══════════════════════════════════════════════════════════════════
def test_batch_edit_denied_without_action_grant(app):
    with app.app_context():
        mgr = _sub_admin("m_b_no"); p = _plan(); b = _batch(p); bid = b.id
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        # both GET and POST are owner-only unless granted
        assert client.get(f"/admin/radius/cards/batches/{bid}/edit").status_code == 403
        res = client.post(f"/admin/radius/cards/batches/{bid}/edit",
                          data={"_csrf_token": "off-csrf", "package_name": "Hacked"})
        assert res.status_code == 403


def test_batch_edit_granted_name_only(app):
    with app.app_context():
        mgr = _sub_admin("m_b_ok"); p1 = _plan("P1"); p2 = _plan("P2")
        b = _batch(p1); bid = b.id
        _grant_edit(mgr, "batch"); _grant_fields(mgr, "batch", ["name"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        assert client.get(f"/admin/radius/cards/batches/{bid}/edit").status_code == 200
        res = client.post(f"/admin/radius/cards/batches/{bid}/edit",
                          data={"_csrf_token": "off-csrf", "package_name": "New Pkg",
                                "price_per_card": "99", "plan_id": str(p2),
                                "status": "active"})
    assert res.status_code in (302, 303)
    with app.app_context():
        assert _col(bid, "package_name") == "New Pkg"        # granted
        assert float(_col(bid, "price_per_card")) == 2.0     # price NOT granted → kept
        assert int(_col(bid, "plan_id")) == p1               # plan NOT granted → kept


def test_batch_edit_granted_price_changes_price(app):
    with app.app_context():
        mgr = _sub_admin("m_b_price"); p1 = _plan("P1")
        b = _batch(p1); bid = b.id
        _grant_edit(mgr, "batch"); _grant_fields(mgr, "batch", ["price"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(f"/admin/radius/cards/batches/{bid}/edit",
                          data={"_csrf_token": "off-csrf", "package_name": "ShouldStay",
                                "price_per_card": "9.5"})
    assert res.status_code in (302, 303)
    with app.app_context():
        assert float(_col(bid, "price_per_card")) == 9.5      # granted
        assert _col(bid, "package_name") == "Orig Pkg"        # name NOT granted → kept


def test_batch_structural_count_stays_locked_even_when_granted(app):
    with app.app_context():
        mgr = _sub_admin("m_b_struct"); p1 = _plan("P1")
        b = _batch(p1, count=4); bid = b.id
        _grant_edit(mgr, "batch"); _grant_fields(mgr, "batch", ["name"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        client.post(f"/admin/radius/cards/batches/{bid}/edit",
                    data={"_csrf_token": "off-csrf", "package_name": "New Pkg",
                          "count": "999"})
    with app.app_context():
        assert int(_col(bid, "count")) == 4                   # structural → never changes


# ═══ config route persists action + field grants for offer/batch ════════════
def test_policy_route_persists_offer_edit_action_and_fields(app):
    with app.app_context():
        mgr = _sub_admin("m_cfg")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            f"/admin/radius/business-operators/manager/{mgr}/policy",
            data={"_csrf_token": "off-csrf",
                  "action_edit_offer": "1",
                  "field_control_offer": "1",
                  "field_offer_name": "1"},
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.action_allowed(mgr, "offer", "edit", tenant_id=1) is True
        assert mg.field_grants(mgr, "offer", tenant_id=1) == {"name"}
        # batch edit not granted in this POST
        assert mg.action_allowed(mgr, "batch", "edit", tenant_id=1) is False
