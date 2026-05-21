"""Operations foundation tests for distributors, live states, schedules and backups."""
from __future__ import annotations

import secrets

import pytest

@pytest.fixture
def app(monkeypatch):
    token = "ops-test-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(client) -> dict:
    return {"Authorization": "Bearer " + client.application.config["TEST_API_TOKEN"]}


def _batch(client) -> dict:
    prefix = "op" + secrets.token_hex(3)
    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": 1, "count": 2, "username_prefix": prefix},
        headers=_auth(client),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["batch"]


def test_distributor_create_assign_batch_and_scoped_listing(client):
    created = client.post(
        "/api/v1/distributors",
        json={
            "name": "dist_" + secrets.token_hex(4),
            "display_name": "North Distributor",
            "permissions": ["cards.read", "cards.sell"],
            "scope": {"card_batches": "assigned"},
            "credit_limit": 100,
        },
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    distributor = created.get_json()["data"]["distributor"]
    assert distributor["permissions_json"] == ["cards.read", "cards.sell"]
    assert distributor["scope_json"]["card_batches"] == "assigned"

    batch = _batch(client)
    assigned = client.post(
        f"/api/v1/distributors/{distributor['id']}/assign-batch",
        json={"batch_id": batch["id"], "notes": "initial assignment"},
        headers=_auth(client),
    )
    assert assigned.status_code == 200, assigned.get_json()
    assert assigned.get_json()["data"]["assignment"]["batch_id"] == batch["id"]

    listed = client.get(
        f"/api/v1/distributors/{distributor['id']}/batches",
        headers=_auth(client),
    )
    assert listed.status_code == 200, listed.get_json()
    items = listed.get_json()["data"]["items"]
    assert [item["id"] for item in items] == [batch["id"]]

    fetched_batch = client.get(f"/api/v1/cards/batches/{batch['id']}", headers=_auth(client))
    assert fetched_batch.status_code == 200
    assert fetched_batch.get_json()["data"]["distributor_id"] == distributor["id"]


def test_distributor_settlement_tracks_debt_and_balance(client):
    created = client.post(
        "/api/v1/distributors",
        json={"name": "settle_" + secrets.token_hex(4)},
        headers=_auth(client),
    )
    distributor = created.get_json()["data"]["distributor"]

    debit = client.post(
        f"/api/v1/distributors/{distributor['id']}/settle",
        json={"amount": 75, "direction": "debit", "entry_type": "card_sale"},
        headers=_auth(client),
    )
    assert debit.status_code == 201, debit.get_json()

    credit = client.post(
        f"/api/v1/distributors/{distributor['id']}/settle",
        json={"amount": 25, "direction": "credit", "entry_type": "settlement"},
        headers=_auth(client),
    )
    assert credit.status_code == 201, credit.get_json()

    summary = client.get(
        f"/api/v1/distributors/{distributor['id']}/summary",
        headers=_auth(client),
    )
    assert summary.status_code == 200
    data = summary.get_json()["data"]["summary"]
    assert data["ledger"]["debit"] == 75
    assert data["ledger"]["credit"] == 25
    assert data["debt_balance"] == 50


def test_online_state_classifier_covers_customer_states():
    from datetime import datetime, timedelta
    from app.radius.services.operations import classify_online_state

    assert classify_online_state(account_status="enabled")["state"] == "online"
    assert classify_online_state(account_status="disabled")["state"] == "frozen"
    assert classify_online_state(account_status="expired")["state"] == "expired"
    assert classify_online_state(expire_at=datetime.utcnow() - timedelta(days=1))["state"] == "expired"
    assert classify_online_state(is_online=False)["state"] == "disconnected"


def test_profile_advanced_options_are_saved_and_validated(client):
    patch = client.patch(
        "/api/v1/profiles/1",
        json={
            "service_scope": "hotspot",
            "loan_enabled": True,
            "max_loan_minutes": 180,
            "speed_override_allowed": True,
        },
        headers=_auth(client),
    )
    assert patch.status_code == 200, patch.get_json()
    data = patch.get_json()["data"]
    assert data["service_scope"] == "hotspot"
    assert data["loan_enabled"] is True
    assert data["max_loan_minutes"] == 180
    assert data["speed_override_allowed"] is True

    bad = client.patch(
        "/api/v1/profiles/1",
        json={"service_scope": "satellite"},
        headers=_auth(client),
    )
    assert bad.status_code == 422


def test_bandwidth_schedule_validation_and_planned_apply(client):
    valid = client.post(
        "/api/v1/bandwidth-schedules",
        json={
            "plan_id": 1,
            "name": "Night boost " + secrets.token_hex(3),
            "starts_at_time": "22:00",
            "ends_at_time": "06:00",
            "speed_down_kbps": 3072,
            "speed_up_kbps": 1024,
        },
        headers=_auth(client),
    )
    assert valid.status_code == 201, valid.get_json()
    schedule = valid.get_json()["data"]["schedule"]

    applied = client.post(
        f"/api/v1/bandwidth-schedules/{schedule['id']}/apply",
        json={},
        headers=_auth(client),
    )
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["data"]["applied_to_radius"] is False
    assert applied.get_json()["data"]["log"]["status"] == "planned"

    invalid = client.post(
        "/api/v1/bandwidth-schedules",
        json={"plan_id": 1, "name": "bad", "starts_at_time": "25:00", "ends_at_time": "06:00"},
        headers=_auth(client),
    )
    assert invalid.status_code == 422


def test_print_template_persistence_and_json_preview(client):
    created = client.post(
        "/api/v1/print-templates",
        json={
            "name": "template_" + secrets.token_hex(4),
            "orientation": "landscape",
            "cards_per_row": 3,
            "cards_per_column": 4,
            "show_qr": True,
            "username_x": 10,
            "username_y": 15,
            "font_size": 11,
        },
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    template = created.get_json()["data"]["template"]
    assert template["cards_per_row"] == 3
    assert template["cards_per_column"] == 4

    preview = client.post(
        f"/api/v1/print-templates/{template['id']}/render",
        json={"sample": {"username": "QA123", "has_password": True}},
        headers=_auth(client),
    )
    assert preview.status_code == 200, preview.get_json()
    data = preview.get_json()["data"]
    assert data["preview"]["renderer"] == "visual_card_preview"
    assert data["preview"]["cards_per_page"] == 12
    assert data["preview"]["card"]["width_mm"] == 85
    assert "username" in data["preview"]["placements"]
    assert data["export_generated"] is False

    export = client.get(
        f"/api/v1/print-templates/{template['id']}/export.pdf",
        headers=_auth(client),
    )
    assert export.status_code == 200, export.get_json()
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")


def test_print_template_presets_update_batch_export_and_jobs(client):
    # The old helpers (_card_snapshot_metrics, _pdf_safe_latin,
    # _scaled_card_rect) were removed when the PDF export moved onto
    # the unified card_renderer. The behaviour they used to validate
    # — pure builder, consistent canvas units — is now covered by
    # tests/test_card_renderer.py. Keep one minimal smoke-check on
    # the new renderer so this end-to-end test still exercises the
    # rendering pipeline before driving the HTTP API below.
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    smoke_template = {
        "username_x": 10, "username_y": 15,
        "password_x": 10, "password_y": 25,
        "qr_x": 60, "qr_y": 12,
        "layout_json": {
            "card_orientation": "horizontal",
            "card_width_mm": 200, "card_height_mm": 100,
            "brand_name": "HobeRadius", "card_title": "Smoke",
            "show_brand": True, "show_username": True,
            "show_password": True, "show_qr": True,
        },
    }
    smoke_model = build_card_render_model(
        smoke_template, {"id": 1, "username": "USR-A", "password": "PWD-A"}
    )
    assert smoke_model["canvas"] == {"width": 1000, "height": 600}
    # The builder is deterministic for the same input.
    assert smoke_model == build_card_render_model(
        smoke_template, {"id": 1, "username": "USR-A", "password": "PWD-A"}
    )
    # SVG embeds the username, masks the password.
    svg = render_card_svg(smoke_model)
    assert "USR-A" in svg
    assert "PWD-A" not in svg

    presets = client.get("/api/v1/print-templates/presets", headers=_auth(client))
    assert presets.status_code == 200, presets.get_json()
    preset_items = presets.get_json()["data"]["items"]
    assert {item["key"] for item in preset_items} >= {
        "modern", "telecom", "aurora", "fiber", "sunset", "matrix"
    }

    created = client.post(
        "/api/v1/print-templates",
        json={
            "name": "ops_room_" + secrets.token_hex(4),
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "page_size": "A4",
            "show_qr": True,
            "layout": {
                "design_preset": "telecom",
                "card_orientation": "vertical",
                "pattern_style": "wave",
                "image_opacity": 0.7,
                "background_image_data_url": "data:image/png;base64,iVBORw0KGgo=",
                "background_image_name": "bg.png",
                "brand_name": "HobeRadius",
                "card_title": "Internet Voucher",
                "show_password": True,
            },
        },
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    template = created.get_json()["data"]["template"]
    assert template["layout_json"]["card_orientation"] == "vertical"
    assert template["layout_json"]["card_width_mm"] <= template["layout_json"]["card_height_mm"]
    assert template["layout_json"]["background_image_name"] == "bg.png"

    updated = client.patch(
        f"/api/v1/print-templates/{template['id']}",
        json={
            "name": template["name"] + " updated",
            "layout": {
                "design_preset": "gold",
                "brand_name": "HobeRadius ISP",
                "card_title": "VIP Internet",
            },
        },
        headers=_auth(client),
    )
    assert updated.status_code == 200, updated.get_json()
    updated_template = updated.get_json()["data"]["template"]
    assert updated_template["name"].endswith("updated")
    assert updated_template["layout_json"]["design_preset"] == "gold"
    assert updated_template["layout_json"]["brand_name"] == "HobeRadius ISP"

    preview = client.post(
        f"/api/v1/print-templates/{template['id']}/render",
        json={"sample": {"username": "SAFE123", "password": "SHOULD_NOT_LEAK"}},
        headers=_auth(client),
    )
    assert preview.status_code == 200, preview.get_json()
    preview_text = str(preview.get_json())
    assert "SHOULD_NOT_LEAK" not in preview_text

    batch = _batch(client)
    export = client.get(
        f"/api/v1/print-templates/{template['id']}/export.pdf?batch_id={batch['id']}",
        headers=_auth(client),
    )
    assert export.status_code == 200, export.get_json()
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")

    resized_sheet = client.patch(
        f"/api/v1/print-templates/{template['id']}",
        json={"cards_per_row": 4, "cards_per_column": 7},
        headers=_auth(client),
    )
    assert resized_sheet.status_code == 200, resized_sheet.get_json()
    export_resized = client.get(
        f"/api/v1/print-templates/{template['id']}/export.pdf?batch_id={batch['id']}",
        headers=_auth(client),
    )
    assert export_resized.status_code == 200, export_resized.get_json()
    assert export_resized.content_type.startswith("application/pdf")
    assert export_resized.data.startswith(b"%PDF")

    jobs = client.get("/api/v1/print-jobs", headers=_auth(client))
    assert jobs.status_code == 200, jobs.get_json()
    job_items = jobs.get_json()["data"]["items"]
    assert job_items[0]["template_id"] == template["id"]
    assert job_items[0]["batch_id"] == batch["id"]
    assert job_items[0]["status"] == "success"
    assert job_items[0]["card_count"] == batch["count"]


def test_backup_status_and_local_run_are_non_destructive(client):
    status = client.get("/api/v1/backups/status", headers=_auth(client))
    assert status.status_code == 200, status.get_json()
    assert status.get_json()["data"]["job"]["target"] == "local"

    run = client.post("/api/v1/backups/run", json={}, headers=_auth(client))
    assert run.status_code == 201, run.get_json()
    payload = run.get_json()["data"]
    assert payload["verified"] is True
    assert payload["run"]["status"] == "success"
    assert payload["run"]["path"].endswith(".sqlite3")
