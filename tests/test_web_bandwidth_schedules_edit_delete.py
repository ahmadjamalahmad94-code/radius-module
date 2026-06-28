"""Web UI: edit + delete a bandwidth schedule end to end, plus RBAC gating.

Owner-reported bug: on /admin/radius/bandwidth-schedules a schedule could be
created and «apply»-checked, but NOT edited or deleted — the edit/delete
routes and UI controls simply did not exist (the service layer did). These
tests prove the full lifecycle now works and is correctly permission-gated:

  * super-admin (owner): create → edit → delete works end to end;
  * a non-super admin with plans.edit/plans.delete is also authorized;
  * a non-super admin lacking those (seeded `operator`, has plans.view only)
    can open the page but gets a friendly 403 on edit and on delete.
"""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # NO_SEED + the conftest LICENSE_GATE_TEST_BYPASS together let the panel
    # render without a provider license snapshot (else the lifecycle gate
    # treats the fresh DB as NEVER_ACTIVATED and 302-redirects every page).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_plans(
                    id, tenant_id, name, code, plan_type, service_type,
                    duration_minutes, validity_days, speed_down_kbps,
                    speed_up_kbps, price, currency, enabled, created_at
                )
                VALUES(1,1,'Bandwidth Edit/Delete Plan','BWED','time','Hotspot',
                       1440,1,4000,2000,1,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"bwed_super_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username,
        password="bwed-pass",
        full_name="BW Owner",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "bwed-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _login_with_perms(client, perms: tuple[str, ...]) -> None:
    """Create a non-super admin in a role carrying exactly ``perms`` and log in."""
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import json_dump, now_iso
    from app.radius.db.repos import admins_repo

    rname = f"bwed_role_{uuid4().hex[:8]}"
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO roles(tenant_id, name, display_name, description,
                              permissions, is_system, created_at)
            VALUES(NULL, ?, ?, '', ?, 0, ?)
            """,
            (rname, rname, json_dump(list(perms)), now_iso()),
        )
        role_id = cur.lastrowid

    username = f"bwed_user_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username,
        password="bwed-pass",
        full_name="BW Limited",
        role_id=role_id,
        is_super_admin=False,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "bwed-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    res = client.get("/admin/radius/bandwidth-schedules")
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _create_schedule(client, token, name) -> dict:
    res = client.post(
        "/admin/radius/bandwidth-schedules",
        data={
            "_csrf_token": token,
            "name": name,
            "plan_id": "1",
            "starts_at_time": "22:00",
            "ends_at_time": "06:00",
            "speed_down_kbps": "3000",
            "speed_up_kbps": "1000",
            "restore_mode": "profile_default",
            "enabled": "1",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    from app.radius.db.repos import operations_repo

    schedules = operations_repo.list_bandwidth_schedules(1, limit=1000)
    return next(s for s in schedules if s["name"] == name)


# ─────────────────────── full lifecycle (owner / super) ───────────────────────
def test_owner_can_edit_and_delete_schedule(client):
    _login_super(client)
    token = _csrf(client)

    # the page must now expose edit + delete controls
    page = client.get("/admin/radius/bandwidth-schedules")
    html = page.get_data(as_text=True)
    sched = _create_schedule(client, token, "Owner night window")

    page = client.get("/admin/radius/bandwidth-schedules")
    html = page.get_data(as_text=True)
    assert f"schedule-edit-{sched['id']}" in html      # edit modal present
    assert f"/bandwidth-schedules/{sched['id']}/delete" in html  # delete form present
    assert "data-confirm" in html                      # design-system confirm, not native

    # EDIT
    edited = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/edit",
        data={
            "_csrf_token": token,
            "name": "Owner edited window",
            "priority": "3",
            "starts_at_time": "20:00",
            "ends_at_time": "23:30",
            "speed_down_kbps": "8000",
            "speed_up_kbps": "2000",
            "restore_mode": "keep_current",
            "enabled": "1",
            "notes": "tuned",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    from app.radius.db.repos import operations_repo

    after = operations_repo.get_bandwidth_schedule(1, sched["id"])
    assert after["name"] == "Owner edited window"
    assert after["speed_down_kbps"] == 8000
    assert after["restore_mode"] == "keep_current"
    assert after["starts_at_time"] == "20:00"

    # DELETE
    deleted = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/delete",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert deleted.status_code == 200
    assert operations_repo.get_bandwidth_schedule(1, sched["id"]) is None


# ─────────────────── authorized non-super admin (plans.edit/delete) ───────────────────
def test_authorized_admin_can_edit_and_delete(client):
    _login_with_perms(
        client, ("dashboard.view", "plans.view", "plans.edit", "plans.delete")
    )
    token = _csrf(client)
    sched = _create_schedule(client, token, "Authorized window")

    edited = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/edit",
        data={
            "_csrf_token": token,
            "name": "Authorized edited",
            "starts_at_time": "21:00",
            "ends_at_time": "23:00",
            "speed_down_kbps": "5000",
            "restore_mode": "profile_default",
            "enabled": "1",
        },
        follow_redirects=False,
    )
    assert edited.status_code in {302, 303}  # not 403

    deleted = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/delete",
        data={"_csrf_token": token},
        follow_redirects=False,
    )
    assert deleted.status_code in {302, 303}
    from app.radius.db.repos import operations_repo

    assert operations_repo.get_bandwidth_schedule(1, sched["id"]) is None


# ─────────────────── unauthorized admin (plans.view only → friendly 403) ───────────────────
def test_unauthorized_admin_gets_403_on_edit_and_delete(client):
    # seed a schedule as super first, then exercise as a view-only admin.
    _login_super(client)
    token = _csrf(client)
    sched = _create_schedule(client, token, "Guarded window")

    # fresh client logged in as a view-only admin (plans.view, no edit/delete)
    _login_with_perms(client, ("dashboard.view", "plans.view"))
    tok2 = _csrf(client)

    # can still open the page (plans.view grants the view guard)
    assert client.get("/admin/radius/bandwidth-schedules").status_code == 200

    blocked_edit = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/edit",
        data={
            "_csrf_token": tok2,
            "name": "hack",
            "starts_at_time": "01:00",
            "ends_at_time": "02:00",
            "speed_down_kbps": "9000",
            "restore_mode": "profile_default",
        },
        follow_redirects=False,
    )
    assert blocked_edit.status_code == 403

    blocked_delete = client.post(
        f"/admin/radius/bandwidth-schedules/{sched['id']}/delete",
        data={"_csrf_token": tok2},
        follow_redirects=False,
    )
    assert blocked_delete.status_code == 403

    # nothing was changed/removed
    from app.radius.db.repos import operations_repo

    still = operations_repo.get_bandwidth_schedule(1, sched["id"])
    assert still is not None and still["name"] == "Guarded window"
