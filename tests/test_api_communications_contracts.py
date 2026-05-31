from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


AUTH = {"Authorization": "Bearer communications-api-token"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "communications_api.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "communications-api-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _data(response):
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_communications_api_creates_template_segment_and_preview(client):
    created = client.post(
        "/api/v1/communications/templates",
        headers=AUTH,
        json={
            "template_key": "renewal-reminder",
            "title": "تذكير تجديد",
            "channel": "internal",
            "subject": "تذكير",
            "body": "أهلًا {{name}}",
            "variables": ["name"],
        },
    )
    assert created.status_code == 201
    template = _data(created)["template"]
    assert template["template_key"] == "renewal-reminder"

    listed = client.get("/api/v1/communications/templates", headers=AUTH)
    assert listed.status_code == 200
    assert _data(listed)["items"][0]["id"] == template["id"]

    segment = client.post(
        "/api/v1/communications/audience",
        headers=AUTH,
        json={
            "segment_key": "company-alerts",
            "title": "تنبيهات الشركة",
            "target": "company",
            "limit": 20,
        },
    )
    assert segment.status_code == 201
    segment_data = _data(segment)
    assert segment_data["segment"]["segment_key"] == "company-alerts"
    assert segment_data["preview"][0]["recipient_type"] == "company"

    preview = client.post(
        "/api/v1/communications/audience/preview",
        headers=AUTH,
        json={"target": "company"},
    )
    assert preview.status_code == 200
    assert _data(preview)["count"] == 1


def test_communications_api_queues_manual_message_and_campaign_dry_run(client):
    template = _data(
        client.post(
            "/api/v1/communications/templates",
            headers=AUTH,
            json={
                "template_key": "maintenance",
                "title": "صيانة",
                "channel": "internal",
                "body": "يوجد صيانة الليلة",
            },
        )
    )["template"]

    queued = client.post(
        "/api/v1/communications/send",
        headers=AUTH,
        json={
            "target": "company",
            "channel": "internal",
            "subject": "صيانة",
            "message": "يوجد صيانة الليلة",
        },
    )
    assert queued.status_code == 201
    assert _data(queued)["queued_count"] == 1

    deliveries = client.get("/api/v1/communications/deliveries", headers=AUTH)
    assert deliveries.status_code == 200
    delivery = _data(deliveries)["items"][0]
    assert delivery["status"] == "queued"
    assert delivery["recipient_type"] == "company"

    campaign = client.post(
        "/api/v1/communications/campaigns",
        headers=AUTH,
        json={
            "campaign_key": "maintenance-dry-run",
            "title": "حملة الصيانة",
            "template_id": template["id"],
            "target": "company",
            "actions": ["record_event"],
        },
    )
    assert campaign.status_code == 201
    campaign_data = _data(campaign)["campaign"]
    assert campaign_data["status"] == "dry_run_ready"
    assert campaign_data["dry_run"]["recipient_count"] == 1
    assert campaign_data["dry_run"]["external_send"] is False

    summary = client.get("/api/v1/communications/summary", headers=AUTH)
    assert summary.status_code == 200
    assert _data(summary)["summary"]["templates"] == 1


def test_communications_api_rejects_invalid_channel_in_arabic(client):
    response = client.post(
        "/api/v1/communications/templates",
        headers=AUTH,
        json={
            "template_key": "bad-channel",
            "title": "قناة خاطئة",
            "channel": "fax",
            "body": "نص",
        },
    )

    assert response.status_code == 422
    payload = response.get_json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "القناة غير مدعومة."
