"""Phase 4 — message quota + paid packages tests.

Exercised entirely WITHOUT network: ``comms_providers.http_send`` is replaced
by a spy that records calls and returns a fake 2xx. We assert:

  * admin_quota with balance N → a real send decrements the balance to N-1 and
    actually calls http_send (consumed exactly one unit);
  * admin_quota with balance 0 → the send is SKIPPED (http_send is NOT called)
    and the delivery carries the Arabic «نفدت كوتة الرسائل» reason;
  * self_api → the send goes out without ever touching the quota counters;
  * credit_quota adds to the balance and appends a ledger entry;
  * consume_quota decrements + floors at zero + ledgers the debit.

The gate lives in ``GenericHttpProvider.send`` (the single real-send chokepoint
reached via ``NotificationCampaignService.queue_notification``), so these tests
drive that exact path.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.radius.services.comms_providers import HttpSendOutcome

KNOWN_PHONE = "0790004455"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "comms_quota.db"))
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


def _seed_subscriber(*, tenant_id=1, username="quota_rami", mobile=KNOWN_PHONE):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="pw",
        user_type="subscriber", full_name="رامي", mobile=mobile,
        balance=0.0, status="enabled", expire_at=None,
    ))


def _configure_channel(channel, *, mode, tenant_id=1):
    """Enable a channel with a usable URL + the given mode."""
    from app.radius.services import comms_providers

    comms_providers.save_channel_config(tenant_id, channel, {
        "enabled": "1",
        "mode": mode,
        "send_url_template": "https://gw.example.com/send?to={phone}&text={msg}",
        "http_method": "GET",
    })


class _HttpSpy:
    """Records http_send calls, returns a fake 2xx success — NO network."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, template, method, phone, message, **kwargs):
        self.calls.append({"phone": phone, "message": message})
        return HttpSendOutcome(ok=True, status_code=200, body_excerpt='{"id":"ok"}', final_url="https://gw.example.com")


def _send_once(channel, body="مرحبا", tenant_id=1, recipient_id=0):
    """Drive the real chokepoint: queue_notification → provider.send → http_send."""
    from app.radius.services.notification_campaigns import NotificationCampaignService

    svc = NotificationCampaignService(tenant_id=tenant_id)
    return svc.queue_notification(
        recipient_type="subscriber",
        recipient_id=int(recipient_id),
        channel=channel,
        subject="",
        body=body,
        actor="test",
    )


# ── helper-level tests ───────────────────────────────────────────────────
def test_credit_quota_adds_and_ledgers(app):
    with app.app_context():
        from app.radius.services import comms_quota

        assert comms_quota.quota_balance(1, "sms") == 0
        new_balance = comms_quota.credit_quota(1, "sms", 50, by="operator", note="حزمة تجريبية")
        assert new_balance == 50
        assert comms_quota.quota_balance(1, "sms") == 50

        ledger = comms_quota.quota_ledger(1, "sms")
        assert len(ledger) == 1
        entry = ledger[-1]
        assert entry["delta"] == 50
        assert entry["balance_after"] == 50
        assert entry["by"] == "operator"
        assert entry["note"] == "حزمة تجريبية"
        assert entry["ts"]

        # A second credit stacks + appends another ledger row.
        assert comms_quota.credit_quota(1, "sms", 10, by="panel") == 60
        assert len(comms_quota.quota_ledger(1, "sms")) == 2


def test_consume_quota_decrements_floors_and_ledgers(app):
    with app.app_context():
        from app.radius.services import comms_quota

        comms_quota.credit_quota(1, "whatsapp", 2, by="op")
        assert comms_quota.consume_quota(1, "whatsapp", 1) == 1
        assert comms_quota.quota_used(1, "whatsapp") == 1
        # draining past zero floors at 0 (never negative)
        assert comms_quota.consume_quota(1, "whatsapp", 5) == 0
        assert comms_quota.quota_balance(1, "whatsapp") == 0
        assert comms_quota.quota_used(1, "whatsapp") == 2  # only the 1 available was spent
        # credit(+) then 2 debits(-) = 3 ledger rows
        assert len(comms_quota.quota_ledger(1, "whatsapp")) == 3


def test_quota_available_reflects_balance(app):
    with app.app_context():
        from app.radius.services import comms_quota

        assert comms_quota.quota_available(1, "sms") is False
        comms_quota.credit_quota(1, "sms", 1, by="op")
        assert comms_quota.quota_available(1, "sms") is True
        comms_quota.consume_quota(1, "sms", 1)
        assert comms_quota.quota_available(1, "sms") is False


# ── send-path gate tests (the heart) ───────────────────────────────────────
def test_admin_quota_send_decrements_balance(app, monkeypatch):
    """admin_quota with balance N → real send happens and balance → N-1."""
    with app.app_context():
        from app.radius.services import comms_providers, comms_quota

        sub = _seed_subscriber()
        _configure_channel("sms", mode="admin_quota")
        comms_quota.credit_quota(1, "sms", 3, by="op")

        spy = _HttpSpy()
        monkeypatch.setattr(comms_providers, "http_send", spy)

        result = _send_once("sms", recipient_id=int(sub.id))

        # The send actually went out (one http call) and was marked sent.
        assert len(spy.calls) == 1
        assert spy.calls[0]["phone"] == KNOWN_PHONE
        assert result["delivery"]["status"] == "sent"
        # Exactly one unit consumed: 3 → 2.
        assert comms_quota.quota_balance(1, "sms") == 2
        assert comms_quota.quota_used(1, "sms") == 1
        # result payload surfaces the post-send balance.
        assert result["delivery"]["result"].get("quota_balance") == 2


def test_admin_quota_zero_balance_skips_send(app, monkeypatch):
    """admin_quota with balance 0 → NO http call, delivery skipped w/ reason."""
    with app.app_context():
        from app.radius.services import comms_providers, comms_quota

        sub = _seed_subscriber()
        _configure_channel("sms", mode="admin_quota")  # balance stays 0

        spy = _HttpSpy()
        monkeypatch.setattr(comms_providers, "http_send", spy)

        result = _send_once("sms", recipient_id=int(sub.id))

        # The gate stopped the send BEFORE any network call.
        assert spy.calls == []
        delivery = result["delivery"]
        assert delivery["status"] == "skipped"
        assert delivery["error_message"] == comms_quota.QUOTA_EXHAUSTED_REASON
        assert delivery["result"].get("reason") == "quota_exhausted"
        # Nothing was consumed (still 0 / 0).
        assert comms_quota.quota_balance(1, "sms") == 0
        assert comms_quota.quota_used(1, "sms") == 0


def test_self_api_send_never_touches_quota(app, monkeypatch):
    """self_api → unlimited: send goes out and the quota counters stay at 0."""
    with app.app_context():
        from app.radius.services import comms_providers, comms_quota

        sub = _seed_subscriber()
        _configure_channel("whatsapp", mode="self_api")
        # Even a pre-existing balance must be ignored in self_api mode.
        comms_quota.credit_quota(1, "whatsapp", 5, by="op")
        used_before = comms_quota.quota_used(1, "whatsapp")
        bal_before = comms_quota.quota_balance(1, "whatsapp")

        spy = _HttpSpy()
        monkeypatch.setattr(comms_providers, "http_send", spy)

        result = _send_once("whatsapp", recipient_id=int(sub.id))

        assert len(spy.calls) == 1
        assert result["delivery"]["status"] == "sent"
        # Quota untouched — no consume in self_api mode.
        assert comms_quota.quota_balance(1, "whatsapp") == bal_before == 5
        assert comms_quota.quota_used(1, "whatsapp") == used_before == 0
        # No quota_balance stamped on a self_api result.
        assert "quota_balance" not in result["delivery"]["result"]


def test_admin_quota_drains_to_zero_then_skips(app, monkeypatch):
    """End-to-end: balance 1 → first send works, second is skipped."""
    with app.app_context():
        from app.radius.services import comms_providers, comms_quota

        sub = _seed_subscriber()
        _configure_channel("sms", mode="admin_quota")
        comms_quota.credit_quota(1, "sms", 1, by="op")

        spy = _HttpSpy()
        monkeypatch.setattr(comms_providers, "http_send", spy)

        first = _send_once("sms", recipient_id=int(sub.id))
        assert first["delivery"]["status"] == "sent"
        assert comms_quota.quota_balance(1, "sms") == 0

        second = _send_once("sms", recipient_id=int(sub.id))
        assert second["delivery"]["status"] == "skipped"
        assert second["delivery"]["error_message"] == comms_quota.QUOTA_EXHAUSTED_REASON
        # Only the first send hit the network.
        assert len(spy.calls) == 1
