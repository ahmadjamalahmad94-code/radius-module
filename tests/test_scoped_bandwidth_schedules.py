"""Scoped bandwidth schedule tests."""
from __future__ import annotations

import os
import secrets
import sys
import tempfile


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_speed_rules_")
    os.environ.pop("HOBERADIUS_NO_SEED", None)
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_API_TOKENS"] = "speed-rules-test-token"
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    return create_app()


def _auth() -> dict:
    return {"Authorization": "Bearer speed-rules-test-token"}


def _web_login(client) -> None:
    res = client.post(
        "/admin/radius/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_bandwidth_schedule_api_accepts_plan_subscriber_and_card_batch_targets():
    app = _fresh_app()
    client = app.test_client()
    username = "speed_" + secrets.token_hex(4)
    created_sub = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw", "plan_id": 1},
        headers=_auth(),
    )
    assert created_sub.status_code == 201, created_sub.get_json()
    created_batch = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": "sr" + secrets.token_hex(2)},
        headers=_auth(),
    )
    assert created_batch.status_code == 201, created_batch.get_json()
    batch_id = created_batch.get_json()["data"]["batch"]["id"]

    payloads = [
        {"target_type": "plan", "plan_id": 1, "name": "Plan speed"},
        {"target_type": "subscriber", "subscriber_username": username, "name": "Subscriber speed"},
        {"target_type": "card_batch", "card_batch_id": batch_id, "name": "Card speed"},
    ]
    for index, payload in enumerate(payloads, start=1):
        res = client.post(
            "/api/v1/bandwidth-schedules",
            json={
                **payload,
                "starts_at_time": "00:00",
                "ends_at_time": "00:00",
                "speed_down_kbps": 1000 * index,
                "speed_up_kbps": 500 * index,
                "priority": index,
            },
            headers=_auth(),
        )
        assert res.status_code == 201, res.get_json()
        schedule = res.get_json()["data"]["schedule"]
        assert schedule["target_type"] == payload["target_type"]

    listed = client.get("/api/v1/bandwidth-schedules", headers=_auth())
    assert listed.status_code == 200, listed.get_json()
    target_types = {item["target_type"] for item in listed.get_json()["data"]["items"]}
    assert {"plan", "subscriber", "card_batch"}.issubset(target_types)


def test_policy_engine_speed_precedence_subscriber_then_card_batch_then_plan():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch, Subscriber
        from app.radius.db.repos import cards_repo, plans_repo, subscribers_repo
        from app.radius.services.operations import get_operations_service
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(
            AccessPlan(
                id=None,
                tenant_id=1,
                name="Speed Priority",
                plan_type="time",
                speed_down_kbps=1000,
                speed_up_kbps=100,
                concurrent_sessions=10,
            )
        )
        batch = cards_repo.create_batch(
            CardBatch(id=None, batch_code="", plan_id=plan.id, count=1, package_name="Speed Cards")
        )
        subscribers_repo.upsert_subscriber(
            Subscriber(
                id=None,
                tenant_id=1,
                username="sub-priority",
                password="pw",
                status="enabled",
                plan_id=plan.id,
                card_batch_id=batch.id,
            )
        )
        subscribers_repo.upsert_subscriber(
            Subscriber(
                id=None,
                tenant_id=1,
                username="batch-priority",
                password="pw",
                status="enabled",
                plan_id=plan.id,
                card_batch_id=batch.id,
            )
        )
        subscribers_repo.upsert_subscriber(
            Subscriber(
                id=None,
                tenant_id=1,
                username="plan-priority",
                password="pw",
                status="enabled",
                plan_id=plan.id,
            )
        )

        svc = get_operations_service()
        common = {
            "starts_at_time": "00:00",
            "ends_at_time": "00:00",
            "enabled": True,
        }
        svc.create_bandwidth_schedule(
            tenant_id=1,
            actor="test",
            data={
                **common,
                "target_type": "plan",
                "plan_id": plan.id,
                "name": "Plan rule",
                "speed_down_kbps": 5000,
                "speed_up_kbps": 500,
            },
        )
        svc.create_bandwidth_schedule(
            tenant_id=1,
            actor="test",
            data={
                **common,
                "target_type": "card_batch",
                "card_batch_id": batch.id,
                "name": "Batch rule",
                "speed_down_kbps": 3000,
                "speed_up_kbps": 300,
            },
        )
        svc.create_bandwidth_schedule(
            tenant_id=1,
            actor="test",
            data={
                **common,
                "target_type": "subscriber",
                "subscriber_username": "sub-priority",
                "name": "Subscriber rule",
                "speed_down_kbps": 7000,
                "speed_up_kbps": 700,
            },
        )

        sub_decision = authorize(AuthRequest(username="sub-priority", password="pw", tenant_id=1))
        batch_decision = authorize(AuthRequest(username="batch-priority", password="pw", tenant_id=1))
        plan_decision = authorize(AuthRequest(username="plan-priority", password="pw", tenant_id=1))

        assert sub_decision.reply_attrs["Mikrotik-Rate-Limit"] == "700k/7000k"
        assert batch_decision.reply_attrs["Mikrotik-Rate-Limit"] == "300k/3000k"
        assert plan_decision.reply_attrs["Mikrotik-Rate-Limit"] == "500k/5000k"


def test_effective_speed_rule_api_explains_precedence():
    app = _fresh_app()
    client = app.test_client()
    username = "effective_" + secrets.token_hex(4)
    created_sub = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw", "plan_id": 1},
        headers=_auth(),
    )
    assert created_sub.status_code == 201, created_sub.get_json()
    created_batch = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": "eff" + secrets.token_hex(2)},
        headers=_auth(),
    )
    assert created_batch.status_code == 201, created_batch.get_json()
    batch_id = created_batch.get_json()["data"]["batch"]["id"]
    for payload in [
        {"target_type": "plan", "plan_id": 1, "name": "Plan rule", "speed_down_kbps": 1000, "speed_up_kbps": 100},
        {"target_type": "card_batch", "card_batch_id": batch_id, "name": "Batch rule", "speed_down_kbps": 3000, "speed_up_kbps": 300},
        {"target_type": "subscriber", "subscriber_username": username, "name": "Subscriber rule", "speed_down_kbps": 7000, "speed_up_kbps": 700},
    ]:
        res = client.post(
            "/api/v1/bandwidth-schedules",
            json={
                **payload,
                "starts_at_time": "00:00",
                "ends_at_time": "00:00",
                "priority": 10,
            },
            headers=_auth(),
        )
        assert res.status_code == 201, res.get_json()

    resolved = client.get(
        f"/api/v1/bandwidth-schedules/effective?subscriber_username={username}&card_batch_id={batch_id}&plan_id=1",
        headers=_auth(),
    )
    assert resolved.status_code == 200, resolved.get_json()
    data = resolved.get_json()["data"]
    assert data["source"] == "subscriber"
    assert data["rate_limit"] == "700k/7000k"
    assert data["precedence"] == ["subscriber", "card_batch", "plan"]


def test_bandwidth_schedule_apply_is_dry_run_unless_live_flag_enabled(monkeypatch):
    app = _fresh_app()
    client = app.test_client()
    username = "live_speed_" + secrets.token_hex(4)
    created_sub = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw", "plan_id": 1},
        headers=_auth(),
    )
    assert created_sub.status_code == 201, created_sub.get_json()
    schedule_res = client.post(
        "/api/v1/bandwidth-schedules",
        json={
            "target_type": "subscriber",
            "subscriber_username": username,
            "name": "Live speed",
            "starts_at_time": "00:00",
            "ends_at_time": "00:00",
            "speed_down_kbps": 9000,
            "speed_up_kbps": 900,
        },
        headers=_auth(),
    )
    assert schedule_res.status_code == 201, schedule_res.get_json()
    schedule_id = schedule_res.get_json()["data"]["schedule"]["id"]

    captured = []

    def _fake_change_user_rate(tenant_id, called_username, *, new_rate_limit):
        captured.append((tenant_id, called_username, new_rate_limit))
        from app.radius.integration.radius_coa import CoaResult
        return CoaResult(ok=True, code=44, code_name="CoA-ACK", reply_message="ok")

    monkeypatch.setattr(
        "app.radius.integration.radius_coa.change_user_rate",
        _fake_change_user_rate,
    )
    blocked = client.post(
        f"/api/v1/bandwidth-schedules/{schedule_id}/apply",
        json={"live": True},
        headers=_auth(),
    )
    assert blocked.status_code == 200, blocked.get_json()
    blocked_data = blocked.get_json()["data"]
    assert blocked_data["dry_run"] is True
    assert blocked_data["live_enabled"] is False
    assert captured == []

    monkeypatch.setenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "1")
    live = client.post(
        f"/api/v1/bandwidth-schedules/{schedule_id}/apply",
        json={"live": True},
        headers=_auth(),
    )
    assert live.status_code == 200, live.get_json()
    data = live.get_json()["data"]
    assert data["dry_run"] is False
    assert data["applied_to_radius"] is True
    assert data["target_count"] == 1
    assert data["applied_count"] == 1
    assert captured == [(1, username, "900k/9000k")]


def test_card_batch_update_edits_package_settings_and_available_cards_plan():
    app = _fresh_app()
    client = app.test_client()
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.db.repos import plans_repo

        new_plan = plans_repo.upsert_plan(
            AccessPlan(
                id=None,
                tenant_id=1,
                name="Edited Card Plan",
                plan_type="time",
                speed_down_kbps=2200,
                speed_up_kbps=220,
            )
        )

    created_batch = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 2, "username_prefix": "edit" + secrets.token_hex(2)},
        headers=_auth(),
    )
    assert created_batch.status_code == 201, created_batch.get_json()
    batch_id = created_batch.get_json()["data"]["batch"]["id"]

    update = client.patch(
        f"/api/v1/cards/batches/{batch_id}",
        json={
            "package_name": "باقة كروت معدلة",
            "plan_id": new_plan.id,
            "count": 2,
            "price_per_card": 3.5,
            "total_price": 7,
            "total_quota_mb": 2048,
            "time_value": 12,
            "time_unit": "hours",
            "device_count": 3,
            "on_quota_exhaust": "reduce_speed",
            "notes": "تعديل آمن بدون إعادة توليد",
        },
        headers=_auth(),
    )
    assert update.status_code == 200, update.get_json()
    batch = update.get_json()["data"]["batch"]
    assert batch["package_name"] == "باقة كروت معدلة"
    assert batch["plan_id"] == new_plan.id
    assert batch["time_unit"] == "hours"
    assert batch["device_count"] == 3

    cards = client.get(f"/api/v1/cards/batches/{batch_id}/cards", headers=_auth())
    assert cards.status_code == 200, cards.get_json()
    assert {item["plan_id"] for item in cards.get_json()["data"]["items"]} == {new_plan.id}


def test_card_batch_edit_web_route_shows_speed_rules_entry():
    app = _fresh_app()
    client = app.test_client()
    created_batch = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 1, "username_prefix": "web" + secrets.token_hex(2)},
        headers=_auth(),
    )
    assert created_batch.status_code == 201, created_batch.get_json()
    batch_id = created_batch.get_json()["data"]["batch"]["id"]

    _web_login(client)
    res = client.get(f"/admin/radius/cards/batches/{batch_id}/edit")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "تعديل باقة كروت" in html
    assert "قواعد سرعة هذه الحزمة" in html
    assert "sr_starts_at_time" in html
    assert "sr_source_schedule_id" in html
