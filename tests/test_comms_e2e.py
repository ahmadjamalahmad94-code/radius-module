"""Phase 6 — end-to-end smoke for the Communications / Operations Center.

Drives a realistic, multi-phase flow with **NO network** (the Phase-1 HTTP
sender and the Phase-2 Telegram sender are both replaced by recording spies),
and asserts that everything is *recorded* the way the Operations Center hub
relies on:

  (a) Configure the SMS channel (mode ``self_api``, enabled, a ``{phone}``/
      ``{msg}`` URL) → ``queue_notification(channel="sms")`` creates a
      ``message_deliveries`` row with status ``sent`` and calls ``http_send``
      exactly once.

  (b) Enable a notification rule (``subscriber_created``) for sms + telegram →
      ``notify_event(...)`` dispatches the SMS (one more ``http_send``) AND
      calls the Telegram ``send_to_tenant`` once.

  (c) Switch SMS to ``admin_quota`` with balance 2 → two ``notify_event`` sends
      succeed (balance 2 → 0), the third is SKIPPED with the Arabic reason
      «نفدت كوتة الرسائل» and burns no quota.

These exercise the real phase services end-to-end through the same chokepoints
the UI uses — without duplicating any logic.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.radius.services.comms_providers import HttpSendOutcome

KNOWN_PHONE = "0790007788"
SMS_URL = "https://gw.example.com/send?to={phone}&text={msg}"


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


def _configure_sms(*, mode, tenant_id=1):
    from app.radius.services import comms_providers

    comms_providers.save_channel_config(tenant_id, "sms", {
        "enabled": "1",
        "mode": mode,
        "send_url_template": SMS_URL,
        "http_method": "GET",
    })


class _HttpSpy:
    """Records http_send calls, returns a fake 2xx success — NO network."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, template, method, phone, message, **kwargs):
        self.calls.append({"template": template, "phone": phone, "message": message})
        return HttpSendOutcome(ok=True, status_code=200, body_excerpt='{"id":"ok"}', final_url="https://gw.example.com")


class _TgSpy:
    """Records telegram send_to_tenant calls, returns (True, '') — NO network."""

    def __init__(self):
        self.calls = []

    def __call__(self, tenant_id, text):
        self.calls.append({"tenant_id": tenant_id, "text": text})
        return True, ""


def test_e2e_communications_operations_center(app, monkeypatch):
    """One realistic flow across phases 1-4, asserting recording at each step."""
    with app.app_context():
        from app.radius.services import comms_providers, comms_quota, notifications_engine as ne, telegram_notifier
        from app.radius.services.notification_campaigns import NotificationCampaignService

        sub = _seed_subscriber()
        svc = NotificationCampaignService(tenant_id=1)

        http_spy = _HttpSpy()
        tg_spy = _TgSpy()
        monkeypatch.setattr(comms_providers, "http_send", http_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_tenant", tg_spy)

        # ── (a) configure SMS (self_api) + queue a notification ─────────────
        _configure_sms(mode="self_api")
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
        # http_send called exactly once for this single send.
        assert len(http_spy.calls) == 1
        assert http_spy.calls[0]["phone"] == KNOWN_PHONE
        assert "غرفة العمليات" in http_spy.calls[0]["message"]
        # A real message_deliveries row exists and the dashboard counts it.
        assert svc.dashboard()["sent"] >= 1
        assert any(d["status"] == "sent" for d in svc.delivery_log(limit=10))

        # ── (b) enable subscriber_created for sms + telegram, then fire ─────
        ne.save_rules(1, {
            "subscriber_created__enabled": "1",
            "subscriber_created__channels": ["sms", "telegram"],
            "subscriber_created__template": "تم إنشاء حساب {username} ({name})",
        })
        http_before = len(http_spy.calls)
        outcome = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)

        assert outcome.fired is True
        assert set(outcome.channels) == {"sms", "telegram"}
        assert outcome.sent.get("sms") is True
        assert outcome.sent.get("telegram") is True
        # SMS went out via http_send (one more call), telegram via send_to_tenant.
        assert len(http_spy.calls) == http_before + 1
        assert len(tg_spy.calls) == 1
        assert "e2e_rami" in tg_spy.calls[0]["text"]
        # The rendered message substituted the subscriber variables.
        assert "{username}" not in outcome.message and "e2e_rami" in outcome.message

        # ── (c) admin_quota with balance 2 → 2 ok, 3rd skipped ──────────────
        _configure_sms(mode="admin_quota")
        # Notify SMS-only so each fire consumes exactly one SMS quota unit.
        ne.save_rules(1, {
            "subscriber_created__enabled": "1",
            "subscriber_created__channels": ["sms"],
            "subscriber_created__template": "تنبيه لـ {username}",
        })
        comms_quota.credit_quota(1, "sms", 2, by="e2e")
        assert comms_quota.quota_balance(1, "sms") == 2

        http_at_c = len(http_spy.calls)

        o1 = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        o2 = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        # First two: sent, each consuming one unit (2 → 1 → 0).
        assert o1.sent.get("sms") is True
        assert o2.sent.get("sms") is True
        assert comms_quota.quota_balance(1, "sms") == 0
        assert comms_quota.quota_used(1, "sms") == 2
        # Two real network sends happened.
        assert len(http_spy.calls) == http_at_c + 2

        # Third: quota exhausted → SKIPPED, NO network call, Arabic reason.
        o3 = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        assert o3.fired is True               # the rule fired …
        assert o3.sent.get("sms") is False    # … but nothing was actually sent
        assert comms_quota.QUOTA_EXHAUSTED_REASON in (o3.errors.get("sms") or "")
        # The gate stopped the send BEFORE http_send — no extra network call.
        assert len(http_spy.calls) == http_at_c + 2
        # Balance untouched at zero.
        assert comms_quota.quota_balance(1, "sms") == 0

        # The skipped attempt still recorded a delivery row carrying the reason.
        skipped = [d for d in svc.delivery_log(limit=20)
                   if d["status"] == "skipped" and comms_quota.QUOTA_EXHAUSTED_REASON in (d.get("error_message") or "")]
        assert skipped, "expected a skipped delivery row with the quota-exhausted reason"
