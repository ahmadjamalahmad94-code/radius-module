from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.operations_speed_center import OperationsSpeedCenterService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "operations_speed_center.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "speed_admin"
        sess["admin_name"] = "Speed Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "speed-csrf"


def _plan(name: str = "Speed Plan", down: int = 10000, up: int = 2000) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 24 * 60, 7, 10.0, "JOD", down, up),
    )
    return int(cur.lastrowid)


def _subscriber(username: str, plan_id: int) -> int:
    cur = db().execute(
        """
        INSERT INTO subscribers(
            tenant_id, username, password, plan_id, status, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (1, username, "masked-test-password", plan_id, "enabled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    return int(cur.lastrowid)


def _open_session(username: str) -> None:
    db().execute(
        """
        INSERT INTO radacct(
            tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
            acctstarttime, acctstoptime, framedipaddress, callingstationid
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (1, f"s-{username}", f"u-{username}", username, "10.0.0.1", "2026-01-01T00:00:00Z", None, "10.1.1.10", "AA:BB:CC"),
    )


def test_speed_preview_calculates_multiplier_and_effective_speeds(app):
    with app.app_context():
        plan_id = _plan()
        _subscriber("speed-user", plan_id)
        preview = OperationsSpeedCenterService(tenant_id=1).speed_preview(
            preset="normal",
            multiplier=0.5,
            profile_ids=[plan_id],
        )

    impact = preview["impact"][0]
    assert impact["base_down_kbps"] == 10000
    assert impact["base_up_kbps"] == 2000
    assert impact["effective_down_kbps"] == 5000
    assert impact["effective_up_kbps"] == 1000
    assert impact["affected_subscribers"] == 1
    assert preview["applied_to_radius"] is False


def test_pressure_preset_uses_safe_default_multiplier(app):
    with app.app_context():
        plan_id = _plan("Pressure Plan", down=9000, up=3000)
        preview = OperationsSpeedCenterService(tenant_id=1).speed_preview(
            preset="pressure",
            profile_ids=[plan_id],
        )

    assert preview["multiplier"] == pytest.approx(0.7)
    assert preview["impact"][0]["effective_down_kbps"] == 6300
    assert preview["impact"][0]["effective_up_kbps"] == 2100


def test_preview_marks_coa_required_when_selected_profile_has_online_sessions(app):
    with app.app_context():
        plan_id = _plan("Online Plan")
        _subscriber("online-user", plan_id)
        _open_session("online-user")
        preview = OperationsSpeedCenterService(tenant_id=1).speed_preview(
            preset="emergency",
            profile_ids=[plan_id],
        )

    assert preview["total_online_sessions"] == 1
    assert preview["coa_required"] is True
    assert preview["impact"][0]["online_sessions"] == 1
    assert preview["impact"][0]["coa_required"] is True


def test_save_speed_policy_persists_dry_run_and_audit_event(app):
    with app.app_context():
        plan_id = _plan("Audit Plan")
        policy = OperationsSpeedCenterService(tenant_id=1).save_speed_policy(
            policy_key="pressure-may",
            title="Pressure May",
            preset="pressure",
            profile_ids=[plan_id],
            actor="qa-admin",
        )
        event = db().execute(
            "SELECT * FROM business_events WHERE event_key='speed_control.dry_run_saved'"
        ).fetchone()

    assert policy["status"] == "dry_run_ready"
    assert policy["applied_to_radius"] is False
    assert policy["preview"]["applied_to_radius"] is False
    assert event is not None
    assert event["target_type"] == "speed_control_policy"


def test_operations_routes_render_and_save_dry_run_policy(app):
    with app.app_context():
        plan_id = _plan("Route Plan")
        _subscriber("route-user", plan_id)
    with app.test_client() as client:
        _auth_session(client)
        index = client.get("/admin/radius/operations")
        speed = client.get("/admin/radius/operations/speed-control")
        saved = client.post(
            "/admin/radius/operations/speed-control",
            data={
                "_csrf_token": "speed-csrf",
                "policy_key": "route-pressure",
                "title": "Route Pressure",
                "preset": "pressure",
                "profile_ids": str(plan_id),
                "save_policy": "1",
            },
            follow_redirects=True,
        )

    assert index.status_code == 200
    assert "operations-summary" in index.get_data(as_text=True)
    assert speed.status_code == 200
    assert "speed-control-form" in speed.get_data(as_text=True)
    assert saved.status_code == 200
    body = saved.get_data(as_text=True)
    assert "route-pressure" in body
    assert "لا" in body
