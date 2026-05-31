"""Phase 2 — WhatsApp bot inbound webhook tests.

These tests exercise the bot end-to-end WITHOUT any network:
  * an inbound message with a known command ("الرصيد" / "balance") for a known
    subscriber builds the reply from the subscriber's data AND attempts a send
    through the Phase-1 WhatsApp provider (``http_send`` is monkeypatched);
  * a disabled bot is a quiet no-op (no send attempted), and the webhook still
    answers 200.

The provider's ``http_send`` is replaced with a spy so no real HTTP call is
ever made — we only assert that the bot *tried* to send and with what text.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.radius.services.comms_providers import HttpSendOutcome


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "comms_bot.db"))
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


@pytest.fixture
def client(app):
    return app.test_client()


WEBHOOK = "/admin/radius/communications/bot/webhook"
KNOWN_PHONE = "0790001122"


def _seed_subscriber(*, tenant_id=1, mobile=KNOWN_PHONE):
    """Create a subscriber with a known phone + a non-zero balance."""
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username="ahmad", password="pw",
        full_name="أحمد", mobile=mobile, balance=12.5, status="enabled",
    ))


def _enable_whatsapp_channel(tenant_id=1):
    """Mark the WhatsApp channel active so the bot proceeds to http_send."""
    from app.radius.services import comms_providers

    comms_providers.save_channel_config(tenant_id, "whatsapp", {
        "enabled": "1",
        "send_url_template": "https://gw.example.com/send?to={phone}&text={msg}",
        "http_method": "GET",
    })


class _Spy:
    """Records http_send calls and returns a fake success — never hits network."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, template, method, phone, message, **kwargs):
        self.calls.append({"template": template, "method": method, "phone": phone, "message": message})
        return HttpSendOutcome(ok=True, status_code=200, body_excerpt='{"id":"x"}', final_url="https://gw.example.com")


def test_inbound_balance_command_builds_reply_and_sends(app, client, monkeypatch):
    """A known subscriber texts «الرصيد» → reply is built + a send is attempted."""
    with app.app_context():
        _seed_subscriber()
        _enable_whatsapp_channel()
        from app.radius.services import comms_bot

        # Enable the bot with a balance command that uses the {balance} variable.
        comms_bot.save_bot_config(1, {
            "enabled": "1",
            "greeting": "مرحبا",
            "fallback": "لم أفهم",
            "commands": [
                {"keyword": "الرصيد", "reply_template": "رصيدك: {balance}", "enabled": "1"},
                {"keyword": "balance", "reply_template": "Your balance: {balance}", "enabled": "1"},
            ],
        })

    # Spy on http_send (the only place a real network call could happen).
    spy = _Spy()
    monkeypatch.setattr("app.radius.services.comms_providers.http_send", spy)

    # Arabic command via JSON body (common gateway shape: phone + message).
    resp = client.post(WEBHOOK, json={"phone": KNOWN_PHONE, "message": "الرصيد"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["handled"] is True
    assert body["reason"] == "command"

    # Exactly one send was attempted, to the sender, with the rendered balance.
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["phone"] == KNOWN_PHONE
    assert "12.5" in call["message"]  # balance substituted from the subscriber
    assert "{balance}" not in call["message"]  # variable was replaced

    # English alias works too, through a different field name ("text").
    resp2 = client.post(WEBHOOK, json={"from": KNOWN_PHONE, "text": "balance"})
    assert resp2.status_code == 200
    assert resp2.get_json()["reason"] == "command"
    assert len(spy.calls) == 2
    assert "12.5" in spy.calls[1]["message"]


def test_disabled_bot_is_a_noop(app, client, monkeypatch):
    """When the bot is disabled the webhook answers 200 and never sends."""
    with app.app_context():
        _seed_subscriber()
        _enable_whatsapp_channel()
        from app.radius.services import comms_bot

        comms_bot.save_bot_config(1, {
            "enabled": "0",  # ← bot OFF
            "commands": [{"keyword": "الرصيد", "reply_template": "رصيدك: {balance}", "enabled": "1"}],
        })

    spy = _Spy()
    monkeypatch.setattr("app.radius.services.comms_providers.http_send", spy)

    resp = client.post(WEBHOOK, json={"phone": KNOWN_PHONE, "message": "الرصيد"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["handled"] is False
    assert body["reason"] == "disabled"
    # No send attempted at all.
    assert spy.calls == []


def test_handle_inbound_unit_balance(app, monkeypatch):
    """Direct service-level check: handle_inbound renders + reports a send."""
    with app.app_context():
        _seed_subscriber()
        _enable_whatsapp_channel()
        from app.radius.services import comms_bot, comms_providers

        comms_bot.save_bot_config(1, {
            "enabled": "1",
            "commands": [{"keyword": "balance", "reply_template": "Balance={balance}", "enabled": "1"}],
        })

        spy = _Spy()
        monkeypatch.setattr(comms_providers, "http_send", spy)

        result = comms_bot.handle_inbound(1, phone=KNOWN_PHONE, text="balance")
        assert result.handled is True
        assert result.reason == "command"
        assert result.matched_keyword == "balance"
        assert result.sent is True
        assert "{balance}" not in result.reply_text
        assert "12.5" in result.reply_text
        assert len(spy.calls) == 1


def test_webhook_never_raises_on_garbage(app, client):
    """A malformed / empty payload still returns 200 (never 500)."""
    with app.app_context():
        from app.radius.services import comms_bot

        comms_bot.save_bot_config(1, {"enabled": "1"})

    # No phone/text anywhere → handled=False, but always 200.
    resp = client.post(WEBHOOK, json={"unrelated": "noise"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Totally non-JSON body.
    resp2 = client.post(WEBHOOK, data="not json", content_type="text/plain")
    assert resp2.status_code == 200


def test_webhook_is_csrf_exempt(app):
    """The webhook path must be in the CSRF-exempt set (server-to-server)."""
    # POST without any CSRF token must not be rejected with 400 CSRF failed.
    client = app.test_client()
    with app.app_context():
        from app.radius.services import comms_bot

        comms_bot.save_bot_config(1, {"enabled": "1"})
    resp = client.post(WEBHOOK, json={"phone": KNOWN_PHONE, "message": "x"})
    assert resp.status_code == 200  # not 400 (CSRF) and not 302 (login redirect)


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "bot_admin"
        sess["admin_name"] = "Bot Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "bot-csrf"


def test_settings_page_renders_and_saves(app):
    """The settings page renders (full layout) and a POST persists the config."""
    with app.test_client() as client:
        _auth_session(client)

        # GET the page — defaults are pre-filled so it works out of the box.
        page = client.get("/admin/radius/communications/bot")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "بوت واتساب" in html
        # The read-only webhook URL is shown for copy.
        assert "/communications/bot/webhook" in html

        # POST a config change and confirm it round-trips.
        saved = client.post(
            "/admin/radius/communications/bot",
            data={
                "_csrf_token": "bot-csrf",
                "enabled": "1",
                "greeting": "أهلًا بك",
                "fallback": "غير معروف",
                "cmd_keyword": ["الرصيد"],
                "cmd_reply": ["رصيدك: {balance}"],
                "cmd_enabled": ["1"],
            },
            follow_redirects=False,
        )
        assert saved.status_code in (302, 303)

    with app.app_context():
        from app.radius.services import comms_bot

        cfg = comms_bot.load_bot_config(1)
        assert cfg.enabled is True
        assert cfg.greeting == "أهلًا بك"
        assert len(cfg.active_commands()) == 1
        assert cfg.commands[0]["keyword"] == "الرصيد"
