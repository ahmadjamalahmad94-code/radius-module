"""
MikrotikClient — اتصال TCP/TLS، login، وإرسال/استقبال جمل.

الاستخدام:
    with MikrotikClient(host="10.0.0.1", username="admin", password="x") as mt:
        for row in mt.print_("/system/resource/print"):
            print(row)
        mt.run("/ip/hotspot/user/add", {"name":"u1","password":"x","profile":"default"})
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
from contextlib import contextmanager
from itertools import count
from typing import Iterable, Iterator, Optional

from .errors import AuthError, ConnectError, MikrotikError, MikrotikTrap, ProtocolError
from .protocol import (
    build_api_attr,
    build_attr,
    decode_sentence,
    encode_sentence,
    words_to_dict,
)

_LOG = logging.getLogger(__name__)

# 20s default — empirically, MT routers on public IPs (the common
# deployment for ISP / hotel WiFi management) often take 8-15s for
# a clean /ip/dhcp-server/lease/print with 500+ leases. The old 10s
# default tripped on slow uplinks. Per-router `timeout_sec` config
# still wins when set explicitly.
_DEFAULT_TIMEOUT = 20.0


class MikrotikClient:
    """عميل متزامن (thread-safe عبر lock داخلي)."""

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        port: int | None = None,
        use_tls: bool = False,
        verify_tls: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.verify_tls = verify_tls
        self.port = port or (8729 if use_tls else 8728)
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._stream = None  # makefile object
        self._lock = threading.Lock()
        self._tag_seq = count(1)

    # ─────────────── context manager ───────────────

    def __enter__(self) -> "MikrotikClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ─────────────── lifecycle ───────────────

    def connect(self) -> None:
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ConnectError(f"تعذّر الاتصال بـ {self.host}:{self.port} — {e}") from e

        if self.use_tls:
            ctx = self._make_tls_context()
            try:
                raw = ctx.wrap_socket(raw, server_hostname=self.host if self.verify_tls else None)
            except ssl.SSLError as e:
                raw.close()
                raise ConnectError(f"فشل TLS handshake: {e}") from e

        raw.settimeout(self.timeout)
        self._sock = raw
        # نُغلّف بـ buffered streams لقراءة بايت بايت بكفاءة
        self._stream = raw.makefile("rwb", buffering=0)
        self._login()

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        except OSError:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._stream = None
        self._sock = None

    def _make_tls_context(self) -> ssl.SSLContext:
        if self.verify_tls:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # MikroTik بدون شهادة يدعم Anonymous DH cipher
            try:
                ctx.set_ciphers("ADH-AES256-SHA:ADH-AES128-SHA:DEFAULT")
            except ssl.SSLError:
                pass
        return ctx

    # ─────────────── login ───────────────

    def _login(self) -> None:
        """RouterOS 6.43+: plain-name+password في جملة واحدة."""
        try:
            replies = self._exchange([
                "/login",
                build_attr("name", self.username),
                build_attr("password", self.password),
            ])
        except MikrotikTrap as t:
            raise AuthError(t.message) from t
        for r in replies:
            if r["reply"] == "!done":
                return
        raise AuthError("login: لم يصلنا !done")

    # ─────────────── low-level I/O ───────────────

    def _send(self, words: list[str | bytes]) -> None:
        if self._stream is None:
            raise ConnectError("الاتصال مغلق")
        data = encode_sentence(words)
        try:
            self._stream.write(data)
        except OSError as e:
            raise ConnectError(f"فشل الإرسال: {e}") from e

    def _read_byte(self) -> int:
        if self._stream is None:
            raise ConnectError("الاتصال مغلق")
        b = self._stream.read(1)
        if not b:
            raise ProtocolError("EOF — الراوتر أغلق الاتصال")
        return b[0]

    def _recv_sentence(self) -> dict:
        raw_words = decode_sentence(self._read_byte)
        return words_to_dict(raw_words)

    def _exchange(self, words: list[str | bytes], *, tag: Optional[str] = None) -> list[dict]:
        """
        يرسل جملة، ثم يجمع كل الردود حتى !done أو !fatal.
        إن وُجد !trap بين النتائج، يُرفع بعد !done.
        """
        with self._lock:
            if tag is not None:
                words = list(words) + [build_api_attr("tag", tag)]
            self._send(words)
            out: list[dict] = []
            trap: dict | None = None
            while True:
                s = self._recv_sentence()
                out.append(s)
                rep = s["reply"]
                if rep == "!trap":
                    trap = s
                elif rep == "!fatal":
                    raise ConnectError(s["attrs"].get("message", "!fatal"))
                elif rep == "!done":
                    break
                elif rep is None:
                    raise ProtocolError(f"جملة بلا reply: {s}")
            if trap:
                cat_str = trap["attrs"].get("category")
                cat = int(cat_str) if cat_str and cat_str.isdigit() else None
                raise MikrotikTrap(
                    trap["attrs"].get("message", "trap"),
                    category=cat,
                    sentence=trap,
                )
            return out

    # ─────────────── high-level API ───────────────

    def run(self, command: str, attrs: dict | None = None,
            queries: list[str] | None = None) -> list[dict]:
        """
        تنفيذ أمر متزامن. يُرجع قائمة جمل (مع `!re` لو وُجدت).
        ارفع `MikrotikTrap` لو فشل، `ConnectError` لو الاتصال انقطع.
        """
        words: list[str | bytes] = [command]
        for k, v in (attrs or {}).items():
            words.append(build_attr(k, str(v)))
        for q in (queries or []):
            words.append(q)
        return self._exchange(words)

    def print_(self, path: str, *,
               proplist: list[str] | None = None,
               queries: list[str] | None = None) -> Iterator[dict]:
        """
        يُرجع iterator لسطور `!re` فقط.
        `path` مثال: `/ip/hotspot/user/print` أو `/system/resource/print`.
        """
        attrs: dict = {}
        if proplist:
            attrs[".proplist"] = ",".join(proplist)
        for s in self.run(path, attrs=attrs, queries=queries):
            if s["reply"] == "!re":
                yield s["attrs"]

    @contextmanager
    def override_timeout(self, seconds: float):
        """Temporarily raise the socket read timeout for one slow operation
        (e.g. ``/system/backup/save`` on a CCR writes a binary backup to flash
        for several seconds — far past the snappy default). Applies to the LIVE
        socket too, so it works even when the pool hands back a reused
        connection that was opened with the short default. Restores the prior
        timeout afterwards."""
        prev = self.timeout
        new = float(seconds)
        self.timeout = new
        if self._sock is not None:
            try:
                self._sock.settimeout(new)
            except OSError:  # pragma: no cover — socket already torn down
                pass
        try:
            yield
        finally:
            self.timeout = prev
            if self._sock is not None:
                try:
                    self._sock.settimeout(prev)
                except OSError:  # pragma: no cover
                    pass

    def next_tag(self) -> str:
        return str(next(self._tag_seq))

    def healthcheck(self) -> bool:
        try:
            list(self.print_("/system/identity/print"))
            return True
        except MikrotikError:
            return False
