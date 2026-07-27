"""Operations foundation tests for distributors, live states, schedules and backups."""
from __future__ import annotations

import secrets
import time

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


def test_operations_api_validation_messages_are_arabic(client):
    bad_schedule_list = client.get("/api/v1/bandwidth-schedules?limit=bad", headers=_auth(client))
    assert bad_schedule_list.status_code == 422
    assert bad_schedule_list.get_json()["error"]["message"] == "معرّفات الخطة والحزمة وقيم الترقيم يجب أن تكون أرقامًا صحيحة."

    bad_effective = client.get("/api/v1/bandwidth-schedules/effective?plan_id=bad", headers=_auth(client))
    assert bad_effective.status_code == 422
    assert bad_effective.get_json()["error"]["message"] == "معرّف الخطة ومعرّف حزمة الكروت يجب أن يكونا أرقامًا صحيحة."

    bad_distributors_list = client.get("/api/v1/distributors?limit=bad", headers=_auth(client))
    assert bad_distributors_list.status_code == 422
    assert bad_distributors_list.get_json()["error"]["message"] == "قيم limit و offset يجب أن تكون أرقامًا صحيحة."

    bad_batch = client.post(
        "/api/v1/distributors/1/assign-batch",
        json={"batch_id": "bad"},
        headers=_auth(client),
    )
    assert bad_batch.status_code == 422
    assert bad_batch.get_json()["error"]["message"] == "معرّف حزمة الكروت يجب أن يكون رقمًا صحيحًا."

    missing_batch = client.post(
        "/api/v1/distributors/1/assign-batch",
        json={},
        headers=_auth(client),
    )
    assert missing_batch.status_code == 422
    assert missing_batch.get_json()["error"]["message"] == "اختر حزمة الكروت أولًا."


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


def test_print_template_api_validation_messages_are_arabic(client):
    bad_templates = client.get("/api/v1/print-templates?limit=bad", headers=_auth(client))
    assert bad_templates.status_code == 422
    assert bad_templates.get_json()["error"]["message"] == "قيم limit و offset يجب أن تكون أرقامًا صحيحة."

    bad_jobs = client.get("/api/v1/print-jobs?offset=bad", headers=_auth(client))
    assert bad_jobs.status_code == 422
    assert bad_jobs.get_json()["error"]["message"] == "قيم limit و offset يجب أن تكون أرقامًا صحيحة."

    created = client.post(
        "/api/v1/print-templates",
        json={"name": "bad_export_" + secrets.token_hex(4)},
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    template_id = created.get_json()["data"]["template"]["id"]

    bad_batch = client.get(
        f"/api/v1/print-templates/{template_id}/export.pdf?batch_id=bad",
        headers=_auth(client),
    )
    assert bad_batch.status_code == 422
    assert bad_batch.get_json()["error"]["message"] == "معرّف حزمة الكروت يجب أن يكون رقمًا صحيحًا."


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
        f"/api/v1/print-templates/{template['id']}/export.pdf",
        query_string={
            "batch_id": batch["id"],
            "print_columns": 3,
            "print_rows": 4,
            "print_column_gap_mm": 3,
            "print_row_gap_mm": 5,
            "print_margin_top_mm": 6,
            "print_margin_right_mm": 7,
            "print_margin_bottom_mm": 8,
            "print_margin_left_mm": 9,
        },
        headers=_auth(client),
    )
    assert export.status_code == 200, export.get_json()
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")

    jobs = client.get("/api/v1/print-jobs", headers=_auth(client))
    assert jobs.status_code == 200, jobs.get_json()
    job_items = jobs.get_json()["data"]["items"]
    matching_job = next(
        item for item in job_items
        if item["template_id"] == template["id"] and item["batch_id"] == batch["id"]
    )
    assert matching_job["status"] == "success"
    assert matching_job["card_count"] == batch["count"]
    assert matching_job["metadata_json"]["print_settings"]["columns"] == 3
    assert matching_job["metadata_json"]["print_settings"]["rows"] == 4
    assert matching_job["metadata_json"]["print_settings"]["column_gap_mm"] == 3.0
    assert matching_job["metadata_json"]["print_settings"]["row_gap_mm"] == 5.0
    assert matching_job["metadata_json"]["print_settings"]["margin_top_mm"] == 6.0

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
    matching_resized_job = next(
        item for item in job_items
        if item["template_id"] == template["id"] and item["batch_id"] == batch["id"]
    )
    assert matching_resized_job["status"] == "success"
    assert matching_resized_job["card_count"] == batch["count"]


def test_print_template_async_export_api_can_be_polled_and_downloaded(client):
    created = client.post(
        "/api/v1/print-templates",
        json={
            "name": "async_api_" + secrets.token_hex(4),
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "layout": {
                "design_preset": "modern",
                "render_engine": "en_horizontal",
                "brand_name": "HobeRadius",
                "card_title": "Internet Card",
                "show_qr": True,
            },
        },
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    template = created.get_json()["data"]["template"]

    started = client.post(
        f"/api/v1/print-templates/{template['id']}/export-jobs",
        json={"sample": {"username": "ASYNC-API", "password": "SECRET"}},
        headers=_auth(client),
    )
    assert started.status_code == 202, started.get_json()
    job = started.get_json()["data"]["job"]
    assert job["status"] == "queued"
    assert job["progress"] >= 0

    status_payload = None
    for _ in range(100):
        status = client.get(f"/api/v1/print-jobs/{job['id']}", headers=_auth(client))
        assert status.status_code == 200, status.get_json()
        status_payload = status.get_json()["data"]["job"]
        if status_payload["status"] == "success" and status_payload["download_ready"]:
            break
        time.sleep(0.05)

    assert status_payload is not None
    assert status_payload["status"] == "success"
    assert status_payload["download_ready"] is True
    assert status_payload["progress"] == 100

    download = client.get(f"/api/v1/print-jobs/{job['id']}/download", headers=_auth(client))
    assert download.status_code == 200, download.get_json()
    assert download.content_type.startswith("application/pdf")
    assert download.data.startswith(b"%PDF")


def test_print_template_management_api_matches_web_actions(client):
    created_a = client.post(
        "/api/v1/print-templates",
        json={
            "name": "default_api_" + secrets.token_hex(4),
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "layout": {
                "design_preset": "modern",
                "brand_name": "HobeRadius",
                "card_title": "Internet Card",
                "show_qr": True,
            },
        },
        headers=_auth(client),
    )
    assert created_a.status_code == 201, created_a.get_json()
    template_a = created_a.get_json()["data"]["template"]

    created_b = client.post(
        "/api/v1/print-templates",
        json={
            "name": "default_api_" + secrets.token_hex(4),
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "layout": {
                "design_preset": "telecom",
                "brand_name": "HobeRadius",
                "card_title": "Backup Card",
                "show_qr": True,
            },
        },
        headers=_auth(client),
    )
    assert created_b.status_code == 201, created_b.get_json()
    template_b = created_b.get_json()["data"]["template"]

    first_default = client.post(
        f"/api/v1/print-templates/{template_a['id']}/set-default",
        headers=_auth(client),
    )
    assert first_default.status_code == 200, first_default.get_json()
    assert first_default.get_json()["data"]["template"]["layout_json"]["is_default"] is True

    second_default = client.post(
        f"/api/v1/print-templates/{template_b['id']}/set-default",
        headers=_auth(client),
    )
    assert second_default.status_code == 200, second_default.get_json()
    listed = client.get("/api/v1/print-templates", headers=_auth(client))
    assert listed.status_code == 200, listed.get_json()
    rows = {
        item["id"]: item
        for item in listed.get_json()["data"]["items"]
        if item["id"] in {template_a["id"], template_b["id"]}
    }
    assert rows[template_a["id"]]["layout_json"].get("is_default") is False
    assert rows[template_b["id"]]["layout_json"].get("is_default") is True

    fragment = client.get(
        f"/api/v1/print-templates/{template_b['id']}/preview-fragment",
        headers=_auth(client),
    )
    assert fragment.status_code == 200, fragment.data[:200]
    assert fragment.content_type.startswith("text/html")
    assert b"data-preview-fragment" in fragment.data
    assert b"SHOULD_NOT_LEAK" not in fragment.data

    deleted = client.delete(
        f"/api/v1/print-templates/{template_a['id']}",
        headers=_auth(client),
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["data"]["deleted"] is True

    missing = client.delete(
        f"/api/v1/print-templates/{template_a['id']}",
        headers=_auth(client),
    )
    assert missing.status_code == 404, missing.get_json()

    fixture_name = "template_" + secrets.token_hex(4)
    fixture = client.post(
        "/api/v1/print-templates",
        json={
            "name": fixture_name,
            "layout": {
                "design_preset": "matrix",
                "brand_name": "HobeRadius",
                "card_title": "Fixture",
            },
        },
        headers=_auth(client),
    )
    assert fixture.status_code == 201, fixture.get_json()
    cleanup = client.post(
        "/api/v1/print-templates/cleanup-fixtures",
        headers=_auth(client),
    )
    assert cleanup.status_code == 200, cleanup.get_json()
    assert cleanup.get_json()["data"]["purged"] >= 1
    assert fixture_name in {
        item["name"] for item in cleanup.get_json()["data"]["items"]
    }


def test_recharge_cards_api_generates_lists_and_validates_values(client):
    invalid = client.post(
        "/api/v1/cards/recharge",
        json={
            "package_name": "حزمة شحن غير صالحة",
            "denominations": [{"value": 0, "count": 10}],
        },
        headers=_auth(client),
    )
    assert invalid.status_code == 422, invalid.get_json()

    created = client.post(
        "/api/v1/cards/recharge",
        json={
            "package_name": "حزمة شحن API",
            "notes": "اختبار عقد تطبيق الجوال وسطح المكتب",
            "denominations": [
                {"value": 5, "count": 2},
                {"value": 10, "count": 1},
            ],
        },
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    payload = created.get_json()["data"]
    assert payload["inserted_count"] == 3
    assert payload["total_value"] == 20
    batch = payload["batch"]
    assert batch["package_name"] == "حزمة شحن API"
    assert batch["count"] == 3
    assert batch["remaining_count"] == 3
    assert batch["denominations"] == [
        {"value": 5.0, "count": 2},
        {"value": 10.0, "count": 1},
    ]

    listed = client.get("/api/v1/cards/recharge", headers=_auth(client))
    assert listed.status_code == 200, listed.get_json()
    assert batch["id"] in {
        item["id"] for item in listed.get_json()["data"]["items"]
    }

    detail = client.get(
        f"/api/v1/cards/recharge/{batch['id']}",
        headers=_auth(client),
    )
    assert detail.status_code == 200, detail.get_json()
    detail_data = detail.get_json()["data"]
    assert detail_data["batch"]["total_value"] == 20
    assert detail_data["total_cards"] == 3
    assert len(detail_data["cards"]) == 3
    assert {card["wallet_value"] for card in detail_data["cards"]} == {5.0, 10.0}

    cards = client.get(
        f"/api/v1/cards/recharge/{batch['id']}/cards",
        headers=_auth(client),
    )
    assert cards.status_code == 200, cards.get_json()
    assert cards.get_json()["data"]["total"] == 3

    deleted = client.delete(
        f"/api/v1/cards/recharge/{batch['id']}",
        headers=_auth(client),
    )
    assert deleted.status_code == 200, deleted.get_json()

    missing = client.get(
        f"/api/v1/cards/recharge/{batch['id']}",
        headers=_auth(client),
    )
    assert missing.status_code == 404, missing.get_json()


def test_print_sheet_geometry_respects_visible_margins_and_gaps():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    from app.radius.services.operations import (
        _print_sheet_settings,
        _strict_print_geometry,
    )

    sheet = _print_sheet_settings({
        "print_columns": 5,
        "print_rows": 3,
        "print_column_gap_mm": 4,
        "print_row_gap_mm": 9,
        "print_margin_top_mm": 12,
        "print_margin_right_mm": 13,
        "print_margin_bottom_mm": 14,
        "print_margin_left_mm": 10,
        "print_fit_mode": "uniform",
    })
    geometry = _strict_print_geometry(
        page_width=A4[0],
        page_height=A4[1],
        canvas_width=1000,
        canvas_height=600,
        sheet=sheet,
        unit=mm,
    )

    first = geometry["positions"][0]
    second = geometry["positions"][1]
    next_row = geometry["positions"][5]
    card_width = geometry["card_width"]
    card_height = geometry["card_height"]

    # عرضيًّا البطاقة تملأ العمود (width-limited) → الهامش الأيسر حرفي.
    assert first["x"] == pytest.approx(10 * mm)
    # الفواصل تبقى حرفيًّا كما ضُبطت — لا تتمدد بالفائض.
    assert second["x"] - (first["x"] + card_width) == pytest.approx(4 * mm)
    assert first["y"] - (next_row["y"] + card_height) == pytest.approx(9 * mm)
    # عموديًّا يبقى فائض من قصر النسبة — يتوزّع بالتساوي حول الكتلة
    # (الهوامش حدّ أدنى): فوق = الهامش العلوي + نصف الفائض.
    available_h = A4[1] - (12 + 14) * mm - 2 * (9 * mm)
    leftover_h = available_h - 3 * card_height
    assert leftover_h > 0
    top_gap = A4[1] - (first["y"] + card_height)
    assert top_gap == pytest.approx(12 * mm + leftover_h / 2.0)
    # التماثل: المسافة الزائدة عن الهامش متساوية فوق وتحت.
    last_row = geometry["positions"][-1]
    bottom_gap = last_row["y"]
    assert top_gap - 12 * mm == pytest.approx(bottom_gap - 14 * mm)


def test_print_sheet_grid_block_centered_horizontally():
    """حالة client1 (2026-07-27): بطاقة طولية بشبكة 6×9 على A4 — البطاقات
    كانت تتكدس يسار الورقة ويبقى ~85mm فراغًا يمينًا. الكتلة الآن تتوسط."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    from app.radius.services.operations import (
        _print_sheet_settings,
        _strict_print_geometry,
    )

    sheet = _print_sheet_settings({
        "print_columns": 6,
        "print_rows": 9,
        "print_column_gap_mm": 1,
        "print_row_gap_mm": 1,
        "print_margin_top_mm": 4,
        "print_margin_right_mm": 4,
        "print_margin_bottom_mm": 4,
        "print_margin_left_mm": 4,
        "print_fit_mode": "uniform",
    })
    geometry = _strict_print_geometry(
        page_width=A4[0],
        page_height=A4[1],
        canvas_width=600,      # بطاقة طولية (نفس كانفس القوالب العمودية)
        canvas_height=1000,
        sheet=sheet,
        unit=mm,
    )
    assert geometry["fit_limited_by"] == "height"
    card_width = geometry["card_width"]
    positions = geometry["positions"]
    first = positions[0]
    last_col = positions[5]
    left_gap = first["x"]
    right_gap = A4[0] - (last_col["x"] + card_width)
    # يسار = يمين (توسيط) وكلاهما ≥ الهامش المضبوط.
    assert left_gap == pytest.approx(right_gap)
    assert left_gap >= 4 * mm
    # الفاصل العمودي يبقى حرفيًّا 1mm.
    assert positions[1]["x"] - (first["x"] + card_width) == pytest.approx(1 * mm)


def test_print_sheet_geometry_stretch_fills_slots_exactly():
    """وضع «تمدد يملأ الخانة» (طلب المالك): البطاقة تأخذ خانتها كاملة
    حسب الأعمدة/الصفوف — هوامش وفواصل حرفية، لا فائض ولا التزام بالنسبة."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    from app.radius.services.operations import (
        _print_sheet_settings,
        _strict_print_geometry,
    )

    sheet = _print_sheet_settings({
        "print_columns": 6,
        "print_rows": 9,
        "print_column_gap_mm": 2,
        "print_row_gap_mm": 2,
        "print_margin_top_mm": 4,
        "print_margin_right_mm": 4,
        "print_margin_bottom_mm": 4,
        "print_margin_left_mm": 4,
        "print_fit_mode": "stretch",
    })
    assert sheet["fit_mode"] == "stretch"
    geometry = _strict_print_geometry(
        page_width=A4[0],
        page_height=A4[1],
        canvas_width=600,      # بطاقة طولية — النسبة تُتجاهل في هذا الوضع
        canvas_height=1000,
        sheet=sheet,
        unit=mm,
    )
    assert geometry["stretch"] is True
    available_w = A4[0] - 8 * mm - 5 * (2 * mm)
    available_h = A4[1] - 8 * mm - 8 * (2 * mm)
    assert geometry["card_width"] == pytest.approx(available_w / 6)
    assert geometry["card_height"] == pytest.approx(available_h / 9)
    positions = geometry["positions"]
    first, last_col = positions[0], positions[5]
    # الهوامش حرفية من الجهتين (الشبكة تملأ كامل المساحة المتاحة).
    assert first["x"] == pytest.approx(4 * mm)
    assert A4[0] - (last_col["x"] + geometry["card_width"]) == pytest.approx(4 * mm)
    # الفاصل حرفي.
    assert positions[1]["x"] - (first["x"] + geometry["card_width"]) == pytest.approx(2 * mm)
    # عموديًا: الصف الأول عند الهامش العلوي والأخير عند السفلي.
    assert A4[1] - (first["y"] + geometry["card_height"]) == pytest.approx(4 * mm)
    assert positions[-1]["y"] == pytest.approx(4 * mm)


def test_print_sheet_fit_mode_defaults_to_stretch():
    """التمدد افتراضي خادميًّا (حسم كاش المتصفح) — uniform يُطلب صراحةً."""
    from app.radius.services.operations import _print_sheet_settings

    assert _print_sheet_settings({})["fit_mode"] == "stretch"
    assert _print_sheet_settings({"print_fit_mode": "weird"})["fit_mode"] == "stretch"
    assert _print_sheet_settings({"print_fit_mode": "uniform"})["fit_mode"] == "uniform"


def test_backup_status_and_local_run_are_non_destructive(client):
    status = client.get("/api/v1/backups/status", headers=_auth(client))
    assert status.status_code == 200, status.get_json()
    data = status.get_json()["data"]
    assert data["job"]["target"] == "local"
    assert data["google_drive"]["status"] == "not_configured"
    assert "غير مفعل حاليًا" in data["google_drive"]["message_ar"]

    run = client.post("/api/v1/backups/run", json={}, headers=_auth(client))
    assert run.status_code == 201, run.get_json()
    payload = run.get_json()["data"]
    assert payload["verified"] is True
    assert payload["run"]["status"] == "success"
    # Backups are gzip-compressed by default (.sqlite3.gz); a raw .sqlite3 is
    # still valid when compression is disabled.
    assert payload["run"]["path"].endswith((".sqlite3", ".sqlite3.gz"))


def test_google_drive_connect_contract_returns_arabic_disabled_message(client):
    res = client.post(
        "/api/v1/backups/google-drive/connect",
        json={},
        headers=_auth(client),
    )
    assert res.status_code == 501
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_implemented"
    assert "غير مفعل حاليًا" in body["error"]["message"]
    assert "OAuth is intentionally not enabled" not in body["error"]["message"]
