"""Web UI smoke tests for bandwidth schedule screen."""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
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
                VALUES(1,1,'Bandwidth Schedule Test Plan','BWSCHED','time','Hotspot',
                       1440,1,4000,2000,1,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"bandwidth_web_{uuid4().hex[:10]}"
    password = "bandwidth-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Bandwidth Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_bandwidth_schedules_web_route_is_login_guarded(client):
    res = client.get("/admin/radius/bandwidth-schedules", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_bandwidth_schedules_create_and_apply_dry_run(client):
    _web_login(client)
    page = client.get("/admin/radius/bandwidth-schedules")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "فحص الجاهزية" in html
    assert "applied_to_radius = false" not in html

    token = _csrf(client, "/admin/radius/bandwidth-schedules")
    created = client.post(
        "/admin/radius/bandwidth-schedules",
        data={
            "_csrf_token": token,
            "name": "Night speed window",
            "plan_id": "1",
            "starts_at_time": "22:00",
            "ends_at_time": "06:00",
            "speed_down_kbps": "3000",
            "speed_up_kbps": "1000",
            "cir_down_kbps": "0",
            "cir_up_kbps": "0",
            "restore_mode": "profile_default",
            "enabled": "1",
            "notes": "test schedule",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    created_html = created.get_data(as_text=True)
    assert "Night speed window" in created_html
    assert "3000" in created_html

    from app.radius.db.repos import operations_repo

    schedules = operations_repo.list_bandwidth_schedules(1, limit=1000)
    schedule = next(item for item in schedules if item["name"] == "Night speed window")

    applied = client.post(
        f"/admin/radius/bandwidth-schedules/{schedule['id']}/apply",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert applied.status_code == 200
    applied_html = applied.get_data(as_text=True)
    assert "Night speed window" in applied_html
    assert "فحص الجاهزية" in applied_html
    assert "applied_to_radius = false" not in applied_html


def test_card_batch_edit_embeds_speed_rule_creator(client):
    _web_login(client)

    from app.radius.core.types import CardBatch
    from app.radius.db.repos import cards_repo, operations_repo

    batch = cards_repo.create_batch(
        CardBatch(
            id=None,
            tenant_id=1,
            batch_code="",
            plan_id=1,
            count=10,
            package_name="Embedded Speed Batch",
            created_by="test",
        )
    )

    edit_url = f"/admin/radius/cards/batches/{batch.id}/edit"
    page = client.get(edit_url)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "sr_starts_at_time" in html
    assert "sr_source_schedule_id" in html

    token = _csrf(client, edit_url)
    created = client.post(
        edit_url,
        data={
            "_csrf_token": token,
            "_speed_rule_action": "manual",
            "sr_name": "Batch evening speed",
            "sr_starts_at_time": "14:00",
            "sr_ends_at_time": "18:00",
            "sr_speed_down_kbps": "3000",
            "sr_speed_up_kbps": "1000",
            "sr_restore_mode": "profile_default",
            "sr_priority": "20",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200

    rules = operations_repo.list_bandwidth_schedules(
        1,
        target_type="card_batch",
        card_batch_id=batch.id,
        limit=100,
    )
    rule = next(r for r in rules if r["name"] == "Batch evening speed")

    updated = client.post(
        edit_url,
        data={
            "_csrf_token": token,
            "_speed_rule_action": f"update:{rule['id']}",
            f"sr_edit_name_{rule['id']}": "Batch edited speed",
            f"sr_edit_starts_at_time_{rule['id']}": "18:00",
            f"sr_edit_ends_at_time_{rule['id']}": "23:00",
            f"sr_edit_speed_down_kbps_{rule['id']}": "5000",
            f"sr_edit_speed_up_kbps_{rule['id']}": "1500",
            f"sr_edit_restore_mode_{rule['id']}": "keep_current",
            f"sr_edit_priority_{rule['id']}": "10",
            f"sr_edit_enabled_{rule['id']}": "1",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    edited = operations_repo.get_bandwidth_schedule(1, rule["id"])
    assert edited["name"] == "Batch edited speed"
    assert edited["speed_down_kbps"] == 5000
    assert edited["restore_mode"] == "keep_current"

    toggled = client.post(
        edit_url,
        data={"_csrf_token": token, "_speed_rule_action": f"toggle:{rule['id']}"},
        follow_redirects=True,
    )
    assert toggled.status_code == 200
    disabled = operations_repo.get_bandwidth_schedule(1, rule["id"])
    assert disabled["enabled"] == 0

    bulk_enabled = client.post(
        edit_url,
        data={"_csrf_token": token, "_speed_rule_action": "enable_all"},
        follow_redirects=True,
    )
    assert bulk_enabled.status_code == 200
    enabled = operations_repo.get_bandwidth_schedule(1, rule["id"])
    assert enabled["enabled"] == 1

    deleted = client.post(
        edit_url,
        data={"_csrf_token": token, "_speed_rule_action": f"delete:{rule['id']}"},
        follow_redirects=True,
    )
    assert deleted.status_code == 200
    assert operations_repo.get_bandwidth_schedule(1, rule["id"]) is None
