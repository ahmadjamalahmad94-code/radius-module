"""
اختبار MikrotikClient ضد mock server محلي.

يبني TCP server بسيط يلبّي البروتوكول، يفحص login + run + queries + traps.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

import pytest

from app.radius.integration.mikrotik import MikrotikClient
from app.radius.integration.mikrotik.errors import AuthError, MikrotikTrap
from app.radius.integration.mikrotik.protocol import (
    decode_sentence,
    encode_sentence,
)


class MockRouter:
    """خادم TCP يحاكي MikroTik. يدير أمرًا واحدًا في كل مرة."""

    def __init__(self, handler: Callable[[list[str]], list[list[str | bytes]]]) -> None:
        """`handler` يأخذ كلمات الجملة الواردة، يُرجع قائمة جمل ردود."""
        self.handler = handler
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.host, self.port = self._srv.getsockname()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass

    def _run(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        try:
            stream = conn.makefile("rwb", buffering=0)
            while not self._stop:
                try:
                    raw_words = decode_sentence(lambda: stream.read(1)[0])
                except (IndexError, OSError):
                    break
                words = [w.decode("utf-8") for w in raw_words]
                replies = self.handler(words)
                for rep in replies:
                    stream.write(encode_sentence(rep))
        finally:
            conn.close()


@pytest.fixture
def router_factory():
    routers: list[MockRouter] = []
    def _make(handler):
        r = MockRouter(handler)
        r.start()
        routers.append(r)
        return r
    yield _make
    for r in routers:
        r.stop()


# ─────────────── login flows ───────────────

def test_login_success(router_factory):
    def handler(words):
        # تحقّق من شكل login
        assert words[0] == "/login"
        return [["!done"]]
    r = router_factory(handler)
    c = MikrotikClient(host=r.host, port=r.port, username="admin", password="x")
    c.connect()
    c.close()


def test_login_bad_creds(router_factory):
    def handler(words):
        return [["!trap", "=message=invalid user or password"], ["!done"]]
    r = router_factory(handler)
    c = MikrotikClient(host=r.host, port=r.port, username="admin", password="bad")
    with pytest.raises(AuthError):
        c.connect()


# ─────────────── run / print_ ───────────────

def test_run_returns_re_then_done(router_factory):
    def handler(words):
        if words[0] == "/login":
            return [["!done"]]
        if words[0] == "/system/identity/print":
            return [
                ["!re", "=name=MyRouter"],
                ["!done"],
            ]
        return [["!done"]]
    r = router_factory(handler)
    with MikrotikClient(host=r.host, port=r.port, username="u", password="p") as c:
        rows = list(c.print_("/system/identity/print"))
        assert rows == [{"name": "MyRouter"}]


def test_trap_raises_with_category(router_factory):
    def handler(words):
        if words[0] == "/login":
            return [["!done"]]
        return [
            ["!trap", "=category=1", "=message=input does not match"],
            ["!done"],
        ]
    r = router_factory(handler)
    with MikrotikClient(host=r.host, port=r.port, username="u", password="p") as c:
        with pytest.raises(MikrotikTrap) as exc:
            c.run("/ip/route/add", {"address": "bad"})
        assert exc.value.category == 1
        assert "input" in exc.value.message


def test_print_multiple_re(router_factory):
    def handler(words):
        if words[0] == "/login":
            return [["!done"]]
        return [
            ["!re", "=name=u1", "=disabled=no"],
            ["!re", "=name=u2", "=disabled=yes"],
            ["!re", "=name=u3", "=disabled=no"],
            ["!done"],
        ]
    r = router_factory(handler)
    with MikrotikClient(host=r.host, port=r.port, username="u", password="p") as c:
        rows = list(c.print_("/ip/hotspot/user/print"))
        assert len(rows) == 3
        assert rows[0]["name"] == "u1"
        assert rows[2]["disabled"] == "no"


def test_healthcheck_true_false(router_factory):
    def good(words):
        if words[0] == "/login":
            return [["!done"]]
        return [["!re", "=name=ok"], ["!done"]]
    r = router_factory(good)
    with MikrotikClient(host=r.host, port=r.port, username="u", password="p") as c:
        assert c.healthcheck() is True
