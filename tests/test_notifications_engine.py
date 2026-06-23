"""Phase 3 — event-driven notifications engine tests.

These exercise the engine end-to-end WITHOUT any network:
  * an ENABLED rule renders its template (subscriber + extra variables) and
    dispatches to exactly the channels the operator chose — sms/whatsapp via
    the Phase-1 HTTP provider (``http_send`` is spied) and telegram via the
    Phase-2 sender (``send_to_tenant`` is spied);
  * a DISABLED rule is a quiet no-op — nothing is sent on any channel;
  * the ``near_expiry`` dunning template renders ``{days}`` and ``{exp}`` from
    the supplied context + subscriber.

Both senders are monkeypatched with spies, so no real SMS/WhatsApp/Telegram
traffic is ever produced — we only assert *that* the engine tried to send and
*with what text*.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.radius.services.comms_providers import HttpSendOutcome


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "notif_engine.db"))
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


KNOWN_PHONE = "0790005566"


def _seed_subscriber(*, tenant_id=1, username="rami", mobile=KNOWN_PHONE, balance=20.0, expire_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="pw",
        user_type="subscriber", full_name="رامي", mobile=mobile,
        balance=balance, status="enabled", expire_at=expire_at,
    ))


def _enable_http_channels(tenant_id=1):
    """Mark sms + whatsapp channels active so the engine reaches http_send."""
    from app.radius.services import comms_providers

    for ch in ("sms", "whatsapp"):
        comms_providers.save_channel_config(tenant_id, ch, {
            "enabled": "1",
            "send_url_template": "https://gw.example.com/send?to={phone}&text={msg}",
            "http_method": "GET",
        })


class _HttpSpy:
    """Records http_send calls, returns a fake 2xx success — no network."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, template, method, phone, message, **kwargs):
        self.calls.append({"phone": phone, "message": message})
        return HttpSendOutcome(ok=True, status_code=200, body_excerpt='{"id":"ok"}', final_url="https://gw.example.com")


class _TgSpy:
    """Records telegram send_to_tenant calls, returns (True, '') — no network."""

    def __init__(self):
        self.calls = []

    def __call__(self, tenant_id, text):
        self.calls.append({"tenant_id": tenant_id, "text": text})
        return True, ""


class _TgChatSpy:
    """Records subscriber-chat telegram (send_to_chat) calls. No network."""

    def __init__(self):
        self.calls = []

    def __call__(self, tenant_id, chat_id, text):
        self.calls.append({"tenant_id": tenant_id, "chat_id": chat_id, "text": text})
        return True, ""


def test_enabled_rule_renders_and_dispatches_to_chosen_channels(app, monkeypatch):
    """An enabled rule fans out to exactly the chosen channels with rendered text."""
    with app.app_context():
        sub = _seed_subscriber(balance=20.0)
        # Subscriber connected their Telegram → billing/subscriber events now
        # deliver Telegram to THEIR chat (not the operator chat).
        from app.radius.db.connection import transaction
        with transaction() as _c:
            _c.execute("UPDATE subscribers SET telegram_chat_id='cust777' "
                       "WHERE tenant_id=1 AND username=?", (sub.username,))
        _enable_http_channels()
        from app.radius.services import notifications_engine as ne, comms_providers, telegram_notifier

        # recharge_added: enable on all three channels with a template using
        # both a subscriber variable ({balance}) and an extra ({amount}).
        ne.save_rules(1, {
            "recharge_added__enabled": "1",
            "recharge_added__channels": ["sms", "whatsapp", "telegram"],
            "recharge_added__template": "تم شحن {amount}. رصيدك: {balance}",
        }, only_keys=["recharge_added"])

        http_spy = _HttpSpy()
        tg_spy = _TgSpy()
        tg_chat_spy = _TgChatSpy()
        monkeypatch.setattr(comms_providers, "http_send", http_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_tenant", tg_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_chat", tg_chat_spy)

        outcome = ne.notify_event(
            "recharge_added", tenant_id=1, subscriber=sub, context={"amount": "5 د.أ"}
        )

        # Fired, on all three chosen channels, each reporting success.
        assert outcome.fired is True
        assert set(outcome.channels) == {"sms", "whatsapp", "telegram"}
        assert outcome.sent.get("sms") is True
        assert outcome.sent.get("whatsapp") is True
        assert outcome.sent.get("telegram") is True

        # The rendered message substituted BOTH variable kinds.
        assert "5 د.أ" in outcome.message       # extra context var
        assert "20" in outcome.message            # subscriber balance var
        assert "{amount}" not in outcome.message
        assert "{balance}" not in outcome.message

        # sms + whatsapp went through http_send (2 calls); telegram went to the
        # SUBSCRIBER's chat (send_to_chat), NOT the operator chat (send_to_tenant).
        assert len(http_spy.calls) == 2
        # phone may be E.164-normalised (e.g. +962…) → match the significant digits.
        assert all(KNOWN_PHONE.lstrip("0") in c["phone"] for c in http_spy.calls)
        assert all("5 د.أ" in c["message"] for c in http_spy.calls)
        assert len(tg_chat_spy.calls) == 1 and tg_chat_spy.calls[0]["chat_id"] == "cust777"
        assert "5 د.أ" in tg_chat_spy.calls[0]["text"]
        assert tg_spy.calls == []                 # operator chat NOT used for billing


def test_disabled_rule_sends_nothing(app, monkeypatch):
    """A disabled rule is a quiet no-op — no channel is touched."""
    with app.app_context():
        sub = _seed_subscriber()
        _enable_http_channels()
        from app.radius.services import notifications_engine as ne, comms_providers, telegram_notifier

        ne.save_rules(1, {
            "recharge_added__enabled": "0",  # ← OFF
            "recharge_added__channels": ["sms", "whatsapp", "telegram"],
            "recharge_added__template": "تم شحن {amount}",
        })

        http_spy = _HttpSpy()
        tg_spy = _TgSpy()
        monkeypatch.setattr(comms_providers, "http_send", http_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_tenant", tg_spy)

        outcome = ne.notify_event("recharge_added", tenant_id=1, subscriber=sub, context={"amount": "5"})

        assert outcome.fired is False
        assert outcome.reason == "disabled"
        assert http_spy.calls == []
        assert tg_spy.calls == []


def test_rule_with_no_channels_is_noop(app, monkeypatch):
    """Enabled but with every channel unticked → nothing is sent."""
    with app.app_context():
        sub = _seed_subscriber()
        _enable_http_channels()
        from app.radius.services import notifications_engine as ne, comms_providers, telegram_notifier

        ne.save_rules(1, {
            "recharge_added__enabled": "1",
            "recharge_added__channels": [],  # ← no channels
            "recharge_added__template": "تم شحن {amount}",
        })

        http_spy = _HttpSpy()
        tg_spy = _TgSpy()
        monkeypatch.setattr(comms_providers, "http_send", http_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_tenant", tg_spy)

        outcome = ne.notify_event("recharge_added", tenant_id=1, subscriber=sub, context={"amount": "5"})

        assert outcome.fired is False
        assert outcome.reason == "no_channels"
        assert http_spy.calls == []
        assert tg_spy.calls == []


def test_near_expiry_template_renders_days_and_exp(app, monkeypatch):
    """The dunning template renders {days} (extra) and {exp} (subscriber)."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        soon = datetime.now(timezone.utc) + timedelta(days=3)
        sub = _seed_subscriber(username="dunny", expire_at=soon)
        _enable_http_channels()
        from app.radius.services import notifications_engine as ne, comms_providers, telegram_notifier

        ne.save_rules(1, {
            "near_expiry__enabled": "1",
            "near_expiry__channels": ["sms"],
            "near_expiry__template": "اشتراك {username} ينتهي خلال {days} يوم بتاريخ {exp}",
            "near_expiry__days_before": "5",
        })

        http_spy = _HttpSpy()
        tg_spy = _TgSpy()
        monkeypatch.setattr(comms_providers, "http_send", http_spy)
        monkeypatch.setattr(telegram_notifier, "send_to_tenant", tg_spy)

        outcome = ne.notify_event("near_expiry", tenant_id=1, subscriber=sub, context={"days": 3})

        assert outcome.fired is True
        assert outcome.sent.get("sms") is True
        # {days} substituted from the context, {exp} from the subscriber record.
        assert "3" in outcome.message
        assert "dunny" in outcome.message
        exp_date = soon.strftime("%Y-%m-%d")
        assert exp_date in outcome.message
        assert "{days}" not in outcome.message
        assert "{exp}" not in outcome.message


def test_unknown_event_is_safe_noop(app):
    """An unknown event key never raises and reports unknown_event."""
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        outcome = ne.notify_event("does_not_exist", tenant_id=1)
        assert outcome.fired is False
        assert outcome.reason == "unknown_event"


def test_defaults_prepopulate_every_event(app):
    """load_rules ships the full registry pre-filled so the page works OOTB."""
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        rules = ne.load_rules(1)
        assert len(rules) == len(ne.EVENT_KEYS)
        # near_expiry has a sane default window and a non-empty template.
        near = next(r for r in rules if r.key == "near_expiry")
        assert near.days_before >= 1
        assert near.template.strip()
        # A few lifecycle events are ON by default (works out of the box).
        activated = next(r for r in rules if r.key == "subscriber_activated")
        assert activated.enabled is True
        assert activated.active_channels()  # has at least one default channel
