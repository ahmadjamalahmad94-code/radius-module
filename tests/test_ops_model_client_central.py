"""Operations-assistant model client against the CENTRAL model server
(hoberadius-ai-support deploy/central_model DESIGN §7.3): gateway key header,
allow-listed body, connect/read timeouts, message deadline, circuit breaker,
per-process concurrency cap, key never logged. Pure client tests with a fake
gateway; no app, no DB."""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.radius.services.ops_assistant import model_client as mc

KEY = "hrops_" + "Zx9" * 16
MSGS = [{"role": "system", "content": "S"}, {"role": "user", "content": "مرحبا"},
        {"role": "assistant", "content": '{"action":"choose"}'}, {"role": "tool", "content": "CHOICES []"}]
OK_CONTENT = '{"action":"reply","message":"أهلًا"}'


class FakeGateway:
    """``status``/``delay``/``body`` are read per request; ``seen`` keeps the
    headers and JSON body of every request that reached the server."""

    def __init__(self):
        self.status, self.delay, self.body = 200, 0.0, None
        self.seen: list[tuple[dict, dict]] = []
        self.arrived = threading.Semaphore(0)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.seen.append((dict(self.headers.items()), body))
                outer.arrived.release()
                if outer.delay:
                    time.sleep(outer.delay)
                raw = outer.body if outer.body is not None else json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": OK_CONTENT}}]}).encode()
                try:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass                      # the client gave up (timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gw(monkeypatch):
    g = FakeGateway()
    monkeypatch.setenv(mc.ENV_URL, g.url)
    monkeypatch.setenv(mc.ENV_KEY, KEY)
    monkeypatch.delenv(mc.ENV_TIMEOUT, raising=False)
    mc.reset_state(2)
    yield g
    g.close()
    mc.reset_state()


def refused_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ─────────────────────────── key + body ────────────────────────────

def test_key_header_and_allowlisted_body(gw):
    assert mc.chat(MSGS) == OK_CONTENT
    headers, body = gw.seen[-1]
    assert headers["Authorization"] == "Bearer " + KEY
    assert set(body) <= set(mc.ALLOWED_FIELDS)
    assert all(set(m) == {"role", "content"} and m["role"] in mc.ALLOWED_ROLES for m in body["messages"])
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["max_tokens"] <= 512 and body["temperature"] == 0 and body["stream"] is False


def test_no_key_no_header(gw, monkeypatch):
    monkeypatch.delenv(mc.ENV_KEY)
    mc.chat(MSGS)
    assert "Authorization" not in gw.seen[-1][0]


def test_extra_message_keys_are_dropped_and_bad_roles_refused(gw):
    mc.chat([{"role": "user", "content": "x", "name": "evil", "tool_calls": []}])
    assert gw.seen[-1][1]["messages"][-1] == {"role": "user", "content": "x"}
    n = len(gw.seen)
    with pytest.raises(mc.ModelError) as e:
        mc.chat([{"role": "user", "content": "x"}, {"role": "function", "content": "y"}])
    assert e.value.code == "bad_message" and len(gw.seen) == n
    assert not mc.breaker_open()


def test_key_never_logged_nor_in_errors(gw, caplog):
    caplog.set_level(logging.DEBUG)
    mc.chat(MSGS)
    errors = []
    for status in (401, 500):
        mc.reset_state(2)
        gw.status = status
        with pytest.raises(mc.ModelError) as e:
            mc.chat(MSGS)
        errors.append(e.value)
    assert [e.code for e in errors] == ["unauthorized", "http_error"]
    assert all(KEY not in str(e) and KEY not in repr(e.__dict__) for e in errors)
    assert KEY not in caplog.text


# ─────────────────────────── timeouts + deadline ────────────────────────────

def test_connect_timeout_is_3s_and_read_timeout_from_env(gw, monkeypatch):
    seen = {}
    real = mc.http.client.HTTPConnection

    class Spy(real):
        def __init__(self, *a, **kw):
            seen["connect"] = kw.get("timeout")
            super().__init__(*a, **kw)

        def connect(self):
            super().connect()
            seen["sock"] = self.sock

    monkeypatch.setattr(mc.http.client, "HTTPConnection", Spy)
    monkeypatch.setenv(mc.ENV_TIMEOUT, "17")
    mc.chat(MSGS)
    assert seen["connect"] == mc.CONNECT_TIMEOUT == 3.0
    assert mc.model_timeout() == 17.0
    monkeypatch.delenv(mc.ENV_TIMEOUT)
    assert mc.model_timeout() == 45.0


def test_read_timeout_trips_breaker(gw, monkeypatch):
    monkeypatch.setenv(mc.ENV_TIMEOUT, "1")
    gw.delay = 2.5
    t0 = time.monotonic()
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS)
    assert e.value.code == "timeout" and time.monotonic() - t0 < 2.2
    assert mc.breaker_open()


def test_deadline_caps_the_read_timeout(gw):
    gw.delay = 3.0
    t0 = time.monotonic()
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS, deadline=time.monotonic() + 1.3)   # env timeout is 45 s
    assert e.value.code == "timeout" and time.monotonic() - t0 < 2.5


def test_spent_deadline_never_calls(gw):
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS, deadline=time.monotonic() + 0.2)
    assert e.value.code == "budget_exhausted" and gw.seen == [] and not mc.breaker_open()


# ─────────────────────────── breaker ────────────────────────────

def test_refused_port_opens_breaker_then_it_closes(gw, monkeypatch):
    monkeypatch.setattr(mc, "BREAKER_SECONDS", 0.6)
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS, url=refused_url())
    assert e.value.code == "unreachable" and mc.breaker_open()
    t0 = time.monotonic()
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS)                                     # the healthy server is not even tried
    assert e.value.code == "circuit_open" and gw.seen == [] and time.monotonic() - t0 < 0.1
    time.sleep(0.7)
    assert not mc.breaker_open()
    assert mc.chat(MSGS) == OK_CONTENT and len(gw.seen) == 1


def test_breaker_default_window_is_30s(gw):
    gw.status = 502
    t0 = time.monotonic()
    with pytest.raises(mc.ModelError):
        mc.chat(MSGS)
    assert mc.BREAKER_SECONDS == 30.0
    assert t0 + 29 < mc._breaker_until <= time.monotonic() + 30.0


@pytest.mark.parametrize("status,code,trips", [
    (503, "busy", True), (502, "http_error", True), (401, "unauthorized", True),
    (429, "busy", False), (400, "http_error", False),
])
def test_status_mapping(gw, status, code, trips):
    gw.status = status
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS)
    assert e.value.code == code and mc.breaker_open() is trips
    assert len(gw.seen) == 1                               # no automatic retry


def test_garbage_reply_trips_breaker(gw):
    gw.body = b"<html>proxy error</html>"
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS)
    assert e.value.code == "bad_response" and mc.breaker_open()


# ─────────────────────────── concurrency ────────────────────────────

def test_at_most_two_concurrent_calls(gw):
    gw.delay = 1.5
    results = []

    def worker():
        try:
            results.append(mc.chat(MSGS))
        except mc.ModelError as e:  # pragma: no cover — must not happen
            results.append(e.code)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    assert gw.arrived.acquire(timeout=3) and gw.arrived.acquire(timeout=3)
    t0 = time.monotonic()
    with pytest.raises(mc.ModelError) as e:
        mc.chat(MSGS)
    assert e.value.code == "busy" and time.monotonic() - t0 < 0.1
    assert not mc.breaker_open()
    for t in ts:
        t.join(5)
    assert results == [OK_CONTENT, OK_CONTENT] and len(gw.seen) == 2
    gw.delay = 0
    assert mc.chat(MSGS) == OK_CONTENT                     # slots are released


def test_slot_released_after_failure(gw, monkeypatch):
    mc.reset_state(1)
    with pytest.raises(mc.ModelError):
        mc.chat(MSGS, url=refused_url())
    monkeypatch.setattr(mc, "_breaker_until", 0.0)         # close the breaker, keep the semaphore
    assert mc.chat(MSGS) == OK_CONTENT
