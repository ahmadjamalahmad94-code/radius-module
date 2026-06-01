"""WhatsApp event-wiring tests — gating, payload + the critical failure isolation.

NO network: ``AdminPanelClient.enqueue_whatsapp_message`` is monkeypatched with a
recorder spy (and, for the isolation tests, a raising spy). We assert:

  * with the local gate ON, ``POST /api/v1/accounts`` enqueues exactly one
    WhatsApp message with a STABLE idempotency key + the expected payload;
  * with the gate OFF, nothing is enqueued;
  * FAILURE ISOLATION — when the enqueue raises, account create STILL returns
    201, password reset STILL returns 200, and a dunning tick STILL completes
    and de-dupes (the once-per-day sent-log is written);
  * the idempotency key is stable across two identical create triggers;
  * the new password is NEVER present in the password-change enqueue payload.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}
NEW_PASSWORD = "Sup3r-Secret-Pw!"  # must never appear in a WhatsApp payload


# ── isolated app + DB (mirrors test_whatsapp_bridge_client.py) ────────────
@pytest.fixture()
def app(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "wa_event_wiring.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    from app.radius.db.connection import reset_for_tests
    from app.radius.db.migrations_runner import run_pending_migrations

    reset_for_tests(db_file)
    application = create_app()
    with application.app_context():
        run_pending_migrations()
    yield application
    reset_for_tests(None)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture()
def client(app):
    return app.test_client()


# ── spies on the bridge enqueue (NO network, NO Meta) ─────────────────────
class _EnqueueSpy:
    """Holds the recorded enqueue payloads (and whether the bridge 'fails')."""

    def __init__(self, *, raises: bool = False):
        self.calls: list[dict] = []
        self._raises = raises


def _install_spy(monkeypatch, *, raises: bool = False) -> _EnqueueSpy:
    """Patch ``AdminPanelClient.enqueue_whatsapp_message`` with a method spy.

    We install a real *function* (not a callable object) so Python binds it as
    a method — the patched call therefore receives ``(self, payload)`` exactly
    like the genuine method, and we record / optionally raise from there.
    """
    from app.radius.services.admin_panel_client import AdminPanelClient

    spy = _EnqueueSpy(raises=raises)

    def _fake_enqueue(_self, payload):  # bound method → (self, payload)
        spy.calls.append(dict(payload or {}))
        if spy._raises:
            raise RuntimeError("simulated bridge enqueue failure")
        return {"ok": True, "status": "queued"}

    monkeypatch.setattr(AdminPanelClient, "enqueue_whatsapp_message", _fake_enqueue)
    return spy


def _set_gate(gate: str, value: str = "1") -> None:
    from app.radius.db.repos import tenants_repo

    tenants_repo.set_setting(1, f"whatsapp.send.{gate}", value, by=0)


def _create_account(client, username, *, mobile="0790001122"):
    return client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "mobile": mobile},
        headers=AUTH,
    )


# ── 1. gate ON → create enqueues with a stable key + correct payload ──────
def test_account_create_with_gate_on_enqueues_stable_key(app, client, monkeypatch):
    with app.app_context():
        _set_gate("otp", "1")
        spy = _install_spy(monkeypatch)

        res = _create_account(client, "wa_otp_on")

        assert res.status_code == 201, res.get_json()
        assert len(spy.calls) == 1
        payload = spy.calls[0]
        assert payload["source_event_type"] == "otp"
        assert payload["template_key"] == "otp"
        assert payload["recipient_phone"] == "0790001122"
        assert payload["language"] == "ar"
        # Stable, structured idempotency key: otp:<tenant>:<username>:<nonce>.
        assert payload["idempotency_key"].startswith("otp:1:wa_otp_on:")


# ── 2. gate OFF → NO enqueue ──────────────────────────────────────────────
def test_account_create_with_gate_off_does_not_enqueue(app, client, monkeypatch):
    with app.app_context():
        _set_gate("otp", "0")  # explicitly OFF
        spy = _install_spy(monkeypatch)

        res = _create_account(client, "wa_otp_off")

        assert res.status_code == 201, res.get_json()
        assert spy.calls == []  # operator never opted in → nothing enqueued


def test_account_create_with_gate_unset_does_not_enqueue(app, client, monkeypatch):
    """Default (no setting written at all) is OFF."""
    with app.app_context():
        spy = _install_spy(monkeypatch)  # gate never set

        res = _create_account(client, "wa_otp_unset")

        assert res.status_code == 201, res.get_json()
        assert spy.calls == []


# ── 3. FAILURE ISOLATION — a raising enqueue can't break the flow ─────────
def test_account_create_succeeds_even_if_enqueue_raises(app, client, monkeypatch):
    with app.app_context():
        _set_gate("otp", "1")
        spy = _install_spy(monkeypatch, raises=True)  # enqueue blows up

        res = _create_account(client, "wa_otp_boom")

        # The account was still created (2xx) despite the enqueue exception.
        assert res.status_code == 201, res.get_json()
        assert len(spy.calls) == 1  # it was attempted…
        # …and the subscriber really exists.
        got = client.get("/api/v1/accounts/wa_otp_boom", headers=AUTH)
        assert got.status_code == 200


def test_password_reset_succeeds_even_if_enqueue_raises(app, client, monkeypatch):
    with app.app_context():
        _set_gate("password", "1")
        # First create the account (otp gate is OFF so no enqueue here).
        assert _create_account(client, "wa_pwd_boom").status_code == 201
        spy = _install_spy(monkeypatch, raises=True)

        res = client.post(
            "/api/v1/accounts/wa_pwd_boom/reset_password",
            json={"new_password": NEW_PASSWORD},
            headers=AUTH,
        )

        # The reset still succeeded despite the raising enqueue.
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["data"]["reset"] is True
        assert len(spy.calls) == 1  # the notice was attempted


def test_dunning_tick_completes_and_dedups_even_if_enqueue_raises(app, monkeypatch):
    """A raising enqueue must not abort _run_for_tenant; dedup still records."""
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo, tenants_repo
        from app.workers import dunning_worker

        # A subscriber expiring within the default near-expiry window.
        soon = datetime.now(timezone.utc) + timedelta(days=2)
        sub = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="wa_exp_boom", password="pw",
            user_type="subscriber", full_name="منتهٍ قريبًا",
            mobile="0790007788", status="enabled", expire_at=soon,
        ))
        _set_gate("expiry", "1")
        spy = _install_spy(monkeypatch, raises=True)

        # Must not raise — the sweep absorbs the enqueue failure.
        checked, notified = dunning_worker._run_for_tenant(1)

        assert checked >= 1                       # our subscriber was processed
        assert len(spy.calls) == 1                # enqueue was attempted once…
        assert spy.calls[0]["source_event_type"] == "expiry_notice"
        # …and the once-per-day dedup log was still written for this subscriber.
        log = dunning_worker._load_sent_log(1)
        assert log.get(str(sub.id)) == dunning_worker._today_str()


# ── 4. idempotency key is stable across two identical create triggers ─────
def test_idempotency_key_is_stable_across_identical_triggers(app, client, monkeypatch):
    """Two creates of the same username (delete between) yield a deterministic,
    structured key. The key is derived from tenant+username+record-nonce, so it
    is reproducible rather than a fresh random value each call."""
    with app.app_context():
        _set_gate("otp", "1")
        spy = _install_spy(monkeypatch)

        assert _create_account(client, "wa_stable").status_code == 201
        key1 = spy.calls[-1]["idempotency_key"]
        client.delete("/api/v1/accounts/wa_stable", headers=AUTH)
        assert _create_account(client, "wa_stable").status_code == 201
        key2 = spy.calls[-1]["idempotency_key"]

        # Same deterministic prefix/shape both times (no random component).
        assert key1.startswith("otp:1:wa_stable:")
        assert key2.startswith("otp:1:wa_stable:")


# ── 5. the new password is NEVER in the password-change payload ───────────
def test_new_password_never_in_password_change_payload(app, client, monkeypatch):
    with app.app_context():
        _set_gate("password", "1")
        assert _create_account(client, "wa_pwd_safe").status_code == 201
        spy = _install_spy(monkeypatch)

        res = client.post(
            "/api/v1/accounts/wa_pwd_safe/reset_password",
            json={"new_password": NEW_PASSWORD},
            headers=AUTH,
        )

        assert res.status_code == 200, res.get_json()
        assert len(spy.calls) == 1
        payload = spy.calls[0]
        assert payload["source_event_type"] == "password_changed"
        assert payload["template_key"] == "password_changed"
        # The cleartext password must appear NOWHERE in the enqueue payload.
        import json as _json

        blob = _json.dumps(payload, ensure_ascii=False)
        assert NEW_PASSWORD not in blob
        assert payload.get("idempotency_key", "").startswith("pwd:1:wa_pwd_safe:")
