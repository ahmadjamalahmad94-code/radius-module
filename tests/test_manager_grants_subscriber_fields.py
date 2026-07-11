"""Granular per-manager FIELD control on subscriber edit (Stage 2, level 3).

The owner picks EXACTLY which subscriber fields a manager may change; every
ungranted field is reverted to its stored value SERVER-SIDE on submit — the
manager cannot change it even via a crafted POST. Owner/super bypasses.

Core spec test: a manager granted only «name» can change the name, but a
posted password / MAC / plan / IP change is ignored, while the owner can
change all. Field-control OFF (default) => every field editable (non-regressive).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_grants_subfields.db")
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
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ──────────────────────────────────────────────────────────────
def _mk_admin(username: str, *, is_super: bool = False) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"A {username}",
        is_super_admin=is_super,
    )
    return int(adm.id)


def _plan(name: str) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 30, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _mk_subscriber(username: str, *, plan_id: int, manager_id: int):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="origpass1",
        status="enabled", plan_id=plan_id, manager_id=manager_id,
        full_name="Original Name", mac_lock="AA:AA:AA:AA:AA:AA",
        static_ip="10.0.0.9", device_count=1,
    ))


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["users.view", "users.edit", "users.create"]


def _grant_fields(mgr: int, fields):
    from app.radius.services import manager_grants as mg

    mg.set_field_grants(mgr, "subscriber", fields, tenant_id=1)


def _edit_payload(**over):
    base = {
        "_csrf_token": "off-csrf",
        "full_name": "Changed Name",
        "password": "hackedpass9",
        "plan_id": "",
        "status": "disabled",
        "mac_lock": "BB:BB:BB:BB:BB:BB",
        "static_ip": "10.9.9.9",
        "device_count": "7",
    }
    base.update(over)
    return base


def _get(username):
    from app.radius.services.users import get_users_service

    return get_users_service().get(username)


# ═══ 1. registry + storage ══════════════════════════════════════════════════
def test_field_registry_has_subscriber_keys(app):
    from app.radius.services import manager_grants as mg

    keys = mg.field_keys("subscriber")
    for k in ("name", "password", "mac", "ip", "plan", "status", "quota",
              "expiry", "device_count", "speed"):
        assert k in keys


def test_field_control_off_by_default(app):
    from app.radius.services import manager_grants as mg

    with app.app_context():
        mgr = _mk_admin("mgr_fc_off")
        # unconfigured => control OFF => no reverts (all editable)
        assert mg.field_grants(mgr, "subscriber", tenant_id=1) is None
        assert mg.reverted_attrs(mgr, "subscriber", tenant_id=1) == set()


def test_reverted_attrs_excludes_granted(app):
    from app.radius.services import manager_grants as mg

    with app.app_context():
        mgr = _mk_admin("mgr_rev")
        mg.set_field_grants(mgr, "subscriber", ["name"], tenant_id=1)
        reverts = mg.reverted_attrs(mgr, "subscriber", tenant_id=1)
        assert "full_name" not in reverts          # granted → editable
        assert "password" in reverts               # ungranted → reverted
        assert "plan_id" in reverts
        assert "mac_lock" in reverts


# ═══ 2. server-side enforcement on the update handler ═══════════════════════
def test_manager_granted_only_name_can_change_name_only(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)   # min-id owner
        mgr = _mk_admin("mgr_edit")
        p1 = _plan("P1"); p2 = _plan("P2")
        _mk_subscriber("sub1", plan_id=p1, manager_id=mgr)
        _grant_fields(mgr, ["name"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users/sub1",
                          data=_edit_payload(plan_id=str(p2)))
    assert res.status_code in (302, 303)
    with app.app_context():
        sub = _get("sub1")
        # granted field changed:
        assert sub.full_name == "Changed Name"
        # ungranted fields IGNORED (reverted to stored values):
        assert sub.password == "origpass1"
        assert (sub.mac_lock or "") == "AA:AA:AA:AA:AA:AA"
        assert (sub.static_ip or "") == "10.0.0.9"
        assert sub.plan_id == p1
        assert sub.status == "enabled"
        assert int(sub.device_count or 0) == 1


def test_manager_granted_password_and_plan_can_change_those(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_edit2")
        p1 = _plan("P1"); p2 = _plan("P2")
        _mk_subscriber("sub2", plan_id=p1, manager_id=mgr)
        _grant_fields(mgr, ["password", "plan"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users/sub2",
                          data=_edit_payload(plan_id=str(p2)))
    assert res.status_code in (302, 303)
    with app.app_context():
        sub = _get("sub2")
        assert sub.password == "hackedpass9"       # granted
        assert sub.plan_id == p2                    # granted
        assert sub.full_name == "Original Name"     # ungranted → reverted
        assert (sub.mac_lock or "") == "AA:AA:AA:AA:AA:AA"


def test_field_control_off_allows_all_changes(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_nofc")
        p1 = _plan("P1"); p2 = _plan("P2")
        _mk_subscriber("sub3", plan_id=p1, manager_id=mgr)
        # no set_field_grants => control OFF
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users/sub3",
                          data=_edit_payload(plan_id=str(p2)))
    assert res.status_code in (302, 303)
    with app.app_context():
        sub = _get("sub3")
        assert sub.full_name == "Changed Name"
        assert sub.password == "hackedpass9"
        assert sub.plan_id == p2


def test_owner_bypasses_field_control(app):
    with app.app_context():
        owner = _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_owner_case")
        p1 = _plan("P1"); p2 = _plan("P2")
        _mk_subscriber("sub4", plan_id=p1, manager_id=mgr)
        # even if a restrictive grant existed for the owner id, super bypasses
        _grant_fields(owner, ["name"])
    with app.test_client() as client:
        _login(client, admin_id=owner, is_super=True)
        res = client.post("/admin/radius/users/sub4",
                          data=_edit_payload(plan_id=str(p2)))
    assert res.status_code in (302, 303)
    with app.app_context():
        sub = _get("sub4")
        assert sub.full_name == "Changed Name"
        assert sub.password == "hackedpass9"
        assert sub.plan_id == p2


# ═══ 3. config route persists field grants ══════════════════════════════════
def test_policy_route_persists_field_grants(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_cfg")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            f"/admin/radius/business-operators/manager/{mgr}/policy",
            data={"_csrf_token": "off-csrf",
                  "field_control_subscriber": "1",
                  "field_subscriber_name": "1",
                  "field_subscriber_password": "1"},
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg

        granted = mg.field_grants(mgr, "subscriber", tenant_id=1)
        assert granted == {"name", "password"}


def test_policy_route_field_control_off_clears(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_cfg_off")
        _grant_fields(mgr, ["name"])
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        # omit field_control_subscriber => control OFF => cleared
        res = client.post(
            f"/admin/radius/business-operators/manager/{mgr}/policy",
            data={"_csrf_token": "off-csrf"},
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg

        assert mg.field_grants(mgr, "subscriber", tenant_id=1) is None


# ═══ 4. read-only UI on the edit form ═══════════════════════════════════════
def test_locked_field_renders_readonly_in_form(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_ui")
        p1 = _plan("P1")
        _mk_subscriber("sub5", plan_id=p1, manager_id=mgr)
        _grant_fields(mgr, ["name"])   # password NOT granted
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/users/sub5/edit").get_data(as_text=True)
    # the password input is rendered read-only for this manager
    import re
    m = re.search(r'name="password"[^>]*>', html)
    assert m and "readonly" in m.group(0)


# ═══ 5. subscription expiry (expire_at) is gated by the «expiry» grant ═══════
def test_expiry_change_gated_by_field_grant(app):
    """A manager WITHOUT the «expiry» grant cannot change the subscription
    expiry via the manual date field — the POST is reverted server-side.
    Granting «expiry» lets the change through."""
    from datetime import datetime
    from app.radius.services import manager_grants as mg
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_exp")
        p1 = _plan("P1")
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="sube", password="origpass1",
            status="enabled", plan_id=p1, manager_id=mgr,
            expire_at=datetime(2026, 8, 1, 23, 59, 59)))
        _grant_fields(mgr, ["name"])   # «expiry» NOT granted
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users/sube",
                          data=_edit_payload(expire_year="2027",
                                             expire_month="1", expire_day="1"))
    assert res.status_code in (302, 303)
    with app.app_context():
        # ungranted → reverted to stored expiry
        assert _get("sube").expire_at.strftime("%Y-%m-%d") == "2026-08-01"
        mg.set_field_grants(mgr, "subscriber", ["name", "expiry"], tenant_id=1)
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users/sube",
                          data=_edit_payload(expire_year="2027",
                                             expire_month="1", expire_day="1"))
    assert res.status_code in (302, 303)
    with app.app_context():
        # granted → change applied
        assert _get("sube").expire_at.strftime("%Y-%m-%d") == "2027-01-01"


def test_expiry_field_readonly_when_ungranted(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_exp_ui")
        p1 = _plan("P1")
        _mk_subscriber("sube_ui", plan_id=p1, manager_id=mgr)
        _grant_fields(mgr, ["name"])   # «expiry» NOT granted
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/users/sube_ui/edit").get_data(as_text=True)
    import re
    # the Arabic month/day/year selects render disabled for this manager
    m = re.search(r'name="expire_month"[^>]*>', html)
    assert m and "disabled" in m.group(0)
