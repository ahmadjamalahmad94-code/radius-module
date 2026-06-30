"""Distributor ownership scoping + the new ``can_manage_distributors`` gate.

Covers the owner's report: a LIMITED (non-super) manager on the card-batch
form was wrongly offered a free «اختر المدير» dropdown and foreign distributors
under «بدون موزع». Server-side enforcement (not just UI hiding):

  1. card-batch manager field auto-set to the limited manager (locked),
  2. card-batch distributor must belong to the relevant manager,
  3. distributors carry a manager-owner (distributors.admin_id),
  4. a new toggleable permission ``can_manage_distributors`` gates add/manage.

Auth/fixture pattern mirrors test_card_offers_plan_owner.py.
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
    db_file = os.path.join(tmp_path, "distributor_scoping_rbac.db")
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
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ────────────────────────────────────────────────────────────────
def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "باقة كروت", 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Manager {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _distributor(*, name: str, admin_id):
    from app.radius.db.repos import operations_repo

    return operations_repo.create_distributor(
        1, {"name": name, "admin_id": admin_id, "status": "active"}, actor="test",
    )


def _grant_manage_distributors(manager_id: int) -> None:
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=manager_id,
        permissions={"can_manage_distributors": True},
    )


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        # Nav-level RBAC perms so the limited manager clears the PRE-EXISTING
        # route guard (distributors writes → reports.finance, batch generate →
        # cards.generate) and our FINER gate (can_manage_distributors / owner
        # scope) is what the assertions actually exercise.
        sess["permissions"] = ["reports.finance", "cards.generate"]


def _latest_batch():
    row = db().execute(
        "SELECT manager_id, distributor_id FROM card_batches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ═══ 1. the new permission exists, defaults OFF, and is toggleable ═══════════
def test_default_permissions_has_can_manage_distributors_off(app):
    from app.radius.services.manager_distributor_ops import DEFAULT_PERMISSIONS

    assert DEFAULT_PERMISSIONS.get("can_manage_distributors") is False


def test_policy_route_persists_can_manage_distributors(app):
    with app.app_context():
        mgr = _sub_admin("mgr_toggle")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            f"/admin/radius/business-operators/manager/{mgr}/policy",
            data={"_csrf_token": "off-csrf", "can_manage_distributors": "1"},
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

        assert ManagerDistributorOpsService(tenant_id=1).has_permission(
            entity_type="manager", entity_id=mgr, permission="can_manage_distributors"
        ) is True


def test_permission_label_is_arabic(app):
    # unified label source (services/permission_labels.py), surfaced as the
    # ``permission_label`` Jinja global on the operator profile page.
    from app.radius.services.permission_labels import permission_label

    assert permission_label("can_manage_distributors") == "إدارة الموزعين"


# ═══ 2. add-distributor gate (server-side 403) ══════════════════════════════
def test_limited_manager_without_perm_cannot_create_distributor(app):
    with app.app_context():
        mgr = _sub_admin("mgr_noperm")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(
            "/admin/radius/distributors",
            data={"_csrf_token": "off-csrf", "name": "dist_x", "status": "active"},
        )
    assert res.status_code == 403
    with app.app_context():
        from app.radius.db.repos import operations_repo

        assert operations_repo.list_distributors(1) == []


def test_limited_manager_with_perm_creates_distributor_owned_by_self(app):
    with app.app_context():
        mgr = _sub_admin("mgr_ok")
        _grant_manage_distributors(mgr)
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        # Even if he tampers admin_id to point at someone else, it is forced to self.
        res = client.post(
            "/admin/radius/distributors",
            data={"_csrf_token": "off-csrf", "name": "dist_self",
                  "status": "active", "admin_id": "99999"},
            follow_redirects=False,
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import operations_repo

        items = operations_repo.list_distributors(1)
        assert len(items) == 1
        assert int(items[0]["admin_id"]) == mgr


def test_super_creates_distributor_with_chosen_owner(app):
    with app.app_context():
        mgr = _sub_admin("mgr_target")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            "/admin/radius/distributors",
            data={"_csrf_token": "off-csrf", "name": "dist_super",
                  "status": "active", "admin_id": str(mgr)},
            follow_redirects=False,
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import operations_repo

        items = operations_repo.list_distributors(1)
        assert len(items) == 1 and int(items[0]["admin_id"]) == mgr


# ═══ 3. distributor list + detail scoping ═══════════════════════════════════
def test_distributor_list_scoped_to_owning_manager(app):
    with app.app_context():
        a = _sub_admin("mgr_a")
        b = _sub_admin("mgr_b")
        _distributor(name="a_dist", admin_id=a)
        _distributor(name="b_dist", admin_id=b)
    with app.test_client() as client:
        _login(client, admin_id=a, is_super=False)
        page = client.get("/admin/radius/distributors")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "a_dist" in html
    assert "b_dist" not in html


def test_limited_manager_cannot_open_foreign_distributor(app):
    with app.app_context():
        a = _sub_admin("mgr_a2")
        b = _sub_admin("mgr_b2")
        foreign = _distributor(name="b_only", admin_id=b)
    with app.test_client() as client:
        _login(client, admin_id=a, is_super=False)
        detail = client.get(f"/admin/radius/distributors/{foreign['id']}")
        edit = client.get(f"/admin/radius/distributors/{foreign['id']}/edit")
    assert detail.status_code == 403
    assert edit.status_code == 403


def test_super_sees_all_distributors(app):
    with app.app_context():
        a = _sub_admin("mgr_a3")
        b = _sub_admin("mgr_b3")
        _distributor(name="a_d3", admin_id=a)
        _distributor(name="b_d3", admin_id=b)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        page = client.get("/admin/radius/distributors")
    html = page.get_data(as_text=True)
    assert "a_d3" in html and "b_d3" in html


# ═══ 4. card-batch form scoping (the reported bug) ══════════════════════════
def _gen_data(plan_id, **extra):
    base = {"_csrf_token": "off-csrf", "plan_id": str(plan_id), "count": "3"}
    base.update({k: str(v) for k, v in extra.items()})
    return base


# NOTE: the role-split (agent/cardgen-offer-accounting) makes the FULL
# /cards/generate form OWNER-ONLY. A sub-manager can no longer POST it (403) —
# he generates via the charged offer flow instead. These tests assert the new
# gate; the distributor owner-scope accept/reject is now exercised via the
# super full-form path (and the offer flow, covered elsewhere).
def test_card_batch_limited_manager_blocked_from_full_form(app):
    with app.app_context():
        plan = _plan_id()
        me = _sub_admin("mgr_batch")
        other = _sub_admin("mgr_other")
    with app.test_client() as client:
        _login(client, admin_id=me, is_super=False)
        res = client.post(
            "/admin/radius/cards/generate",
            data=_gen_data(plan, manager_id=other),
            follow_redirects=False,
        )
    assert res.status_code == 403
    with app.app_context():
        assert _latest_batch() is None


def test_card_batch_foreign_distributor_rejected(app):
    # super attributes a batch to manager A but picks a distributor owned by B
    # → owner-scope mismatch rejected (form re-rendered, no batch).
    with app.app_context():
        plan = _plan_id()
        a = _sub_admin("mgr_f1")
        b = _sub_admin("mgr_f2")
        foreign = _distributor(name="foreign_dist", admin_id=b)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            "/admin/radius/cards/generate",
            data=_gen_data(plan, manager_id=a, distributor_id=foreign["id"]),
            follow_redirects=False,
        )
    assert res.status_code == 200
    with app.app_context():
        assert _latest_batch() is None


def test_card_batch_own_distributor_accepted(app):
    # super path: a distributor owned by the chosen manager is accepted and
    # persisted on the batch (the owner-scope accept branch).
    with app.app_context():
        plan = _plan_id()
        me = _sub_admin("mgr_g1")
        mine = _distributor(name="mine_dist", admin_id=me)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            "/admin/radius/cards/generate",
            data=_gen_data(plan, manager_id=me, distributor_id=mine["id"]),
            follow_redirects=False,
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        batch = _latest_batch()
        assert int(batch["manager_id"]) == me
        assert int(batch["distributor_id"]) == int(mine["id"])


def test_card_batch_super_owner_mismatch_rejected(app):
    with app.app_context():
        plan = _plan_id()
        a = _sub_admin("mgr_s1")
        b = _sub_admin("mgr_s2")
        b_dist = _distributor(name="b_dist_s", admin_id=b)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        # super picks manager A but a distributor owned by B → rejected.
        res = client.post(
            "/admin/radius/cards/generate",
            data=_gen_data(plan, manager_id=a, distributor_id=b_dist["id"]),
            follow_redirects=False,
        )
    assert res.status_code == 200
    with app.app_context():
        assert _latest_batch() is None


def test_card_generate_manager_sees_offer_picker_not_full_form(app):
    # Under the role-split, a sub-manager's /cards/generate is the offer-picker
    # (no plan/price/validity/distributor full form, no «اختر المدير» dropdown).
    with app.app_context():
        _plan_id()
        me = _sub_admin("mgr_form")
    with app.test_client() as client:
        _login(client, admin_id=me, is_super=False)
        page = client.get("/admin/radius/cards/generate")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    # manager-mode help is always rendered; the owner full-form fields are not.
    assert "كيف تُولّد البطاقات" in html   # manager offer-picker view
    assert "نوع الحزمة" not in html        # owner-only full-form field absent
    assert "— اختر المدير —" not in html
