"""VPS-side TCP proxy for remote-access sessions (Sprint 5 amend).

The operator sits outside the customer's network. The only thing
reachable from the public internet is the HobeRadius VPS. The
customer's MikroTik is reachable from the VPS via the `hr-wg`
WireGuard tunnel, but NOT from the operator's browser. So we
relay TCP both ways:

  Operator browser  ──TCP──►  VPS_PUBLIC_IP:port
                                    │
                                    │  this module's thread
                                    ▼
                              ROUTER_WG_IP:port  (over hr-wg)
                                    │
                                    │  customer-side NAT (Sprint 5)
                                    ▼
                              DEVICE_INTERNAL_IP:device_port

One thread per session, daemonised so they die with the process.
Each accepted connection forks a pair of forwarder threads
(client→upstream and upstream→client) that close cleanly when
either side hangs up.

Why Python-level proxy instead of iptables DNAT:
  • No root / sudo needed inside the HobeRadius container.
  • Works regardless of host firewall, Docker network mode.
  • Per-session port can be a high non-privileged number.
  • Cleanup is trivial — kill the listener socket and the
    accept loop returns.
"""
from __future__ import annotations

import logging
import select
import socket
import threading
from typing import Optional

_LOG = logging.getLogger(__name__)

# Tunables.
LISTEN_HOST = "0.0.0.0"              # bind on every iface
BUFFER_SIZE = 65536
ACCEPT_TIMEOUT_SEC = 1.0             # how often the accept loop
                                     # checks the stop flag


class _SessionProxy:
    """One listener + accept loop tied to one session_id."""

    def __init__(
        self,
        *,
        session_id: int,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
    ) -> None:
        self.session_id = int(session_id)
        self.listen_port = int(listen_port)
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self._stop = threading.Event()
        self._listener: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind + spawn the accept loop. Raises OSError on bind
        failure so the caller can flip the session row to
        `failed` and surface a clear error."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(ACCEPT_TIMEOUT_SEC)
        sock.bind((LISTEN_HOST, self.listen_port))
        sock.listen(8)
        self._listener = sock
        self._thread = threading.Thread(
            target=self._accept_loop,
            name=f"vps-proxy-{self.session_id}",
            daemon=True,
        )
        self._thread.start()
        _LOG.info(
            "[vps-proxy %d] listening :%d → %s:%d",
            self.session_id, self.listen_port,
            self.upstream_host, self.upstream_port,
        )

    def stop(self) -> None:
        """Tell the accept loop to die + close the listener.
        Existing connections will close when either end hangs
        up — we don't track them individually."""
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        _LOG.info("[vps-proxy %d] stopped", self.session_id)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client_sock, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                # Listener closed — exit cleanly.
                break
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock, addr),
                name=f"vps-proxy-{self.session_id}-conn",
                daemon=True,
            )
            t.start()

    def _handle_client(self, client_sock: socket.socket, addr) -> None:
        upstream: Optional[socket.socket] = None
        try:
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port),
                timeout=10.0,
            )
        except OSError as exc:
            _LOG.warning(
                "[vps-proxy %d] upstream %s:%d unreachable: %s",
                self.session_id, self.upstream_host,
                self.upstream_port, exc,
            )
            try: client_sock.close()
            except OSError: pass
            return

        _LOG.debug(
            "[vps-proxy %d] conn from %s opened",
            self.session_id, addr,
        )

        # Two-way splice via select. Each side reads and forwards
        # to the other. Closes both ends when either returns 0.
        try:
            client_sock.setblocking(False)
            upstream.setblocking(False)
            sockets = [client_sock, upstream]
            while True:
                readable, _, errored = select.select(
                    sockets, [], sockets, 30.0,
                )
                if errored:
                    break
                if not readable:
                    # 30 s idle — close to free resources.
                    break
                disconnected = False
                for r in readable:
                    try:
                        data = r.recv(BUFFER_SIZE)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        disconnected = True
                        break
                    if not data:
                        disconnected = True
                        break
                    other = upstream if r is client_sock else client_sock
                    try:
                        other.sendall(data)
                    except OSError:
                        disconnected = True
                        break
                if disconnected:
                    break
        finally:
            for s in (client_sock, upstream):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass


# ─── registry ──────────────────────────────────────────────────


_proxies: dict[int, _SessionProxy] = {}
_lock = threading.Lock()


def start_proxy(
    *,
    session_id: int,
    listen_port: int,
    upstream_host: str,
    upstream_port: int,
) -> tuple[bool, str]:
    """Public API. Returns (ok, error). On success the proxy is
    running in a daemon thread and `stop_proxy(session_id)` will
    clean it up later (cron sweep or manual close)."""
    with _lock:
        if session_id in _proxies:
            return True, "already running"
        proxy = _SessionProxy(
            session_id=session_id,
            listen_port=listen_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
        )
        try:
            proxy.start()
        except OSError as exc:
            return False, f"bind failed: {exc}"
        _proxies[session_id] = proxy
        return True, ""


def stop_proxy(session_id: int) -> None:
    """Idempotent — silently no-ops if the proxy isn't running.
    Useful so the cron sweep can call it on every expired
    session without first asking «is it ours?»."""
    with _lock:
        proxy = _proxies.pop(session_id, None)
    if proxy is not None:
        proxy.stop()


def active_count() -> int:
    with _lock:
        return len(_proxies)
