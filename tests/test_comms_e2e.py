"""End-to-end smoke for the Communications / Operations Center.

Drives a realistic flow with **NO network** (the TweetSMS HTTP GET and the
Telegram sender are both replaced by recording spies), and asserts that
everything is *recorded* the way the Operations Center hub relies on:

  (a) Connect the tenant's TweetSMS account (api_key + sender, enabled) →
      ``queue_notification(channel="sms")`` creates a ``message_deliveries`` row
      with status ``sent`` and calls the TweetSMS adapter exactly once.

  (b) Enable a notification rule (``subscriber_created``) for sms + telegram →
      ``notify_event(...)`` dispatches the SMS through TweetSMS (one more GET)
      AND calls the Telegram ``send_to_tenant`` once.

SMS is a FREE bring-your-own-provider service now: there is no admin-sold
message bundle / quota. These exercise the real services end-to-end through the
same chokepoints the UI uses — without duplicating any logic.
"""
from __future__ import annotations

import os
import sys

import pytest

KNOWN_PHONE = "0790007788"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "comms_e2e.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app

    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_subscriber(*, tenant_id=1, username="e2e_rami", mobile=KNOWN_PHONE, balance=10.0):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="pw",
        user_type="subscriber", full_name="رامي", mobile=mobile,
        balance=balance, status="enabled", expire_at=None,
    ))


def _connect_tweetsms(*, tenant_id=1, sender="HOBE"):
    from app.radius.db.repos import tenant_sms_settings_repo

    tenant_sms_settings_repo.upsert(
        tenant_id=tenant_id, provider="tweetsms",
        api_key="e2e-test-key", sender=sender, enabled=True,
    )


class _TweetSpy:
    """Records TweetSMS GET calls, returns a fake success line — NO network.

    Mirrors the adapter's ``_http_get`` signature: returns
    ``(ok, status, text, error_ar)``. The text is a per-number success line
    ``1:<id>:<mobile>`` so ``parse_send_response`` yields a sent result.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, url, timeout=12.0):
        self.calls.append(url)
        return True, 200, "1:556677:972790007788", ""


class _TgSpy:
    """Records telegram send_to_chat calls, returns (True, '') — NO network.

    Subscriber-facing events deliver Telegram to the SUBSCRIBER's own connected
    chat (``subscribers.telegram_chat_id``) via ``send_to_chat``.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, tenant_id, chat_id, text):
        self.calls.append({"tenant_id": tenant_id, "chat_id": chat_id, "text": text})
        return True, ""


def test_e2e_communications_operations_center(app, monkeypatch):
    """One realistic flow: TweetSMS connect → queue → event dispatch."""
    with app.app_context():
        from app.radius.services import tweetsms, notifications_engine as ne, telegram_notifier
        from app.radius.services.notification_campaigns import NotificationCampaignService

        sub = _seed_subscriber()
        # Link a Telegram chat so the subscriber-facing telegram path has a target.
        from app.radius.db.connection import db
        db().execute("UPDATE subscribers SET telegram_chat_id=? WHERE id=?", ("555000", int(sub.id)))
        svc = NotificationCampaignService(tenant_id=1)

        tweet_spy = _TweetSpy()
        tg_spy = _TgSpy()
        # Spy at the adapter's single network chokepoint so the full adapter
        # path (build URL → parse response → ProviderResult) is exercised.
        monkeypatch.setattr(tweetsms, "_http_get", tweet_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_chat", tg_spy)

        # ── (a) connect TweetSMS + queue a notification ─────────────────────
        _connect_tweetsms()
        result = svc.queue_notification(
            recipient_type="subscriber",
            recipient_id=int(sub.id),
            channel="sms",
            subject="",
            body="مرحبًا من غرفة العمليات",
            actor="e2e",
        )
        delivery = result["delivery"]
        assert delivery["status"] == "sent", f"expected sent, got {delivery['status']} ({delivery.get('error_message')})"
        assert delivery["channel"] == "sms"
        assert delivery["recipient_address"] == KNOWN_PHONE
        # The TweetSMS adapter hit the network exactly once for this send.
        assert len(tweet_spy.calls) == 1
        assert "comm=sendsms" in tweet_spy.calls[0]
        # A real message_deliveries row exists and the dashboard counts it.
        assert svc.dashboard()["sent"] >= 1
        assert any(d["status"] == "sent" for d in svc.delivery_log(limit=10))

        # ── (b) enable subscriber_created for sms + telegram, then fire ─────
        ne.save_rules(1, {
            "subscriber_created__enabled": "1",
            "subscriber_created__channels": ["sms", "telegram"],
            "subscriber_created__template": "تم إنشاء حساب {username} ({name})",
        })
        sms_before = len(tweet_spy.calls)
        outcome = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)

        assert outcome.fired is True
        assert set(outcome.channels) == {"sms", "telegram"}
        assert outcome.sent.get("sms") is True
        assert outcome.sent.get("telegram") is True
        # SMS went out via TweetSMS (one more call), telegram via send_to_chat.
        assert len(tweet_spy.calls) == sms_before + 1
        assert len(tg_spy.calls) == 1
        assert tg_spy.calls[0]["chat_id"] == "555000"
        assert "e2e_rami" in tg_spy.calls[0]["text"]
        # The rendered message substituted the subscriber variables.
        assert "{username}" not in outcome.message and "e2e_rami" in outcome.message
