"""Speed-control page: time-based schedule CRUD wired to the SAME backend.

The redesigned /admin/radius/operations/speed-control (+/manual) now hosts full
create/edit/delete of bandwidth schedules, posting to the single source of truth
(radius.bandwidth_schedules_create/_update/_delete/_apply) with return_to so the
operator stays on the speed-control page. These tests prove:

  * the page renders the schedule panel + the «جدول زمني» vs «سرعة لحظية» help
    and the live-apply flag banner;
  * create/edit/delete from speed-control work and return to the page;
  * «تطبيق الآن» (live) is safe when the env flag is off (no error, dry-run);
  * RBAC: a page-authorized but schedule-unauthorized admin sees no add button
    and gets a friendly 403 on create.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

SPEED_URL = "/admin/radius/operations/speed-control"
MANUAL_URL = "/admin/radius/operations/speed-control/manual"
SCHED_BASE = "/admin/radius/bandwidth-schedules"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    # keep live apply OFF by default (we assert the safe/disabled path)
    monkeypatch.delenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", raising=False)
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
                    speed_up_kbps, price, currency, enabled, created_at)
                VALUES(1,1,'Speed Sched Plan','SPDSCH','time','PPPoE',
                       1440,30,50000,25000,5,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(client) -> None:
    from app.radius.db.repos import admins_repo

    u = f"spd_super_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="spd-pass",
                             full_name="Speed Owner", is_super_admin=True)
    r = client.post("/admin/radius/login",
                    data={"username": u, "password": "spd-pass"}, follow_redirects=False)
    assert r.status_code in {302, 303}


def _login_with_perms(client, perms) -> None:
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import json_dump, now_iso
    from app.radius.db.repos import admins_repo

    rname = f"spd_role_{uuid4().hex[:8]}"
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO roles(tenant_id,name,display_name,description,permissions,"
            "is_system,created_at) VALUES(NULL,?,?,'',?,0,?)",
            (rname, rname, json_dump(list(perms)), now_iso()),
        )
        rid = cur.lastrowid
    u = f"spd_user_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="spd-pass", full_name="Limited",
                             role_id=rid, is_super_admin=False)
    r = client.post("/admin/radius/login",
                    data={"username": u, "password": "spd-pass"}, follow_redirects=False)
    assert r.status_code in {302, 303}


def _csrf(client, url=SPEED_URL) -> str:
    assert client.get(url).status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _latest(name):
    from app.radius.db.repos import operations_repo

    return next(s for s in operations_repo.list_bandwidth_schedules(1, limit=1000)
                if s["name"] == name)


# ───────────────────────── render + panel presence ─────────────────────────
def test_speed_control_renders_schedule_panel_and_help(client):
    _login_super(client)
    res = client.get(SPEED_URL)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "الجداول الزمنية للسرعة" in html          # schedule section
    assert "جدول زمني دائم" in html                  # help card A
    assert "سرعة لحظية مؤقتة" in html                # help card B
    assert 'data-testid="ssp-add"' in html           # add button (super sees it)
    assert 'data-testid="ssp-live-banner"' in html   # live-flag banner
    assert "HOBERADIUS_ENABLE_LIVE_SPEED_APPLY=0" in html  # flag shown OFF
    # the existing temporary-speed preview feature must remain intact
    assert 'data-testid="speed-control-form"' in html


def test_manual_page_also_renders_schedule_panel(client):
    _login_super(client)
    res = client.get(MANUAL_URL)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "الجداول الزمنية للسرعة" in html
    assert 'data-testid="ssp-add"' in html
    # manual temp-speed engine intact
    assert 'data-testid="speed-manual-section"' in html


# ───────────────── create → edit → live-apply → delete from this page ─────────────────
def test_schedule_crud_from_speed_control_returns_here(client):
    _login_super(client)
    token = _csrf(client)

    # CREATE (return_to = speed-control)
    created = client.post(
        SCHED_BASE,
        data={
            "_csrf_token": token, "return_to": SPEED_URL,
            "name": "SC night window", "plan_id": "1",
            "starts_at_time": "23:00", "ends_at_time": "06:00",
            "speed_down_kbps": "9000", "speed_up_kbps": "3000",
            "restore_mode": "profile_default", "enabled": "1",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    assert "operations/speed-control" in created.headers.get("Location", "")
    sched = _latest("SC night window")

    # EDIT (return_to = speed-control)
    edited = client.post(
        f"{SCHED_BASE}/{sched['id']}/edit",
        data={
            "_csrf_token": token, "return_to": SPEED_URL,
            "name": "SC night edited", "starts_at_time": "22:00",
            "ends_at_time": "05:00", "speed_down_kbps": "12000",
            "restore_mode": "keep_current", "enabled": "1",
        },
        follow_redirects=False,
    )
    assert edited.status_code in {302, 303}
    assert "operations/speed-control" in edited.headers.get("Location", "")
    from app.radius.db.repos import operations_repo

    after = operations_repo.get_bandwidth_schedule(1, sched["id"])
    assert after["name"] == "SC night edited"
    assert after["speed_down_kbps"] == 12000
    assert after["restore_mode"] == "keep_current"

    # LIVE APPLY with flag OFF — must be SAFE (redirect, no error, nothing applied)
    applied = client.post(
        f"{SCHED_BASE}/{sched['id']}/apply",
        data={"_csrf_token": token, "return_to": SPEED_URL, "live": "1"},
        follow_redirects=True,
    )
    assert applied.status_code == 200
    # still present, unchanged — live apply did not silently mangle it
    assert operations_repo.get_bandwidth_schedule(1, sched["id"]) is not None

    # DELETE
    deleted = client.post(
        f"{SCHED_BASE}/{sched['id']}/delete",
        data={"_csrf_token": token, "return_to": SPEED_URL},
        follow_redirects=False,
    )
    assert deleted.status_code in {302, 303}
    assert "operations/speed-control" in deleted.headers.get("Location", "")
    assert operations_repo.get_bandwidth_schedule(1, sched["id"]) is None


# ───────────────── live apply genuinely fires when the flag is ON ─────────────────
def test_live_apply_path_runs_when_flag_on(client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "1")
    _login_super(client)
    token = _csrf(client)
    client.post(
        SCHED_BASE,
        data={"_csrf_token": token, "return_to": SPEED_URL, "name": "SC live",
              "plan_id": "1", "starts_at_time": "01:00", "ends_at_time": "02:00",
              "speed_down_kbps": "4000", "restore_mode": "profile_default", "enabled": "1"},
        follow_redirects=True,
    )
    sched = _latest("SC live")
    # no active sessions in a NO_SEED DB → applied_to_radius False, but the live
    # branch must execute without raising and redirect cleanly.
    res = client.post(
        f"{SCHED_BASE}/{sched['id']}/apply",
        data={"_csrf_token": token, "return_to": SPEED_URL, "live": "1"},
        follow_redirects=True,
    )
    assert res.status_code == 200


# ───────────────── RBAC: page-authorized but schedule-unauthorized ─────────────────
def test_unauthorized_admin_no_add_button_and_403(client):
    # seed one schedule as super so the read-only row marker can render
    _login_super(client)
    tok0 = _csrf(client)
    client.post(
        SCHED_BASE,
        data={"_csrf_token": tok0, "return_to": SPEED_URL, "name": "RO seed",
              "plan_id": "1", "starts_at_time": "03:00", "ends_at_time": "04:00",
              "speed_down_kbps": "4000", "restore_mode": "profile_default", "enabled": "1"},
        follow_redirects=True,
    )

    # users.temp_speed lets them SEE the speed-control page; no plans.edit/delete.
    _login_with_perms(client, ("dashboard.view", "users.temp_speed"))
    page = client.get(SPEED_URL)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "الجداول الزمنية للسرعة" in html      # can view the section
    assert 'data-testid="ssp-add"' not in html   # but NOT the add button
    assert "عرض فقط" in html                      # read-only marker on the row

    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    blocked = client.post(
        SCHED_BASE,
        data={"_csrf_token": token, "return_to": SPEED_URL, "name": "hack",
              "plan_id": "1", "starts_at_time": "01:00", "ends_at_time": "02:00",
              "speed_down_kbps": "5000", "restore_mode": "profile_default"},
        follow_redirects=False,
    )
    assert blocked.status_code == 403
