from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
from app.radius.services.notification_campaigns import (
    NotificationCampaignError,
    NotificationCampaignService,
    NotificationProvider,
    ProviderResult,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "notification_campaigns.db")
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
        sess["admin_user"] = "comms_admin"
        sess["admin_name"] = "Comms Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "comms-csrf"


class RecordingProvider(NotificationProvider):
    def __init__(self) -> None:
        self.calls = []

    def send(self, *, delivery, notification):
        self.calls.append((delivery, notification))
        return ProviderResult(
            status="queued",
            provider_key="recording",
            result={"external_send": False},
        )


def test_template_render_replaces_known_variables(app):
    with app.app_context():
        service = NotificationCampaignService(tenant_id=1)
        template = service.create_template(
            template_key="renewal",
            title="Renewal",
            channel="sms",
            body="Hello {{ name }}, expires {{ expire_at }}.",
        )
        rendered = service.render_template(template["id"], {"name": "Ali", "expire_at": "tomorrow"})

    assert rendered["body"] == "Hello Ali, expires tomorrow."


def test_audience_filter_scopes_subscribers_under_manager(app):
    with app.app_context():
        db().execute("UPDATE subscribers SET manager_id=1 WHERE id IN (1,2)")
        db().execute("UPDATE subscribers SET manager_id=2 WHERE id IN (3,4)")
        audience = NotificationCampaignService(tenant_id=1).preview_audience(
            {"target": "subscriber", "manager_id": 1, "limit": 20}
        )

    assert {item["recipient_id"] for item in audience} == {1, 2}
    assert all(item["recipient_type"] == "subscriber" for item in audience)


def test_manual_delivery_is_queued_with_provider_abstraction(app):
    provider = RecordingProvider()
    with app.app_context():
        service = NotificationCampaignService(tenant_id=1, provider=provider)
        result = service.send_manual(
            audience={"target": "selected_subscribers", "ids": [1], "limit": 1},
            channel="whatsapp",
            subject="Notice",
            message="Maintenance tonight",
            actor="qa",
        )
        delivery = result["deliveries"][0]["delivery"]

    assert result["queued_count"] == 1
    assert delivery["status"] == "queued"
    assert delivery["provider_key"] == "recording"
    assert len(provider.calls) == 1


def test_unknown_channel_and_audience_are_rejected(app):
    with app.app_context():
        service = NotificationCampaignService(tenant_id=1)
        with pytest.raises(NotificationCampaignError, match="unsupported channel"):
            service.queue_notification(
                recipient_type="subscriber",
                recipient_id=1,
                channel="fax",
                subject="",
                body="Nope",
            )
        with pytest.raises(NotificationCampaignError, match="unsupported audience"):
            service.preview_audience({"target": "everyone-everywhere"})


def test_action_coupled_campaign_is_dry_run_only(app):
    with app.app_context():
        service = NotificationCampaignService(tenant_id=1)
        template = service.create_template(
            template_key="compensation",
            title="Compensation",
            channel="internal",
            body="We added compensation.",
        )
        campaign = service.campaign_dry_run(
            campaign_key="compensation-may",
            title="Compensation May",
            template_id=template["id"],
            audience={"target": "selected_subscribers", "ids": [1, 2]},
            actions=[{"type": "add_free_days"}, {"type": "wallet_credit"}],
            actor="qa",
        )

    assert campaign["status"] == "dry_run_ready"
    assert campaign["dry_run"]["recipient_count"] == 2
    assert {item["status"] for item in campaign["dry_run"]["actions"]} == {"dry_run_only"}


def test_card_user_and_manager_audiences_are_supported(app):
    with app.app_context():
        user = CardUsersMarketplaceService(tenant_id=1).create_card_user(
            display_name="Buyer",
            mobile="0590000000",
        )
        service = NotificationCampaignService(tenant_id=1)
        card_users = service.preview_audience({"target": "card_user", "ids": [user["id"]]})
        managers = service.preview_audience({"target": "manager", "ids": [1]})

    assert card_users[0]["recipient_type"] == "card_user"
    assert card_users[0]["recipient_id"] == user["id"]
    assert managers[0]["recipient_type"] == "manager"


def test_communications_routes_render_and_queue(app):
    with app.test_client() as client:
        _auth_session(client)
        index = client.get("/admin/radius/communications")
        templates = client.get("/admin/radius/communications/templates")
        create_template = client.post(
            "/admin/radius/communications/templates",
            data={
                "_csrf_token": "comms-csrf",
                "template_key": "route-test",
                "title": "Route Test",
                "channel": "internal",
                "body": "Hello",
            },
            follow_redirects=True,
        )
        send = client.post(
            "/admin/radius/communications/send",
            data={
                "_csrf_token": "comms-csrf",
                "target": "selected_subscribers",
                "ids": "1",
                "limit": "1",
                "channel": "internal",
                "message": "Hello",
                "send_now": "1",
            },
            follow_redirects=True,
        )

    assert index.status_code == 200
    assert "communications-summary" in index.get_data(as_text=True)
    assert templates.status_code == 200
    assert create_template.status_code == 200
    assert "route-test" in create_template.get_data(as_text=True)
    assert send.status_code == 200
    assert "delivery-log-table" in send.get_data(as_text=True)
